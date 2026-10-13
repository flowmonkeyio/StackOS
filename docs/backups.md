# Backup and recovery

StackOS creates encrypted local backups of its database, matching credential
seed and daemon token, plus every explicitly enrolled external folder. Enroll
the entire finance workspace to cover `finance.json`, invoice originals,
schemas, reviews and exports. A project binding or workflow `workspace_ref`
does not enroll a folder or authorize filesystem access.

Automatic backups are disabled on fresh and upgraded installations. Setup is
local operator administration through the CLI or Home console. These controls
are unavailable to ordinary MCP agents. Backup creation needs only a public
key; recovery requires the separate private key.

## Set up the recovery key and finance coverage

Choose a private key location outside StackOS data/state, enrolled folders and
the backup destination. Keep a separate secure copy of that key, preferably
off the machine. Losing it makes these archives unrecoverable. Do not put it
next to the archives or paste it into the console or agent chat.

```bash
stackos backups keygen \
  --private-key-file /secure-recovery/stackos-private.pem \
  --public-key-file /secure-recovery/stackos-public.pem

stackos backups configure \
  --public-key-file /secure-recovery/stackos-public.pem \
  --destination /private-backups/stackos \
  --target-id business-finance \
  --target-label "Business finance" \
  --target-path /business/finance \
  --target-lock /business/finance/.writer.lock

stackos backups status
stackos backup --output /private-backups/stackos/manual.sbak
```

Use the finance workspace's existing persistent `.writer.lock`. Backup takes
a shared, nonblocking lock while copying; a busy finance writer postpones the
attempt. Missing folders or locks, symlinks, unsupported special files and
files changing during copying fail the backup. A successful archive includes
all enrolled folders; it never silently omits one. Configure additional
targets separately. Omitted fields in a later configuration update preserve
existing settings and targets.

The recovery key generator records private key exclusions. Later enrollment
of that key's parent folder is rejected, and copying the matching private key
into a source folder is also rejected. The backup destination and explicit
outputs cannot be inside sources or the daemon's public generated-assets
directory.

## Opt in to automatic backups

After checking the destination, public key and enrolled folders, enable the
schedule explicitly:

```bash
stackos backups configure --enabled --interval-hours 24 --keep-last 30
```

The daemon checks the saved policy with one maintenance job. Default cadence
is daily; intervals may be 1–168 hours. Retention defaults to 30 successful
scheduled archives and may be 1–365. Retention runs after a successful backup
and preserves manual and unrelated files. Keep the daemon running for the
schedule to execute. Opt-in survives restart; disabling prevents future due
attempts:

```bash
stackos backups configure --disabled
```

The Home console and `backups status` report coverage, last attempt, last
success and a repairable error. A failed attempt preserves the last successful
archive and retries with backoff. Local archives protect against accidental
changes; use a separate copy of the encrypted archives for device loss.

## Verify and recover

Restore authenticates the encrypted archive, validates every member and hash,
and checks SQLite integrity before publishing recovered files into a fresh
directory:

```bash
stackos restore /private-backups/stackos/manual.sbak \
  --private-key-file /secure-recovery/stackos-private.pem \
  --output-dir /recovery/stackos-check
```

The recovered `data/`, `state/` and `external/<target-id>/` folders are inert.
Restore does not replace a live installation, reconnect providers or enable
a schedule. Inspect the recovered manifest and finance originals before any
operator-controlled adoption. Stop the target daemon before replacing its
database, seed and token together, and retain the existing installation until
the recovered copy passes health and credential checks. Enroll recovered
finance folders and opt in again through local setup if moving machines.

Wrong keys, modified or truncated archives, unsafe members and hash failures
leave no published recovery directory. Test recovery periodically with the
actual private key; backup success alone does not prove that key is available.

## Format, exclusions and existing archives

The complete ZIP payload and manifest are encrypted using X25519 key exchange,
HKDF-SHA256 and streaming AES-256-GCM. Each archive uses fresh key material;
the header is authenticated and the complete authentication tag is verified
before recovered plaintext is inspected. The recovery key is independent of
the seed stored inside the archive. See the library's
[X25519](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/x25519/),
[HKDF](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/)
and [GCM](https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/)
contracts.

Owned directories use mode `0700`; archive and staging files use `0600`.
Private temporary plaintext is removed on normal success or failure. Database
snapshots include committed WAL state, and seed rotation is fenced to keep the
snapshot and seed coherent.

TDLib native authorization databases, provider caches, logs, plugin/skill
mirrors and generated assets are excluded unless explicitly supported as an
external target. Telegram user Accounts need a new sign-in after archive-only
recovery on another machine. Financial state remains owned by its external
workspace; StackOS stores no ledger copy in its core database.

Older `.zip` backups are plaintext even when their permissions are private.
This change does not encrypt those files retrospectively. Create and verify a
new encrypted backup before securely handling older copies under your
retention policy. The new restore command rejects legacy plaintext archives.
