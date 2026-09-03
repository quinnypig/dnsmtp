"""DNS record naming helpers for DNSMTP.

Every message is stored as a set of Route 53 records whose names encode the
user, folder, and zone. This module is the single source of truth for those
names so the writer and reader agree on where to look.
"""

import secrets


def new_message_id() -> str:
    """Return a random 12-character lowercase hex message id."""
    return secrets.token_hex(6)


def index_name(user: str, folder: str, zone: str) -> str:
    """Return the record name for a user/folder message index."""
    return f"_index.{user}.{folder}.{zone}"


def meta_name(mid: str, user: str, folder: str, zone: str) -> str:
    """Return the record name holding a message's metadata."""
    return f"_meta.{mid}.{user}.{folder}.{zone}"


def chunk_name(mid: str, i: int, user: str, folder: str, zone: str) -> str:
    """Return the record name for chunk ``i`` of a message.

    The chunk index is zero-padded to four digits so lexical ordering of
    record names matches numeric chunk ordering.
    """
    return f"p{i:04d}.{mid}.{user}.{folder}.{zone}"
