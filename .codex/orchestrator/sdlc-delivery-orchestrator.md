# SDLC Delivery Orchestrator

Source skill preset: `stackos.sdlc.delivery-orchestrator` v0.4.1
Source: `plugins/engineering/skill-presets/sdlc.yaml`
Workflow: `engineering.tracked-delivery` v0.4.0 source contract
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
  guidance, so use targeted `response_mode=raw` descriptions/resolution.
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

For new v0.4.0 plans the sequence is:

`scope-work -> define-requirements -> discover-impact -> design-approach ->
design-tests -> plan-tickets -> review-design -> deliver-tickets ->
verify-delivery -> review-delivery -> audit-tracker -> release-closeout`

The readiness review at `review-design` checks requirements, design, proof,
ticket decomposition, dependencies and prerequisites together. Reconcile every
derived acceptance criterion with the authoritative request and its amendments;
an omitted requirement needs adjudication, not an automatic out-of-scope label.
For non-micro work, design the changed user/data/system/business flows with
before/change/now/why context, denied/error cases and concrete expected outcomes.
For unchanged flows, record why they do not need further proof.

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
- exact write ownership, shared resources, dirty baseline and isolation needs;
- ticket-local checks, commands/scenarios, expected results, environment,
  authority and evidence; separately name remaining integration obligations;
- return format, candidate identity, stop/blocker conditions and repair route.

Fetch the full current `tracker.brief` with `response_mode=raw` when compact
navigation omits needed fields. Send applicable context, not the entire thread
or a demand to rediscover everything. The planner returns proposed packets;
the main agent validates and writes the durable ticket graph.

Ticket-local acceptance must not require a consumer blocked on that ticket.
For example, accept an interface producer on its contract proof, then unblock
its consumers; final verification proves the assembled journey. Use `activates`
from the active step mirror to every independent first child and `blocks` for
real accepted prerequisites and terminal children feeding the next step. Check
`tracker.get(include_graph=true)` before dispatch; attachment alone is not order.

## Dispatch And Review Each Ticket

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
5. A worker implements within ownership, self-checks and returns a candidate
   receipt. Keep its ticket in-progress. Dispatch a separate reviewer at the
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

Map each criterion to ticket or integration proof before delivery. Use TDD or
red-first proof when behavior/contract risk warrants it. Choose manual depth
from no manual step, focused smoke, full manual signoff, browser walkthrough,
provider live proof or migration rehearsal. Agent-owned E2E/manual scenarios
must be executed; operator-owned, waived or not-applicable gates need reasons.
Login-dependent proof uses the planned stable StackOS browser `profile_key`,
with operator-login prerequisites and cookie/session reuse made explicit.

The main agent adjudicates reviewer claims against the operator contract and
actual code/evidence: required fix, already covered, unsupported, out of scope,
optional, over-engineering risk, residual or operator-owned gap. Admit only
required work and correctness/safety fixes; ask before materially expanding the
outcome. Require concrete evidence and impact for blockers. Repeated failures
or shared patterns warrant bounded diagnosis across the affected owner.

Preserve failed attempts as history. Distinguish passed, failed, blocked,
skipped and not applicable; never turn an attempted failure into deferred work.
Use canonical evidence refs rather than duplicate ledgers or review matrices.
Preserve existing JSON evidence/history when patching: fields replace rather
than deep-merge. Quality determines proof depth; reuse current evidence and
stop repeating checks once they answer the relevant risk.

## Recovery And Closeout

Ordinary scoped repairs stay in the active step. Before parking, reassignment
or a material rewind, quiesce affected agents and processes; before resetting
workflow steps, quiesce all concurrent agents/processes/in-flight actions.
Preserve receipts outside step `result_json`, then use `tracker.reopen` at the
earliest affected gate. Reopen clears that and later step results, not child
acceptance. Explicitly invalidate/reopen affected children and dependents,
retain blockers/resume conditions, reclaim the step and rebuild assignments.
Re-evaluate authority and approval freshness. Late receipts from superseded
attempts remain historical and cannot satisfy the current acceptance gate.

Integrated verification/review reuses valid ticket proof and tests remaining
cross-ticket behavior and mandatory project gates. A single unchanged ticket's
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
