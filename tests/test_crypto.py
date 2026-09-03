"""Tests for dnsmtp.crypto."""

import base64

import pytest
from cryptography.fernet import InvalidToken

from dnsmtp import crypto


def test_new_salt_length_and_randomness():
    s1 = crypto.new_salt()
    s2 = crypto.new_salt()
    assert isinstance(s1, bytes)
    assert len(s1) == 16
    assert s1 != s2


def test_roundtrip():
    passphrase = "correct horse battery staple"
    salt = crypto.new_salt()
    raw = b"the quick brown fox jumps over the lazy dog"
    token = crypto.encrypt(raw, passphrase, salt)
    assert token != raw
    assert crypto.decrypt(token, passphrase, salt) == raw


def test_empty_roundtrip():
    salt = crypto.new_salt()
    token = crypto.encrypt(b"", "pw", salt)
    assert crypto.decrypt(token, "pw", salt) == b""


def test_wrong_passphrase_raises():
    salt = crypto.new_salt()
    token = crypto.encrypt(b"secret", "right-pass", salt)
    with pytest.raises(InvalidToken):
        crypto.decrypt(token, "wrong-pass", salt)


def test_wrong_salt_raises():
    token = crypto.encrypt(b"secret", "pw", crypto.new_salt())
    with pytest.raises(InvalidToken):
        crypto.decrypt(token, "pw", crypto.new_salt())


def test_tampered_token_raises():
    salt = crypto.new_salt()
    token = bytearray(crypto.encrypt(b"secret", "pw", salt))
    token[-1] ^= 0x01
    with pytest.raises(InvalidToken):
        crypto.decrypt(bytes(token), "pw", salt)


def test_salt_survives_base64_roundtrip():
    # The meta dict stores salt as a base64 string; ensure that round-trips.
    salt = crypto.new_salt()
    encoded = base64.b64encode(salt).decode("ascii")
    decoded = base64.b64decode(encoded.encode("ascii"))
    assert decoded == salt
    token = crypto.encrypt(b"hi", "pw", salt)
    assert crypto.decrypt(token, "pw", decoded) == b"hi"


def test_binary_payload_roundtrip():
    salt = crypto.new_salt()
    raw = bytes(range(256)) * 8
    token = crypto.encrypt(raw, "pw", salt)
    assert crypto.decrypt(token, "pw", salt) == raw
