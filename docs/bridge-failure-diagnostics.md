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

Each bridge launch also has one `attempt_id`, shared by CLI and proxy failure
events, startup completion, and bridge-generated error data. A supervising
consumer can supply `STACKOS_BRIDGE_ATTEMPT_ID` as 32 lowercase hexadecimal
characters (UUID hex). Missing or invalid values generate a new ID; arbitrary
environment text never enters the log. This correlation ID is distinct from
the per-event `diagnostic_id` and carries no request payload or authority.

The actual bridge passes `stackos-bridge:<attempt_id>` as the **AgentSession**
`client_session_id`. A native `workspace.startSession` receipt exposes that
same value, linking the host's observed tool response to producer evidence.
It does not identify a Codex thread, turn, admission request, or prove that an
AgentSession was created before that response exists. Do not export arbitrary
`client_session_id` fields: the similarly named **Run** field is a secret
controller capability.

Automatic `stackos.mcp_bridge.lifecycle` events record the stage before state
setup, token read, workspace discovery, input read, request handling and output
write. They also record stdin EOF and cleanup reached. They use the same log,
attempt ID and command-entry timing origin. The producer's package version and,
when present, validated `build-info.json` build ID/hash accompany these events.
A build timestamp/hash identifies bytes; it does not attest a source revision
or public release. Stages before CLI entry, host queueing, approval waits and
execution progress remain outside this producer's observation boundary.

Inspect or export one retained attempt through the existing local doctor:

```bash
/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos doctor --json --bridge-attempt <32-character-attempt-id>
```

This mode only reads the selected attempt's evidence, including rotations; it
does not run doctor health, credential, registration or process probes. JSON
can be redirected to a private file and retained through the incident's
existing artifact/index owners. It reconstructs allowlisted fields, bounds
file/line/event counts, rejects unsafe file targets, and reports partial or
missing retention. The human output shows the last confirmed stage, awaited
evidence and milliseconds since observation. These are observations of the
producer, not proof it is still alive. A missing event is not a failure;
cleanup reached is not proof of process exit; response flush is not consumer
acknowledgment. Host request/thread/turn IDs remain null until separately
observed through a supported host API. Reconcile uncertain host outcomes
before any replacement launch; do not clear ownership to make progress.

The mounted Codex task API and public App Server protocol are different
capability surfaces. Public documentation of status notifications or
`turn/interrupt` does not make those hooks accessible in a running desktop
session. Record the actual mounted capability, owner, missing trace and
blocked acceptance criterion. Keep original cloud launch, bridge handshake
and writer-state observations separate until their identities are correlated.

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

### Correlated lifecycle exercise

`scripts/diagnostics/bridge_lifecycle.py` exercises the source candidate with
an actual bridge process, a separate synthetic stdio consumer process, and an
ephemeral loopback fixture. It uses private isolated home/data/state directories
and a fixture token. It neither connects to the installed daemon nor modifies
host registrations. Run it with the candidate's Python and a new output path:

```bash
python scripts/diagnostics/bridge_lifecycle.py /private/tmp/bridge-lifecycle-evidence
```

Every scenario gets a fresh attempt ID shared by both sides. `lifecycle.jsonl`
records supervisor spawn, observed child exit codes/signals, consumer request,
response, deadline and cancellation events, and fixture delivery. The producer's
automatic events remain in `state/mcp-bridge.log`. Both use the existing private,
fsynced rotating logger. No raw stderr, bodies, environment or exception text is
retained. Preserve the JSONL and producer logs through existing incident evidence
owners before temporary storage is removed.

| Exercise | Evidence that distinguishes it |
| --- | --- |
| Success | Consumer received a result, producer flushed initialize, both exited zero |
| Consumer timeout | Armed 200 ms consumer deadline expired before response; consumer exit 21 precedes producer write failure |
| Cancellation | Supervisor sent cancellation, consumer acknowledged it and exited 20; producer later failed to write |
| Cancellation acknowledgment pending termination | Consumer acknowledged cancellation; supervisor confirmed it was still running before observing termination separately |
| Cancellation/response race | Consumer cancellation raced the response; actual consumer and producer exits are recorded independently, including producer success with consumer cancellation |
| Abrupt consumer exit | Supervisor observed consumer SIGKILL, without a consumer completion receipt; producer later failed to write |
| Bridge protocol failure | Producer classified invalid daemon response during initialize; consumer received an error, producer exited zero |
| Bridge process exit | Producer retained token-read failure and exit 7; consumer observed EOF |
| Bridge network timeout | Producer classified a 200 ms fixture read timeout; consumer received an error without expiring its deadline |

