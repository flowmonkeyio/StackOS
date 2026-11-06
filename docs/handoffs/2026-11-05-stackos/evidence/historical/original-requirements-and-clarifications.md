# Original requirements and subsequent user clarifications

This is the requirements input for independent review, not an implementation report.

The delegated original outcome is a StackOS flow with four distinct sessions: Astra plans from the original task and requirements; Luna implements the plan without writing tests; fresh Astra independently reviews actual code and outcome against the original requirements without the executor report; only after approval, fresh Luna writes and runs tests. Role models must be configurable per task and repository/project. Completion requires the actual outcome to meet every original requirement with no unrelated changes. Green tests and agent claims alone do not prove completion.

Record prompts, progress, irrelevant work, issues, timings, failures, role models and session identities, code, evidence and outcome durably in StackOS where possible, without secrets. Preserve existing runs and dirty worktrees. Work in the existing `codex/engineering-four-session-delivery` candidate. No unrelated Phalanx, Atlas, migration, production database, MYB-66, PR approval, merge, or deployment work. After independent approval and tests, build/install the updated app and demonstrate its actual flow, coordinating with the existing owner.

The user explicitly requested a fresh Astra correction agent, then a separate fresh review, repeated until complete. Implementation precedes independent logic review; tests are written only after approval by a fresh Luna agent. The two-failure rule stops repeating a failed approach, not ownership. Materially justified corrections remain authorized. Stop an approach after 15 minutes without an outcome. Real denials must not be bypassed; unresolved worker or external effects must be reconciled before replay.

## Review boundary — exact user clarification

> 1. What cobsistues a failure? 2. Yes, it should only review the outcome without any other inputs. Tools that are relevant to the outcome should be available.

The parent clarified that the reviewer receives the original requirements and actual outcome only; relevant inspection tools remain available. Do not silently sacrifice interactive inspection. Known reports, history, host configuration and credential sources must not become reviewer input. Necessary outcome guidance/configuration may be reviewed as artifact data, never automatically loaded as instructions. This does not demand an impossible universal semantic classifier for ordinary code comments.

## Recovery — exact user requirement

> We should try to recover from each situation. A rejection may occur, and it needs to be resolved: either the design is incorrect (new to fings, oversight, dependencies, etc), or the execution failed. The next step for each is different - for design the flow needs to provide the feedback to the design agent and have it revise. For execution failure, return to the execution agent to resolve - only 1 time per failure and if it recurrs, change to a new agent and have it pick up that takes and continue the same flow (the only thing that changes is the agent).
> For failures, we need a retry mechanism and if it fails again, we need to go back to the design agent and have it make a decision - stop, revise or try again. This flow should also be capped at 1 retry

Assistant clarification: “One clarification on the retry cap: after a failure is retried once and the design agent chooses ‘try again,’ should that allow one further attempt total, after which the flow stops and asks you if it still fails?”

User: “Yes”

Thus the same failure has an original attempt, one retry, and at most one designer-authorized final attempt. Replacement consumes the final attempt; it does not reset the budget. Revision or rewording does not reset recurrence. Genuinely distinct failures need evidence-backed classification and their own bounded budget. Return to the same agent means literal session continuity; a replacement has a new identity while retaining the selected role model, task and flow. Preserve receipts before reopening steps in the same plan/run. Unknown effects may receive read-only triage and must remain held unless scope, effects and authority can be established; a model assertion alone cannot establish them.

## Model configuration — exact user clarification

> Make sure the models are configurable as later on we will introduce a complexity mechanism like Phalanx has

Preserve task overrides above repository/project defaults above built-in defaults across initial, resumed and replacement sessions. Do not implement the future complexity mechanism now.

## Compatible observation scope

Keep existing unconfined outcome commands available. An explicit optional per-check read-only/no-network mode may provide positive confinement evidence for automatic recovery. Failed observations should reach read-only reconciliation/designer triage; uncertain external effects or actual denials must not be converted into automatic replay authority. Successful stdout or unchanged repository files alone are insufficient proof.

## Bundled runtime boundary — parent delegation clarification

The parent delegated the following scope based on the user's existing quoted requirement that relevant outcome-inspection tools remain available:

> Within that scope, use StackOS's bundled Python in the confined reviewer environment, provided it preserves the review boundary: no access to executor reports, unrelated user state, credentials, or network. Do not widen permissions beyond what is needed. Verify the actual boundary and report any remaining limitation. Continue the recovery work.

This permits the necessary read-only packaged Python runtime dependency. It does not authorize general host-runtime roots, user state, reports, credentials, network, or bypass of managed requirements. The quoted instruction here is the parent's delegation clarification, not a new verbatim user message.
