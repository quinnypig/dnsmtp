"""Passphrase-based symmetric encryption for DNSMTP payloads.

Inboxes live in world-readable DNS, so message bodies must be encryptable at
rest. We derive a key from the user's passphrase and a per-message salt using
PBKDF2-HMAC-SHA256, then encrypt with Fernet (AES-128-CBC + HMAC), which
authenticates the ciphertext so a wrong passphrase raises instead of
returning garbage.
"""

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_SALT_BYTES = 16
_KEY_BYTES = 32
_KDF_ITERATIONS = 480_000


def new_salt() -> bytes:
    """Return 16 cryptographically random salt bytes."""
    return os.urandom(_SALT_BYTES)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a url-safe base64 Fernet key from passphrase and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt(raw: bytes, passphrase: str, salt: bytes) -> bytes:
    """Encrypt ``raw`` with a key derived from ``passphrase`` and ``salt``."""
    key = _derive_key(passphrase, salt)
    return Fernet(key).encrypt(raw)


def decrypt(token: bytes, passphrase: str, salt: bytes) -> bytes:
    """Decrypt ``token``; raises on a wrong passphrase or tampering.

    Raises :class:`cryptography.fernet.InvalidToken` if the passphrase or
    salt is wrong, or if the ciphertext has been modified.
    """
    key = _derive_key(passphrase, salt)
    try:
        return Fernet(key).decrypt(token)
    except InvalidToken:
        raise
