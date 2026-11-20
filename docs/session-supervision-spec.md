# Session supervision specification

Revision 0.7, 2026-10-14. Accepted operator direction; narrowed implementation
proposal pending design review. Packet labels are proposals, not tracker IDs,
run grants or permission to resume the stopped pilot.

Start with one controlled worker in an isolated worktree. Its existing execution
work item owns scope, criteria, dependencies, blockers, evidence and next action,
and links to the workflow/run/step. Agents and humans must inspect those same
facts. Feedback and verified learning remain integral to the intended process;
the [follow-on contract](./feedback-resolution-spec.md) phases their implementation.

## What has actually happened

The previous revision listed **19** scenarios. Only configuration/access blocking
was directly observed. Inconsistent completion reporting was also observed,
without proof of a lost notification. No supervised worker or synthetic lifecycle
has run here.

| Evidence | Conclusion and limit |
| --- | --- |
| Registration reported Codex absent; CLI discovery missed a working bundled CLI and encountered broken/incompatible alternatives | Observed host discovery/configuration problem |
| MCP initialization closed; bridge startup later exposed missing `stackos_connectors` | Observed dependency/startup problem, not a hung worker |
| Exact required `stackos-connectors==0.1.1` installed; standard MCP SDK initialization subsequently exited successfully | Handshake repaired at that stage; native project access subsequently verified below |
| Earlier availability checks searched an inventory that omitted directly exposed StackOS tools; a direct native call now succeeds | Prior absence claim was incorrect; current project access is verified below |
| Earlier task-preparation fetch reported failure; native LT reads later worked | One reported access failure, not proof of a continuing LT outage |
| Another chat finished while its final text said work remained in progress and omitted promised binding evidence | Observed reporting/evidence inconsistency; no demonstrated lost event or completed-result receipt |

Frozen tool, false stall, scope drift, context-loss recovery, external-effect
restart, duplicate ownership, stale-owner publication, monitor loss, retry
exhaustion, approval handling, recovery, review rejection, learning and disposal
are **untested scenarios**. Source inspection identifies possible gaps, not their
frequency or operational severity.

Source review at `c76238bcd1707c89634e5c085c12d097c85ad487` found reusable owners:
[runs](../stackos/repositories/runs.py), [run-plan reconciliation](../stackos/repositories/run_plan_lifecycle.py),
[request claims](../stackos/repositories/agent_requests.py), [tracker](./task-tracker.md),
[durable effects](../stackos/actions/repository/durable.py),
[events](../stackos/repositories/project_events.py), and
[host lifecycle](../stackos/cli/launchd.py). Preserved run-plan audit heartbeats
do not measure worker activity; request claims do not establish canonical-item
ownership; arbitrary host processes lie outside durable provider-effect control.
These are source observations, not proof of the loaded daemon.

## First build and invariants

The first build adds an epoch record, a detached deadline shim, and publication
checks for a controlled local worker. Reuse existing run/audit/event and host
lifecycle owners; resolve actual operation/host seams before choosing a schema.
No new queue, general agent runtime, autonomous retry or learning controller.

1. **I1 — One work item, one owner.** Resolve external aliases to one existing
   project-scoped canonical execution-item ID before atomic ownership acquisition.
   Replacement retains that identity, scope history and cumulative budgets.
2. **I2 — Explicit epoch changes.** Ownership changes through transactional
   compare-and-swap and explicit revocation/reassignment. Elapsed time conveys
   neither revocation nor authority.
3. **I3 — Controlled publication.** The current epoch, scope and authority are
   checked where a candidate becomes authoritative or produces an external effect.
   Private local activity grants no publication permission.
4. **I4 — Independent deadline.** An acknowledged, pre-armed shim owns the exact
   process-group deadline and cancellation receipt before worker execution starts.
   Its declared capability and sleep policy bound every guarantee.
5. **I5 — Separate activity and acceptance.** Independent activity observations
   can postpone suspicion. Only attributable criterion evidence can advance a
   checkpoint; independent review and the applicable human gate decide acceptance.
6. **I6 — Preserve stops and uncertain effects.** Original stop scope, denials,
   failure counts, deadlines and effect identities survive replacement. Existing
   durable-action readback/reconciliation owns uncertain effects.
7. **I7 — Shared, read-only inspection.** Work-item status exposes freshness,
   disposition, owner, blocker, required actor/input and next action with evidence.
   Observation and feedback are facts, not action authorization.

## Canonical identity and ownership

