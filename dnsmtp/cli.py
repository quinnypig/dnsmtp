"""DNSMTP command-line interface.

A thin Typer front-end over :class:`dnsmtp.route53.Route53Transport`. It turns
a body of text into an RFC 5322 message, optionally encrypts it, encodes it
into TXT-record-sized chunks, and files it under a hosted zone that never
consented to becoming a mail server.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from pathlib import Path

import typer

from dnsmtp.config import (
    DEFAULT_FOLDER,
    DEFAULT_TTL,
    ENV_PASSPHRASE,
    ENV_ZONE,
    ENV_ZONE_ID,
)

app = typer.Typer(
    add_completion=False,
    help="Email over Route 53 TXT records. It works, which is the problem.",
)


def _transport(zone_id: str | None, zone: str | None):
    """Build a transport or exit with a friendly complaint."""
    from dnsmtp.route53 import Route53Transport

    if not zone_id:
        typer.secho(
            f"No hosted zone id. Pass --zone-id or set {ENV_ZONE_ID}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if not zone:
        typer.secho(
            f"No zone domain. Pass --zone or set {ENV_ZONE}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    return Route53Transport(zone_id, zone)


@app.command()
def send(
    to: str = typer.Option(..., "--to", help="Recipient mailbox (local part)."),
    subject: str = typer.Option(..., "--subject", help="Message subject."),
    body: str | None = typer.Option(None, "--body", help="Message body text."),
    body_file: Path | None = typer.Option(
        None, "--body-file", help="Read the body from this file."
    ),
    sender: str = typer.Option("dnsmtp@localhost", "--from", help="Sender address."),
    folder: str = typer.Option(DEFAULT_FOLDER, "--folder"),
    passphrase_env: str = typer.Option(
        ENV_PASSPHRASE,
        "--passphrase-env",
        help="Env var holding the encryption passphrase. If unset, sends in the clear.",
    ),
    wait: bool = typer.Option(
        False, "--wait", help="Block until the change is INSYNC."
    ),
    zone_id: str | None = typer.Option(None, "--zone-id", envvar=ENV_ZONE_ID),
    zone: str | None = typer.Option(None, "--zone", envvar=ENV_ZONE),
):
    """Compose a message and file it into a mailbox's TXT records."""
    from dnsmtp.codec import encode_payload
    from dnsmtp.crypto import encrypt, new_salt
    from dnsmtp.naming import new_message_id

    transport = _transport(zone_id, zone)

    if body_file is not None:
        body_text = body_file.read_text(encoding="utf-8")
    elif body is not None:
        body_text = body
    else:
        body_text = ""

    email_msg = EmailMessage()
    email_msg["From"] = sender
    email_msg["To"] = f"{to}@{zone}"
    email_msg["Subject"] = subject
    email_msg["Date"] = format_datetime(datetime.now(UTC))
    email_msg.set_content(body_text)
    raw = email_msg.as_bytes()

    # Encrypt only if the passphrase env var is actually set.
    passphrase = os.environ.get(passphrase_env)
    salt = new_salt()
    salt_b64 = base64.b64encode(salt).decode("ascii")
    if passphrase:
        payload = encrypt(raw, passphrase, salt)
        encrypted = True
    else:
        payload = raw
        encrypted = False

    records = encode_payload(payload)
    mid = new_message_id()

    meta = {
        "mid": mid,
        "from": sender,
        "subject": subject,
        "date": datetime.now(UTC).isoformat(),
        "chunks": len(records),
        "salt": salt_b64,
        "enc": encrypted,
    }

    typer.secho(
        "Propagating your regrets to every edge location...",
        fg=typer.colors.YELLOW,
    )
    change_id = transport.send(to, folder, meta, records, ttl=DEFAULT_TTL)

    if wait:
        typer.echo("Waiting for global consensus on your bad decision...")
        transport.wait_insync(change_id)
        typer.secho("Delivered (INSYNC).", fg=typer.colors.GREEN)

    lock = "encrypted" if encrypted else "in the clear"
    typer.echo(f"message id: {mid}  ({len(records)} chunk(s), {lock})")
    typer.echo(f"change id:  {change_id}")


