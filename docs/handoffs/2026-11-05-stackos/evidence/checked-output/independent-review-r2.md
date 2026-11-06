# Independent checked-output review R2

Verdict: **APPROVE for the fresh synthetic-canary test session only.** No decisive logic/security blocker remains in the reviewed candidate under its stated sink and deployment boundaries. This is not runtime verification, source-admission readiness, approval to run a migration, or release approval.

## Independence and source identity

I first read `requirements.md`. Review inputs were those requirements, actual frozen/candidate source, the complete R2 patch, baseline source/hash evidence, and applicable repository/product/orchestrator/reviewer instructions. I did not read executor reports, architectural assessments, prior review reports, implementation notes, or held candidate evidence. I did not implement this candidate. No candidate import/execution, tests, parser/model/business runs, installation, secret/session reads, or delegation occurred. Commands only read source, computed hashes/diffs, and wrote this report.

The candidate branch is `codex/checked-output-boundary-20261105`. All 11 entries in `review-source-r2/manifest.json` match both their frozen files and the candidate checkout. Reconstructing the unified diff from `before/` and the frozen source produces exactly the seven-file `candidate-r2.patch`. All supplied `before/` files match their entries in `full-baseline-manifest.json`. Across that manifest's 2,235 entries, only the five expected existing implementation files changed; there are no missing baseline entries or changed symlink targets. The patch adds the owner module and boundary documentation. Thus unrelated pre-existing dirty contents, including recovery code, remain preserved relative to the supplied baseline.

Evidence SHA256:

- Requirements: `488e9485d513a624a9a1175b0c92bdf1a11ff75ce8603026be862e2bb315da30`.
- Frozen source manifest: `168c5d6a2c1512f6a05a1e12a81515e876fe69a55c7ec0c4dae40b6770496412`.
- Complete patch: `6abe3b14db9140c0ef49f8ce4b2747ea4a3e768ce92f9fefc0d27d8440075866`.
- Full baseline manifest: `d208a304653d2ceec24a41fcb3c8d7b040a85b2771b31b89bf9978d5eef57a0c`.

## Logic assessment

`stackos/artifacts/checked_output.py` is a generic opt-in owner under the existing artifacts safety package. `OutputPolicy` admits structured values and final encoded text separately. Its one-pass escape interpretation covers raw/mixed hex, Unicode, surrogate pairs, identity/octal escapes, line continuations and escaped backslashes without recursively interpreting newly generated escapes. Explicit nested JSON decoding has its own admission. Invalid Unicode, malformed escapes, nonfinite numbers and excessive size/depth/node count hold. Retention replacement is independently admitted; executable/source input is rejected instead of rewritten.

The finite sink inventory is supported by the actual callers:

| Surface | Reviewed final ownership |
| --- | --- |
| App-server stdin | `reviewer_isolation.Server.send` encodes the outer record and newline, then passes those bytes to one cumulative `CheckedStream`. Nested tool-result JSON is admitted before being placed in the outer record. Assembled prompts are checked before temporary retention and again in RPC encoding. |
| Helper events | `emit` owns JSON plus newline with one lifetime event stream. Failure events use the same owner; failed admission has no raw print fallback. |
| Controller console/API diagnostics | `_echo` and opt-in API diagnostics share `CheckedConsole`. Descriptor identities coalesce normal stdout/stderr aliases; writes use pinned duplicate descriptors and one cumulative history/lock per physical destination. Wrapper/descriptor changes and unresolved identities hold. |
| Retained logs | `_execute` bounds raw capture, sanitizes retained structures, assembles the complete output including separators, validates both final log payloads before either write, and writes the admitted bytes. Actual callers allocate distinct exclusive log files or distinct anonymous temporary files. Confined observation extraction admits both replacement payloads before truncation. |
| Snapshot/schema files | Manifest and schema JSON are encoded once and consumed by policy-bound atomic replacement. Manifest SHA256 is over those same bytes. Snapshot source preserves original bytes and the existing strict label filter. |
| Broker reads/edits/manifests | Decoded dispatch arguments, paths and source content are checked. Role/path/provenance checks remain. The prospective manifest is admitted before source replacement; source/view replacements and manifest replacement consume the admitted bytes. Failed mutation custody remains broken/held. |
| Wrapped API requests/durable records | `_call` opts the existing operation/API adapters into admission of the final `{"arguments": ...}` body. `Request.data` gets those bytes without another JSON dump. Existing receipt/ledger evidence transforms and reconciliation remain the durable owners; later daemon re-encoding is outside this boundary. |
| API success/error input | Opt-in response reads are capped at 2,000,001 bytes, rejecting over 2,000,000. Both success and error data are checked after decoding; formatted diagnostics are label-redacted and checked with final newline through the shared console. Missing diagnostic ownership does not authorize fallback output. |

