# Bridge failure diagnostics

The stdio MCP bridge emits bounded JSON diagnostics to **stderr**, keeping
stdout exclusively JSON-RPC. This is source-level observability and failure
handling, not evidence that a particular reported installation has been fixed.
Activation requires a reviewed supported release; do not substitute a checkout
virtualenv or locally built runtime for a published installation.

## Receipt and privacy contract

Each caught failure gets a generated `diagnostic_id`, observation time, phase,
elapsed milliseconds, outcome, autostart disposition, and separate original,
retry, and recovery failure classifications. Request errors retain JSON-RPC
code `-32000` and include the diagnostic ID and safe details. They explicitly
report uncertain effects and no automatic caller retry. Notification failures
have stderr diagnostics but no unsolicited stdout response.

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

The HTTP client's existing `timeout=None` policy is unchanged. Selecting an
initialization deadline without truncating long-running tool calls is separate
work, not an unexplained timeout adjustment in this patch.

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
