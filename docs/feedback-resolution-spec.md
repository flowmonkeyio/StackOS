# Feedback, resolution and verified learning

Revision 0.2, 2026-10-14. Accepted operator direction; phased implementation
proposal. Depends on the working [supervision slice](./session-supervision-spec.md).
This is documentation, not a workflow, tracker item or authorization to execute.

The system should use its own tracked workflows to correct defects and retain
what works. The canonical work item connects every signal, investigation,
repair, verification and next action. Existing tracker, run-plan, event,
resource and [project-memory](./project-memory.md) owners retain their boundaries.

The existing [MYB-103 follow-on item](https://linear.app/mybrickstack/issue/MYB-103/integrate-feedback-and-verified-learning-after-bounded-execution)
tracks this scope under MYB-71 and is blocked by
[MYB-100's bounded validation](https://linear.app/mybrickstack/issue/MYB-100/validate-the-bounded-stackos-supervision-slice).
The first-milestone signal remains with MYB-93; this link grants no execution.

## Phase boundary

The first supervision slice emits one bounded signal through existing audit/
events and exposes it on the work item: source occurrence, canonical item,
run/step/attempt, scope and candidate digest, outcome/cause, evidence ref,
capture time and next required actor/action. Deduplicate by occurrence identity.
Signal emission alone does not create a repair, write a learning, authorize a
retry or change workflow lifecycle.

Implement the full loop after the deadline/ownership/publication slice has
ticket-local review and one real bounded execution has been inspected through
the loaded interfaces. This gate establishes a usable execution path, not that
supervision improves throughput. A failed first run can still supply evidence;
it must be resolved before treating that path as working.

## Learn during current delivery

Capture manual observations now through the work item's existing checkpoint/
evidence and linked learning/decision owners. Implementation of automated capture,
resolution and learning still follows MYB-100; manual capture creates no parallel
log, queue, authority or new prerequisite for MYB-92.

For each selected incident or verified useful outcome, preserve one linked chain:
incident/source/time → observed facts and cause hypothesis/confidence → bounded
admitted change → before/after proof at exact revisions under fixed criteria →
maintained regression → independent review decision → proposed/reviewed lesson
with applicability and later-use evidence. Unknown cause, inconclusive proof and
no reusable lesson are valid dispositions. Deduplicate reports while retaining
real repeats, original failures and cumulative budgets.

Reuse MYB-100's binding/stale-observation case. Reported merged-red-CI and test-
target/guard mismatches require exact PR/merge/candidate, CI job/target and
command/collection receipts before asserting cause or a fixed regression. Those
results remain pending; no incident outcome or improvement is inferred.
Use its [authorized-receipt, coverage and producer/consumer proof contract](./session-supervision-spec.md#proof-contract-at-bounded-validation)
where applicable. Source assessment is untested/currentness unknown; require
StackOS compatibility review before code adaptation. Do not port the always-
accepting boundary helper, claim-executing verifier or harness, or add an authority.

A reviewed change cannot rewrite an active execution's pinned contract or relax
acceptance retrospectively. Admit a new revision through existing revision/epoch
and review mechanisms; retain original verdicts and evidence. Lessons cannot
self-promote, change permissions/governance, remove independent verification,
reset budgets or recursively authorize repair. Contradictory evidence may
supersede/revoke a lesson through the same process. MYB-71's milestone order and
all existing user/grant/STOP2 limits remain binding.

## Resolution flow

1. **Capture.** Retain failures, issues, rejected candidates, uncertain effects,
   operational friction and successful patterns with attributable evidence.
   Include missing/contradictory evidence rather than inferring a cause.
2. **Correlate and triage.** The lead resolves the occurrence to the existing
   workflow-linked item, or creates an authorized child resolution item.
   Duplicate reports point to the same occurrence. Record cause confidence,
   recurrence, impact, actor and proposed next step.
3. **Disposition.** Choose bounded repair, prerequisite repair, evidence/readback,
   operator decision, defer, reject or already-resolved. Preserve who decided,
   authority, applicability and the exact resume condition.
4. **Execute.** Admit an isolated repair through the same workflow, owner epoch,
   budgets, grants and publication boundaries. Preserve the original failure
   and candidate. Automatic resolution is limited to an existing authorized
   operation/argument shape and remaining budget.
5. **Verify.** Reproduce the broken state, test the proposed repair, review its
   actual diff and confirm the original acceptance criterion. Independent
   review rejects plausible repairs without evidence.
6. **Close and learn.** Link the verified disposition to the occurrence and work
   item. Save a reviewed learning or decision with cause, proof, applicability,
   limitations and superseded guidance through the existing granted memory owner.
7. **Reuse and evaluate.** A later admitted step retrieves applicable reviewed
   guidance, records what it used and measures the outcome. Contradictory evidence
   may revise or retire the lesson through the same review path.

Use the existing work item for current objective, scope, workflow phase, owner,
checkpoint, dependencies, blockers, required input/decision, next action and
resolution disposition. Run-plan operations own generated workflow mirrors;
tracker operations own attached child tickets. Agents, dot and humans read the
same reconciled projection and evidence links.

## Bounded automation and self-repair

A stored signal may suggest a documented resolution route. The trusted controller
resolves authority from current run/step grants, approvals, ownership and budget;
workers and consumers cannot confer that authority with response fields.
Use a structured diagnostic with code, cause/evidence, proposed next action,
required actor/approval, read-only verification and source revision. Omit a
`retry_safe` flag entirely. A diagnostic recommendation is not a retry command.

Configuration failures follow prerequisite repair, uncertain effects follow
durable-action readback, stale evidence follows scope-specific verification,
and reporting failures reconstruct the existing result before fresh inference.
Approved startup grace/restart bounds cannot clear cumulative stops. Result
production and result reporting are separate receipts; reporting reconciliation
reuses the result identity and existing communication idempotency.

StackOS defects use this same process: observed failure, canonical item, isolated
repair, regression, independent review, verified disposition and reviewed lesson.
The broken component cannot be its sole health verifier or acceptance authority.
When the control plane is unavailable, retain the shim/operator route and visible
blocker; queue no autonomous replacement outside the current authority. Repair
does not authorize its own permissions, merge, deployment or stopped-pilot resume.

## Deferred supervision scenarios

The previous supervision revision contained 19 scenarios, mostly hypotheses.
The first slice retains frozen-tool, duplicate-trigger/stale-owner, exhausted-
retry, activity, clock/control and shared-visibility/disposal tests. The following
requirements remain a backlog, not mandatory first-slice acceptance:

| Scenario group | Evidence needed before its implementation packet |
| --- | --- |
| Scope drift and stale evidence | Actual affected authority/source changes; invalidate only affected checkpoint/candidate reuse while preserving historical proof |
| Context loss and bounded recovery | Replacement reconstructs current scope/denials/budgets; cleanup and pending-effect disposition precede dispatch |
| External-effect restart and uncertain send | Existing durable-action/communication receipts hold unknown outcomes; independently observed readback establishes one committed effect |
| Lost result notification | A committed terminal result plus a dropped notification; event/sweep race produces one report intent without new inference |
| Configuration/startup/monitor failure | Distinguish configuration fault from hang; expose unavailable control and demonstrate bounded startup/restart history |
| Approval wait/denial and review rejection | Actor, next check, authority and disposition stay visible; denied effects and acceptance remain blocked |
| Feedback-to-resolution | One occurrence reaches an admitted repair, regression, independent review and linked disposition without duplicate work |
| Working patterns and lesson reuse | Reviewed success/disproven hypothesis with applicability; later admitted use and attributable outcome |
| Self-repair | Bounded defect fixed through the system while an independent monitor/reviewer verifies the result |

An observed incident or explicit risk justification earns a bounded packet with
canonical refs, dependencies, file ownership, ticket-local proof and independent
review. Source inspection alone cannot establish incident frequency.

## Proof and measures

First prove occurrence deduplication, authority denial, failed repair, verified
repair, lesson supersession and read-only stakeholder agreement using disposable
state. Then inspect a bounded real issue through the loaded workflow. Separately
demonstrate later consumption of a reviewed lesson.

Measure unresolved occurrence age, recurrence after repair, rejected/ineffective
repairs, intervention time, repeated work, supervisor dependency blockage,
accepted-work throughput and attributable lesson use. Record baseline,
comparison scope, sample size and uncertainty. One synthetic lifecycle can prove
record linkage and mechanics; it cannot prove that a lesson improved outcomes.
