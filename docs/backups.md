# Backup and recovery

StackOS creates encrypted local backups of its database, matching credential
seed and daemon token, all managed project files under `data/projects/`, and
every explicitly enrolled external folder. Project Finance records, invoices,
receipts, schemas, reviews and exports are included automatically. No Finance
folder selection or backup enrollment is required. Each permanent project ID
has its own private data directory; renames do not move it, and retained files
from disabled or deleted projects remain covered without attaching to another project.

Automatic backups are disabled on fresh and upgraded installations. Setup is
local operator administration through the CLI or the Local backups panel in
Local setup or a project's Setup page. Local setup is always available from the
sidebar, including when no projects exist. The panel manages this StackOS
installation and enrolled folders across projects, regardless of which page is
open. These controls are unavailable to ordinary MCP agents. Backup creation
needs only a public key; recovery requires the separate private key.

## Set up the recovery key

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
  --destination /private-backups/stackos

stackos backups status
stackos backup --output /private-backups/stackos/manual.sbak
```

Backup automatically discovers the managed project tree and takes shared,
nonblocking locks on its persistent `.writer.lock` files, including each project
initialization lock and Finance writer lock. A busy writer postpones the attempt.
Missing locks, symlinks, hard links, unsupported special files, or a changed tree
fail the archive instead of omitting a project. New or replaced coordination locks
also invalidate the snapshot. Explicitly enroll only additional external folders;
they must stay outside application data and use their existing writer lock when
applicable. Omitted configuration fields preserve existing settings and targets.

An interrupted managed-data publication leaves a project-local
`.publication-pending` marker. Backups refuse that incomplete state until normal
initialization or migration recovery finishes; staging files outside project
storage are never silently treated as backed up.

Creation and recovery share the same archive member rules. Unsupported names,
including backslashes in POSIX filenames, fail creation before publication or
retention can replace a usable archive. Path boundaries conservatively reject
case-only and Unicode-normalization overlaps, including equivalent directory
spellings on macOS volumes.

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

A pure disable remains available when an enrolled source, lock or destination
has become unsafe. In Setup, turn off automatic backups and save without other
settings changes to use this action. Enabling or changing other settings still
validates those paths.
Scheduled history stores compact retention receipts, with at most 366 records
(365 retained archives plus a newly published archive awaiting pruning). Older
destinations and files that cannot be pruned cannot grow this metadata without
bound; archives whose receipts leave the history are preserved.

The Setup panel and `backups status` report coverage, last attempt, last
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

The output must be outside this installation's data, state and public
generated-assets trees. Recovery rejects overlapping paths before creating
plaintext staging files. Existing output parents keep their permissions;
recovered directories and newly created ancestors are private.

The recovered `data/`, `state/` and `external/<target-id>/` folders are inert.
Managed Finance records retain `data/projects/<project-id>/finance/`, together
with their project-local identity metadata. Different projects remain separate;
restore never merges their files.
Restore does not replace a live installation, reconnect providers or enable
a schedule. Inspect the recovered manifest and finance originals before any
operator-controlled adoption. Stop the target daemon before replacing its
database, seed and token together, and retain the existing installation until
the recovered copy passes health and credential checks. Stop all host finance
writers before replacing or adopting a recovered finance folder; stopping the
StackOS daemon does not stop those independent writers. Recovery excludes
coordination `.writer.lock` files. After operator-controlled adoption of the
complete recovered data and matching database, opening Finance safely recreates
its locks, verifies project identity and retained data, and refreshes the storage
generation. Old queued requests remain historical. Missing or mismatched data
requires repair and is never replaced with a fresh empty master. Automatic backups
remain opt-in on the adopted installation; managed project files need no enrollment.

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

Newly created directories use mode `0700`; archive and staging files use `0600`.
Private temporary plaintext is removed on normal success or failure. Database
snapshots include committed WAL state, and seed rotation is fenced to keep the
snapshot and seed coherent.

TDLib native authorization databases, provider caches, logs, plugin/skill
mirrors and generated assets are excluded unless explicitly supported as an
external target. Telegram user Accounts need a new sign-in after archive-only
recovery on another machine. Financial state stays in the Finance plugin's
project-local JSON and originals; the core database holds no second financial ledger.

New archives use `stackos.local-backup.v3` and declare their managed project IDs.
Recovery also accepts v2 encrypted archives, which covered database/state and
enrolled external folders. It does not invent missing project files or claim
that those older archives included them.

Older `.zip` backups are plaintext even when their permissions are private.
This change does not encrypt those files retrospectively. Create and verify a
new encrypted backup before securely handling older copies under your
retention policy. The new restore command rejects legacy plaintext archives.
