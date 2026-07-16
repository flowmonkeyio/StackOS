# FTP / Explicit FTPS Connector Contract

Verified: 2026-07-15

This connector is a decision-free bidirectional FTP client. The agent chooses
the remote directory to inspect and the local/remote file or directory mappings
to transfer. StackOS validates protocol-safe inputs, resolves the daemon-held
password, executes the requested operation, and records the result. It does not
restrict transfers to daemon-generated assets or add an FTP-specific approval,
path allowlist, or second opt-in.

## Official documentation ledger

- [FTP protocol and commands (RFC 959)](https://www.rfc-editor.org/rfc/rfc959)
- [MLST/MLSD machine-readable directory facts (RFC 3659)](https://www.rfc-editor.org/rfc/rfc3659)
- [Explicit FTP over TLS and `PROT P` (RFC 4217)](https://www.rfc-editor.org/rfc/rfc4217)
- [Python `ftplib`](https://docs.python.org/3/library/ftplib.html)

FTP servers vary in extensions, permissions, filename encodings, and path
semantics. The connector uses MLSD/MLST when available and falls back to
NLST plus CWD/SIZE probes. It never parses human-formatted `LIST` output.

## Authentication and setup

Provider key: `ftp`. Connector key: `ftp`. The `ftp-password` auth method stores
`password` as secret payload. Safe configuration contains `host`, `port`,
`username`, `tls_mode`, `passive_mode`, `timeout_s`, and `encoding`.

- `port` defaults to `21`.
- `tls_mode=explicit` opens `FTP_TLS`, performs `AUTH TLS`, logs in, then
  enables protected data transfers with `PROT P` before any listing or file
  transfer. Certificate verification uses the system trust store.
- `tls_mode=none` deliberately selects plain FTP. There is no redundant
  `allow_insecure` gate.
- `passive_mode` defaults to `true`, timeout to 30 seconds, and filename
  encoding to UTF-8.
- `auth.test` logs in and calls `PWD`; it does not upload, download, delete, or
  return the password or raw protocol transcript.

## Executable actions

| Action ref | Risk | Contract |
| --- | --- | --- |
| `utils.ftp.directory.list` | read | Open a fresh connection and list one `remote_path` without downloading. Return the resolved directory plus reusable child paths and facts. |
| `utils.ftp.file.upload` | write | Upload one or more `{local_path, remote_path}` mappings. Files map to files; directories map to directory roots and recurse automatically, including empty directories. |
| `utils.ftp.file.download` | write | Download one or more `{remote_path, local_path}` mappings. Files map to files; directories map to directory roots and recurse automatically, including empty directories. |

Local paths are the exact daemon-local paths selected by the agent. They may be
absolute or relative and may point outside StackOS-generated asset directories,
provided the daemon operating-system user can read or write them. Filesystem
access failures are ordinary per-path transfer failures, not hidden policy
decisions.

Upload and download require a global `conflict_policy`:

- `overwrite`: replace existing destination files;
- `skip`: retain existing destination files and report them;
- `fail`: treat an existing destination file as a failure.

They also require `error_policy=stop|continue`. `stop` raises a structured
connector error containing all completed, skipped, and failed paths up to the
failure. `continue` attempts the remaining batch and returns `partial` when
some paths failed; an all-failed batch raises. Results include path lists,
counts, created directories, and transferred byte totals.

Upload accepts `follow_symlinks=false` by default. False reports symlinks as
skipped. True follows them under the link name and detects directory ancestry
cycles. Recursive download does not follow remote symlinks in v1; it reports
them as skipped because FTP servers do not expose one portable symlink contract.

## Browse and path safety

Browsing is stateless: every call reconnects and resolves a relative remote
path from that connection's `PWD`. A result entry reports its name, type, size,
modification fact when available, and reusable remote path. Unsafe server child
names and entries whose server-controlled fields echo the daemon-held password
are redacted and marked non-traversable.

Remote command arguments reject NUL, CR, and LF to prevent command injection.
Recursive download rejects server-supplied `.`, `..`, separator-bearing, and
control-character child names and verifies every descendant remains under the
agent-selected destination root. This is descendant containment for the chosen
mapping, not a StackOS filesystem allowlist. Files download to a sibling
temporary file and become visible through atomic `os.replace`; failed transfers
remove the temporary file.

## Error, audit, and execution behavior

Protocol, authentication, permission, path, and filesystem failures become
structured `ActionConnectorError` output with redacted error type/message and
partial transfer state when applicable. The action audit retains the action
ref, connector/provider, opaque credential ref, sanitized mappings, status,
counts, duration, and partial result. Passwords never enter action input,
output, metadata, or audit JSON, including when a server echoes a credential in
PWD, listing facts, names, paths, or errors.

`STOR` writes directly to the selected remote path because rename/temp-file
semantics are not part of this contract. If the connection fails after upload
bytes may have been accepted, the failed item reports `outcome_unknown=true`,
`remote_partial_possible=true`, `retry_safe=false`, the attempted byte count,
and path-specific reconciliation guidance. StackOS does not silently retry or
add a preflight gate; the agent decides whether to inspect or retry.

The normal shared StackOS mechanics apply: an agent can execute one explicit
direct action or use the same run-plan action grant as other actions. There is
no FTP-only approval, path allowlist, generated-assets ownership check, or
preflight decision gate.

## Deliberately excluded from v1

SFTP, implicit FTPS, delete, rename, remote edit, synchronization, deletion of
extraneous files, glob expansion, resume/range transfer, parallel transfers,
automatic retries, and persistent connection/current-directory sessions are
not exposed. They require separate contracts if later requested.

Mocked protocol tests prove MLSD traversal, fallback-compatible stat behavior,
plain FTP, protected explicit FTPS, arbitrary local paths, multiple recursive
mappings, empty directories, conflicts, partial failures, no-download browse,
credential redaction, traversal rejection, and atomic download placement. A
live disposable FTP/FTPS server remains the operator-owned interoperability
check for server-specific extensions and permissions.
