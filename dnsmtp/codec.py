"""Payload <-> TXT-record encoding for DNSMTP.

A single DNS TXT record is a sequence of character-strings, each at most 255
bytes. We base64-encode the raw payload (so arbitrary binary survives DNS),
slice the base64 text into <=255-char strings, then group those strings into
records of up to ``STRINGS_PER_RECORD`` strings each.

Four strings per record yields roughly 1020 payload characters per record,
which comfortably fits inside Route 53's per-record limits while keeping the
number of records manageable.
"""

import base64

# Each TXT string is <=255 chars; 4 strings gives ~1020 base64 chars/record.
STRINGS_PER_RECORD = 4

# Max length of a single TXT character-string.
_MAX_STRING = 255


def _chunk_str(s: str, size: int) -> list[str]:
    """Split ``s`` into pieces of at most ``size`` characters."""
    return [s[i : i + size] for i in range(0, len(s), size)]


def encode_payload(raw: bytes) -> list[list[str]]:
    """Encode ``raw`` bytes into a list of TXT records.

    Each record is a list of up to ``STRINGS_PER_RECORD`` strings, and each
    string is at most 255 characters. Round-trips exactly with
    :func:`decode_payload`, including for empty input.
    """
    b64 = base64.b64encode(raw).decode("ascii")
    strings = _chunk_str(b64, _MAX_STRING)
    records = [
        strings[i : i + STRINGS_PER_RECORD]
        for i in range(0, len(strings), STRINGS_PER_RECORD)
    ]
    return records


def decode_payload(records: list[list[str]]) -> bytes:
    """Decode a list of TXT records back into the original bytes.

    Inverse of :func:`encode_payload`.
    """
    b64 = "".join("".join(record) for record in records)
    return base64.b64decode(b64.encode("ascii"))
