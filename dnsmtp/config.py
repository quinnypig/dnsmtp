"""Shared defaults and environment-variable names for DNSMTP.

Other modules import constants from here so there is a single source of
truth for folder names, TTLs, and the env vars used to configure the CLI.
"""

# Defaults
DEFAULT_FOLDER = "inbox"
DEFAULT_TTL = 60

# Environment variable names
ENV_PASSPHRASE = "DNSMTP_KEY"
ENV_ZONE_ID = "DNSMTP_ZONE_ID"
ENV_ZONE = "DNSMTP_ZONE"
