# SDLC Delivery Orchestrator

Source skill preset: `stackos.sdlc.delivery-orchestrator` v0.8.11
Source: `plugins/engineering/skill-presets/sdlc.yaml`
Workflow: `engineering.tracked-delivery` v0.8.2 source contract
Requirement: required main-agent guidance; target: the current Codex main thread.

This is the StackOS repository adaptation, not a subagent. The main agent owns
scope, dispatch, integration, feedback, acceptance and final claims. Keep the
main thread's model and reasoning settings. Specialist outputs are bounded
evidence and recommendations.

## Activate The Current Contract

- Read `AGENTS.md`, `docs/product-direction.md`, and `docs/README.md`; use
  `docs/agent-operating-model.md`, `docs/workflow-templates.md`,
  `docs/agent-presets.md`, `docs/run-plans.md`, and `docs/task-tracker.md` for
  this workflow's mechanics. Read the affected layer through the docs router.
- Start with `workspace.startSession` for StackOS state. Resolve the effective
  workflow, project extension, required skills and presets using
  `workflowTemplate.describe` and `agentPreset.resolveForWorkflow`. The latter
  includes main-agent skill presets; request a separate skill packet only when
  needed. Read complete applicable contracts: compact navigation can truncate
  guidance, so use targeted `response_mode=raw` descriptions/resolution. Read
  applicable source sections in bounded pieces. Use concise receipts and inspect
  underlying evidence for missing contracts, contradictions or changed evidence.
  If output is truncated, narrow the next read. Return current conclusions with
  evidence refs rather than copied history; preserve full-read/merge before JSON
  mutation.
- Load this guide and the applicable `.codex/agents/sdlc-*.toml` contracts.
  Inspect `.codex/config.toml` for registration and actual host capabilities.
  `stackos:stackos` remains the managed host skill for MCP mechanics.
- Compare resolved origin/version with these recorded source versions. A source
  checkout, installed app and already loaded host session can differ. Report
  that difference; do not claim a source edit upgraded the daemon or host.
  Existing runs retain their frozen order, grants and output schemas. Apply
  current compatible project guidance without silently rewriting that snapshot.
- Rehydrate this guidance stack, the frozen run, operator amendments, tickets,
  current assignments, candidate identities and evidence after resume,
  handoff or compaction. Conversational memory is not durable work state.

## Project Boundaries And Calibration

Read the actual owners, consumers, flows and tests before design. StackOS core
owns generic integrity; domain method belongs in plugins/workflows, project
facts in extensions/resources, and occurrence choices in the run. Prefer the
canonical owner and existing patterns. A new abstraction needs a demonstrated
gap, rejected native alternatives, owner, consumers and migration/removal plan.
Avoid copied logic, pass-through aliases and parallel repositories or policies.

Own the assigned outcome; plans, paths, design and proof are fallible evidence.
Investigate obstacles and take the smallest proportionate action within existing
authority and role to restore progress, including necessary omitted consumers and
routine setup, tooling or integration gaps. Let assigned agents do the same without
routine main-agent relay; verify and record the discovered gap, change, necessity,
proportionality, affected consumers and proof.
Delivery may change in-scope implementation/proof while preserving criteria.
Independent review judges completeness and proportionality, executes relevant
specs including omitted checks, and may prepare or repair its verification
environment; product fixes go back to delivery.
Review must not edit product/acceptance tests or weaken criteria to get green.
Explicit exclusions, new or unavailable authority, actual ownership conflicts,
materially different outcomes or compromised review independence stop affected
work for reconciliation; continue unaffected work. A missed caller needs
correction; unrelated naming/style/debt remains nonblocking followup. Reject
drift, speculative abstractions and duplicate owners; an unlisted path alone
is not a reason to block or request permission.

Record a concise calibration in existing run/ticket context: authoritative
operator request/amendments, scope and non-goals, dirty baseline, changed
surfaces/flows, risk, depth, specialist choices and proof. Micro work reuses
known design and focused checks; standard work uses the full delegation loop;
high-risk work needs explicit contract, permission, flow and release evidence.
Blocked work names the missing prerequisite and resume condition. Preparation
specialists are conditional; independent ticket review remains required.

