# Recovery policy evidence and source assessment

Assessment date: 2026-11-03. Read-only source assessment of `/Users/adilevinshtein/Documents/Codex/2026-10-24/task/engineering-process-clarity-current`; no implementation, tests, installation, or daemon mutation performed.

## Exact user requirement

> We should try to recover from each situation. A rejection may occur, and it needs to be resolved: either the design is incorrect (new to fings, oversight, dependencies, etc), or the execution failed. The next step for each is different - for design the flow needs to provide the feedback to the design agent and have it revise. For execution failure, return to the execution agent to resolve - only 1 time per failure and if it recurrs, change to a new agent and have it pick up that takes and continue the same flow (the only thing that changes is the agent). For failures, we need a retry mechanism and if it fails again, we need to go back to the design agent and have it make a decision - stop, revise or try again. This flow should also be capped at 1 retry

Parent-reported additional boundary: reviewer receives original requirements and actual outcome only, no other task inputs; relevant tools remain available. The pending question is whether designer `try again` grants one final attempt or resets a retry budget. This document does not choose that cap.

### Subsequent user clarification (2026-11-03)

Assistant: “One clarification on the retry cap: after a failure is retried once and the design agent chooses ‘try again,’ should that allow one further attempt total, after which the flow stops and asks you if it still fails?”

User: “Yes”

This resolves the previously pending cap: original attempt, one retry, then at most one designer-authorized final attempt. Replacing the execution agent consumes that final attempt; it does not create another allowance. A design revision preserves the same failure episode and consumed budget. The earlier unresolved notes below are retained as history, not current authority to reset counters.

Additional user requirement: “Make sure the models are configurable as later on we will introduce a complexity mechanism like Phalanx has”. Keep task overrides above project/repository defaults above built-in defaults; all continuation and replacement sessions retain the selected role model. Do not implement a complexity mechanism in this change.

## Current source and canonical owners

- `plugins/engineering/scripts/delivery.py` owns orchestration, prompts, host processes, receipts, and sequencing. `_run_role` currently invokes `codex exec --ephemeral`, creates one new thread per role invocation, and the main loop rejects reused thread IDs. Neither current behavior supports returning to the same agent thread. Same model/role is not the same agent identity.
- `plugins/engineering/workflows/tracked-delivery.yaml` owns reusable role method and frozen output contracts. Current version 0.6.0 specifies four fresh sessions and no repair loop; it needs a deliberate new contract/version for the new policy. Existing frozen runs must not silently inherit the new policy.
- `runPlan.get` / `runPlan.getStep` expose canonical checkpoints. `runPlan.update(metadata_json=...)` shallow-merges top-level metadata (`stackos/repositories/run_plans.py:645`); there is no metadata compare-and-swap here. A single controller writer is required for safe counters.
- `runPlan.recordStep(blocked)` retains a recoverable pause; `claimStep` may reclaim it. Reclaiming and recording replaces the current result, so preserve prior attempt receipts before doing so.
- `runPlan.reopen(step_id=..., reason=...)` resets the chosen step and all later steps on the same plan/run, returns the existing run token, and updates tracker mirrors (`run_plans.py:500`). It accepts non-draft live plans too, although documentation emphasizes follow-up after closeout. It clears affected `result_json`, so archive prior receipts first. Do not reopen with workers still executing. Its built-in `reopen_history` retains only 25 entries and is not a full attempt ledger.
- `runPlan.recover` is restricted system-lifecycle repair, not the repair-loop primitive; it cannot reset a normal running step.
- Dispatcher idempotency keys help reconcile daemon writes but do not deduplicate host model/filesystem effects. In-flight records and the 24-hour window still require state read-back.

## Concrete transition mapping

