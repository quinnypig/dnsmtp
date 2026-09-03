"""Tests for dnsmtp.naming."""

import re

from dnsmtp import naming


def test_new_message_id_format():
    mid = naming.new_message_id()
    assert isinstance(mid, str)
    assert len(mid) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", mid)


def test_new_message_id_is_random():
    ids = {naming.new_message_id() for _ in range(1000)}
    # Collisions across 1000 12-hex-char ids should be astronomically unlikely.
    assert len(ids) == 1000


def test_index_name():
    assert (
        naming.index_name("alice", "inbox", "mail.example.com")
        == "_index.alice.inbox.mail.example.com"
    )


def test_meta_name():
    assert (
        naming.meta_name("abc123def456", "alice", "inbox", "mail.example.com")
        == "_meta.abc123def456.alice.inbox.mail.example.com"
    )


def test_chunk_name_zero_padded():
    assert (
        naming.chunk_name("abc123def456", 0, "alice", "inbox", "mail.example.com")
        == "p0000.abc123def456.alice.inbox.mail.example.com"
    )


def test_chunk_name_padding_widths():
    assert naming.chunk_name("m", 7, "u", "f", "z").startswith("p0007.")
    assert naming.chunk_name("m", 42, "u", "f", "z").startswith("p0042.")
    assert naming.chunk_name("m", 1234, "u", "f", "z").startswith("p1234.")


def test_chunk_names_sort_lexically():
    names = [naming.chunk_name("m", i, "u", "f", "z") for i in range(12)]
    assert names == sorted(names)