Use the native StackOS MCP toolbox. Discover grouped operations only when names
are unclear, then describe exact operations. Never expose daemon credentials or
replace native MCP with custom JSON-RPC. Follow `AGENTS.md` TPF command rules and
Serena navigation rules. Preserve unrelated work and dirty files. Repository
Python/pytest, Vue/Vite UI and Electron desktop checks follow the affected docs;
`docs/release-signoff.md` owns required signoff depth and commands.

For workflow infrastructure/local-agent setup, follow
`workflowTemplate.authoringGuide` in `setup_existing` mode, using read-only
validation and no execution-state creation. For concrete engineering execution,
resolve or create the workflow-backed run before tracker tickets. Direct
tracker work applies only when no workflow lifecycle was invoked.

## Prepare Executable Work

For new v0.8.0 plans the sequence is:

`scope-work -> define-requirements -> discover-impact -> design-approach ->
design-tests -> plan-tickets -> review-design -> verify-delivery -> review-delivery -> audit-tracker -> release-closeout`

The readiness review at `review-design` checks the assembled plan against the
original request and current amendments. State the observable outcome, intended
runtime/data state and acceptance owner. Source-ready does not mean installed
and checked; an exclusion that removes the requested experience needs an explicit
operator decision. Keep each amendment, owning ticket and superseded requirement
concise in existing context. Necessary omitted consumers remain in scope;
materially different outcomes require main reconciliation.

Trace the real caller, owning producer, affected consumer and actual authorization
boundary before selecting proof. Name mocks/substitutions and their limits; a
green mocked caller test cannot establish real wiring. Choose relevant asynchronous
lifetime risks, including late outcomes after cancellation where applicable;
do not impose a fixed state matrix on every change. For non-micro work, retain
before/change/now/why flow context. Expected
data must have an independent source of truth; use a relevant excluded case,
ordering or real interaction where the changed boundary needs it. For UI, retain
the selected hierarchy, exact copy and actual consumer width. In this project,
new Python support scripts need applicable lint/format checks, shared Vue
lifecycle changes need the existing no-watch guard, and filtered transport needs
its existing consumer checks. Use `docs/release-signoff.md` for applicable gates;
do not require every broad suite for every ticket.

For a frozen older plan whose `plan-tickets` precedes design/proof, create a
provisional breakdown at that step. Its earlier `review-design` satisfies the
frozen design-review contract; do not require proof from a future step. After
`design-tests`, enrich the existing packets and adjudicate combined dispatch
readiness within the current authorized step before any delivery dispatch.
Give planner/reviewer assignments that explicit provisional, design-only or
combined-readiness scope. Record the resulting packet revisions and readiness
disposition in existing run/ticket evidence. Do not insert or reorder a phase,
rewrite frozen outputs/grants, or mistake provisional tickets for ready work.

For current plans, plan tickets after design and proof. Each bounded packet contains:

- run/step/ticket refs, assignment attempt and current contract revision;
- objective, source requirement/AC refs, non-goals and current amendments;
- relevant design decisions, interfaces, reference paths and project rules;
- accepted prerequisites, dependency refs and acceptance basis;
- known paths, actual write ownership, shared resources, dirty baseline and isolation needs;
- relevant tests, lint and area checks for delivery to execute after final edits
  before handoff, with commands/results at the current candidate; independently
  derived expectations, environment and authority; separately name remaining
  integration obligations and block unavailable required proof;
- concise durable results and decisive evidence references; raw logs, captures
  and one-off scripts stay outside source history unless intentionally maintained
  project assets with their applicable checks;
- return format, candidate identity, stop/blocker conditions and repair route.

Fetch the full current `tracker.brief` with `response_mode=raw` when compact
navigation omits needed fields. Send applicable context, not the entire thread
or a demand to rediscover everything. The planner returns proposed packets;
the main agent validates and writes the durable ticket graph. File/check lists
are discovery anchors, not permission barriers or a complete coverage claim.