| Observed event | Decision/transition | Durable treatment |
| --- | --- | --- |
| Valid reviewer rejection | Treat as semantic feedback, not a crashed reviewer. Main agent/designer triages design defect versus implementation defect using finding evidence. | Record finding packet, candidate digest, classification, and selected route. Reviewer need not receive plan, executor narrative, retry history, or other task inputs. |
| Design defect or changed dependency understanding | Return feedback to the design agent; revise the plan; then implement, independently review, and write tests only after approval. | Archive affected receipts, reopen `plan-task` on the same plan, retain the original requirements and failure history. Revised design never changes user intent without clarification. |
| Implementation defect | Return feedback to the current execution agent once for that failure episode. | Archive receipts; reopen `implement-task`; retain role/thread identity and consume the same-agent repair allowance before dispatch. All downstream approval is invalidated. |
| Same implementation failure recurs after that repair | Replace the execution agent with a new thread and continue the same role/plan/worktree under the same scope. | Record old/new thread IDs, replacement reason, preserved task/plan and reconciled candidate state; do not create a new run plan or reset counters merely because identity changed. |
| Process/transport/schema failure | First determine whether the operation or worker actually completed. A lost recordStep response is a persistence problem, not a model failure. Where completion is established, reconcile it without executing again. | Preserve partial evidence, process-exit/custody evidence, and uncertainty. A pending attempt persisted before dispatch must not be mistaken for permission to dispatch again. |
| Confirmed execution failure with retry authority | Retry according to the one-retry policy, preserving task and role identity where the host supports it. | Consume the relevant retry allowance before dispatch and record the observed outcome. Unknown partial effects must be reconciled before any new attempt. |
| Failure repeats after the allowed retry | Return to the designer for an explicit `stop`, `revise`, or `try_again` decision. | Store decision, rationale, evidence references, and budget state. Do not treat an agent decision as broader external-effect authority. |
| Designer chooses `stop` | Stop with truthful failure/blocker evidence. | Use canonical step lifecycle; no silent replacement plan. Distinguish terminal stop from unresolved external blocker. |
| Designer chooses `revise` | Route through design revision, implementation, fresh independent review, then tests. | Reopen from `plan-task` only after archiving receipts; preserve global/episode budgets rather than implicitly resetting them. |
| Designer chooses `try_again` | Dispatch only after the pending cap semantics are resolved. | Do not implement budget reset or final-attempt interpretation by assumption. |
| Worker/controller interruption | Reconcile current step, worker liveness, and candidate effects, then select the applicable branch above. | A new controller invocation may resume the same run. No scheduler or self-restart is implied. |

## Minimum durable attempt record

Use one namespaced field in existing run-plan metadata, not a filesystem workflow database. Retain a policy version, current failure episode ID, stage/role, attempt ID, thread ID and replacement lineage, failure class, input plan/candidate digests, start/end disposition, receipt or evidence reference, consumed retry/repair/replacement/designer-loop counters, and designer decision. Persist dispatch intent and consumed budget before invoking a worker; append completion after it returns. Missing completion means uncertain execution, not automatic redelivery. Preserve all bounded-loop receipts before reopening clears step results. Recompute counters from the ledger to detect accidental reset.

Metadata is canonical run-owned control evidence, but its current shallow update is not a multi-controller lease. A single host controller lock can prevent concurrent local controllers without becoming a second state store; cross-host takeover is not solved by that lock. Do not claim distributed exactly-once execution.

## Smallest implementation boundary

Keep changes in the engineering controller, workflow/preset guidance, corresponding documentation, and focused tests only after independent source approval. Implement one explicit controller transition function and one persistence/reconciliation path; reuse canonical reopen/claim/record/update operations. Keep run/step identity stable, archive evidence before reset, preserve dirty candidate work, and never operate on unrelated MYB-66 paths or run IDs. Treat reviewer attempts as independent review contexts constrained to original requirements plus actual outcome, even when other roles receive corrective feedback.

A resumable host session mechanism is required to satisfy same-agent retry literally. Removing `--ephemeral` alone is not an assessed solution: it changes isolation/storage, and host CLI resume must preserve the approved effective profile. The separate isolation assessment owns that choice. If ephemeral app-server threads support continuation only while the server survives, a server crash must explicitly be recorded as identity loss/replacement, not described as resuming the same agent.

## Unresolved policy and integrity questions

1. The pending designer `try_again` cap question is blocking final retry-budget implementation.
2. The user distinguishes same-agent repair, new-agent replacement, and retry-then-designer escalation. Their ordering when a replacement itself fails is not completely specified. Preserve them as separate counters/events; do not silently multiply budgets or reinterpret a replacement as resetting the same failure episode.
3. Define recurrence by a stable failure episode and adjudicated semantic equivalence, not merely an error-string hash. Wording changes must not evade caps; genuinely new failures must not be mistaken for recurrence.
4. The reviewer cannot reliably diagnose whether the hidden design or implementation caused a rejection from its restricted inputs. Keep that attribution with the main agent/designer; reviewers report observable deficiencies.
5. A retry can repeat host commands or external effects. Existing metadata and daemon idempotency cannot guarantee no duplicated arbitrary shell/provider side effects. Reconcile partial effects and retain scope restrictions before retry; unknown effects remain an explicit blocker to blind replay.
6. Revisions after tests exist require invalidating earlier approval and controlling stale tests. Do not run obsolete tests as current evidence or delete unrelated/user work. The transition must preserve historical tests/evidence while ensuring the next test-writing phase follows fresh approval.
7. No post-crash automatic launch is possible without a surviving host or scheduler. This design supplies automatic recovery within a live controller and resumable recovery when reinvoked, not autonomous daemon scheduling.
