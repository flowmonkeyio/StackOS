# Engineering delivery

Source: `stackos.sdlc.delivery-orchestrator` v0.7.0, from `plugins/engineering/skill-presets/sdlc.yaml`. Apply `AGENTS.md`, `docs/product-direction.md`, and `docs/workflow-templates.md` before execution.

StackOS `delivery run` executes `engineering.tracked-delivery` v0.7.0 for an operator-supplied task in a clean isolated worktree. It records the original requirements in a real run plan. Task model flags override the project's `agent_models` extension; defaults are Astra for planning and review, Luna for implementation and tests. Installed source, presets and workflow must be v0.7.0 before starting a new run. Frozen older plans keep their own contracts.

1. A fresh planner session receives the original requirements and returns a plan or a clarification question. Ambiguity stops the affected work.
2. A separate executor session receives the requirements and plan, implements and documents the change, and does not write tests. StackOS captures the changed code and digest.
3. A fresh reviewer session receives the **original requirements and actual changed code only**. It does not receive the executor's report, plan, host guidance or proposed observation specification. Outcome-scoped command and image tools remain available through a verified read-restricted host. Rejection, missing requirements or irrelevant changes stop before test writing.
4. Only after approval, a fresh test-writer session writes tests for the approved logic. It may not change the reviewed implementation.
5. StackOS retains each role's prompt, model, session ID, timing, progress and failures. After tests and controller observations, the same reviewer session receives only actual argv/output/exit evidence and must approve the outcome against the original requirements before handoff. Test results and agent reports alone cannot complete it.

Recovery reuses the same plan/run and the evidence-backed per-failure policy below.
This CLI controller does not provide unattended daemon scheduling. Follow project
rules and existing approval gates; this workflow grants no publication or deployment
authority.


Engineering delivery recovery v0.7.0 uses `delivery run --resume-plan <id>` with
unchanged original task, worktree, observations, and effective model settings.
Ordinary roles use a verified private app-server host. Commands see a filtered
read-only source view and private writable scratch with no network; only the
synchronous broker edits the actual repository. Applicable project guidance is
included as data for ordinary roles. Reviewer input remains original requirements
and actual outcome only. Use an installed trusted controller outside the candidate
repository. Requested and host-resolved models are recorded and checked on every
fresh, resumed, and replacement turn. Private host pointers are retained in the
canonical run ledger for literal thread continuity.

Supported local failures may retry only with positive scope admission and closed
broker custody receipts. Every earlier worker and observation affected by replay
must be reconciled before consuming an allowance or reopening. A preparation
failure proven to precede dispatch is recorded as not started. Missing custody,
hard interruption without closure, unknown effects, and actual denials remain
held; there is no manual assertion that upgrades old unknown receipts. Recovery
still uses the original attempt, one retry, and one designer-authorized final
attempt total, with no budget reset. No unattended scheduler is provided.

The frozen recovery policy is `bounded-recovery-v3`; older recovery schemas hold
for explicit reconciliation rather than being silently migrated.
`engineering_recovery` run metadata owns prompts, model/thread lineage, receipts,
reconciliation, prior step snapshots, and consumed budgets. File digests and test
archives store paths as values, preserving secret-value checks without treating
filenames as credential-field labels. A local POSIX worktree lock excludes
competing host controllers; it is not a distributed lease.

The original designer classifies each failure using durable evidence and compares
its root cause with every existing episode. Only an evidence-backed distinct
cause starts a new budget. Wording, phase changes, revisions, replacement, or
apparent resolution never reset a recurrence's budget. Each episode allows one
retry, then one designer-authorized final attempt total. The first execution
retry resumes the literal executor thread; recurrence replaces the executor.
Design revisions continue in the original designer thread. Reviewers receive
original requirements and actual outcome only; changed candidates require fresh
independent review before a fresh test writer. Verified test changes are archived
before retirement, and prior step results are archived before reopening.

Resume inspects the canonical handoff and consumed continuation checkpoints
before claiming or dispatching anything. Missing completion or uncertain reopen
state holds; a blocked checkpoint is not permission to replay. Private reviewer
state is retained for exact thread resume. Failed observations reach read-only
designer triage. Optional per-check `"confinement": "read_only"` uses the verified
host command broker with no filesystem writes or network; omitted confinement
retains authorized unconfined execution. Automatic observation recovery requires
positive confinement evidence, settled custody, and no unresolved authority
refusal. Command output or unchanged files alone do not establish safe replay.
Unconfined or uncertain effects remain held after triage; real denials are never
bypassed. Frozen earlier workflow versions are never silently upgraded.