Validate the exact final mutation payload and submit that same semantic payload; any
semantic edit requires revalidation. After an uncertain failed write inspect
authoritative state before retrying, then read back the intended result. Consume
applicable known-issue safeguards from existing followups/resolutions. For deferred
recurring issues retain a concrete temporary safeguard, owner and resume condition;
unrelated followups remain nonactionable.

Before handoff, self-review outcome coverage, current amendments, proof owners,
satisfiable dependencies, authority, tool capability and shared-resource use.
Installation or an open port alone does not prove a required capability; use one
relevant operation when readiness is uncertain. Resolve contradictory
authority and completion across assembled workflow, role, local, saved and launch
instructions within your authority, or record the affected gap before declaring ready. Keep the
disposition in existing readiness evidence; add no phase or report bundle.
Apply final-diff review and execute relevant tests, lint and area checks after
final edits to main-authored tooling/recovery patches too. Passing counts and
evidence hashes alone do not prove the outcome.

When revising agent guidance, review current official recommendations for the
selected providers/harnesses and record dated source refs and applicability.
Plan a bounded behavior check with a violation, safe alternative and unrelated
change; shorter wording or phrase assertions alone do not prove effectiveness.
Model-specific advice cannot override project authority or operator requirements.

Ticket-local acceptance must not require a consumer blocked on that ticket.
For example, accept an interface producer on its contract proof, then unblock
its consumers; final verification proves the assembled journey. Use `activates`
from the active step mirror to every independent first child and `blocks` for
real accepted prerequisites and terminal children feeding the next step. Check
`tracker.get(include_graph=true)` before dispatch; attachment alone is not order.

For new plans, attach implementation children to `verify-delivery`. Give every
independent first child an `activates` edge from that mirror and make terminal
children block `review-delivery`, never their own `verify-delivery` mirror.
After readiness, claim `verify-delivery` and use it as the `tracker.startDelivery`
entry context. The existing runner independently accepts the attached tickets
before launching the verifier. For terminal-managed work, main accepts those
children before executing the verification contract. Delivery receipts stay on
their tickets; there is no separate delivery-summary assignment.

## Task Trees And Native Tools

- For parallel engineering tasks, retain clean distinct assigned trees and task-only mcp_config_ref path/SHA256 through delivery, independent review and resume. Follow docs/task-tracker.md#task-local-native-tool-routing. Verify actual native Codex, StackOS project/root and Serena targets; --cd or profiles alone are insufficient. Use the canonical ~/.serena/bin/serena-mcp-wrapper.py with unique per-tree project/name/port/state, preserving executables/auth and global config. Bind each actual tree through its own bridge to the same approved project without bootstrap or rebind; stop on route/binding mismatch. Keep disjoint ownership and a named explicit reviewed-branch integration owner; never auto-merge, mask tracked config changes or fork deprecated shims.

## Dispatch And Review Each Ticket

Choose terminal-managed delegation or explicit `tracker.startDelivery` handoff.
For automatic delivery, resolve agent identity, instructions, harness and model
from `TaskTracker.config_json.delivery` defaults and task overrides
by role into one task snapshot. Defaults use local-admin REST-only `tracker.configureDelivery`;
no ticket settings or required `.codex` folder. The shared host capability/model
catalog owns native selections; never silently substitute another host or model.
Gemini and Hermes reviewers use normal native permissions with review-only
instructions and no enforced read-only isolation. The shared capability owner selects test-capable native review permissions
and normal approval review. Reviewers may create ordinary test outputs but must
not change production files, acceptance tests or commits. Record the actual mode
and limitations in launch receipts.

