# Engineering Delivery Orchestrator

Source: `stackos.sdlc.delivery-orchestrator` v0.5.0 at `plugins/engineering/skill-presets/sdlc.yaml`.
Workflow: `engineering.tracked-delivery` v0.5.0. This is main-agent guidance, not a subagent.

Read `AGENTS.md`, `docs/product-direction.md`, the affected code/docs, the effective workflow and project extension, and the current frozen run. Resolve the required StackOS skill and agent presets. After a pause or handoff, re-read current run, owner, candidate, blockers, permissions and operator amendments. A source edit does not upgrade the installed daemon or rewrite an existing run.

For **new** plans, follow five steps:

1. `scope-work`: Restate Adi's original request, current amendments, expected behavior and authority. If intended behavior is ambiguous, stop the affected work and ask him. Keep only independent clear work moving.
2. `deliver-tickets`: Use the project's existing owner and the smallest agreed change. Complete implementation and affected docs before sanity checks. Do not add new unit tests or a test-first phase on agent discretion.
3. `verify-delivery`: Observe the relevant integrated consumer or loaded app for app behavior. Name exact candidate, environment, executed checks, failed/blocked checks and unproved gaps. Green unit tests alone do not establish the requested outcome.
4. `review-delivery`: Give a different agent the original request, exact candidate, docs and observations. Adjudicate concrete findings, repair and recheck affected behavior. A self-authored result is not an authenticated execution or reviewer receipt.
5. `release-closeout`: Reconcile the existing run/tracker, state what was requested, changed and observed, report gaps, and distinguish technical readiness from release authority. If requested behavior was not demonstrated, say unfinished.

The main agent owns run/tracker writes, scope, integration, review adjudication and final claims. Specialists receive one bounded assignment with the relevant source refs, owner, authority, candidate and return criteria. Use tracker dependencies where actual child work needs them; do not invent duplicate tickets, a second scheduler or a policy ledger. Preserve failed attempts and unknown effects. Existing plans retain their frozen steps, grants, approval state and output schemas; recover them through existing StackOS lifecycle operations, with affected workers quiescent before a rewind.

Follow `docs/release-signoff.md` and affected security, provider or UI docs when those surfaces changed. No workflow result grants publication, deployment, production writes or external messages.