The canonical item is the existing execution ticket, or task where no ticket
exists. Linear remains myBrickStack's product authority, LT its execution and
checkpoint authority, and StackOS the execution/audit owner. External keys are
aliases, not alternate ownership keys. Store/resolve aliases using existing
tracker links; an ambiguous mapping blocks that item's acquisition.

Key the proposed typed owner record by `(project_id, canonical_item_id)`.
Minimum fields: current owner/session, epoch, revision, scope digest, checkpoint
ref, original stop/budget refs and predecessor. Current runs and immutable
events retain attempt lineage and receipts. Avoid copying source requirements
into another authority.

Acquisition and revision checks occur in one transaction. Duplicate triggers
return the current assignment or a conflict, with no second dispatch. There is
no time-based lease expiry or renewal. Reassignment revokes the old epoch only
after controlled-process quiescence and effect disposition are evidenced.
Uncertain quiescence blocks replacement. Revocation fences publication but is
not process cancellation.

Admission reads the authorities applicable to the specific operation. Unrelated
Linear/LT/service availability is not a universal gate. Cache revisioned
read-only facts with capture time; revalidate affected authority at publication.
Epoch and budget authority remain mandatory for dispatch.

## Worktrees and publication boundaries

Use a detached candidate worktree and a session-specific candidate ref such as
`refs/sessions/<id>/epoch-N`. Promote only after checking the current epoch,
expected destination revision, scope, candidate digest and applicable review.
Check the same contract at push, PR creation, authoritative receipt/checkpoint
publication and external-effect admission. Publication checks and effect-intent
reservation must be atomic at their owner; stable idempotency identities are
independent of epoch. In-flight unknown effects hold reassignment (I6).