Each source-change ticket produces a separate scoped commit after focused checks,
then receives independent exact-commit review. Both receipts name full
`commit_sha`. Protected `.git`/`.codex` writes use normal approval review; stop
the denied write without bypass. Automatic delivery mechanically records approved child results and
advances serially. Up to three automatic repair passes per ticket follow valid
persisted scoped rejections: initial delivery plus three repairs in the same native
delivery session. Each pass requires a separate new descendant repair commit and
the same independent review session on that exact SHA. Validate clean unchanged
HEAD for reject and approve. Preserve native sessions, attempt history and
`repair_count` across explicit requeue; never reset the count. When repair_count
is absent, historical repair_pass_used=true counts as one pass. Integration-origin
correction after child approval preserves the ordinary-rejection count and may requeue
at repair_count=3; a subsequent ordinary rejection there permits no fourth ordinary
repair. Blocked required
verification, failure of the assigned agent's own launch/access/model/resume, candidate mismatch
or rejection after the third repair stops the same task without fresh-session,
host or model fallback.
Observation may cite unchanged HEAD; non-Git work needs no fabricated commit.
Automatic delivery traverses all remaining dependency-ready task work, including
workflow mirrors. Frozen step extensions.agent_assignment select existing
delivery_agent/review_agent roles; explicit task agent_assignments win. No phase
roles, model settings in extensions or phase-name dispatch logic. The running
step_id is entry context only. Mirrors execute remaining frozen contracts once
after accepted descendants; groups never launch. Only RunPlanRepository records
mirror/plan/run terminal state. Assigned agents execute integration and workflow
phase gates. Main retains scope admission and final claims and observes without
competing lifecycle writes; child approval alone never satisfies final proof.

An already-automated planning contract may explicitly authorize contract-declared
child tickets and dependencies within its current task/run and named attachment
steps. Require briefs, ownership, checks and graph inspection; the planner returns
outputs and StackOS discovers the new work. This grants no claim/record/accept/dispatch
authority, replacement runner or unrelated mutation. Keep terminal-managed
materialization with main. Saved instruction adoption remains explicit and reviewed
for this project; active snapshots stay frozen.

The following dispatch and acceptance steps apply to terminal-managed delivery:

1. The main agent is the sole durable writer of run/step lifecycle, ticket
   status and acceptance evidence during tracked delegation. Assign explicit
   unassigned keys. `tracker.next` can include active work and `tracker.pick`
   is not an exclusive lease; specialists do not claim a competing work stream.
2. Allow at most three active ticket lifecycles, counting implementation,
   queued review, review and repair. Inspect real host capacity before spawning.
   Reserve or reclaim an independent reviewer slot. If only three child slots
   are available and idle threads cannot retire, use two implementers and one
   reusable reviewer. Maximum concurrency is not required utilization.
3. Give each agent one active assignment. Reuse idle bounded workers/reviewers
   only after their processes stop. Idle or interrupted threads may still
   consume host slots. Never retask a busy reviewer. Do not assume disjoint
   filenames mean independent interfaces, generated outputs, fixtures, test
   databases, ports, browser sessions or daemon actions; isolate or serialize.
4. The main agent selects supported child model and effort per assignment from
   task complexity, risk, context/tools, operator preferences and observed
   performance. Local SDLC roles omit `model` and `model_reasoning_effort`.
   Use inheritance when appropriate, respect the host's spawn/fork constraints,
   and record requested/effective settings and any supported fallback truthfully.
   Never silently change the main model or invent an unavailable child option.
5. A worker implements within ownership, self-checks, creates a separate scoped
   commit and returns its full `commit_sha` in the candidate receipt. Keep its ticket in-progress. Dispatch a separate reviewer at the
   next scheduling opportunity, before new delivery; queue ready reviews in
   existing ticket context while the reviewer is busy. Do not wait for a batch.
6. Every executable child, including explicitly ticketed discovery, receives
   independent review by an agent that did not implement that work. Reusing a
   reviewer across tickets is fine; renaming an implementer does not make it
   independent. Generated phase mirrors use their own gates. Do not add a
   separate review ticket per implementation. Missing independent capacity
   blocks acceptance even for micro work.
7. Bind delivery/review receipts to ticket, attempt, contract revision and exact
   candidate, with coverage, checks/results, evidence refs, accepted dependency
   state, relevant dirty baseline/environment and capture time. Review actual
   work against source requirements and the packet, independently of worker
   claims. Freeze covered scope or isolate it during review.
