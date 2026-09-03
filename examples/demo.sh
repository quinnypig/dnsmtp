#!/usr/bin/env bash
#
# DNSMTP demo: send, list, and read a message stored in Route 53 TXT records.
#
# This is not a dry run. It talks to real AWS Route 53 and will create,
# read, and (implicitly, on your next cleanup) leave record sets in a hosted
# zone you own. You need:
#   - Valid AWS credentials on this machine (env, profile, or instance role)
#     with the permissions in examples/iam-policy.json.
#   - A real Route 53 hosted zone whose id and domain you plug in below.
#
set -euo pipefail

# --- Configuration (replace these placeholders) ------------------------------
export DNSMTP_ZONE_ID="Z0123456789ABCDEFGHIJ"   # your hosted zone id
export DNSMTP_ZONE="mail.example.com"           # the zone's domain name
export DNSMTP_KEY="a-passphrase-you-actually-remember"  # encrypts payloads

USER_TO="alice"

echo "==> Sending a message to ${USER_TO}@${DNSMTP_ZONE} ..."
echo "    (this writes _meta / payload TXT records and waits for INSYNC)"
MID="$(dnsmtp send \
  --to "${USER_TO}" \
  --subject "Greetings from the DNS layer" \
  --body "This email is now several TXT records. You did this.")"
echo "    delivered message id: ${MID}"
echo

echo "==> Listing ${USER_TO}'s inbox ..."
dnsmtp list --user "${USER_TO}"
echo

echo "==> Reading message ${MID} back out of DNS ..."
dnsmtp read --user "${USER_TO}" --id "${MID}"
echo

echo "==> Done. To clean up: dnsmtp delete --user ${USER_TO} --id ${MID}"