`CheckedStream.write` checks prior history plus the proposed append before the append's first write. `write_all` advances through slices of the same byte payload on short physical writes. An unsuccessful/uncertain stream write poisons that stream. This supports cross-record framing protection under the exclusive-writer condition; it does not retrospectively recall earlier safe bytes.

Policy capture is once per entered controller invocation and uses only the baseline inherited-value selection. The frozen tuple is transported through a bounded anonymous pipe; public argv contains descriptor numbers only. Supervisor and controller copies close, and the helper consumes/closes its copy before host discovery/startup in worker, reviewer and observation modes. Host subprocesses do not receive the policy descriptor. The lease and numeric exit-status channels are custody control, while bearer auth and the existing host auth reference remain with their original privileged owners. No new credential discovery was introduced.

Decoded observation specifications, model answers, received host records, command/tool arguments and broker paths/content have admission at their decode/dispatch/disclosure boundaries. Final supervisor argv/environment are checked again after generated flags/paths are added. The original strict source-selection/label scanner is unchanged, with no parser, exemption or file-exclusion relaxation. Model resolution, separate role sessions, original-requirements-plus-actual-outcome reviewer input, approval before test writing, outcome-scoped permissions and bounded recovery/history are preserved in the reviewed diff; `delivery_recovery.py` is byte-identical to baseline.

## Explicit limits and resolved concern

Approval is bounded to the documented adapters and exclusive ownership, not arbitrary downstream aggregation. Earlier console bytes, other writers, external seeks/truncation, downstream merging of separate files, host-generated session/log files, daemon re-encoding and HTTP-library wire framing are not intercepted. API exact-byte ownership here is the wrapped request body, not the entire authenticated HTTP wire request. These boundaries must remain explicit in any proof/result claim.

General admission is 20,000,000 UTF-8 bytes, 100,000 nodes and depth 64; prompts are 200,000 bytes; individual RPC messages/helper events/capture and API responses use the documented 2,000,000-byte bounds; policy transport is at most 4,096 bytes and platform atomic pipe capacity. Existing tighter argv/source/image bounds remain. Bounds can stop otherwise benign long sessions. Malformed escape syntax in benign prose/source can hold whenever protected values exist; short protected values can collide with fixed syntax or replacement markers. Empty protected sets avoid the additional escape-syntax holds. Image plaintext/base64 checks are not pixel, compressed-content or steganography detection. Descriptor-less/unresolved wrappers and ambiguous tty aliases are conservative holds, not proven compatible sinks.

I initially suspected that an interrupt escaping `_checked_invocation` would reach unchecked abort output. I withdrew that finding after checking actual dependency source: `uv.lock` pins Typer 0.27.3, and the packaged Typer 0.27.3 at `/Applications/StackOS.app/Contents/Resources/stackos/.venv/lib/python3.12/site-packages/typer/core.py:201` converts `KeyboardInterrupt` directly to silent `Exit(130)`. The candidate itself catches ordinary exceptions, not `KeyboardInterrupt`; this silent-interrupt behavior therefore depends on the actual CLI dependency. Do not generalize the conclusion to another Typer/Click version or an unwrapped direct Python invocation without proof. CLI parser output before `run_delivery` entry remains explicitly outside scope.

## Required remaining proof

No actual-sink proof was run in this review. The fresh test session must establish candidate module provenance in the controller and scrubbed helper subprocesses; importing an installed old module would invalidate the result. Then exercise actual local adapters with synthetic protected values and demonstrate:

- Rejection before the affected sink's first write/send/dispatch, including decoded argv/model answers/observation specs, mixed escapes, nested JSON and values assembled across fields, separators, prefixes/newlines and successive stream messages.
- Real pipe/file/socket or local-process destinations for RPC/events/console/logs, including merged stdout/stderr, normal tty aliases, separate handles to the same file, descriptor/wrapper rebinding, unknown identities and poisoned/partial writes. Clearly distinguish injected/mock failures from actual OS sink observations.
- Final retained log concatenations and both confined-observation replacement payloads; benign bytes remain identical except intentional baseline retention formatting/redaction.
- Actual manifest/schema/broker replacement bytes and hashes, including rejection before source mutation when the prospective manifest is unsafe, and preserved custody holds on write failures.
- Final wrapped API `Request.data`, bounded success/error responses and diagnostics, no unchecked fallback when diagnostic admission fails, and unchanged non-opted-in API behavior. A stub transport proves body ownership only; report separately any actual local transport proof.
- Policy absence from public argv/environment/prompts/files/logs, bounded private transfer, and descriptor closure before host child startup for every helper mode, using synthetic policy values only.
- Current CLI interrupt/error paths and dependency provenance; strict-source rejection, model configuration, reviewer isolation, approval gating and recovery stop/budget/history invariants remain unchanged.

The test session may now proceed within the original authorization. Preserve both earlier holds and all consumed budgets; no migration/business run, installation, history rewrite, push, merge or deployment is approved.