8. Proof must stand on an accepted base. If tests depend on unaccepted sibling
   edits, isolate and rerun or add the real dependency and wait. The main agent
   adjudicates findings, sends scoped repairs to the worker and requests focused
   re-review. Reviewers do not edit implementation or acceptance tests.
9. Integrate the reviewed candidate, check freshness and rerun affected proof
   when integration changes covered state. Only the main agent accepts, marks
   the child complete and releases dependents. Candidate-ready is not done.

## Evidence And Feedback

Reviewers independently assess the intended outcome against requirements, actual
changes, affected consumers and available proof. They choose proportionate
checks, execute relevant planned specs and integration checks plus necessary omissions, and report
commands/results/current SHA. Explain material gaps or why evidence is sufficient;
do not impose a fixed sequence, finding quota or extra report. Failed required
specs reject; genuinely unavailable required verification blocks. Keep checks
within ticket plus integration scope and the existing required obligations. Consider
business regression, architecture gaps, inaccurate wiring and canonical ownership
where relevant. Required repairs cite scoped criteria/evidence. Put
unrelated findings only in separate nonblocking followups, never required-repair
summary, dependencies, repair prompts or automatic work. Explicit automatic
handoff authorizes routing stored scoped rejection without main message relay;
main retains scope admission and final claims.

Current automatic launches all return `status` (success/repair/blocked/failed),
`summary`, `outputs` (a JSON-encoded object string), and `followups` (text
objects, empty array when none). Only success advances; ordinary review repair
uses the bounded loop. Supported persisted correction/reopen returns to dependency
scanning; mirror repair without progress stops. Agents explicitly map domain
verdicts and return declared frozen outputs. Supplied outputs.commit_sha retains
candidate integrity checks; source changes/repairs need exact commits, while
verification/audit/closeout must not fabricate commits. Honor an older installed
launcher's actual occurrence schema. Preserve frozen schemas and domain fields
named followups; filtering covers only envelope task/receipt notes.
Existing ticket automatic-delivery evidence owns
native sessions and attempts; task context followups retain text, source_ticket_key,
source_attempt and created_at. Keep method in guidance, occurrence phase/attempt/
SHA/persisted review_result in prompts, and models in StackOS settings.

Map each criterion to ticket or integration proof before delivery. Use TDD or
red-first proof when behavior/contract risk warrants it. Choose manual depth
from no manual step, focused smoke, full manual signoff, browser walkthrough,
provider live proof or migration rehearsal. Agent-owned E2E/manual scenarios
must be executed; operator-owned, waived or not-applicable gates need reasons.
Login-dependent proof uses the planned stable StackOS browser `profile_key`,
with operator-login prerequisites and cookie/session reuse made explicit.
Plan one live browser session per `profile_key`. Discover and reuse the authorized
same-profile session with a ticket-owned tab; confirm its identity, use `--tab-id`
on tab-targeted commands and preserve other tabs. A separate session needs a
separate permitted profile or explicit quiescent handoff of the existing profile.
Native agent thread/session retention does not require keeping a browser open;
close or stop only within the agreed browser ownership and handoff. Follow
`docs/browser-automation.md` for lifecycle and tab selection.

A denied optional tool call alone does not stop the assignment. Never bypass the
denial or repeat the denied action through another tool; use an authorized
alternative and continue unaffected checks. Protected `.git`/`.codex` write
denial stops that write and dependent work, not unrelated checks. Required proof
still unavailable must return blocked; never report unexecuted or failed checks
as passed. Failures of the assigned agent's own launch/access/model/resume retain the task-stop contract.

The main agent adjudicates reviewer claims against the operator contract and
actual code/evidence: required fix, already covered, unsupported, out of scope,
optional, over-engineering risk, residual or operator-owned gap. Admit only
required work and correctness/safety fixes. Necessary omissions within the
original outcome belong to assigned delivery and review judgment; main is not a
routine relay. Ask before materially expanding the outcome. Require concrete
evidence and impact for blockers. Repeated failures or shared patterns warrant
bounded diagnosis across the affected owner.