The supervisor starts its clock before process launch, including interpreter and
imports. Producer events retain `cli_entry` timing; these origins must not be
subtracted as if they were the same interval. Each exercise has a 20-second
overall deadline plus bounded cleanup (one second for terminate and one for kill
per child). It signals and reaps only children it created. Cleanup still runs
if recording fails; incomplete cleanup/persistence fails the exercise. The script
returns nonzero for an overall deadline; scenario correctness is asserted by
`tests/integration/test_mcp/test_bridge_lifecycle_exercise.py`, including cleanup
after an unavailable log sink or a consumer spawn failure.

These are deterministic synthetic observations, not historical root-cause proof.
A broken pipe alone still cannot distinguish cancellation, timeout or consumer
exit. Real hosts must supply their own matching attempt ID and lifecycle receipt;
this change does not instrument Codex's host or retrieve its backend traces.

### Retained findings and claim limits

The October 23 investigation keeps three observations separate. Exact request
IDs, original timestamps and private evidence refs belong to its existing
incident record and receipts, not reusable workflow defaults.

| Observation | Established evidence | Still needed |
| --- | --- | --- |
| Upstream task launch returned `UNKNOWN` | Two failure receipts; an overlapping host queue-full event is a lead only | Backend correlation, admission/delivery/acknowledgment, any created thread/turn IDs and authoritative final effects |
| Repository-Python bridge initialization failed | Caller reported an outer exception group after 4.116 seconds; caller exit does not prove child exit | Original executable/argv/runtime, sanitized exception leaf, deadline origin and child spawn/exit receipt |
| Active-writer/completed contradiction was reported | No exact attributable item/epoch/thread/turn pair was retained | The affected identity, authoritative ownership and terminal result, plus an attributable subsequent write; heartbeat alone is insufficient |

The installed local update passed restart, doctor, retained-evidence export and
source-to-installed file checks. Source regressions and independent review
passed. The existing host session still used an older loaded bridge, so those
results did not establish fresh-host attempt correlation or recovery of the
original routes. The observation improvements do not establish a historical
cause. Original uncertain attempts remain unreplayed.

Source tests exercise synthetic failures, stdout purity, redaction, bounded
causal preservation, retry eligibility/budget, protocol compatibility and
workspace protection. The diagnostics workflow can retain these as explicitly
source-test evidence while the real incident stays unresolved. Preserve the
historical attempt ledger and append newer installed/host observations with
their own identity and capture time. A completed source test is not a live
symptom check or deployment.

## When upstream traces are unavailable

The investigating agent owns a usable escalation handoff. Identify the component
that owns each missing observation, the exact trace or supported hook required,
and the acceptance criterion it blocks. Separate a local filesystem permission
from provider-internal trace access and from permission for a new reproduction.
Do not ask an operator who lacks internal access to find an unspecified platform
contact. Verify the current official reporting route, prepare the sanitized
report, and provide concrete submission steps. Continue independent local work
that remains authorized. A report requests investigation; it does not grant
backend access or guarantee trace retention or a response.

For Codex, the [official feedback and logs guidance](https://learn.chatgpt.com/docs/reference/troubleshooting#feedback-and-logs)
describes selecting feedback from `/` in the app composer. It offers optional
session sharing and returns a session ID after submission. This route was
checked for the October 23 investigation; recheck current instructions before
using it later. If the agent cannot operate the feedback panel, provide a ready
to paste message and the retained sanitized packet. Review any optional full
conversation or log sharing separately. Do not send or post the report without
authorization, or silently substitute a public issue for the chosen route.

The report should include the failing operation, exact available request IDs,
original timestamps with their meaning (request, returned error, or later
readback), runtime/configuration facts that were actually retained, observed
versus expected behavior, unknown effects, and specific questions. For a lost
launch acknowledgment, ask whether the request was admitted, delivered and
executed, which task/thread/turn it created, and its authoritative final outcome.
Keep an uncorrelated queue rejection as a hypothesis rather than naming it the
cause. Ask for sanitized exception and child lifecycle evidence separately.

Store the route, source URL/check time, prepared report and follow-up condition
in existing incident evidence. Track **prepared**, **submitted**,
**acknowledged**, and **response received** separately using actual receipts.
Record a report/session ID only when returned; a prepared draft or unavailable
UI is not proof of submission. Preserve the prior receipt before adding the
submission or response. Reconcile an uncertain send before retrying it. A reply
must be checked against the original request identities and effects before any
replacement launch or causal conclusion.

Keep the same run, incident index and attached ticket. A blocked final step can
leave the plan started and audit run running; these are workflow states, not
proof of an active writer or process termination. Keep the incident unresolved
while required causal or real-host verification is missing. Preserve the frozen
workflow snapshot and distinguish source guidance updates from the installed
template and the host's already loaded process.
