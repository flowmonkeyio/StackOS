# Independent protected-value policy review — r1

**Verdict: REJECT. Logic approval is withheld; do not advance this frozen candidate to test writing or execution.**

Reviewed the original `requirements.md` first, then applicable repository/product/orchestrator guidance, the actual source and the eight-file `candidate-r1.patch`. No executor report, plan, previous review, implementation notes, parser candidate/evidence, secrets or session contents were used. No tests, candidate modules, parser, models or business operations were executed. Only this report was written.

## Source identity

At review start, the checkout was on `codex/protected-value-policy-20261105`. All 11 actual checkout files matched both `review-source-r1/manifest.json` and their frozen copies by SHA-256. The six changed baseline files were the workflow documentation, three engineering adapters plus recovery, and `stackos/artifacts/__init__.py`; the two additions are the policy and its documentation. The frozen snapshot was checked again at review completion and all 11 hashes still matched. Later mutable-candidate corrections are not covered by this verdict.

Patch SHA-256: `aa0328145ad4a595dec1650eb9f8dc20df88bb2f37613cd1f33aaab65edb3c03`.

The supporting unchanged call-chain files also match `full-baseline-manifest.json`:

- `stackos/cli/api_client.py`: `68b352cf96836eaf1da1c2136006b1c4f9a402a5b7155621c6934a87a1a7a663`
- `stackos/cli/operation_commands.py`: `4d6c9260182bf5d2014deb4e337fe04cfcd1719ba1d88ea51397776b85418ad1`
- `stackos/cli/app.py`: `8d9c503d501e79a3595c80d4287dabca86a1b3fca4e03ecde70b5a5646291e1b`

References below use repository-relative paths and line numbers from frozen r1; the supporting CLI files are the unchanged baseline files above.

## Blocking finding: controller diagnostics bypass the policy

Requirements 1, 3 and 6 require protected values to be withheld at disclosure boundaries and progress/errors to be sanitized before retention. `plugins/engineering/scripts/delivery.py:245–253` checks operation arguments and successful return values, but its call at line 249 enters `stackos/cli/operation_commands.py:20–25`, then `stackos/cli/api_client.py:26–72`.

On an HTTP error, `api_client.py:55–62` reads and decodes the daemon body, prints `_format_daemon_api_error(...)` directly to stderr, then raises `typer.Exit`. The formatter at lines 75–84 copies `detail`, `code`, `retryable`, `data`, `retry_after` and `hint`, or the raw payload, into that output. The successful-return check in `delivery.py:252` is never reached. Neither `run_delivery`'s error handler at lines 1626–1628 nor the invocation wrapper at lines 136–150 intercepts and validates this preprinted output. In particular, the former catches `OSError`, `ValueError` and `CalledProcessError`, while this path raises `typer.Exit`.

A daemon error containing a captured synthetic protected canary in `detail` or `hint` therefore reaches stderr without any known-value admission or sanitization. This is a direct source-level counterexample; it was not executed. It does not require arbitrary encoding or host-internal model generation. The transport-error branch at `api_client.py:63–69` similarly prints exception text and settings directly; the missing-token diagnostic at lines 18–23 directly prints its path. The operation adapter also reads successful/error response bodies without a byte cap before JSON decoding (lines 54 and 56), so the engineering policy's later structure limits do not bound this input capture.

The controller itself has additional ungated console emissions at `delivery.py:1287–1290`, `1440`, `1458`, `1492`, and `1622–1624`. Even fixed diagnostic wording can overlap an allowed captured value: the policy deliberately permits short values and the documentation explicitly promises to withhold unsafe fixed diagnostics. For example, the exact protected value `StackOS plan` need not occur in individually admitted IDs or input strings, but is assembled and printed at line 1440. Validating earlier fields does not validate the final emitted string.

Required correction: make the engineering invocation's complete daemon error/result and console paths use the same policy before any output, with bounded response capture. Preserve existing behavior for unrelated CLI consumers. Merely sanitizing an exception after the shared adapter has printed it cannot close this boundary. The correction needs fresh independent review before tests.