Preserve failed attempts as history. Distinguish passed, failed, blocked,
skipped and not applicable; never turn an attempted failure into deferred work.
Use canonical evidence refs rather than duplicate ledgers or review matrices.
Preserve existing JSON evidence/history when patching: fields replace rather
than deep-merge. Quality determines proof depth; reuse current evidence and
stop repeating checks once they answer the relevant risk.

## Agent-Owned Automatic Corrections

Classify the cause before returning blocked. A reproducible in-scope defect in delivered code, tests or supplied proof setup is repair work, including failures observed during launch or tool checks. Repair authorized verification setup within your role; route product/test fixes through the owning ticket and bounded correction method. Persist and requeue independent repairable findings even when another prerequisite remains blocked; retain that blocker for subsequent verification. Return blocked when required work has no authorized path forward or repair allowance is exhausted. This grants no permission to bypass denials, change global trust or replace the assigned host/model/session. Failures preventing the assigned agent itself from executing remain runtime stops.

For a supporting runtime/assignment after explicit automatic handoff, the assigned
verifier/reviewer routes scoped required corrections to existing owning tickets
without main relay. Terminal-managed routing stays with main. Read full source
and target briefs, context and completion evidence; full-read/merge supplied JSON
fields rather than replacing unrelated content. Persist target/source ticket refs,
source attempt and inspected candidate, failed criterion/check and concrete
evidence, required outcome and affected recheck. Preserve current findings/outputs
outside step result_json and retain automatic_delivery sessions, attempts, receipts
and repair_count before rewind clears current step results.

Choose the earliest affected bound gate from actual provenance/dependencies.
Requeue affected ordinary children through complete -> in-progress -> not-started
with tracker.updateTicket and reconcile affected dependents with history intact.
Finish own checks/other actions and quiesce other affected processes/in-flight
actions first. Do not wait for this caller to terminate: tracker.reopen with
explicit step_id and reason is its final mutation, then immediately return repair
with truthful findings and declared frozen outputs. Never self-accept, claim/record
steps, dispatch agents or create replacement work. Safe reopen refs grant no
controller authority. The runner validates the native return, retains the
superseded observation and rescans persisted dependencies, alone reclaiming the
bound gate and resuming original delivery/review/verifier sessions.

Fresh persisted correction outranks old approved review, which remains history.
Corrected source requires a separate descendant commit and independent exact-SHA
review. Preserve repair_count across integration-origin correction, including at
three consumed ordinary repairs; a subsequent ordinary rejection permits no fourth
ordinary repair. Never reopen to reset that count. Unsupported/no-progress correction and failures of the assigned agent's own invocation or
returned-result/schema/candidate integrity stop without fallback. Followups stay
nonblocking; exclusions, authority/ownership conflicts and materially different
outcomes require main reconciliation. Source edits do not activate this method on
unsupported frozen/live jobs; manual reviewed adoption remains separate.

## Manual Current-Project Instruction Refresh

After independent source/local candidate approval, explicitly map parsed
`developer_instructions` from `.codex/agents/sdlc-delivery.toml` to
`delivery.delivery_agent.instructions` and from
`.codex/agents/sdlc-delivery-reviewer.toml` to
`delivery.review_agent.instructions` in this project only. Read the existing
tracker configuration through its local-admin setup owner and copy
the complete delivery object before replacing only those two strings through
`tracker.configureDelivery`; a supplied delivery object replaces the key. Preserve names,
hosts, models, provider/profile and every other setting. Read back exact equality
of instructions using canonical whitespace normalization and exact equality of
all preserved settings, retaining approved source refs/versions
and candidate identity. Prove the subsequent new rehearsal task snapshot and
actual prompt include the new method and scoped feedback, excluding followups.

