# Engineering delivery checked-output boundary

This opt-in boundary is owned by `stackos.artifacts.checked_output`. Its active
consumers are the engineering delivery controller, isolated helper and workspace
broker, through the existing CLI API adapter. Ordinary API consumers retain their
existing behavior. Artifact redaction alone cannot own final JSON separators,
framing or partial writes; caller-side scalar checking cannot establish this
contract. No new HTTP client, workflow engine or source parser is introduced.

## Finite sink inventory

| Sink | Final owner and admission |
| --- | --- |
| App-server stdin | `Server.send`: decoded outer record, nested tool-result JSON, final newline-delimited bytes, then one cumulative `CheckedStream` per server. |
| Helper stdout events | `emit`: final JSON plus newline, one cumulative stream for the helper lifetime, including failure and custody events. |
| Controller stdout/stderr | One `CheckedConsole` resolves destination identities and routes aliases (including a normal terminal and `2>&1`) through one canonical descriptor, cumulative history and lock. Opted-in API diagnostics use this same console owner. |
| Retained stdout/stderr | `_execute` bounds capture, performs retention sanitization, checks replacement structures and the final joined text, then writes those bytes. Confined-observation extraction checks both replacement byte payloads before truncating either scratch log. |
| Snapshot manifest and role schema | The controller encodes once and passes immutable bytes to the policy-bound atomic replacement owner, which checks them before opening a file. Manifest SHA256 hashes that exact payload. |
| Broker source and manifest | Exact UTF-8 source is admitted without rewriting. The next manifest is encoded and checked before source mutation; existing synchronous replacement consumes those same bytes and records their SHA256. Failure still marks custody broken. |
| Durable receipts/recovery metadata | Existing schema-specific evidence transforms and ledger reconciliation remain. `_call` opts the existing `_operation_call`/`_api_request` into final wrapped request-body byte admission. `Request.data` receives those bytes without another JSON encoding. |
| Daemon responses and diagnostics | Opt-in API capture reads at most 2,000,001 bytes, rejects over 2,000,000, checks decoded success/error data, applies existing label redaction to diagnostic text, then checks final error framing before writing. Missing shared diagnostic ownership holds silently. |

Prompts are admitted after assembly and before private input-file retention and
RPC encoding. Observation specifications, model answers, host records, tool
arguments, broker paths/content, and final process argv/environment are admitted
after decoding and again at their dispatch/disclosure boundary. The controller's
final launch admission includes supervisor code, descriptor flags and generated
paths. Source selection, strict label scanning, exact source bytes, model choice,
role permissions, review gating and recovery budgets are unchanged.

## Policy and privileged channels

Each entered controller invocation captures only the existing inherited
protected-value set once. It does not read auth/session content or discover
additional credentials. The frozen tuple policy is transferred to every helper
mode through an anonymous pipe, capped at 4,096 encoded bytes and the platform's
atomic pipe capacity. Only descriptor numbers appear in argv. The supervisor
passes that descriptor only to the helper and closes its copy; the helper consumes
and closes its copy before host discovery or startup. Host children receive no
policy descriptor. Over-limit policy material holds; it is never spilled to a
file or environment variable.

Existing daemon bearer authentication and the existing private host auth
reference remain with their privileged owners. The supervisor lease and its
bounded numeric exit-status record are internal process-custody controls, not
model input or retained user data; they do not transport protected policy values.
No private-channel exemption applies to prompts, tools, events or source.

## Bounds, sequencing and limitations

General admission is capped at 20,000,000 UTF-8 bytes, 100,000 visited structured
nodes and depth 64. Prompts retain the existing 200,000-byte cap, helper event
capture/RPC messages and API responses use 2,000,000 bytes, and private policy
uses 4,096 bytes. Existing tighter source, image and argv limits remain.

Each public byte stream has one exclusive writer. Before every append it checks
the full cumulative byte sequence including all prior framing, bounded by the
stream's limit. Therefore a value formed across messages is rejected before the
new message's first write. Earlier admitted messages cannot be recalled. The
write loop consumes slices of the same immutable payload on short physical
writes; a failed/uncertain write poisons the stream and forbids fallback output.
This is a per-stream contract, not a guarantee about a downstream party combining
separate stdout/stderr files or unrelated requests in an invented format.

Console ownership starts with inspectable OS descriptors. Regular files, pipes
and sockets are grouped by device/inode/type; terminals and the null device use
their device identity. Both wrappers and descriptor identities are checked again
before emission. Writes use private duplicated descriptors, so rebinding cannot
redirect an admitted payload during the check/write interval. Aliased regular-file
handles use the first admitted descriptor's position and one writer rather than
independent offsets. The controller closes its private duplicates on exit.
Descriptor-less wrappers, zero-inode stream identities, unsupported character
devices, and distinct tty device identities that might alias through `/dev/tty`
hold conservatively. Normal terminal aliases and ordinary shell `2>&1` remain
supported. Detected wrapper/descriptor rebinding poisons the console; rebinding
to an indistinguishable identity still cannot redirect its pinned writer.
Unrelated external writers, seeks, truncation, or bytes emitted before this
invocation are outside the exclusive-writer contract. These are not a license
to split a physically aliased stdout/stderr pair into independent histories.

Raw strings and one-pass JSON/JavaScript hex, Unicode (including paired
surrogates), identity, octal, escaped-backslash and line-continuation spellings are
checked. Newly decoded backslashes are not interpreted again. Explicit nested
JSON decode boundaries perform their own admission. Malformed escapes and
unpaired surrogates conservatively hold even in benign prose/source when the
protected set is nonempty. An empty protected set skips escape interpretation
(while retaining size, structure and UTF-8 admission), so there are no unnecessary
escape-syntax holds in that case. The strict label scanner still runs. Short
protected values can collide with JSON punctuation, fixed protocol words,
numbers or redaction markers and hold the operation. Retention may replace a
whole unsafe string with `[redacted]`, but must also admit that replacement; it
never substitutes redacted executable/source content.

The image path checks embedded plaintext and final base64-bearing RPC bytes, not
pixel content, arbitrary computed/compressed/encrypted encodings or steganography.
This boundary does not intercept host-generated session/log files, daemon-side
record re-encoding, HTTP-library protocol framing, or any other downstream
internal generation. Existing host isolation/retention assumptions remain.
CLI argument-parser diagnostics before entry into `run_delivery` are outside this
opt-in boundary. Once entered, errors terminate through checked diagnostics or
silently if diagnostic admission fails; there is no unchecked Typer fallback.

## Review state

Implementation has received static lint inspection only. It requires fresh
independent logic/security review against the original requirements, followed
only on approval by fresh synthetic-canary tests of actual sink adapters.
Tests must prove candidate module provenance, including helper subprocesses
whose scrubbed environment removes `PYTHONPATH`. No model/business execution,
installation, source-admission relaxation or old-run migration is authorized.
The original strict JavaScript snapshot hold and prior failure history remain.
