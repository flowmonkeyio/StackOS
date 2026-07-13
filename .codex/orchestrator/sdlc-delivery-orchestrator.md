# SDLC Delivery Orchestrator

Source skill preset: `stackos.sdlc.delivery-orchestrator` v0.2.0
Workflow: `engineering.tracked-delivery`

This is project-local main-agent guidance for Codex when orchestrating StackOS
engineering delivery in this repository. It is not a subagent and must not be
registered under `.codex/agents`. Specialist agents support the workflow; the
main agent remains accountable for integration, sequencing, adjudication, and
final user-facing claims.

## Use

Use this guide when the user invokes engineering delivery, asks for the
engineering workflow, or asks to set up/use the local SDLC agents. Resolve the
workflow with StackOS when executing durable work, and adapt the current run to
the active project context, dirty worktree, user constraints, and release risk.

## Required Start

- Start with `workspace.startSession` when StackOS project state is needed.
- Read `AGENTS.md`, `docs/README.md`, `docs/agent-operating-model.md`,
  `docs/task-tracker.md`, `docs/workflow-templates.md`, and
  `docs/agent-presets.md`; add `docs/security.md`,
  `docs/integration-contracts/AGENTS.md`, UI docs, or release docs when the
  changed surface requires them.
- Resolve `engineering.tracked-delivery` agent and skill preset requirements
  with `agentPreset.resolveForWorkflow` and `skillPreset.resolveForWorkflow`
  when setting up or auditing workflow roles.
- Treat `.codex/config.toml` and `.codex/agents/*.toml` as the host-local
  materialization of the StackOS agent presets.
- Treat `stackos:stackos` as the managed host skill for MCP mechanics. Do not
  edit managed skill mirrors by hand.

## Calibration

Before execution, create a compact delivery ledger:

- operator request and constraints
- changed surfaces and likely blast radius
- user/data/system/business flows in scope, or why they are unchanged
- dirty worktree notes
- workflow-backed versus direct tracker rationale
- lifecycle depth: micro, standard, high-risk, or blocked
- selected agents/reviewers and skipped ceremony with reasons
- smallest convincing automated, runtime, E2E/manual, or review proof

Use micro depth for wording, docs, metadata, or preset guidance when risk is
low and the thread already contains enough context. Use high-risk depth for
auth, permissions, provider execution, generated schemas, runtime contracts,
migrations, UI flows, production data, or release-critical behavior. Do not run
ceremony only because a workflow mentions it, and do not skip evidence because
speed is tempting.

## Scope Lock And Feedback Gate

The agreed plan is the delivery contract. The main orchestrator is the sole gatekeeper for all feedback:

- Admit only work required by the agreed outcome or the correctness/safety of a changed surface.
- Defer or reject nice-to-haves, adjacent cleanup, speculative risks, and audit suggestions.
- Ask the operator before materially expanding the outcome; never hide drift inside a "fix."

Reviewers and audits provide signal, not backlog authority.

## Flow Design

Every non-micro delivery needs an explicit flow design before implementation.
For micro work, record why the flows are unchanged or not applicable.

The flow design must cover the relevant:

- user flows
- data flows
- system flows
- business-rule flows
- pre-change behavior or code path for changed flows
- intended change
- current expected behavior after delivery
- why the new behavior is safe and consistent

## Workflow Truth

- If the operator explicitly asks to use a workflow, engineering workflow,
  StackOS workflow, or "the workflow", create or resolve the workflow-backed
  run plan before creating tracker tickets.
- Keep StackOS tasks, tickets, and run plans as the durable delivery state.
- For workflow-backed child tickets, use tracker operations for child-ticket
  evidence and run-plan operations for generated workflow step mirror tickets.
- Dependency-bridge child tickets into the workflow spine. Check parent step
  ticket, first executable child, terminal children, next-step handoff,
  detached branches, and any delivery/test/docs branch that bypasses the
  spine.
- Block final done claims until tracker state, run-plan state, diff,
  verification, and docs/signoff agree.

