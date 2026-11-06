# Independent source review — REJECT

Reviewed the frozen `controlled-worker-review-source-2` candidate against `original-requirements-and-clarifications.md`. All 20 file SHA-256 values match the supplied manifest (base reference `31be2fcfad0ded2cc24d0d0d9b3df3b0878da15a`). This verdict concerns source logic, not installed application or live-model execution.

## Blocking findings

### 1. P1 — Unclassified host refusals can acquire replay authority from a model assertion

Locations: `plugins/engineering/scripts/reviewer_isolation.py:159–165,540–545`; `plugins/engineering/scripts/delivery_recovery.py:265–269,320–327,601–635,648–655`.

`Server.call()` turns every RPC error into `host.error` with `refusal_state="unclassified_host_error"`, retaining only the numeric RPC code. It does not preserve a typed distinction between permission/auth/policy rejection and a repairable protocol failure. A rejection before `turn/start` also produces the positive `worker.failed/not_started` receipt. With process custody closed, `role_effect_disposition()` correctly proves no worker effects, but the replay path has no separate controller-owned authority check: `denied()` ignores `refusal_state`, and `recover()` accepts the designer's `authority="clear"` answer.

Concrete source path: a successful original planner exists; executor `thread/start` returns an RPC error; the adapter emits `host.error` plus `worker.failed/not_started`; the failed receipt is classified settled and reaches the original designer; a structurally valid `try_again` decision with `authority=clear` passes the guards and consumes/reopens a retry. The unresolved host rejection is still present in the evidence. No-start custody proves absence of effects, not permission to repeat the refused operation. A command RPC rejection after admission has the analogous closed-broker path.

This violates the requirement that unresolved authority cannot be established by a model assertion, and can obscure an actual denial carried as an RPC error. The issue does not depend on treating arbitrary tool stdout as an authorization signal.

Minimal correction: retain sanitized typed host authority provenance separately from effect disposition. Known denials must stop; unclassified RPC rejection must remain an authority hold unless controller-verifiable host evidence resolves it. Read-only designer triage may still run, but a designer answer must not clear that hold or authorize reopening. Allow known non-authority failures through only an explicitly supported, evidence-backed classification.

### 2. P2 — A settled local failure of the recovery designer receives no retry

Locations: `plugins/engineering/scripts/delivery_recovery.py:90–91,313–327`; `plugins/engineering/scripts/controlled_workspace.py:278–281`; `plugins/engineering/scripts/reviewer_isolation.py:483–487,521–537`.

Every unsuccessful designer invocation is persisted as `designer_hold`, indistinguishable from an intentional designer decision to stop or an unresolved-effect failure. Every subsequent invocation immediately rejects that hold. There is no same-designer retry path for a known safe local execution failure in triage.

Concrete source path: during recovery, the original designer calls `workspace_read` with a nonexistent/non-admitted path. The broker raises `ValueError` without a mutation or broken broker. The adapter closes the broker and server and emits positive controlled-workspace reconciliation; the supervisor can close process custody. The designer receipt is therefore held but has settled effects. Nevertheless lines 320–327 permanently hold the run on that first failure, without the required bounded recovery attempt.

Minimal correction: distinguish an explicit stop, denial, unknown custody and exhausted budget from a positively settled local designer execution failure. Record and bound that failure, then allow its one literal same-designer recovery attempt without consuming a second allowance for the original implementation failure or resetting any existing episode. Continue to hold genuinely unknown effects and actual denials. A safe tool-argument error can alternatively be returned as a structured tool failure for correction in the same bounded turn rather than terminating the entire designer invocation.

## Coverage and supported conclusions

I traced the four-role controller, private-host adapter, file broker, recovery ledger, final observations and same-reviewer handoff; reviewed the workflow, presets, adapted guidance, documentation and CLI changes; inspected the test diff and regression-test inventory as source, not execution evidence. I checked the applicable product contract, package inclusion/dependencies, canonical run-plan metadata merge behavior, and the exact supplied thread/command protocol schemas. No implementer reports, prior reviews, logs, private host configuration, credential stores, or conversation-history sources were used.

The candidate has concrete mechanisms for distinct initial sessions, model precedence and identity checks, an independent review-before-tests gate, fresh review after implementation changes, original-designer/executor thread reuse, final-attempt replacement, durable episode budgets, archival before reopen, typed pre-dispatch evidence, and conservative interruption holds. The file broker serializes repository mutation, rejects path traversal/symlinks and stale expected contents, preserves modes, and updates the filtered manifest. Commands receive a read-only filtered view, private worker scratch and disabled network; reviewer tools remain available. These mechanisms are substantial, but do not cure the two recovery gaps above.

No unrelated Phalanx mechanism, production operation, merge or deployment was found in the reviewed change scope. I did not require protection against a universally malicious same-UID host. Model interpretation of semantic recurrence remains judgment; the source preserves comparisons and budgets rather than claiming a universal semantic classifier.

## Verification limits and next gate

Only read-only source inspection and snapshot digest verification were performed. No tests were written or run, no model turns were launched, no source files were edited, and no installation or app-flow demonstration was attempted. After correction, a fresh independent source review is needed before the fresh Luna test phase. Installed/live proof remains outstanding regardless of a future source approval.

The existing candidate tests visibly retain stale `0.6.0` expectations while the workflow/preset are `0.7.0` (`tests/unit/test_engineering_tracked_delivery_workflow.py:30`, `tests/unit/test_skill_presets.py:70,110`). This is a source-observed test-maintenance item for the authorized post-approval test phase, not a claim about a test run and not the basis for this source rejection.
