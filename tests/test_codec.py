"""Tests for dnsmtp.codec."""

import os

import pytest

from dnsmtp import codec


def test_empty_roundtrip():
    records = codec.encode_payload(b"")
    assert codec.decode_payload(records) == b""


def test_small_roundtrip():
    raw = b"hello, cursed world"
    records = codec.encode_payload(raw)
    assert codec.decode_payload(records) == raw


def test_structure_string_and_record_limits():
    raw = os.urandom(4096)
    records = codec.encode_payload(raw)
    assert isinstance(records, list)
    for record in records:
        assert isinstance(record, list)
        assert len(record) <= codec.STRINGS_PER_RECORD
        for s in record:
            assert isinstance(s, str)
            assert len(s) <= 255


def test_multi_record_binary_roundtrip():
    # Large enough to require many records.
    raw = os.urandom(50_000)
    records = codec.encode_payload(raw)
    assert len(records) > 1
    assert codec.decode_payload(records) == raw


@pytest.mark.parametrize(
    "size", [1, 2, 3, 191, 192, 255, 256, 764, 765, 766, 1020, 1021]
)
def test_boundary_sizes_roundtrip(size):
    raw = os.urandom(size)
    assert codec.decode_payload(codec.encode_payload(raw)) == raw


def test_all_byte_values_roundtrip():
    raw = bytes(range(256))
    assert codec.decode_payload(codec.encode_payload(raw)) == raw