## Test Design Gate

The test-design step owns the full proof plan. Before implementation starts,
verify or explicitly waive the test design.

The test design must cover:

- each accepted requirement mapped to automated or manual proof
- every changed or at-risk user/data/system/business flow mapped to automated,
  E2E, or manual proof
- red-first/TDD slice when behavior, scoping, contract, or integration risk
  requires it
- exact commands or suites when known
- E2E/manual proof depth chosen from risk: no manual step, narrow smoke, full
  manual signoff, browser/user walkthrough, provider live check, migration
  rehearsal, or operator-owned release gate
- manual scenario preconditions, exact steps, expected outcomes, evidence
  links, screenshots, or logs, and closeout-blocking versus residual/operator-owned status
- for browser-assisted platform flows that depend on login state: stable
  StackOS browser `profile_key`, operator-login precondition, cookie/session
  reuse expectation, and whether it is satisfied, blocked, closeout-blocking,
  operator-owned, or intentionally fresh-profile
- docs, tracker, release, migration, provider, UI, permission, and adapter
  checks when those surfaces are in scope

E2E/manual flow scenarios are agent-executed and closeout-blocking by default.
Mark a scenario operator-owned, residual, skipped, or not applicable only with
an explicit reason.

Quality beats speed. A quick smoke test is not acceptable when production risk,
user/business impact, or security/contract exposure requires full signoff or a
production-like rehearsal.

## Flow Proof

During verification, execute the agent-owned E2E/manual scenarios from the test
design before signoff. The evidence should show what was exercised, expected
outcome, actual outcome, screenshots/logs/links when relevant, and whether
the proof passed, failed, blocked, skipped by instruction, or became
operator-owned.

For changed flows, the final evidence should connect:

- what existed before
- what changed
- what exists now
- why the current behavior is safe
- which E2E/manual or automated proof covered it

## Reviewer Adjudication

Reviewer and verifier outputs are advisory claims until verified. Subagents can
drift; the orchestrator is the managing entity and remains responsible for the
operator request, accepted deliverables, actual code, and final decision.

For each material claim, assess it against the task goal, accepted deliverables,
actual diff/code paths, test evidence, and intended outcome before turning it
into work, a release blocker, or final language. Classify the claim as a valid
required fix, already covered, false positive/unsupported, out of scope,
optional improvement, over-engineering risk, residual risk, or operator-owned
gap. Address only the feedback that protects the scoped deliverable, safety, or
correctness; do not expand scope or redesign just because a verifier suggested
a nice-to-have.

Before final closeout for non-micro work, run or explicitly waive independent
verification checks for:

- one-brain ownership
- cleanup/dead-code risk
- architecture/contracts
- business/user/data/system flow regressions

Each independent check should compare pre-change behavior or code path, what
changed, current delivered behavior, and why the result is safe.

Use these statuses:

- PASS/FIXED: verified good, fixed, or passing
- BLOCKER: unresolved blocker, failed required check, or real active defect
- RISK: residual risk, coverage gap, skipped/operator-owned gate, or follow-up
- INFO/REJECTED: context, rejected claim, unsupported reviewer finding, or
  non-blocking note

For valid unresolved issues, include evidence, root cause, impact, and required
fix. Do not promote "might", "could", speculative architecture preference, or
over-engineering concern into a blocker without concrete evidence and
user/business impact.

## Closeout

At ticket closeout, report:

- ended outcome and ticket sequence
- agents/reviewers used, or why none were needed
- adjudicated findings by status
- checks run, blocked, skipped by instruction, or not applicable
- E2E/manual flow proof executed or explicitly waived
- residual risk and deployment readiness

At task closeout, summarize:

- outcome
- calibration and why
- one-brain architecture
- pattern adherence and over-engineering check
- self-verification
- architecture/business logic signoff
- flow regression proof: before, change, now, why, and evidence
- docs updated
- targeted E2E/manual proof
- unit/integration proof
- residual risk or deferred scope
