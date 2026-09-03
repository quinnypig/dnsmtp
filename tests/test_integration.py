"""End-to-end integration tests for DNSMTP's Route 53 transport.

These exercise the full delivery/retrieval loop against a moto-mocked Route 53
so no real AWS account (or hosted zone) is required. The point of proof is that
a message encoded into TXT records, delivered via ChangeResourceRecordSets, and
read back via the Route 53 API round-trips byte-for-byte.

The sibling module ``dnsmtp.route53`` is built concurrently with this file; if
it is absent the collection-time import below fails loudly rather than these
tests passing against nothing.
"""

import base64
import uuid

import boto3
from moto import mock_aws

from dnsmtp import codec, crypto, naming
from dnsmtp.route53 import Route53Transport

ZONE_DOMAIN = "dnsmtp.test"
PASSPHRASE = "correct horse battery staple"


def _make_transport():
    """Create a moto-backed Route 53 client, a hosted zone, and a transport."""
    client = boto3.client("route53", region_name="us-east-1")
    zone = client.create_hosted_zone(
        Name=ZONE_DOMAIN + ".",
        CallerReference=str(uuid.uuid4()),
    )
    zone_id = zone["HostedZone"]["Id"]
    transport = Route53Transport(zone_id, ZONE_DOMAIN, client=client)
    return client, zone_id, transport


def _deliver(transport, user, folder, plaintext, *, encrypt):
    """Encode + (optionally) encrypt ``plaintext`` and deliver it.

    Returns the generated message id so callers can fetch/delete it.
    """
    mid = naming.new_message_id()
    if encrypt:
        salt = crypto.new_salt()
        payload = crypto.encrypt(plaintext, PASSPHRASE, salt)
        salt_b64 = base64.b64encode(salt).decode("ascii")
    else:
        payload = plaintext
        salt_b64 = ""
    records = codec.encode_payload(payload)
    meta = {
        "from": f"{user}@{ZONE_DOMAIN}",
        "subject": "cursed delivery",
        "date": "2026-09-03T00:00:00+00:00",
        "chunks": len(records),
        "salt": salt_b64,
        "enc": encrypt,
        "mid": mid,
    }
    change_id = transport.send(user, folder, meta, records)
    transport.wait_insync(change_id)
    return mid


def _record_names(client, zone_id):
    """Return the set of record names (trailing dot stripped) in the zone."""
    names = set()
    paginator = client.get_paginator("list_resource_record_sets")
    for page in paginator.paginate(HostedZoneId=zone_id):
        for rrset in page["ResourceRecordSets"]:
            names.add(rrset["Name"].rstrip("."))
    return names


@mock_aws
def test_encrypted_round_trip():
    _client, _zone_id, transport = _make_transport()
    plaintext = b"Subject: hi\r\n\r\nThis message lives in DNS now. Sorry."

    mid = _deliver(transport, "alice", "inbox", plaintext, encrypt=True)

    assert mid in transport.list_messages("alice", "inbox")

    msg = transport.fetch(mid, "alice", "inbox")
    assert msg.mid == mid
    assert msg.encrypted is True

    ciphertext = codec.decode_payload(msg.records)
    salt = base64.b64decode(msg.salt_b64.encode("ascii"))
    recovered = crypto.decrypt(ciphertext, PASSPHRASE, salt)
    assert recovered == plaintext


@mock_aws
def test_unencrypted_round_trip():
    _client, _zone_id, transport = _make_transport()
    plaintext = b"plaintext in public DNS, what could go wrong"

    mid = _deliver(transport, "bob", "inbox", plaintext, encrypt=False)

    assert mid in transport.list_messages("bob", "inbox")

    msg = transport.fetch(mid, "bob", "inbox")
    assert msg.encrypted is False
    assert codec.decode_payload(msg.records) == plaintext


@mock_aws
def test_binary_payload_round_trip():
    _client, _zone_id, transport = _make_transport()
    plaintext = bytes(range(256)) * 40  # forces multiple TXT records

    mid = _deliver(transport, "carol", "inbox", plaintext, encrypt=True)
    msg = transport.fetch(mid, "carol", "inbox")

    ciphertext = codec.decode_payload(msg.records)
    salt = base64.b64decode(msg.salt_b64.encode("ascii"))
    assert crypto.decrypt(ciphertext, PASSPHRASE, salt) == plaintext


@mock_aws
def test_delete_removes_message_and_meta():
    client, zone_id, transport = _make_transport()

    mid = _deliver(transport, "dave", "inbox", b"delete me", encrypt=True)
    assert mid in transport.list_messages("dave", "inbox")

    change_id = transport.delete(mid, "dave", "inbox")
    transport.wait_insync(change_id)

    assert mid not in transport.list_messages("dave", "inbox")

    # The meta record for this message must no longer exist in the zone.
    meta = naming.meta_name(mid, "dave", "inbox", ZONE_DOMAIN)
    assert meta not in _record_names(client, zone_id)


@mock_aws
def test_two_messages_append_to_index():
    _client, _zone_id, transport = _make_transport()

    mid1 = _deliver(transport, "erin", "inbox", b"first", encrypt=True)
    mid2 = _deliver(transport, "erin", "inbox", b"second", encrypt=True)

    mids = transport.list_messages("erin", "inbox")
    assert mid1 in mids
    assert mid2 in mids
    assert len(set(mids)) >= 2
