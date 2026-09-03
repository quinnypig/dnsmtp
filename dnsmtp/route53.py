"""Route 53 transport for DNSMTP.

This module treats a Route 53 hosted zone as a (deeply inadvisable) mail
store. Each message becomes a small constellation of TXT records:

* an ``_index`` record per mailbox holding the list of message ids,
* a ``_meta`` record per message holding a JSON envelope, and
* one ``p{NNNN}`` payload record per codec chunk.

All reads go through the Route 53 API (``list_resource_record_sets``) rather
than a DNS resolver, so we see authoritative state immediately and never fight
resolver caches.

TXT wire format
---------------
A Route 53 TXT record's ``Value`` is a sequence of double-quoted
character-strings joined by spaces, e.g. ``"abc" "def"``. Inside a quoted
string, a backslash and a literal double-quote must be backslash-escaped, and
``\\DDD`` decimal escapes may appear on read. Because everything we store is
base64 text, escaping is effectively a no-op in practice, but we handle it
correctly regardless. :func:`_format_txt_value` produces this on write;
:func:`_parse_txt_value` reverses it on read.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass


@dataclass
class Message:
    """A message reassembled from its Route 53 records.

    ``records`` is the list-of-records payload form expected by
    :func:`dnsmtp.codec.decode_payload`.
    """

    mid: str
    sender: str
    subject: str
    date: str
    salt_b64: str
    encrypted: bool
    records: list[list[str]]


# ---------------------------------------------------------------------------
# TXT value (de)serialization
# ---------------------------------------------------------------------------
def _format_txt_value(strings: list[str]) -> str:
    """Render a list of character-strings as a Route 53 TXT ``Value``.

    Each string is wrapped in double quotes with backslashes and double
    quotes escaped; strings are joined by single spaces.
    """
    parts = []
    for s in strings:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{escaped}"')
    return " ".join(parts)


def _parse_txt_value(value: str) -> list[str]:
    """Parse a Route 53 TXT ``Value`` back into its character-strings.

    Understands ``\\"``/``\\\\`` escapes and ``\\DDD`` decimal escapes. Text
    outside of quotes (whitespace between strings) is ignored.
    """
    strings: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        if value[i].isspace():
            i += 1
            continue
        if value[i] != '"':
            # Unexpected unquoted text; skip defensively.
            i += 1
            continue
        i += 1  # consume opening quote
        buf: list[str] = []
        while i < n and value[i] != '"':
            if value[i] == "\\" and i + 1 < n:
                nxt = value[i + 1]
                if (
                    nxt.isdigit()
                    and i + 3 < n
                    and value[i + 2].isdigit()
                    and value[i + 3].isdigit()
                ):
                    buf.append(chr(int(value[i + 1 : i + 4])))
                    i += 4
                else:
                    buf.append(nxt)
                    i += 2
            else:
                buf.append(value[i])
                i += 1
        i += 1  # consume closing quote
        strings.append("".join(buf))
    return strings


def _encode_json_strings(obj: object) -> list[str]:
    """Serialize ``obj`` to compact JSON, base64 it, split into <=255 strings."""
    raw = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return [b64[i : i + 255] for i in range(0, len(b64), 255)] or [""]


def _decode_json_strings(strings: list[str]) -> object:
    """Inverse of :func:`_encode_json_strings`."""
    b64 = "".join(strings)
    raw = base64.b64decode(b64.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def _same_name(a: str, b: str) -> bool:
    """Compare two DNS names ignoring trailing dots and case."""
    return a.rstrip(".").lower() == b.rstrip(".").lower()


class Route53Transport:
    """Send, list, fetch, and delete DNSMTP messages in a hosted zone."""

    def __init__(self, hosted_zone_id: str, zone_domain: str, client=None):
        self.hosted_zone_id = hosted_zone_id
        self.zone_domain = zone_domain.rstrip(".")
        if client is None:
            import boto3

            client = boto3.client("route53")
        self.client = client

    # -- low-level record IO ------------------------------------------------
    def _read_rrset(self, name: str) -> dict | None:
        """Return the full TXT ResourceRecordSet for ``name``, or None."""
        resp = self.client.list_resource_record_sets(
            HostedZoneId=self.hosted_zone_id,
            StartRecordName=name,
            StartRecordType="TXT",
            MaxItems="1",
        )
        for rrset in resp.get("ResourceRecordSets", []):
            if rrset.get("Type") == "TXT" and _same_name(rrset.get("Name", ""), name):
                return rrset
        return None

    def _read_strings(self, name: str) -> list[str] | None:
        """Return the flattened character-strings of TXT record ``name``.

        All ResourceRecords of the set are concatenated in order, so a record
        that Route 53 split across entries reassembles correctly.
        """
        rrset = self._read_rrset(name)
        if rrset is None:
            return None
        strings: list[str] = []
        for rr in rrset.get("ResourceRecords", []):
            strings.extend(_parse_txt_value(rr["Value"]))
        return strings

    def _txt_change(self, action: str, name: str, strings: list[str], ttl: int) -> dict:
        return {
            "Action": action,
            "ResourceRecordSet": {
                "Name": name,
                "Type": "TXT",
                "TTL": ttl,
                "ResourceRecords": [{"Value": _format_txt_value(strings)}],
            },
        }

    def _apply(self, changes: list[dict], comment: str = "") -> str:
        resp = self.client.change_resource_record_sets(
            HostedZoneId=self.hosted_zone_id,
            ChangeBatch={"Comment": comment, "Changes": changes},
        )
        return resp["ChangeInfo"]["Id"]

    # -- index helpers ------------------------------------------------------
    def _read_index(self, user: str, folder: str) -> list[str]:
        from dnsmtp.naming import index_name

        strings = self._read_strings(index_name(user, folder, self.zone_domain))
        if not strings:
            return []
        value = _decode_json_strings(strings)
        return list(value) if isinstance(value, list) else []

    def _write_index(self, user: str, folder: str, ids: list[str], ttl: int) -> str:
        from dnsmtp.naming import index_name

        name = index_name(user, folder, self.zone_domain)
        change = self._txt_change("UPSERT", name, _encode_json_strings(ids), ttl)
        return self._apply([change], comment="dnsmtp index update")

    # -- public API ---------------------------------------------------------
    def send(
        self,
        to_user: str,
        folder: str,
        meta: dict,
        records: list[list[str]],
        ttl: int = 60,
    ) -> str:
        """UPSERT the meta + payload records, then append mid to the index.

        Returns the Change Id of the index update (the final operation), which
        is a superset-in-time of the content write submitted just before it.
        """
        from dnsmtp.naming import chunk_name, meta_name

        mid = meta["mid"]

        stored_meta = {
            "from": meta.get("from", ""),
            "subject": meta.get("subject", ""),
            "date": meta.get("date", ""),
            "chunks": len(records),
            "salt": meta.get("salt", ""),
            "enc": bool(meta.get("enc", False)),
        }

        changes: list[dict] = [
            self._txt_change(
                "UPSERT",
                meta_name(mid, to_user, folder, self.zone_domain),
                _encode_json_strings(stored_meta),
                ttl,
            )
        ]
        for i, record in enumerate(records):
            changes.append(
                self._txt_change(
                    "UPSERT",
                    chunk_name(mid, i, to_user, folder, self.zone_domain),
                    record,
                    ttl,
                )
            )
        self._apply(changes, comment=f"dnsmtp send {mid}")

        # Read-modify-write the mailbox index.
        ids = self._read_index(to_user, folder)
        if mid not in ids:
            ids.append(mid)
        return self._write_index(to_user, folder, ids, ttl)

    def wait_insync(self, change_id: str, timeout: float = 120) -> None:
        """Poll ``get_change`` until the change reaches INSYNC or we time out."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.client.get_change(Id=change_id)["ChangeInfo"]["Status"]
            if status == "INSYNC":
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"change {change_id} still {status} after {timeout}s"
                )
            time.sleep(2)

    def list_messages(self, user: str, folder: str = "inbox") -> list[str]:
        """Return the message ids in a mailbox (empty if the index is absent)."""
        return self._read_index(user, folder)

    def fetch(self, mid: str, user: str, folder: str = "inbox") -> Message:
        """Read the meta + payload records and reassemble a :class:`Message`."""
        from dnsmtp.naming import chunk_name, meta_name

        meta_strings = self._read_strings(
            meta_name(mid, user, folder, self.zone_domain)
        )
        if meta_strings is None:
            raise LookupError(f"no such message: {mid} in {user}/{folder}")
        meta = _decode_json_strings(meta_strings)
        if not isinstance(meta, dict):
            raise ValueError(f"corrupt meta record for {mid}")

        chunks = int(meta.get("chunks", 0))
        records: list[list[str]] = []
        for i in range(chunks):
            strings = self._read_strings(
                chunk_name(mid, i, user, folder, self.zone_domain)
            )
            records.append(strings if strings is not None else [])

        return Message(
            mid=mid,
            sender=meta.get("from", ""),
            subject=meta.get("subject", ""),
            date=meta.get("date", ""),
            salt_b64=meta.get("salt", ""),
            encrypted=bool(meta.get("enc", False)),
            records=records,
        )

    def delete(self, mid: str, user: str, folder: str = "inbox") -> str:
        """DELETE the meta + payload records and drop mid from the index.

        Returns the Change Id of the record deletion batch.
        """
        from dnsmtp.naming import chunk_name, meta_name

        changes: list[dict] = []
        meta_name_str = meta_name(mid, user, folder, self.zone_domain)
        meta_rrset = self._read_rrset(meta_name_str)

        chunks = 0
        if meta_rrset is not None:
            strings: list[str] = []
            for rr in meta_rrset.get("ResourceRecords", []):
                strings.extend(_parse_txt_value(rr["Value"]))
            try:
                meta = _decode_json_strings(strings)
                chunks = int(meta.get("chunks", 0)) if isinstance(meta, dict) else 0
            except Exception:
                chunks = 0
            changes.append({"Action": "DELETE", "ResourceRecordSet": meta_rrset})

        for i in range(chunks):
            rrset = self._read_rrset(chunk_name(mid, i, user, folder, self.zone_domain))
            if rrset is not None:
                changes.append({"Action": "DELETE", "ResourceRecordSet": rrset})

        change_id = ""
        if changes:
            change_id = self._apply(changes, comment=f"dnsmtp delete {mid}")

        # Remove from index regardless of what physically existed.
        ids = self._read_index(user, folder)
        if mid in ids:
            ids = [x for x in ids if x != mid]
            index_change = self._write_index(user, folder, ids, 60)
            if not change_id:
                change_id = index_change

        return change_id
