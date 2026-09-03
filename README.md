# DNSMTP

**Email, delivered over Route 53.** Your messages live in DNS TXT records. Sending is a `ChangeResourceRecordSets` call. Reading is a `ListResourceRecordSets` call. It works, it has 37 passing tests, and it should not exist.

## What I built and why you should be worried

I have spent the better part of a decade telling anyone within earshot that Route 53 is a database with a 100% SLA that AWS inexplicably refuses to market as one. Say a thing out loud enough times and eventually you owe the world a proof of concept. So here it is, one layer of the stack deeper: if Route 53 is a database, then Route 53 is a mail store, and I am the only person morally obligated to demonstrate it.

DNSMTP takes an email, base64-encodes it, optionally encrypts it, shreds it into 255-byte TXT strings, and files the wreckage into a hosted zone that did not consent to any of this. "Delivery" is a record-set change. The delivery receipt is Route 53 reporting the change reached `INSYNC`, which certifies that every authoritative name server on Earth now agrees your email exists and certifies exactly nothing about whether a human read it.

None of this is how DNS works. None of this is how email works. Both of those facts are load-bearing.

## How the sausage resolves

A message is not one record. It is an index entry, a metadata record, and a small pile of payload records, all named so the sender and the reader agree on where the bytes went.

| Record | Name pattern | Holds |
|--------|--------------|-------|
| Index | `_index.<user>.<folder>.<zone>` | The list of message ids in that folder |
| Meta | `_meta.<mid>.<user>.<folder>.<zone>` | Base64'd JSON: sender, subject, date, chunk count, salt, encryption flag |
| Payload | `p<NNNN>.<mid>.<user>.<folder>.<zone>` | One slice of the encoded body, per record |

Payload records carry a four-digit zero-padded index (`p0000`, `p0001`, and onward), so sorting the record names lexically happens to sort the bytes back into the right order. This is the single design decision in the entire project I am not embarrassed by.

Turning a message into TXT records:

1. **Encrypt first, because your inbox is a public DNS zone.** Anyone holding `dig` and your naming scheme can read your mail. DNSMTP encrypts client-side before a single byte touches Route 53, using a passphrase and a per-message salt (PBKDF2-HMAC-SHA256 down to a Fernet key, which is AES-128-CBC with an HMAC). The salt rides along in the meta record. The alternative is publishing your correspondence to the entire internet and calling it a feature, which is a thing AWS would do, not something I will.
2. The bytes, ciphertext or plaintext, are base64-encoded so arbitrary binary survives the trip.
3. That base64 gets cut into strings of at most 255 bytes, because that is the ceiling on a single TXT character-string, and grouped a few strings to a record.
4. Every record is written under its `p<NNNN>` name, the meta record is written, and the message id is appended to the index.

Delivery is one `ChangeResourceRecordSets` batch carrying the index, the meta, and every payload record at once. It hands back a change id. Polling `GetChange` until it says `INSYNC` is the closest thing here to a read receipt, and it means only that Route 53's name servers have converged. Reading a folder walks `ListResourceRecordSets`: pull the index for the message ids, pull each meta for the headers, pull the `p<NNNN>` records, reassemble. Deleting drops the meta and payload records and rewrites the index without that id.

## The part where it falls apart

DNS and Route 53 were built for zone data, not for your correspondence, and the seams show up fast and on schedule.

| Limit | Value | Why it hurts |
|-------|-------|--------------|
| Record sets per hosted zone | ~10,000 (soft) | Every message is several records, so a "mailbox" fills the zone alarmingly fast |
| TXT character-string length | 255 bytes | Every body gets shredded and stapled back together |
| ChangeResourceRecordSets batch | 1,000 changes / 32,000 characters | One large message can blow past a single batch |
| Consistency | Eventual | "Sent" is not "readable" until the change reaches INSYNC |
| Route 53 API rate | ~5 requests/sec/account | This is the punchline |

That last row is the entire joke. Your whole AWS account gets roughly five Route 53 API requests per second, shared across every human, every Terraform run, and every other tool touching Route 53. Sending and reading both draw from that same thimble. DNSMTP does not scale, cannot be rushed, and would fold instantly under anything resembling real mail volume. In fairness, that is also the one and only respect in which running your email over the DNS control plane is safer than the alternative.

## What it costs

Route 53 bills a hosted zone at roughly $0.50 per zone per month, plus about $0.40 per million standard DNS queries. **Verify current AWS pricing before you quote either of these**; the numbers move and this is not a commitment. Reading your mail is a Route 53 API operation rather than a billed DNS query, so the query line mostly bills you for strangers resolving your cursed records. Somewhere in here is a genuinely competitive per-message cost, and that is the most upsetting sentence in this file.

## Using it anyway

Requires Python 3.14 and `uv`.

```bash
uv sync
```

Three environment variables run the whole thing:

| Variable | Meaning |
|----------|---------|
| `DNSMTP_ZONE_ID` | The Route 53 hosted zone id (for example `Z0123456789ABCDEFGHIJ`) |
| `DNSMTP_ZONE` | The zone's domain name (for example `mail.example.com`) |
| `DNSMTP_KEY` | The passphrase used to encrypt and decrypt payloads. Set it, or your mail ships in the clear |

```bash
export DNSMTP_ZONE_ID="Z0123456789ABCDEFGHIJ"
export DNSMTP_ZONE="mail.example.com"
export DNSMTP_KEY="a-passphrase-you-will-actually-remember"

# Send a message (prints the new message id)
dnsmtp send --to alice --subject "Greetings from the DNS layer" \
  --body "This email is now several TXT records. You did this."

# List a mailbox
dnsmtp list --user alice

# Read a message back out of DNS
dnsmtp read --user alice --mid <message-id>

# Delete a message (drops its records and de-indexes it)
dnsmtp delete --user alice --mid <message-id>
```

`examples/iam-policy.json` is a least-privilege IAM policy granting only the three Route 53 actions the transport actually needs. `examples/demo.sh` is a runnable end-to-end walkthrough against a real hosted zone, for when reading about this stops being enough and you need to feel it.

## Please do not actually use this

This is a proof of concept and a thought experiment, not an email service. There is no MX record, no SMTP, no deliverability story, and no reason for it to exist beyond proving that it can. That was the whole assignment.

DNS is public. Anyone can resolve your records. Encrypt anything you would not staple to a billboard, and understand that even encrypted, the existence, size, and timing of every message are visible to the entire world.

It is also not a spam tool, and not only for the obvious ethical and legal reasons. The ~5 request/sec/account ceiling means you could not push meaningful volume through it if you devoted your life to the attempt. Do not put real mail in here. Do put it on your resume.

## License

MIT. Do with it what you will. I have already done the worst part.
