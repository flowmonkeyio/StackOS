# StackOS Delivery Plan

This plan tracks the clean-cut delivery sequence for aligning the repository
with the StackOS architecture.

## Delivery Rules

- Every task must align with the project -> template -> run plan model.
- Agents own decisions; StackOS stores, retrieves, validates, executes, and
  audits.
- Tools resolve auth in their own process and never expose secrets.
- UI work must use generic renderers unless a domain-specific editor is
  explicitly justified.
- Removed flows must be deleted from code, docs, tests, generated API types,
  install assets, and plugin distributions.
- Every signed-off task is committed with a detailed message.
- Cleanup and signoff agents are used before commit when the blast radius is
  meaningful.

## Task Graph

### D13: Clean-Cut Architecture Alignment

Goal: remove old execution surfaces and make the current repo match StackOS.

Work:

- Delete old execution routes, runners, schedulers, install assets, and tests.
- Remove old table models when they are not part of the new architecture.
- Replace old run kinds with `run-plan` where execution history is needed.
- Regenerate UI API types after the OpenAPI surface is clean.
- Rewrite docs and repo agent notes around StackOS.
- Keep SEO as a plugin domain with workflow templates, resources, and actions.

Verification:

- repository grep for removed-flow vocabulary in active code/tests/docs
- focused Python and UI tests
- MCP direct-surface tests
- schema tests
- plugin manifest tests
- independent cleanup/signoff agent review

### D14: Auth Provider Execution Boundary

Goal: make no-secret tool execution the default for all actions.

Work:

- Ensure providers declare auth type, scopes, and account binding shape.
- Ensure action execution accepts provider/account refs, not secrets.
- Record credential usage events.
- Add tests proving secrets do not appear in MCP responses, UI payloads, run
  plans, action calls, or logs.

Dependencies: D13.

### D15: Generic UI Simplification

Goal: finish the simplified StackOS console.

Work:

- Project overview focused on enabled plugins, recent runs, auth state, and
  active work.
- Generic workflow template detail.
- Generic run-plan detail with step state, outputs, artifacts, and action calls.
- Generic resource and artifact browsers.
- Auth provider status and connect/test/revoke flows.
- Remove per-workflow UI assumptions.

Dependencies: D13, D14 for auth flows.

### D16: Plugin Expansion Pattern

Goal: prove the plugin model beyond SEO.

Work:

- Add a minimal media-buying plugin manifest with providers, resources,
  actions, and workflow templates. Current state: first contract-only scaffold
  is in place; provider connectors remain a separate delivery.
- Add a minimal GTM plugin manifest with providers, resources, actions, and
  workflow templates. Current state: first contract-only scaffold is in place;
  provider connectors remain a separate delivery.
- Keep utility actions available across domains.
- Add catalog and UI tests proving generic renderers handle the new domains.

Dependencies: D13, D15.

### D17: Learning And Experiment Loop

Goal: make repeated work improve without hiding decisions in tools.

Work:

- Record learnings from run outputs and human decisions.
- Query learnings with filters by domain, plugin, resource key, tag, and status.
- Link runs to experiments and observations.
- Render experiments and decisions in the generic UI.
- Add tests for bounded context retrieval.

Dependencies: D13.

## Done Definition

A task is done when:

- implementation, tests, docs, and generated artifacts are aligned
- old-flow references are absent from active surfaces
- no-secret auth behavior is proven where relevant
- a signoff agent has reviewed the task
- the commit message explains the design intent, key changes, and verification