## Other reviewed boundaries

The following are supported by static source inspection, not acceptance or live proof:

- The generic opt-in owner is appropriate. `stackos/artifacts/protected_values.py` owns immutable explicitly supplied values, private serialization, one-pass original-escape normalization and bounded JSON-type traversal. The existing `redaction.py` is unchanged. Normalized bytes are not recursively fed into the escape decoder. The implementation covers mixed raw escapes, hex/fixed/braced Unicode, identity/octal escapes, all listed continuation forms and surrogate pairs. Invalid/oversized structures hold; sanitization independently validates replacements.
- The controller captures the prior sensitive inherited environment selection once (`delivery.py:115–163`), checks its filtered environment and reuses the policy. Requirements, assembled prompts and decoded observation specifications have explicit admission (`226–237`, `789–812`; `delivery_recovery.py:184–200`). Confined and unconfined argv are rechecked through `_execute`, with the helper also checking outcome-command argv.
- Source capture checks original old/current changed source before assembling review input (`delivery.py:323–361`), snapshot content before writing it (`396–458`), and paths/manifests. Broker reads and edits use the same policy and unchanged strict label rejection (`controlled_workspace.py:385–402`, `419–449`, `510–546`, `570–645`). There is no Acorn/source-expression exemption and no source rewriting to obtain admission.
- Worker, reviewer and observation helper paths pass one bounded anonymous socket transport (`delivery.py:637–657`, `661–780`). The supervisor passes only the descriptor to the trusted helper; the helper consumes/closes it before native probes or host startup (`reviewer_isolation.py:39–57`, `455–458`). Policy material is absent from intended argv/environment/model payloads. Static code supports descriptor closure and unchanged permission scope; actual descriptor and process isolation still need proof.
- The app-server adapter checks decoded inbound messages, tool arguments and command/tool results, then checks outgoing structured responses before sending (`reviewer_isolation.py:183–220`, `415–452`, `875–960`). Broker source output and images have checks before dynamic-tool disclosure. Model JSON answers are checked after decoding (`978–994`), and controller answer parsing checks again. Progress/event retention and final operation payloads are independently checked, subject to the diagnostic bypass above.
- The diff does not alter model-selection precedence, reviewer prompt isolation, role/thread identity checks, approval-before-test sequencing, or retry/stop-budget logic. Recovery ledger reads/saves gain policy validation; old unsafe records hold rather than being silently rewritten. Existing typed path/digest encoding and canonical run-plan secret-key checks remain.

## Limits and remaining proof

The documented lexical normalization conservatism is real: benign prose/source containing incomplete escape-looking sequences can hold when the protected set is nonempty. The implementation does not establish detection of computed, concatenated, encrypted, compressed, base64 or URL-encoded values. Binary assets are inspected as embedded UTF-8, not OCR or decoded compressed image content. These limits must remain explicit.

The host-retention distinction is accurately documented. `Server.receive` can gate only after the app-server provides an event; it cannot undo a host's prior storage of internally generated model text or command results. The adapter gates its own outbound prompts/tool responses and subsequent StackOS actions/records. This is not proof that host-internal pre-adapter persistence never occurs. The blocking diagnostic finding above is different: that output is emitted by StackOS code and is controllable here.

After correction and fresh logic approval, a fresh test session must prove candidate import/module paths rather than using the older installed package; exercise synthetic raw/mixed escaped canaries through actual decoded requirements, observations, confined/unconfined argv, broker source/path operations, model tool arguments/results, final answers and durable records; and now explicitly cover daemon HTTP/connection errors, bounded daemon responses and final console output. It must establish rejection before execution/disclosure/retention, transport EOF/descriptor closure and argv/environment isolation, benign acceptance, configurable models, independent review gating and recovery-history/budget preservation. No such behavioral or live proof was run in this review.

The candidate is not installed, and the documented filtered-environment helper import integration remains to be demonstrated with an isolated candidate bootstrap. No installation or runtime changes are authorized by this review. The strict JavaScript snapshot hold remains expected. Source-admission integration and a real safe business run remain separate unresolved work.
