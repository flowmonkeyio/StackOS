# Bridge failure diagnostics

The stdio MCP bridge automatically persists bounded JSON diagnostics in
`Settings.state_dir/mcp-bridge.log` and mirrors them to **stderr**, keeping
stdout exclusively JSON-RPC. Capture does not depend on an agent, a running
daemon, a workflow, or the host retaining stderr. This is source-level observability and failure
handling, not evidence that a particular reported installation has been fixed.
Activation requires a reviewed release or an explicitly authorized installed
local build with retained build identity and evidence. Local builds remain
labeled as such; do not substitute a checkout virtualenv for the installed
executable or claim that local authorization proves publication.

## Receipt and privacy contract

Each caught failure gets a generated `diagnostic_id`, observation time, phase,
elapsed milliseconds, outcome, autostart disposition, and separate original,
retry, and recovery failure classifications. Request errors retain JSON-RPC
code `-32000` and include the diagnostic ID and safe details. They explicitly
report uncertain effects and no automatic caller retry. Notification failures
have stderr diagnostics but no unsolicited stdout response.

The CLI distinguishes `initialize` (daemon request handling) from
`response_write` (writing or flushing stdout). A successful daemon response
followed by a broken output pipe is a response-write failure; an earlier
initialization error remains its own event. Write failures terminate the bridge
without a replay or an attempt to write another error to the broken sink.
Input-stream failures use `request_read` rather than inheriting the prior phase.

CLI failure events include `bridge_elapsed_ms` from command entry alongside
the failure interval's `elapsed_ms`. Once per bridge, after the first successful
initialize response is flushed, a persistent `stackos.mcp_bridge.startup_completed`
event records `startup_elapsed_ms` (command entry through flush),
`initialize_elapsed_ms` (request handling, including any eligible recovery), and
`response_write_elapsed_ms`. The same private rotating log and explicit
persistence-failure reporting apply. An error response or failed flush never
produces this success event; a flush does not establish that the host consumed it.
All these measurements declare `timing_origin=cli_entry`: they exclude Python
interpreter/module import time and host queueing before command entry. True
host-to-child startup duration and child exit status require the host's retained
receipt; bridge timings must not be presented as that missing historical proof.

The log location follows the existing `--state-dir`, `STACKOS_STATE_DIR`, and
Settings dotenv configuration. Its default is
`~/.local/state/stackos/mcp-bridge.log`. Persistence uses the existing
`stackos.logging` owner and its 10 MiB file / five-backup retention policy.
Each JSON line is flushed and synced before the error response; separate
bridge processes serialize append and rotation with a bounded file lock.
Files are mode 0600; symlink/non-regular targets are rejected. The bridge does
not reconfigure the daemon logger or persist arbitrary library messages.

Every receipt includes `persistence.status`: `persisted` or `failed`. Disk,
permission, or lock failures preserve the original error and report a safe
persistence failure classification in stderr and the JSON-RPC error data.
They never silently claim a saved record or trigger a request replay. If all
output/storage sinks are unavailable, no software guarantee of retention is
possible. Configuration failures still resolve the state path independently
of unrelated invalid runtime settings.

Use the returned `diagnostic_id` to locate the same JSON event in the current
log or `.1` through `.5` rotations with authorized host file tools. For example:

```bash
rg --fixed-strings '<diagnostic_id>' ~/.local/state/stackos/mcp-bridge.log*
```

Use the configured state directory for a non-default install. These are local
runtime logs, not project-scoped MCP resources: the bridge may fail before a
project is bound. Do not expose all local logs through an unscoped agent tool.
The existing diagnostics workflow can reference the saved event through
tracker evidence, granted `artifact.create` / `resource.upsert`, and step
results. That association requires incident scope; automatic capture does not.
Rotation is bounded retention, so evidence needed longer should be retained
through those existing owners. Neither capture nor retention permits a repair.

Classifications are a closed vocabulary. Exception text, arbitrary exception
class names, tracebacks, paths, URLs, headers, authentication, configuration,
request IDs, request arguments and response bodies are never copied into these
diagnostics. HTTP failures expose only a numeric status. Exception groups and
cause/context chains are inspected with a 12-node bound and a truncation flag.
Original and retry failures remain distinct. An autostart helper exception is
separate recovery evidence, not mislabeled as a retried request.

Pre-loop settings, state-directory, token-read, workspace-hint and client
failures are classified without raw traceback output. A missing token retains
exit code 7; other caught startup failures exit 1. A closed stderr sink does not
replace the original failure. Cancellation and other `BaseException` exits
are not swallowed.

## Narrowed replay policy

The pre-existing one-autostart-per-session budget is retained, but eligibility
is narrower. Only `initialize` or `ping` with an HTTP connection-establishment
error (`ConnectError` or `ConnectTimeout`), a loopback target, and an unreachable
port may invoke the existing autostart helper and replay once.

Tool calls, tool listing, notifications, HTTP rejection, response validation
failure, read/write timeout, protocol error, and arbitrary/nested exceptions
never trigger this replay. Port liveness after failure cannot establish that
an earlier request had no effect. A reachable listener is not restarted, and
no process scan or new repair route is added. Caller-level retries of unknown
effects still require authoritative reconciliation.

Initialization, ping, tool discovery, initialized acknowledgements, and
workspace/project/run metadata reads default to a **15-second network
inactivity timeout**. Set `STACKOS_BRIDGE_STARTUP_TIMEOUT_SECONDS` (or the same
Settings dotenv key) to a positive number up to 300 seconds; invalid, infinite,
and NaN values fail configuration. The setting also bounds connection/pool
waits for ordinary tool requests, whose response reads remain unlimited.
It applies to each eligible request, including the single permitted retry.
HTTPX measures each connect/read/write/pool wait, not total wall-clock duration;
continuous response bytes or several startup requests can take longer. A read
timeout is an uncertain failure and never authorizes autostart/replay.

## Response validation

Before forwarding daemon text, the proxy validates JSON-RPC version, response
identity, exactly one result/error, and error-object shape. Invalid JSON,
non-finite JSON constants, malformed envelopes, mismatched IDs and empty
request responses produce safe `BridgeProtocolError` classifications instead
of arbitrary text on stdout. Valid JSON-RPC application errors remain intact;
this is envelope validation, not application-result rewriting or log capture.

Valid JSON, single-event SSE (including comments and multiline data), and empty
notification acknowledgements remain supported. This adapter is not a general
SSE streaming multiplexer: multiple data events are rejected rather than
silently choosing one. A full streaming implementation is outside this patch.

Workspace-resolution exceptions now emit the same safe diagnostic while
retaining the existing fail-closed scope behavior. A failed resolution does
not select a project or downgrade explicit workspace intent to global scope.
Project-validation and run-context failures also persist automatically; a
failed grant refresh still clears stale permissions. Workspace/project error
guidance includes the diagnostic ID for lookup. Invalid input JSON is logged
without retaining the request contents.

## Recorded incident and verification limit

The October 23 incident has two separate boundaries: upstream task-launch
`UNKNOWN` results and a bridge initialization failure with no available leaf
exception. These changes fix inspected source weaknesses; they do not identify
that missing leaf or establish a common cause. No upstream create is retried.

Source tests exercise synthetic failures, stdout purity, redaction, bounded
causal preservation, retry eligibility/budget, protocol compatibility and
workspace protection. The diagnostics workflow can retain these as explicitly
source-test evidence while the real incident stays unresolved. Its prior
attempt ledger and missing installed-package/handshake/backend evidence are
unchanged. A completed source test is not a live symptom check or deployment.