Git worktrees share most refs and their object store. A private namespace is
organization, not access control. An unrestricted shell can move other refs or
use the network directly. The first slice therefore supports only a cooperative
controlled host whose publish credentials/tools are held by the lead/controller
and whose worker access is actually verified. Any unenforced shared-ref access
is disclosed; broader containment requires a separate clone or host-enforced
filesystem/network controls. Do not claim all shell writes are fenced.
[Git's sharing rules](https://git-scm.com/docs/git-worktree#_refs) define this limit.

Before disposing of an owned tree, preserve its diff/candidate, receipts and
failure/review evidence, then verify quiescence and effect disposition. A tree
discard does not undo external effects or alter another owner's checkout.

## Deadline shim, activity and sleep

The proposed shim is a small host process launched independently of the daemon
and worker, using the existing host lifecycle integration. On POSIX, use
`start_new_session=True` rather than thread-unsafe pre-exec hooks; spawn the
worker in a distinct process group. The shim first persists its identity,
manifest digest, deadline and cancellation intent, acknowledges readiness,
then releases the worker start barrier.
[Python's process API](https://docs.python.org/3/library/subprocess.html#popen-constructor)
defines those mechanisms.

The shim owns the timer, validates PID birth/group identity, applies bounded
TERM/KILL escalation within the total ceiling, observes group exit, and
atomically writes one sanitized receipt in the run-owned evidence location.
The daemon later reconciles that receipt idempotently. A disappeared shim or
unwritable receipt location produces an explicit control failure.

Capability: **signal the registered process group and verify observed members**.
This is not arbitrary descendant containment. Descendants may create another
session/group or become reparented; known escapes can be tested and reported,
but complete escape detection is not guaranteed. A suspected escape prevents
verified-cleanup claims and replacement until adjudicated. Shim kill, OS failure
and host sleep remain outside an unconditional deadline guarantee.

Activity is observer-computed: process identity/state, bounded diff-hash changes
at an operation-specific cadence, and independently captured command/test
start, completion and output changes. A heartbeat or token stream alone is
weak evidence. Activity may postpone a soft suspicion check up to the frozen
hard ceiling; it cannot advance acceptance or renew the ceiling. Tests retain
their own command/deadline and are not repeatedly launched just to prove activity.

There is **no universal 300-second coding stall rule**. Freeze expected duration,
activity check cadence and hard ceiling per operation from relevant baseline
data. A missed soft check records suspicion; only independent operation/deadline
evidence supports a hang finding. Ordinary implementation may legitimately take
20 minutes without an accepted result. The inherited pilot's 900-second stop
is specific to that scope, not a general development default.

macOS needs two clocks. Local Python reports `mach_absolute_time()` for
`time.monotonic`; it pauses during system sleep. Use a verified continuous
monotonic clock for total elapsed deadlines and an uptime clock for awake
activity gaps, recording boot identity and UTC for audit.
[Python](https://docs.python.org/3/library/time.html#time.monotonic) and
[Apple](https://developer.apple.com/documentation/driverkit/c-runtime-support?changes=_3)
document the distinction.

Proposed default: suspended time counts toward total ceilings. Continuous versus
uptime divergence, corroborated by host suspend/wake evidence, produces
`host_suspended`; wall-clock versus monotonic divergence alone may be clock
adjustment and remains `clock_uncertain`. On wake, inspect expired deadlines
and cancel/reconcile before new publication. Sleep itself is not a worker/tool
failure; any actual deadline expiry retains its reason and existing budget rule.
There is no restart-time extension. After reboot, reconcile the original
deadline and receipts rather than comparing clocks across boot identities.

User-space cancellation cannot run while the host is suspended. An exact
wall-time ceiling therefore requires a verified awake-host control/capability
or an explicitly approved contract change. The stopped pilot's existing limits
remain unchanged; do not silently adopt this proposed policy for that manifest.

## Observation and terminal dispositions

Keep observation separate from existing run/tracker lifecycle. Readiness is
`unknown/checking/ready/configuration_blocked`. Report monitor freshness as
measured age/unknown; losing an optional view does not interrupt a worker whose
independent deadline shim remains healthy. A missing required control blocks
the affected operation, not every read or unrelated operation.

| Observation | Allowed next observations and required evidence |
| --- | --- |
| running | waiting (named dependency/actor), suspected_stall (missed soft check), confirmed_hang (independent hard-deadline evidence), host_suspended (host evidence), blocked/scope_drift (identified fault), review_ready (candidate proof) |
| waiting | running (resume condition satisfied), blocked (prerequisite fails), host_suspended (host evidence) |
| suspected_stall | running (new observed activity), confirmed_hang (independent deadline/responsiveness evidence), waiting/blocked (diagnosed reason), host_suspended, review_ready (candidate proof) |
| confirmed_hang | blocked after bounded cancellation and receipt, or terminal attempt disposition; no implicit retry |
| host_suspended | prior observation after verified wake/recheck, or blocked/confirmed_hang when control/deadline evidence requires it |
| blocked or scope_drift | running/waiting only with recorded clearance/adjudication and remaining authority/budget; or terminal disposition |
| review_ready | accepted_completion (applicable independent/human acceptance), blocked (review rejects or proof missing), scope_drift (candidate invalidated) |
| accepted_completion | final for the accepted scope; subsequent work uses explicit canonical reopen/new attempt with preserved history |

Persist terminal attempt dispositions separately: `succeeded` (worker production
finished), `failed` (attempt unsuccessful), `cancelled` (authorized stop),
`abandoned` (explicit decision to end incomplete work), `superseded` (verified
replacement). Only active attempts transition into these states; no transition
back into active. Record actor, cause and evidence for each. A successful worker
attempt can still leave the item review-ready. Rejected review may lead to a
bounded repair; exhausted attempt budget ends that attempt as failed/cancelled
and leaves the work item's next required decision explicit.

Map canonical lifecycle through its existing owner: tracker `complete`,
`failed`, `aborted`, `deferred`, `skipped`, or run-plan lifecycle as applicable.
This adds neither tracker enum aliases nor a second workflow writer.

## Thin-slice proof and packets

First verify native workspace resolution, effective workflow/project extension,
adapted guidance, existing work-item/run/step ownership, exact grants and actual
host capability. Registration and initialization checks are prerequisites, not
a workflow run. This preparation folds the previous SUP-01 design gate into
readiness; no implementation ticket or run is asserted by this document.

| Proposed packet | Owner and scope | Ticket-local proof |
| --- | --- | --- |
| SUP-02 — Canonical epoch | Lead; existing runs/tracker links, typed schema/migration and operation/grant boundary | T2/T3/T4; alias convergence, atomic concurrent acquisition/revocation, transition matrix, restart/budget preservation on disposable state |
| SUP-03 — Detached deadline | Lead; verified host shim/lifecycle seam, process observation and receipt reconciliation; depends on SUP-02 | T1/T4/T5/T6; real bounded processes plus fake-clock boundaries, TERM/KILL/group identity, daemon loss, suspend/clock policy and escape limits |
| SUP-04 — Publication and inspection | Lead integration; bounded executor may handle reviewed adapters/docs; depends on SUP-02/SUP-03 | T2/T3/T7; private candidate refs, boundary race checks, read-only shared status, preserved disposal evidence and one emitted feedback signal |

The Linear planning items reuse the existing [MYB-71 parent](https://linear.app/mybrickstack/issue/MYB-71/build-and-prove-stackos-session-supervision):
[MYB-92 / SUP-02](https://linear.app/mybrickstack/issue/MYB-92/persist-canonical-stackos-ownership-epochs),
[MYB-95 / SUP-03](https://linear.app/mybrickstack/issue/MYB-95/enforce-controlled-worker-deadlines-with-a-detached-shim) and
[MYB-93 / SUP-04](https://linear.app/mybrickstack/issue/MYB-93/fence-stackos-publication-and-expose-shared-status), followed by
[MYB-100 bounded validation](https://linear.app/mybrickstack/issue/MYB-100/validate-the-bounded-stackos-supervision-slice).
Their native blockers encode this order. Read current status and assignment
from the work item; verify execution linkage and authority before dispatch.

| Test | Observable result | Invariants |
| --- | --- | --- |
| T1 Frozen tool, healthy controller heartbeat | Shim terminates the registered synthetic group within its declared awake-host ceiling and records one receipt without daemon responsiveness | I4/I5 |
| T2 Duplicate trigger/alias | Linear/LT aliases resolve to one item; concurrent admission launches at most one worker | I1/I2 |
| T3 Stale owner | Explicit revocation rejects stale promotion, checkpoint/receipt publication and controlled effect admission; no changed effect key | I2/I3/I6 |
| T4 Exhausted retry | Restart/rename/replacement preserves stop/counts; zero additional dispatches without exact resume authority | I1/I6 |
| T5 Activity without acceptance | A longer coding fixture changes its diff without false hang/acceptance; activity cannot exceed the hard ceiling | I4/I5 |
| T6 Suspend/control limitations | Clock adjustment differs from suspend; expired wake is reconciled; known process escape prevents a cleanup claim | I4/I6 |
| T7 Shared visibility/disposal | Agent/API/human inspection agrees on item, disposition, freshness, needs and next action; disposal preserves evidence and unrelated work | I3/I7 |

### Incident-derived acceptance: binding preflight and honest progress

Extend existing T5/T6/T7 and pre-dispatch proof to match
[MYB-100's incident-derived acceptance](https://linear.app/mybrickstack/issue/MYB-100/validate-the-bounded-stackos-supervision-slice):

- Before controlled-worker dispatch, read back the canonical item, project,
  workflow/run/step and applicable authority. Classify missing bookkeeping,
  unavailable tools, permission denial and stale/incomplete observation separately.
  Name the exact corrective operation or decision, responsible actor/input and
  next action; use existing owners and supported routes.
- Planning-only records, including `execution_authorized=false`, cannot launch
  execution. Separately authorized source work may continue with its own
  prerequisites. Classification expands no authority and resets no stop/budget.
- Expose observer freshness and last successful read, checkpoint provenance
  (candidate/source, capture time and evidence), report state and uncertainty.
  Distinguish baseline tests, implementation changes, acceptance and status;
  active status alone proves neither health nor hang.
- In a separately approved bounded fixture, induce missing binding and
  stale/incomplete progress observation through actual bookkeeping, readback and
  escalation routes. Freeze an explicitly approved operation-specific threshold
  before proof; add no universal timeout. Independently review detection/routing
  timestamps, authority and traces showing zero unintended dispatch/effects.
  This acceptance case remains **not run** until that proof is performed.

The original binding/progress incident's verified evidence comprised project 14's draft plan 22,
task 27, MYB-92 ticket 231 and pending delivery step 164, with controller
`run_id=null`, plus the two existing baseline tests. These readbacks and
source-command receipts establish bookkeeping/baseline facts, not implemented
supervision, observer freshness, independent acceptance or a worker-hang finding.
Subsequent MYB-92 implementation and component-review receipts remain linked to
its current work item; this historical incident paragraph is not that progress report.

### Proof contract at bounded validation

Apply [MYB-100's current proof refinements](https://linear.app/mybrickstack/issue/MYB-100/validate-the-bounded-stackos-supervision-slice)
to its existing obligations, after MYB-92 supplies the canonical contract.
Preserve MYB-92 → MYB-95 → MYB-93 → MYB-100 and the current milestone boundaries;
these refinements add no prerequisite to MYB-92 source work.

- Store explicit versioned artifact, required-test and invariant obligations on
  the existing item. Deterministic checks consume authorized receipts from their
  existing owners. Bind each receipt to item, relevant epoch, candidate/source
  revision, obligation, producer/consumer and verifier version. Reject stale,
  mismatched or unverifiable evidence and cross-obligation/attempt replay;
  same-scope evidence reuse remains idempotent. Receipts confer no permission.
  Never execute claim-supplied commands or introduce an `execSync` verifier.
- Use pure coverage/dependency checks over existing canonical references to find
  unknown IDs, unintended duplicate assignments, unassigned obligations/feature
  pieces, missing links and cycles. Preserve valid shared coverage. Do not import
  another ownership map/lifecycle or make keyword matches an acceptance gate.
  Structural completeness cannot establish behavior.
- Exercise each selected invariant through a real StackOS producer → consumer
  path in the admitted fixture. Under the same pinned verifier, pair its passing
  baseline with a disposable, deliberately weakened control whose negative
  result fails for that invariant. Setup failures and always-failing validators
  are insufficient. Restore/isolate the fixture; weaken no active control.
  Uncovered obligations remain unproven, with failed/blocked/skipped/not-run facts.
- Reconcile required test targets and guards against tests actually collected
  and executed, and relevant CI status, at the exact candidate revision. A merge,
  generic green command or guard aimed at another suite cannot prove acceptance.
  Reuse the binding/freshness case above rather than duplicating it.

The source assessment at `b76a98e4beee5a8d1eecbf94e7fe71e659e3b97d`
is untested, its currentness is unknown and no StackOS compatibility review has
occurred. Before any code adaptation, the existing owner and independent reviewer
must inspect the exact source plus StackOS schema/interfaces/authority, select
only necessary behavior and prove StackOS positive/negative cases. Adapt the
requirements meanwhile. Exclude the always-accepting boundary helper, harness
restart and a second authority; the stopped project remains untouched.

Each packet gets actual canonical ticket/run/step refs, allowed files, accepted
dependency revisions, implementer/reviewer, exact commands/expected results,
candidate identity and stop conditions before dispatch. Independent review
follows each candidate, not a final all-tests packet. Existing focused run,
request, durable-action, event, route and host suites supply component seams;
real process proof supplies cancellation evidence. Installation changes also
follow [release signoff](./release-signoff.md) and the lifecycle checks in AGENTS.

After local proof, independently review one exact bounded lifecycle through
loaded interfaces, then a separately admitted useful bounded work item. That
can establish operability, not improved learning or universal hang containment.
Additional supervision packets require an observed incident or explicit risk
justification; the [scenario backlog](./feedback-resolution-spec.md#deferred-supervision-scenarios)
retains the earlier requirements.

## Measures and current readiness

Record raw outcomes and denominators: false stall/hang findings versus
adjudication, missed/late deadline interventions, time blocked by each supervisor
dependency, shim/observation overhead, accepted-work throughput, intervention
count, duplicate work/effects, first-review acceptance, recovery time and actual
token/provider cost. Compare with a similar unsupervised baseline before claiming
improvement; missing data is unknown. One synthetic run proves no improvement.

Native `workspace.resolve` now succeeds: project 14, repository binding 14,
daemon reachable and project-scoped tools usable. `workspace.startSession`
reused that binding and registered bridge session 2440 without creating a
project, binding or run plan. The saved registration uses this repo's venv
bridge with `--runtime codex`; the missing package was repaired earlier.

Native reads resolved `engineering.tracked-delivery` v0.4.0 with no project
extension, and its main-agent preset v0.4.1 matches local guidance. Missing
framework/content-model profile hints are advisory, not access blockers.
Earlier catalog-only checks incorrectly treated direct tools as unavailable;
the actual native calls supersede that assessment. Those initial setup reads
did not establish execution grants or host deadline capability. Subsequent
draft-bookkeeping readback is recorded in the incident-derived acceptance
section; it supplies no runtime launch or feature proof.

The [MYB-72 / merged PR90 prerequisite](https://github.com/justguy/myBrickStack/pull/90)
and its [frozen manifest](https://github.com/justguy/myBrickStack/blob/08c052167d259c297bfabfb7914f159831eae582/docs/knowledge/stackos-synthetic-evaluation-manifest.json)
retain one run, two cumulative setup/tool failures, a 900-second no-outcome
limit, 90-second process-group limit and zero retries. Preserve inherited LT
and bridge failure provenance; ambiguous scope accounting remains blocked.
Exact owner-directed STOP2 resume, repaired prerequisites, admitted manifest,
host controls and cleanup/evidence authority remain required for that pilot.
This design revision supplies none of those execution permissions. Phalanx
remains excluded and stopped.

MYB-73–76 retain their existing acceptance criteria and ownership. The MYB-72
no-change approval fixture does not prove a connected incident-to-repair flow.