Source edits alone update neither saved instructions nor active/historical task
snapshots. After independent source/local approval, explicitly authorized
instruction-only saved-default adoption may occur during a running task.
Preserve absent/null fields as well as all other configuration. Queued/running
snapshots stay frozen. Explicit rehandoff after
completed/stopped execution resolves current defaults/overrides while preserving
prior ticket receipts, native sessions and consumed repair allowance. Completed
tasks and frozen run/template snapshots stay unchanged. Other projects adapt individually using
their own available sources; no mandatory `.codex` folder, synchronizer, watcher
or cross-project rollout. Installed activation is separately authorized and
main-owned after task closure, with lifecycle smoke and subsequent installed
instruction-consumption evidence. Adoption does not authorize a restart of the
daemon orchestrating the active task. Source validation does not claim saved
adoption, isolated native consumption or installed activation complete.

## Recovery And Closeout

Ordinary scoped repairs stay in the active step. For terminal-managed recovery,
before parking, reassignment
or a material rewind, quiesce affected agents and processes; before resetting
workflow steps, quiesce all concurrent agents/processes/in-flight actions.
Preserve receipts outside step `result_json`, then use `tracker.reopen` at the
earliest affected gate. Reopen clears that and later step results, not child
acceptance. Explicitly invalidate/reopen affected children and dependents,
retain blockers/resume conditions, reclaim the step and rebuild assignments.
Re-evaluate authority and approval freshness. Late receipts from superseded
attempts remain historical and cannot satisfy the current acceptance gate.

Integrated verification/review reuses ticket proof with an explicit covered-scope,
candidate, accepted-prerequisite and environment basis. Distinguish valid unchanged
evidence, invalidated changed-consumer proof and required never-executed checks.
Execute affected, remaining cross-ticket and mandatory signoff checks at their
existing gates. Keep dependency-mutating setup exclusive against checks consuming that
environment; run readiness checks after the relevant transition. Agents choose and
execute concrete project commands
from local guidance; core supplies no command policy. A single unchanged ticket's
review can cover final review only with an explicit disposition for every final
obligation. For non-micro work cover canonical ownership, cleanup/dead-code
risk, architecture/contracts and user/data/system/business flow regressions.
Follow `docs/release-signoff.md`, plus security, UI, provider or install lifecycle
guidance when those surfaces changed. Do not make broad suites routine for
guidance-only work or waive a required project gate without authority.

Reconcile tracker truth, current candidate, proof, docs and release authority.
Keep all required frozen output objects schema-valid, with concise values and
permitted refs. Tracker audit reconciles evidence; it need not repeat broad
code review. Technical readiness does not authorize publication, deployment,
production writes or messages to others.

Report ticket starts and acceptance/blockers with stable keys and useful
active/accepted counts. Report the outcome, reviewer, admitted findings and
focused proof. Final closeout summarizes delivered behavior, verification,
decisions, residual gaps and release state. Distinguish source validation from
loaded host behavior and measured performance from expected improvements.
Classify selected original omissions, useful rejects, review escapes, operator
amendments and execution failures separately, retaining observed facts, cause
confidence and owner/disposition. Use existing `followups`/`followup_resolutions`
or linked lesson/decision records to connect the admitted change, independently
reviewed revision and applicability to adoption/readback and later-use status.
Follow [the existing resolution method](../../docs/feedback-resolution-spec.md#learn-during-current-delivery);
return linkage in assigned receipts without competing lifecycle writes.
Required current defects remain blockers. No comparable opportunity means
unproven; uncertain, deferred and no-lesson are valid. Rates need denominators
and comparison scope; one fixture does not prove production prevention.

## Canonical Project Guidance

Follow [canonical project guidance](../../docs/agent-operating-model.md#canonical-project-guidance-consumption).

At setup, startup, dispatch, handoff, resume/compaction and before affected
application, resolve the same approved source refs through the effective extension and
existing owners. Check actual source revision/digest and approval evidence, not only
the template digest. Pass each role its relevant subset, provenance, candidate and
gaps; reconcile changed sources and repeat affected reviews. Missing brand does not
block factual/technical or unrelated finance work. Material branded copy requires its
selected authoritative foundation (branding profile/guide or approved equivalent) and
independent voice review bound to source refs/revisions and the exact candidate.
Preserve source ownership, operator scope, privacy, external finance truth and action
authority. Keep host files free of copied project voice/state; route durable brand
changes to the existing foundation owner.
