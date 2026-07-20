# FTP / Explicit FTPS Connector Contract

Verified: 2026-07-16

This connector is a decision-free bidirectional FTP client. The agent chooses
the remote directory to inspect, the local/remote mappings to transfer, and the
remote paths to create, delete, rename, or move. StackOS validates protocol-safe
inputs, resolves the daemon-held password, executes the requested operation,
and records the result. It does not restrict operations to daemon-generated
assets or add an FTP-specific approval, path allowlist, or second opt-in.

## Official documentation ledger

- [FTP protocol and commands (RFC 959)](https://www.rfc-editor.org/rfc/rfc959)
- [MLST/MLSD machine-readable directory facts (RFC 3659)](https://www.rfc-editor.org/rfc/rfc3659)
- [Explicit FTP over TLS and `PROT P` (RFC 4217)](https://www.rfc-editor.org/rfc/rfc4217)
- [Python `ftplib`](https://docs.python.org/3/library/ftplib.html)

FTP servers vary in extensions, permissions, filename encodings, and path
semantics. Browse and download use MLSD/MLST when available and fall back to
NLST plus CWD/SIZE probes. Recursive deletion requires machine-readable MLSD or
NLST plus MLST entry types before any mutation. The connector never parses
human-formatted `LIST` output.

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
| `utils.ftp.file.delete` | write | Send `DELE` for one exact `remote_path`. A missing path or provider rejection is an error, not silent success. |
| `utils.ftp.directory.create` | write | Send one `MKD` for the exact `remote_path`. Missing parents are not created implicitly, and StackOS does not convert an existing-path provider rejection into success. |
| `utils.ftp.directory.delete` | write | Delete one exact directory. `recursive=false` sends one `RMD`; `recursive=true` inventories the full typed tree before mutation, deletes files and symlinks with `DELE`, and removes directories post-order with `RMD`. |
| `utils.ftp.path.rename` | write | Send `RNFR` followed immediately by `RNTO` for exact source and destination paths. It can operate on files or directories when the selected server supports that operation. |

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

## Remote management semantics

Remote management actions operate on one explicit target per call. Repeated
calls compose naturally when an agent needs to manage several paths; StackOS
does not add a separate batch-mutation framework.

Recursive directory deletion first builds the complete deletion plan from
machine-readable entry types. Unsafe child names, unknown types, duplicate
names, or directory cycles fail before the first `DELE` or `RMD`. Once mutation
starts, confirmed deletions are returned in order if a later command fails.
There is no rollback because FTP has no transactional delete primitive.

Rename/move does not pre-delete an existing destination, create destination
parents, retry, or emulate a rejected move with download/upload/delete. Server
collision rules and directory-rename support remain provider-defined. StackOS
returns success when `RNTO` is accepted, including provider-defined
replacement, and surfaces rejections as structured failures.

## Browse and path safety

Browsing is stateless: every call reconnects and resolves a relative remote
path from that connection's `PWD`. A result entry reports its name, type, size,
modification fact when available, and reusable remote path. Unsafe server child
names and entries whose server-controlled fields echo the daemon-held password
are redacted and marked non-traversable.

Remote command arguments reject NUL, CR, and LF to prevent command injection.
Recursive download and deletion reject server-supplied `.`, `..`,
separator-bearing, and control-character child names. Downloads additionally
verify every descendant remains under the agent-selected destination root.
This is descendant containment for the chosen mapping, not a StackOS filesystem
allowlist. Files download to a sibling temporary file and become visible
through atomic `os.replace`; failed transfers remove the temporary file.

## Error, audit, and execution behavior

Protocol, authentication, permission, path, and filesystem failures become
structured `ActionConnectorError` output with redacted error type/message and
partial transfer or deletion state when applicable. The action audit retains
the action ref, connector/provider, opaque credential ref, sanitized paths,
status, counts, duration, and partial result. Passwords never enter action
input, output, metadata, or audit JSON, including when a server echoes a
credential in PWD, listing facts, names, paths, or errors.

Only `utils.ftp.file.upload` and `utils.ftp.file.download` run in the shared
background action mode; browse and remote-management actions remain inline.
An accepted transfer returns status `running`, an `action_call_id`, and
`actionCall.get` poll arguments. While that stored call remains running, the
probe may include allowlisted process-live byte and item counters. Those
counters are not completion proof. The stored terminal status, output or
error, and completion timestamp are authoritative. If the daemon restarts,
live progress disappears and an orphaned running call is stored as failed with
`outcome_unknown=true` and `retry_safe=false`; StackOS does not infer success
or retry the transfer.

`STOR` writes directly to the selected remote path because rename/temp-file
semantics are not part of this contract. If the connection fails after upload
bytes may have been accepted, the failed item reports `outcome_unknown=true`,
`remote_partial_possible=true`, `retry_safe=false`, the attempted byte count,
and path-specific reconciliation guidance. StackOS does not silently retry or
add a preflight gate; the agent decides whether to inspect or retry.

The same ambiguity applies when a connection is lost or a post-command reply
cannot be classified after `DELE`, `MKD`, `RMD`, or `RNTO` may have reached the
server. Those failures report `outcome_unknown=true`, `retry_safe=false`, the
attempted path or paths, and reconciliation guidance. Explicit FTP 4xx/5xx
replies remain known provider failures.

The normal shared StackOS mechanics apply: an agent can execute one explicit
direct action or use the same run-plan action grant as other actions. There is
no FTP-only approval, path allowlist, generated-assets ownership check, or
preflight decision gate.

## Deliberately excluded from v1

SFTP, implicit FTPS, remote edit, synchronization, deletion of extraneous files,
glob expansion, recursive parent creation, bulk mutation, resume/range transfer,
parallel transfers, automatic retries, rollback, and persistent
connection/current-directory sessions are not exposed. They require separate
contracts if later requested.

Mocked protocol tests prove MLSD traversal, fallback-compatible stat behavior,
plain FTP, protected explicit FTPS, arbitrary local paths, multiple recursive
mappings, empty directories, conflicts, partial failures, no-download browse,
exact `DELE`/`MKD`/`RMD`/rename calls, recursive post-order deletion,
pre-mutation MLSx safety, credential redaction, traversal rejection, grant/audit
linkage, and atomic download placement. A live disposable FTP/FTPS server
remains the operator-owned interoperability check for server-specific
extensions and permissions.
