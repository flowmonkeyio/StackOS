# SDLC Delivery Orchestrator

Source skill preset: `stackos.sdlc.delivery-orchestrator` v0.1.0
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
- dirty worktree notes
- workflow-backed versus direct tracker rationale
- lifecycle depth: micro, standard, high-risk, or blocked
- selected agents/reviewers and skipped ceremony with reasons
- smallest convincing automated, runtime, manual, or review proof

Use micro depth for wording, docs, metadata, or preset guidance when risk is
low and the thread already contains enough context. Use high-risk depth for
auth, permissions, provider execution, generated schemas, runtime contracts,
migrations, UI flows, production data, or release-critical behavior. Do not run
ceremony only because a workflow mentions it, and do not skip evidence because
speed is tempting.

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
- red-first/TDD slice when behavior, scoping, contract, or integration risk
  requires it
- exact commands or suites when known
- manual proof depth chosen from risk: no manual step, narrow smoke, full
  manual signoff, browser/user walkthrough, provider live check, migration
  rehearsal, or operator-owned release gate
- manual scenario preconditions, exact steps, expected outcomes, evidence
  artifacts, and closeout-blocking versus residual/operator-owned status
- docs, tracker, release, migration, provider, UI, permission, and adapter
  checks when those surfaces are in scope

Quality beats speed. A quick smoke test is not acceptable when production risk,
user/business impact, or security/contract exposure requires full signoff or a
production-like rehearsal.

## Reviewer Adjudication

Reviewer outputs are claims until verified. The orchestrator must adjudicate
each material claim before turning it into work, a release blocker, or final
language.

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
- residual risk and deployment readiness

At task closeout, summarize:

- outcome
- calibration and why
- one-brain architecture
- pattern adherence and over-engineering check
- self-verification
- architecture/business logic signoff
- docs updated
- targeted E2E/manual proof
- unit/integration proof
- residual risk or deferred scope