@app.command("list")
def list_messages(
    user: str = typer.Option(..., "--user", help="Mailbox to inspect."),
    folder: str = typer.Option(DEFAULT_FOLDER, "--folder"),
    zone_id: str | None = typer.Option(None, "--zone-id", envvar=ENV_ZONE_ID),
    zone: str | None = typer.Option(None, "--zone", envvar=ENV_ZONE),
):
    """List message ids (and subjects, if reachable) in a mailbox."""
    transport = _transport(zone_id, zone)

    ids = transport.list_messages(user, folder)
    if not ids:
        typer.echo(f"{user}/{folder} is empty. Inbox zero, achieved via DNS.")
        return

    typer.secho(f"{'MESSAGE ID':<14}  {'ENC':<3}  SUBJECT", bold=True)
    for mid in ids:
        subject = ""
        enc = "?"
        try:
            msg = transport.fetch(mid, user, folder)
            subject = msg.subject
            enc = "yes" if msg.encrypted else "no"
        except Exception:
            subject = "(unreadable)"
        typer.echo(f"{mid:<14}  {enc:<3}  {subject}")


@app.command()
def read(
    user: str = typer.Option(..., "--user"),
    mid: str = typer.Option(..., "--mid", help="Message id to read."),
    folder: str = typer.Option(DEFAULT_FOLDER, "--folder"),
    passphrase_env: str = typer.Option(ENV_PASSPHRASE, "--passphrase-env"),
    zone_id: str | None = typer.Option(None, "--zone-id", envvar=ENV_ZONE_ID),
    zone: str | None = typer.Option(None, "--zone", envvar=ENV_ZONE),
):
    """Fetch, decode, decrypt if needed, and print a message."""
    from dnsmtp.codec import decode_payload
    from dnsmtp.crypto import decrypt

    transport = _transport(zone_id, zone)

    try:
        msg = transport.fetch(mid, user, folder)
    except LookupError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    payload = decode_payload(msg.records)

    if msg.encrypted:
        passphrase = os.environ.get(passphrase_env)
        if not passphrase:
            typer.secho(
                f"This message is encrypted, but ${passphrase_env} is not set. "
                "The bytes are right there in DNS; the key is not.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        try:
            salt = base64.b64decode(msg.salt_b64.encode("ascii"))
            raw = decrypt(payload, passphrase, salt)
        except Exception:
            typer.secho(
                "Decryption failed: wrong passphrase, or the ciphertext was tampered with.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
    else:
        raw = payload

    # The payload is an RFC 5322 message; parse and pretty-print it.
    try:
        parsed = BytesParser().parsebytes(raw)
        headers = {
            "From": parsed.get("From", msg.sender),
            "To": parsed.get("To", ""),
            "Subject": parsed.get("Subject", msg.subject),
            "Date": parsed.get("Date", msg.date),
        }
        body_part = (
            parsed.get_body(preferencelist=("plain",))
            if parsed.is_multipart()
            else parsed
        )
        if body_part is not None and body_part.get_content_maintype() == "text":
            body_text = body_part.get_content()
        else:
            body_text = parsed.get_payload()
    except Exception:
        headers = {"From": msg.sender, "Subject": msg.subject, "Date": msg.date}
        body_text = raw.decode("utf-8", errors="replace")

    for key, value in headers.items():
        if value:
            typer.secho(f"{key}: ", nl=False, bold=True)
            typer.echo(str(value))
    typer.echo("")
    typer.echo(body_text.rstrip("\n") if isinstance(body_text, str) else str(body_text))


@app.command()
def delete(
    user: str = typer.Option(..., "--user"),
    mid: str = typer.Option(..., "--mid", help="Message id to delete."),
    folder: str = typer.Option(DEFAULT_FOLDER, "--folder"),
    zone_id: str | None = typer.Option(None, "--zone-id", envvar=ENV_ZONE_ID),
    zone: str | None = typer.Option(None, "--zone", envvar=ENV_ZONE),
):
    """Delete a message's records and remove it from the mailbox index."""
    transport = _transport(zone_id, zone)

    change_id = transport.delete(mid, user, folder)
    if change_id:
        typer.secho(
            f"Deleted {mid} from {user}/{folder}. Change id: {change_id}",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo(f"Nothing to delete: {mid} was not in {user}/{folder}.")


if __name__ == "__main__":
    app()
