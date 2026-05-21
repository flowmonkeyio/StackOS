# StackOS Deliverable Task Plan

## Purpose

This document turns the StackOS vision and gap analysis into dependency-aware
deliverable tasks. It is intentionally stricter than a normal roadmap: each
task must be independently verifiable, signed off, and committed before the next
task that depends on it is delivered.

The guiding boundary remains:

```text
Agent / human:
  decides, interprets, chooses strategy, writes decisions/learnings

StackOS:
  stores, retrieves, schema-checks, enforces auth/grants/budgets,
  executes external actions, redacts, persists, and audits
```

## Delivery Rules

- One deliverable equals one coherent commit after signoff.
- No deliverable should mix unrelated architecture layers.
- Compatibility for current SEO flows must be preserved unless a task
  explicitly says otherwise.
- Clean cut means additive schema and surface changes. Do not write migrations
  that drop old SEO/procedure/content-stack tables during this pivot. Legacy
  tables stay in place until a separately signed-off destructive cleanup task.
- New domain-specific behavior belongs in plugin manifests/resources/actions,
  not StackOS core.
- Workflow UI must be generic. Do not create bespoke UI per workflow.
- Agents never receive secrets.
- Any task touching auth, actions, context, logs, or audit must include
  redaction tests.
- Every task must update docs if the behavior or architecture changes.

Clean-cut migration rule:

- Add new StackOS tables/primitives as sidecars.
- Disable, hide, wrap, or stop routing legacy surfaces in code/UI when a task
  scopes that behavior.
- Do not physically drop previous tables or erase historical state in a pivot
  migration. Any destructive database cleanup requires its own explicit ticket,
  verification plan, backup/restore path, and user signoff.
- New migrations in this pivot may create new StackOS-owned sidecar tables and
  indexes. They must not alter legacy tables just to remove old behavior, and
  they must not drop any table that predates the deliverable being implemented.

## Verification Status

This plan has been independently checked against the pivot docs. The first
verification pass found three blockers that this version resolves:

- Mutating resource/context/template/action tools must not become normal
  agent-facing tools before D09 run-plan grants exist.
- The dependency graph must match the actual task dependencies.
- Capability/provider discovery, auth refresh/redaction, risky write denial,
  and migration/rollback concerns must be explicit in the tasks that need them.

## Dependency Graph

```text
D00 Vision lock and governance
  -> D01 Repo architecture and AGENTS alignment
  -> D02 Core plugin/catalog skeleton

D02 -> D03 Generic resource and artifact primitives
D02 -> D04 Generic auth provider boundary
D02 -> D05 Project context, learnings, experiments, decisions
D05 -> D06 Workflow template schema and loaders
D05 + D06 -> D07 Run plans, run-plan steps, approvals
D03 + D04 + D07 -> D08 Internal action executor
D03 + D04 + D05 + D07 + D08 -> D09 Generic MCP bridge and permission model
D03 + D04 + D08 + D09 -> D10 Exposed action execution and first utility action
D02 + D03 + D04 + D05 + D06 + D07 + D09 -> D11 Generic UI shell and renderers
D02 + D03 + D06 + D11 -> D12 SEO plugin facade
D06 + D07 + D12 -> D13 SEO workflow migration and procedure compatibility
D02 + D03 + D04 + D10 + D12 -> D14 Utils plugin extraction
D02 + D03 + D04 + D06 + D07 + D09 + D10 + D11 + D14 -> D15 Media buying MVP plugin
D02 + D04 + D06 + D07 + D09 + D10 + D12 + D13 -> D16 Jobs and schedules plugin ownership
D01 + D02 + D10 + D11 + D12 + D13 + D14 + D15 + D16 -> D17 Naming, install, docs, and compatibility cleanup
```

Some implementation can happen in parallel after D02, but signoff should still
respect dependency order where one task relies on another task's schema/API.

## Task Template

Each task should be implemented with this checklist:

```text
Task ID:
Goal:
Scope:
Non-goals:
Dependencies:
Affected files/modules:
Data model changes:
API/MCP changes:
UI changes:
Tests:
Docs:
Compatibility:
Migration/rollback concerns:
Risks:
Acceptance criteria:
Verification commands:
Signoff notes:
Commit message:
```

## D00: Vision Lock And Governance

Goal:

Lock the pivot docs and delivery governance before implementation begins.

Scope:

- Keep `docs/stackos-pivot-design-and-delivery-plan.md` as the north-star
  architecture doc.
- Keep `docs/stackos-current-setup-gap-analysis.md` as the current-state and
  cleanup map.
- Keep this task plan as the executable delivery map.
- Run independent verification on the docs before task execution.

Non-goals:

- No runtime code changes.
- No UI changes.
- No package rename.

Dependencies:

- None.

Affected files/modules:

- `docs/stackos-pivot-design-and-delivery-plan.md`
- `docs/stackos-current-setup-gap-analysis.md`
- `docs/stackos-deliverable-task-plan.md`

Data model changes:

- None.

API/MCP changes:

- None.

UI changes:

- None.

Tests:

- Docs-only sanity checks.

Docs:

- These three StackOS docs must agree on boundaries, terminology, and delivery
  order.

Compatibility:

- Existing product unchanged.

Migration/rollback concerns:

- Docs-only. Rollback is reverting the docs before implementation starts.

Risks:

- Architecture drift if implementation starts before docs are verified.

Acceptance criteria:

- Docs explicitly state StackOS is a tool runtime, not a decision engine.
- Docs explicitly state agents/humans make business decisions.
- Docs explicitly state secrets are never exposed to agents.
- Docs explicitly state procedures are legacy/compatibility.
- Docs explicitly state UI is generic-template/run/resource rendering by
  default.
- Independent verification findings are resolved or explicitly deferred.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf rg -n "tool runtime|decision engine|procedure|Workflow Template|Run Plan|Cleanup Register|Delivery Governance" docs
```

Signoff notes:

- Signoff means the user agrees the docs reflect the vision.

Commit message:

```text
docs: lock StackOS pivot governance
```

## D01: Repo Architecture And AGENTS Alignment

Goal:

Make the repository instructions and file ownership reflect StackOS before
large implementation begins.

Scope:

- Add or rewrite root `AGENTS.md` for StackOS.
- Add plugin-local `AGENTS.md` placeholders only where useful.
- Document intended module boundaries for core, compatibility, plugins, UI
  renderers, and tests.
- Add repository-level architecture guardrails in docs.

Non-goals:

- No behavior changes.
- No physical relocation of SEO code yet.
- No CLI/package rename.

Dependencies:

- D00.

Affected files/modules:

- `AGENTS.md`
- `plugins/seo/AGENTS.md` if the directory exists in this task
- `plugins/utils/AGENTS.md` if the directory exists in this task
- `plugins/media-buying/AGENTS.md` if the directory exists in this task
- `docs/architecture.md`
- StackOS docs as needed

Data model changes:

- None.

API/MCP changes:

- None.

UI changes:

- None.

Tests:

- Add a lightweight docs/instructions test if practical:
  - root `AGENTS.md` mentions no-secrets-to-agent
  - root `AGENTS.md` mentions StackOS as tool runtime
  - root `AGENTS.md` mentions generic UI renderers

Docs:

- Update architecture docs to stop presenting SEO as the generic product
  direction.

Compatibility:

- Existing commands and flows unchanged.

Migration/rollback concerns:

- Docs/instructions only. Rollback is restoring the previous repo guidance; no
  runtime or schema migration is involved.

Risks:

- Too much instruction sprawl. Keep root instructions authoritative and
  plugin-local instructions short.

Acceptance criteria:

- Agents working in the repo see StackOS boundaries before touching code.
- SEO is described as plugin/compatibility ownership, not core ownership.
- UI guidance says workflow UI is generic by default.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf rg -n "StackOS|tool runtime|no secrets|generic UI|plugin" AGENTS.md docs plugins
```

Signoff notes:

- User confirms the instructions match the operating model.

Commit message:

```text
docs: align repo instructions with StackOS
```

## D02: Core Plugin And Catalog Skeleton

Goal:

Introduce first-class StackOS plugin/catalog primitives without breaking
current SEO flows.

Scope:

- Add core tables for plugins, project plugins, capabilities, providers,
  actions, action versions, resources, and artifacts if not split into D03.
- Add plugin manifest schema and parser.
- Register built-in `core`, `seo`, and `utils` manifests.
- Add `plugin.list`, `plugin.enable`, `plugin.disable`.
- Add `catalog.list` and `catalog.describe`.
- Add `capability.list`, `capability.describe`, `provider.list`, and
  `provider.describe`.
- Add read-only REST endpoints for UI catalog views.
- Keep current tools and routes intact.

Non-goals:

- No generic action execution yet.
- No auth refactor yet.
- No physical SEO code move yet.

Dependencies:

- D01.

Affected files/modules:

- `content_stack/db/models.py` or new `content_stack/db/models_core.py`
- new Alembic migration
- `content_stack/plugins/*`
- `content_stack/mcp/tools/plugins.py`
- `content_stack/mcp/tools/catalog.py`
- `content_stack/mcp/tools/__init__.py`
- REST router under `content_stack/api`
- tests under `tests/unit`, `tests/integration/test_mcp`, and
  `tests/integration/test_routes`

Data model changes:

- `plugins`
- `project_plugins`
- `capabilities`
- `providers`
- `actions`
- `action_versions`
- `resources`
- `artifacts` if not deferred to D03

API/MCP changes:

- Add:
  - `plugin.list`
  - `plugin.enable`
  - `plugin.disable`
  - `catalog.list`
  - `catalog.describe`
  - `capability.list`
  - `capability.describe`
  - `provider.list`
  - `provider.describe`

UI changes:

- Optional minimal placeholder route for Plugins/Capabilities, or defer full UI
  to D11.

Tests:

- Plugin manifest parser tests.
- Built-in plugin registration tests.
- Project enable/disable tests through local-admin/project-owner paths.
- MCP list/describe tests.
- Capability/provider discovery tests.
- Bridge/discovery tests proving `plugin.enable` and `plugin.disable` are not
  advertised as normal agent-facing tools before D09.
- Existing SEO MCP/tools still work.

Docs:

- Add plugin manifest docs.
- Update gap analysis if schema differs.

Compatibility:

- Current `procedure.*` and SEO tools remain available.

Migration/rollback concerns:

- This task adds catalog metadata before moving behavior. Rollback means
  disabling the new plugin/catalog registrations while leaving existing SEO
  tables, routes, and tools untouched.

Risks:

- Registry design can become too abstract. Keep manifest fields minimal and
  driven by current SEO plus utils.

Acceptance criteria:

- StackOS can list installed plugins.
- Project can enable/disable plugins through a local-admin/project-owner path.
- Normal agents can discover plugins, catalog entries, capabilities, and
  providers before D09, but cannot mutate project plugin state.
- SEO is represented as a plugin while current flows still run.
- Catalog can describe built-in plugins, capabilities, and providers.

Agent exposure before D09:

- `plugin.list`, `catalog.list`, `catalog.describe`, `capability.list`,
  `capability.describe`, `provider.list`, and `provider.describe` may be
  direct/discovery tools when responses are sanitized.
- `plugin.enable` and `plugin.disable` are local-admin/project-owner setup
  operations. They must not be advertised as normal agent-facing tools before
  D09 or an explicit admin grant model.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/unit tests/integration/test_mcp tests/integration/test_routes -q
```

Signoff notes:

- User confirms the plugin/catalog shape is the right foundation.

Commit message:

```text
stackos: add plugin catalog skeleton
```

## D03: Generic Resource And Artifact Primitives

Goal:

Add generic storage/retrieval primitives for plugin-defined resources and
artifacts.

Scope:

- Add generic resource schemas and records if not fully done in D02.
- Add artifact metadata and storage references.
- Add `resource.get`, `resource.query`, `resource.upsert`.
- Add `artifact.create`, `artifact.get`, `artifact.query`.
- Map existing generated assets into artifact-compatible metadata.
- Keep article assets/research sources as SEO compatibility surfaces.

Non-goals:

- No media buying resources yet beyond manifest examples.
- No replacement of typed SEO tables.
- No action executor yet.

Dependencies:

- D02.

Affected files/modules:

- `content_stack/resources/*`
- `content_stack/artifacts/*`
- `content_stack/mcp/tools/resources.py`
- `content_stack/mcp/tools/artifacts.py`
- REST router if UI needs generic explorer
- migrations
- tests

Data model changes:

- `resources`
- `resource_records`
- `artifacts`

API/MCP changes:

- Add:
  - `resource.get`
  - `resource.query`
  - `resource.upsert`
  - `artifact.create`
  - `artifact.get`
  - `artifact.query`
- Before D09, mutating tools are implemented as internal handlers,
  admin/local APIs, or compatibility-only calls. They must not be advertised as
  normal agent-facing tools.

UI changes:

- Defer full Resource Explorer to D11 unless a minimal diagnostic view is
  useful.

Tests:

- Resource CRUD/query tests.
- Artifact create/query tests.
- Redaction tests for artifact metadata.
- Bridge/discovery tests proving `resource.upsert` and `artifact.create` are
  not advertised as normal agent-facing tools before D09.
- SEO article asset compatibility is not broken.

Docs:

- Resource and artifact manifest docs.

Compatibility:

- Existing `asset.*` and `source.*` remain compatibility/SEO tools.

Migration/rollback concerns:

- Add generic records beside typed SEO storage. Rollback keeps typed SEO tables
  authoritative and disables generic record writes for new plugin resources.

Risks:

- Generic records can become a dumping ground. Require plugin/resource schema
  ids and provenance.

Acceptance criteria:

- Plugins can register resource types.
- StackOS can query/upsert generic resource records through internal or
  compatibility paths.
- Agents can use only safe read/discovery paths until D09 exposes gated
  mutation.
- Artifacts can be created and queried without SEO coupling.

Agent exposure before D09:

- `resource.get`, `resource.query`, `artifact.get`, and `artifact.query` may be
  exposed only when responses are sanitized and bounded.
- `resource.upsert` and `artifact.create` stay internal, local-admin, or
  existing compatibility-gated until D09 run-plan grants are available.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_repositories tests/integration/test_mcp -q
```

Signoff notes:

- User confirms generic resource/artifact shape is sufficient for SEO and media
  buying.

Commit message:

```text
stackos: add generic resources and artifacts
```

## D04: Generic Auth Provider Boundary

Goal:

Replace agent-facing integration setup with no-secret auth provider flows.

Scope:

- Add provider auth schema and credential refs.
- Add `auth.status` and `auth.test` as sanitized read/test operations.
- Add `auth.start` and `auth.revoke` as local-admin/human setup operations.
- Keep `integration_credentials` as backing storage initially if practical.
- Convert GSC OAuth to generic auth flow or wrap it through the new surface.
- Stop advertising `integration.set` to agents in the bridge.
- Track credential expiry/refresh metadata and record redacted
  usage/refresh events. Generic refresh scheduling lands in D16.
- Add local UI support for setup URLs/status when D11 UI is not ready.

Non-goals:

- No full action executor yet.
- No deletion of old integration routes.

Dependencies:

- D02.

Affected files/modules:

- `content_stack/auth_providers/*`
- `content_stack/repositories/projects.py` or new credential repository
- `content_stack/api/projects.py` auth-related parts
- `content_stack/api/auth*.py`
- `content_stack/mcp/tools/auth.py`
- `content_stack/mcp/bridge.py`
- `content_stack/mcp/permissions.py`
- tests

Data model changes:

- `auth_providers`
- `credentials`
- `credential_scopes`
- `credential_accounts`
- `oauth_states`
- `credential_usage_events`
- `credential_refresh_events` if existing usage events cannot represent refresh
  attempts cleanly

API/MCP changes:

- Add:
  - `auth.status`
  - `auth.start`
  - `auth.test`
  - `auth.revoke`
- Mark `integration.set` internal/compatibility.
- Before D09, `auth.start` and `auth.revoke` are local-admin/project-owner
  setup operations and must not be advertised as normal agent-facing tools.

UI changes:

- Auth setup/status can remain in existing Integrations tab temporarily.
- Full Auth Connections view lands in D11.

Tests:

- Auth MCP tools return no secret fields.
- OAuth state is checked and consumed.
- Credential refs are opaque.
- Credential refresh paths never return or log raw tokens.
- Auth usage/refresh audit is redacted.
- Bridge/discovery tests proving `auth.start` and `auth.revoke` are not
  advertised as normal agent-facing tools before D09.
- Bridge no longer advertises `integration.set` to agents.
- Existing GSC setup still works through compatibility path.

Docs:

- Auth provider docs.
- Security docs updated from integrations to auth provider boundary.

Compatibility:

- Existing stored credentials remain usable.

Migration/rollback concerns:

- Prefer wrapping existing credential storage before migrating rows. Rollback
  restores the old integration route while preserving new opaque credential
  refs as aliases where possible.

Risks:

- Credential migration can break existing users. Prefer wrapper layer first.

Acceptance criteria:

- Agent can inspect sanitized auth status and run allowed auth tests without
  seeing a secret.
- Human/local admin can initiate and revoke auth setup without exposing secrets
  to agents.
- Human can complete setup through local UI/OAuth.
- Credential refresh can be represented/audited without exposing tokens.
- Old integrations still function.

Agent exposure before D09:

- `auth.status` and `auth.test` may be exposed when responses are sanitized and
  contain no secret-bearing fields.
- `auth.start` and `auth.revoke` require local-admin/project-owner/human setup
  gates. They must not be advertised as normal agent-facing tools before D09 or
  an explicit admin grant model.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_integrations tests/integration/test_mcp tests/unit/test_mcp_bridge.py -q
```

Signoff notes:

- User confirms no-secret boundary is acceptable before adding media buying.

Commit message:

```text
stackos: add auth provider refs
```

## D05: Project Context, Learnings, Experiments, Decisions

Goal:

Add project memory as data-only primitives with bounded retrieval.

Scope:

- Add project event timeline, context snapshots, learnings, experiments,
  experiment observations, decisions, and metric snapshots.
- Add bounded query tools.
- Ensure tools do not infer conclusions or promote learnings.

Non-goals:

- No semantic summarizer inside StackOS.
- No automatic experiment winner logic.
- No workflow template dependency yet, beyond schema references.

Dependencies:

- D02.
- D03 is useful but not strictly required if references are opaque.

Affected files/modules:

- `content_stack/context/*`
- new repositories
- `content_stack/mcp/tools/context_tools.py`
- REST router for UI
- migrations
- tests

Data model changes:

- `project_events`
- `context_index_entries`
- `context_snapshots`
- `learnings`
- `experiments`
- `experiment_variants`
- `experiment_observations`
- `decisions`
- `metric_snapshots`

API/MCP changes:

- Add:
  - `context.query`
  - `context.timeline`
  - `learning.query`
  - `learning.create`
  - `learning.update`
  - `experiment.query`
  - `experiment.create`
  - `experiment.recordObservation`
  - `experiment.recordDecision`
  - `decision.record`
  - `decision.query`
- Before D09, write tools are internal, local-admin, or compatibility-only.
  Agent-facing reads must enforce projection, limits, and sanitization.

UI changes:

- Defer full views to D11, or add simple read-only diagnostic views.

Tests:

- Field projection tests.
- Limit/max item tests.
- Provenance tests.
- Redaction tests.
- Tools store/retrieve data but do not decide/promote.
- Bridge/discovery tests proving learning/experiment/decision writes are not
  advertised as normal agent-facing tools before D09.

Docs:

- Project memory/context docs.

Compatibility:

- Existing runs remain queryable as source evidence where possible.

Migration/rollback concerns:

- Add project memory tables as append-only sidecars first. Rollback disables
  new writes while preserving existing run history and typed SEO state.
- No migration may drop existing SEO/procedure/content-stack tables. D05 only
  adds project memory sidecars.

Risks:

- Context query can overload agent window. Enforce limits and projection.

Acceptance criteria:

- Agent can request last N relevant runs with selected fields.
- Agent can query active experiments and accepted learnings.
- Learning, experiment, and decision writes are gated or internal until D09.
- Run can reference/store a context snapshot.
- StackOS never decides which learning is true.

Agent exposure before D09:

- `context.query`, `context.timeline`, `learning.query`,
  `experiment.query`, and `decision.query` may be exposed only with field
  projection, item limits, and redaction.
- `learning.create`, `learning.update`, `experiment.create`,
  `experiment.recordObservation`, `experiment.recordDecision`,
  `decision.record`, and similar writes stay internal, local-admin, or
  compatibility-gated until D09.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_mcp tests/integration/test_repositories -q
```

Signoff notes:

- User confirms project memory is data-only and useful for future workflows.

Commit message:

```text
stackos: add project context primitives
```

## D06: Workflow Template Schema And Loaders

Goal:

Introduce reusable workflow templates from plugin, repo/company, and project
sources.

Scope:

- Add `WorkflowTemplateSpec`.
- Add parser/validator.
- Add plugin template loading.
- Add `.stackos/workflows/*.yaml` repo-local loading.
- Add project/user DB template storage if not deferred.
- Add `workflowTemplate.*` tools.

Non-goals:

- No run execution yet.
- No action execution yet.
- No physical procedure migration yet.

Dependencies:

- D02.
- D05 for context requirement references, though templates can parse before
  context tools are fully wired.

Affected files/modules:

- `content_stack/workflows/template_schema.py`
- `content_stack/workflows/template_loader.py`
- `content_stack/mcp/tools/workflows.py`
- plugin workflow directories
- migrations if DB templates included
- tests

Data model changes:

- `workflow_templates`
- `workflow_template_versions`
- `project_workflow_templates`
- These are additive sidecars only. No legacy procedure/SEO table is removed.

API/MCP changes:

- Add:
  - `workflowTemplate.list`
  - `workflowTemplate.describe`
  - `workflowTemplate.validate`
  - `workflowTemplate.save`
  - `workflowTemplate.fork`
- Before D09, `save` and `fork` are local-admin/project APIs or
  compatibility-only. Normal agents can list, describe, and validate templates
  without mutating state.
- `workflowTemplate.validate` is read-only for template schema checks. It must
  not create rows, start runs, or execute actions.

UI changes:

- Defer renderer to D11.

Tests:

- Template parser tests.
- Precedence tests:
  - repo/company > project/user > plugin > ad hoc
- Invalid policy/context/action references fail validation.
- Templates do not contain secret values.
- Bridge/discovery tests proving template mutation is not broadly advertised
  before D09.

Docs:

- Workflow template authoring docs.

Compatibility:

- Existing procedures remain primary execution path until D07/D12.

Migration/rollback concerns:

- Load plugin/repo templates beside existing procedure definitions first.
  Rollback disables template loading and leaves procedure execution unchanged.
- No migration may drop existing SEO/procedure/content-stack tables. D06 only
  adds workflow template sidecars.

Risks:

- Template schema can become too large. Keep required fields minimal and allow
  extension blocks.

Acceptance criteria:

- Built-in templates can be listed/described.
- Repo-local templates can override plugin templates.
- Agent can validate a template without executing it.
- Template save/fork is guarded until D09 establishes the final mutation
  grants.

Agent exposure before D09:

- `workflowTemplate.list`, `workflowTemplate.describe`, and
  `workflowTemplate.validate` may be direct/discovery tools.
- `workflowTemplate.save` and `workflowTemplate.fork` require local-admin,
  project-owner, or compatibility gates until D09.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/unit tests/integration/test_mcp -q
```

Signoff notes:

- User confirms templates are generic enough and not over-prescriptive.

Commit message:

```text
stackos: add workflow template parser
```

## D07: Run Plans, Run-Plan Steps, Approval Gates

Goal:

Add concrete run plans as the execution object produced from templates by the
agent.

Scope:

- Add `RunPlan` and `RunPlanStep` models/tables.
- Add approval gate records/state.
- Add run plan snapshots linked to `runs`.
- Add selected context snapshot linkage.
- Add `runPlan.*` MCP tools.
- Keep existing `runs`, `run_steps`, and `run_step_calls`.

Non-goals:

- No generic action executor required for initial plan creation.
- No deletion of procedure runner.

Dependencies:

- D05.
- D06.

Affected files/modules:

- `content_stack/workflows/run_plans.py`
- `content_stack/repositories/runs.py`
- `content_stack/mcp/tools/workflows.py`
- migrations
- API router
- tests

Data model changes:

- `run_plans`
- `run_plan_steps`
- `approval_requests`
- links from run plans to existing `runs` if needed; prefer sidecar ownership
  over modifying legacy/current execution tables.

API/MCP changes:

- Add:
  - `runPlan.create`
  - `runPlan.validate`
  - `runPlan.start`
  - `runPlan.get`
  - `runPlan.list`
  - `runPlan.update`
  - `runPlan.claimStep`
  - `runPlan.recordStep`
- `runPlan.update` is admin/human-scoped in D07. The run-plan controller
  token must not be able to approve its own gates.

UI changes:

- Defer full view to D11.

Tests:

- Run plan creation from template.
- Snapshot immutability after start.
- Approval gate state transitions.
- Context snapshot linked.
- Existing `procedure.*` tests still pass.

Docs:

- Run plan docs.

Compatibility:

- Procedure runner remains compatibility execution path.

Migration/rollback concerns:

- Store run-plan data beside existing `runs`/procedure run data.
- D07 migrations must only create D07-owned sidecar tables/indexes. They must
  not drop, rewrite, or add cleanup logic for old SEO/procedure/content-stack
  tables.
- If a downgrade hook is required by Alembic conventions, it may remove only
  D07-owned tables/indexes created by this deliverable. Operational rollback is
  endpoint/tool disablement while old procedure runs continue to use the
  existing runner.

Risks:

- Two execution models can diverge. Add wrappers or shared repository helpers
  early.

Acceptance criteria:

- Agent can create a concrete run plan from a template.
- Plan can be started and stepped through.
- Started plan is auditable even if template changes later.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_mcp tests/integration/test_repositories tests/integration/test_routes -q
```

Signoff notes:

- User confirms run plan is the specific execution object, not another rigid
  procedure.

Commit message:

```text
stackos: add run plans and approval gates
```

## D08: Internal Action Executor

Goal:

Build the internal action manifest/executor/connector foundation without
exposing risky action execution to agents before run-plan grants are ready.

Scope:

- Add action manifest parser.
- Add action validation.
- Add action executor.
- Add connector adapter contract.
- Add redaction/idempotency/cost/audit handling.
- Add internal tests with a fake connector.

Non-goals:

- No full provider migration.
- No media buying plugin yet.
- Do not advertise `action.execute` to agents yet.
- Do not port real provider actions yet unless they remain internal/test-only.

Dependencies:

- D03.
- D04.
- D07 for run-plan-scoped execution, though read-only validation can begin
  earlier.

Affected files/modules:

- `content_stack/actions/*`
- `content_stack/mcp/tools/actions.py`
- existing `content_stack/integrations/*` wrappers
- `content_stack/repositories/runs.py`
- migrations if action call audit needs new columns/tables
- tests

Data model changes:

- Use D02 action tables.
- Add action-call audit fields if existing `run_step_calls` is insufficient.

API/MCP changes:

- Add internal or non-advertised tool/handler support for:
  - `action.describe`
  - `action.validate`
- `action.execute` remains unavailable to normal agents until D09 grants are in
  place and D10 exposes the first real action.

UI changes:

- Defer schema renderer to D11.

Tests:

- Action schema validation.
- Credential ref resolution without secret return.
- Redacted request/response audit.
- Idempotency behavior.
- Fake connector action works through generic executor.
- `action.execute` is not advertised to agents before grant enforcement is in
  place.

Docs:

- Action manifest docs.
- Connector adapter docs.
- `docs/action-executor.md` is the D08 operator/developer reference.

Compatibility:

- Existing hidden vendor tools remain aliases/wrappers during migration.

Migration/rollback concerns:

- Executor code can be disabled without removing existing vendor tools because
  provider operations remain behind current hidden/compatibility paths until
  D10 exposes the first real action.

Risks:

- Generic executor can become opaque. Require action describe/validate and
  audit fields.

Agent exposure before D09:

- `action.describe` and `action.validate` may be direct/discovery.
- `action.execute` must remain internal, test-only, or explicitly gated by the
  existing compatibility grant path until D09.

Acceptance criteria:

- Internal executor can describe, validate, and execute a configured fake action.
- Execution receives secrets internally only.
- Result contains no secrets.
- Audit is redacted and source-backed.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_integrations tests/integration/test_mcp -q
```

Signoff notes:

- User confirms the action model stays tool-level and decision-free.

Commit message:

```text
stackos: add generic action executor
```

## D09: Generic MCP Bridge And Permission Model

Goal:

Move the agent-facing surface from procedure/SEO controls toward StackOS
generic controls.

Scope:

- Update direct bridge surface.
- Add toolbox behavior for generic action/resource/artifact/context calls.
- Add run-plan scoped grants.
- Add setup/admin gates for project plugin enable/disable and auth
  start/revoke. These gates are distinct from run-plan action grants.
- Add context source/field grant checks.
- Keep `procedure.*` as compatibility.

Non-goals:

- No deletion of SEO tools.
- No plugin-specific business logic in permission checks.

Dependencies:

- D02.
- D03.
- D04.
- D05.
- D07.
- D08.

Affected files/modules:

- `content_stack/mcp/bridge.py`
- `content_stack/mcp/permissions.py`
- `content_stack/mcp/server.py` if context binding changes
- tests

Data model changes:

- None beyond prior tasks, unless grant snapshots need persistence.

API/MCP changes:

- Direct/discovery surface includes:
  - workspace/project
  - plugin/catalog/capability/provider
  - auth
  - workflowTemplate list/describe/validate
  - runPlan create/validate/start/get/list
  - run status/control
- Setup/admin-gated surface includes:
  - project plugin enable/disable
  - auth start/revoke
- Step-scoped/gated surface includes:
  - action execution
  - mutating resource/artifact calls
  - context queries beyond safe metadata
  - learning/experiment/decision writes
- `procedure.*` remains compatibility/direct or toolbox depending on migration
  decision.

UI changes:

- None.

Tests:

- Bridge hides domain internals.
- Generic direct tools visible.
- Setup mutations denied without admin/project-owner gate.
- Disabled plugin actions denied.
- Mutating resource/artifact calls are denied without run-plan grants.
- Ungranted context fields denied.
- Learning/experiment/decision writes are denied without appropriate grants.
- Secret-bearing fields denied/redacted.
- Legacy procedure flows still callable.

Docs:

- MCP bridge docs updated.
- Plugin skill entrypoint updated.

Compatibility:

- Existing content-stack plugin may still expose old direct surface until
  `plugins/stackos-agent` is ready. If so, document the transition clearly.

Migration/rollback concerns:

- Keep a compatibility bridge profile while introducing the StackOS bridge
  surface. Rollback switches agents back to the old bridge profile without
  deleting new run-plan or grant records.

Risks:

- Breaking agent path. Keep compatibility bridge mode if needed.

Acceptance criteria:

- New StackOS bridge surface exists.
- Generic grants work.
- SEO operations are no longer treated as core direct tools.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/unit/test_mcp_bridge.py tests/unit/test_mcp_permissions.py tests/integration/test_mcp -q
```

Signoff notes:

- User confirms agent-facing tool surface reflects StackOS.

Commit message:

```text
stackos: update bridge permissions for run plans
```

## D10: Exposed Action Execution And First Utility Action

Goal:

Expose grant-gated `action.execute` and port the first real utility action.

Scope:

- Wire `action.execute` through D09 run-plan/action grants.
- Port one low-risk utility action, preferably `openaiImages.generate`, through
  the generic executor.
- Keep old hidden vendor tool as compatibility alias.
- Prove redacted audit and credential-ref execution with a real connector
  wrapper.

Non-goals:

- No broad provider migration.
- No media buying plugin.
- No active/risky external writes.

Dependencies:

- D03.
- D04.
- D08.
- D09.

Affected files/modules:

- `content_stack/actions/*`
- `content_stack/integrations/openai_images.py`
- `content_stack/mcp/tools/actions.py`
- `content_stack/mcp/permissions.py`
- tests

Data model changes:

- Use existing D02/D08 action and audit tables.

API/MCP changes:

- `action.execute` becomes available only through grant-gated execution.

UI changes:

- Defer schema renderer/action display to D11.

Tests:

- `action.execute` denied without run-plan/action grant.
- `action.execute` allowed with grant.
- Utility action returns sanitized artifact refs.
- Old `openaiImages.generate` compatibility path still works if kept.
- Redacted audit contains no secret fields.

Docs:

- Update action manifest docs with the first real action example.

Compatibility:

- Existing utility vendor tool remains available where currently granted.

Migration/rollback concerns:

- If the generic executor fails, compatibility alias can continue to use the
  old wrapper while the action manifest is fixed.

Risks:

- Accidentally exposing execution without grant. Tests must assert denial.

Acceptance criteria:

- Agent can execute the first real action only inside a granted run-plan step.
- Result contains no secrets.
- Audit is redacted and source-backed.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_integrations tests/integration/test_mcp -q
```

Signoff notes:

- User confirms exposed action execution remains tool-level and gated.

Commit message:

```text
stackos: expose gated utility action execution
```

## D11: Generic UI Shell And Renderers

Goal:

Simplify UI around core StackOS objects and generic plugin contributions.

Scope:

- Introduce core nav plus plugin contribution loading.
- Add generic views:
  - Plugins
  - Capabilities
  - Auth / Connections
  - Workflow Templates
  - Runs
  - Project Data
  - Resource Explorer
- Add generic renderers:
  - `TemplateRenderer`
  - `RunPlanRenderer`
  - `ActionSchemaRenderer`
  - `ResourceViewRenderer`
  - `ContextQueryRenderer`
  - `PluginNavRenderer`
- Implement generic nav contribution loading with core/fixture contributions.
- Do not move SEO nav under plugin contribution in this task; that happens in
  D12.
- Keep existing hard-coded SEO views accessible during migration.
- Update run detail to render run-plan steps, action calls, context snapshots,
  artifacts, observations, decisions, learnings, and linked experiments.
- Ensure every raw JSON panel renders sanitized server payloads only.
- Group context snapshots, learnings, experiments, decisions, artifacts,
  metrics, and timeline under `Project Data` to avoid crowding top-level nav.

Non-goals:

- No bespoke UI per workflow.
- No direct product mutations outside auth setup/human approvals.
- No removal of SEO views until plugin nav is working.

Dependencies:

- D02.
- D03.
- D04.
- D05.
- D06.
- D07.
- D09.

Affected files/modules:

- `ui/src/App.vue`
- `ui/src/router.ts`
- new stores/views/renderers
- `ui/src/read-only-ui.spec.ts`
- backend API type generation path
- API routers for generic endpoints

Data model changes:

- None beyond prior tasks.

API/MCP changes:

- REST endpoints may be added for UI if MCP tools are not used directly by UI.

UI changes:

- This is the main UI simplification deliverable.

Tests:

- Plugin nav contribution tests.
- Renderer fixture tests.
- Run detail tests for generic run-plan steps.
- Resource explorer tests.
- Read-only UI contract.
- API type contract regeneration.

Docs:

- UI design system updated for generic StackOS renderers.
- UI component inventory updated.

Compatibility:

- SEO pages remain reachable through the existing legacy nav or compatibility
  route until D12 moves them under the SEO plugin contribution.

Migration/rollback concerns:

- Keep legacy SEO routes reachable while generic nav/renderers ship. Rollback
  restores the old nav shell and leaves generic API endpoints unused.

Risks:

- UI could become too abstract. Keep resource list/detail views useful and
  scannable.

Acceptance criteria:

- Core UI is useful with no domain plugins enabled.
- Generic plugin nav contribution rendering works with core/fixture
  contributions.
- Workflow templates and run plans render generically.
- Raw JSON panels cannot expose connector payloads or secrets.
- No per-workflow bespoke UI is introduced.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pnpm --dir ui test
TPF_LLM_TOOL=codex tpf pytest tests/unit/test_ui_api_contract.py -q
```

Signoff notes:

- User reviews screenshots/behavior and confirms UI is simpler and aligned.

Commit message:

```text
stackos: add generic UI shell
```

## D12: SEO Plugin Facade

Goal:

Make SEO explicitly plugin-owned while preserving existing SEO functionality.

Scope:

- Add `plugins/seo/plugin.yaml`.
- Map current SEO resources/capabilities/actions/providers.
- Add SEO UI nav contribution.
- Add SEO resource-view manifests where possible.
- Add compatibility aliases for existing tool names.
- Keep current SEO code physically in place if relocation is too risky.

Non-goals:

- No full rewrite of article/topic internals.
- No deletion of direct SEO routes yet.

Dependencies:

- D02.
- D03.
- D06.
- D11.

Affected files/modules:

- `plugins/seo/*`
- `content_stack/plugins/*`
- `content_stack/mcp/tools/*` compatibility registration
- `ui/src/App.vue` or plugin nav loader
- tests

Data model changes:

- Use plugin/catalog tables.

API/MCP changes:

- SEO catalog entries available through `catalog.describe`.
- Existing SEO tools still work as aliases.

UI changes:

- SEO nav comes from plugin contribution.
- Existing SEO views stay reachable.

Tests:

- SEO plugin manifest validates.
- Disabling SEO hides SEO nav/capabilities for the project.
- Existing SEO tests still pass.

Docs:

- SEO plugin docs.
- Migration docs for old SEO surfaces.

Compatibility:

- Existing projects and runs remain readable.

Migration/rollback concerns:

- Start with a facade manifest instead of moving SEO code. Rollback disables
  the SEO plugin facade while the existing SEO imports/routes keep serving the
  old UI and MCP paths.

Risks:

- Splitting ownership can break imports. Start with manifest facade.

Acceptance criteria:

- SEO appears as enabled plugin.
- Current SEO flows still run.
- SEO no longer appears as universal core in UI/catalog.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_mcp tests/integration/test_routes tests/integration/test_repositories -q
TPF_LLM_TOOL=codex tpf pnpm --dir ui test
```

Signoff notes:

- User confirms SEO is now a plugin boundary, not the platform identity.

Commit message:

```text
seo: register legacy surfaces as plugin
```

## D13: SEO Workflow Migration And Procedure Compatibility

Goal:

Convert existing SEO procedures into workflow templates and keep legacy
procedure wrappers.

Scope:

- Create SEO workflow templates for current procedures.
- Map procedure start/resume/fork to workflow/run-plan wrappers where possible.
- Keep `procedure.*` compatibility tests.
- Move root procedure assets toward `plugins/seo/workflows`.

Non-goals:

- No custom UI per SEO workflow.
- No immediate removal of `procedures/`.

Dependencies:

- D06.
- D07.
- D12.

Affected files/modules:

- `procedures/*`
- `plugins/seo/workflows/*`
- `content_stack/procedures/*`
- `content_stack/workflows/*`
- tests

Data model changes:

- Compatibility mapping fields if needed.

API/MCP changes:

- `procedure.*` wrappers call or mirror workflow/run-plan primitives.

UI changes:

- Procedures page is gone/replaced by workflow templates.
- Legacy procedure runs render in generic run detail where possible.

Tests:

- Each legacy procedure has a corresponding workflow template.
- Procedure compatibility wrappers still pass.
- Workflow templates render in UI.

Docs:

- Procedure guide replaced/deprecated by workflow template guide.

Compatibility:

- Existing run history remains readable.

Migration/rollback concerns:

- Legacy procedure entry points remain wrappers. Rollback points wrappers back
  to the old procedure runner and keeps workflow templates as inert metadata.

Risks:

- Procedure semantics such as loop-back/retry may not map one-to-one. Document
  compatibility behavior explicitly.

Acceptance criteria:

- Existing SEO procedure catalog is represented as SEO workflow templates.
- Agents use workflow templates for new runs.
- Legacy procedure entry points remain available.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_procedure_runner tests/integration/test_mcp -q
```

Signoff notes:

- User confirms procedures are no longer the product model.

Commit message:

```text
seo: migrate procedures to workflow templates
```

## D14: Utils Plugin Extraction

Goal:

Make utility providers reusable across SEO, media buying, GTM, and private
plugins.

Scope:

- Add `plugins/utils/plugin.yaml`.
- Register utility capabilities.
- Port OpenAI Images, Firecrawl, Jina, Reddit to utility provider/action
  manifests incrementally.
- Keep old hidden vendor tools as compatibility aliases.

Non-goals:

- No media buying domain objects.
- No SEO workflow rewrite beyond using utility capabilities where practical.

Dependencies:

- D02.
- D03.
- D04.
- D10.
- D12.

Affected files/modules:

- `plugins/utils/*`
- `content_stack/integrations/*`
- `content_stack/actions/*`
- `content_stack/mcp/tools/vendor_ops.py`
- tests

Data model changes:

- Use existing catalog/action tables.

API/MCP changes:

- Utility actions available through `action.*`.

UI changes:

- Utility providers appear in Auth/Connections and Capabilities.

Tests:

- Utility manifest validates.
- Utility actions describe/validate/execute.
- Utility actions are denied without D09/D10 grants.
- Utility action audits are redacted and store sanitized artifact refs only.
- SEO can still use compatibility aliases.

Docs:

- Utils plugin docs.

Compatibility:

- Existing vendor ops remain callable where granted.

Migration/rollback concerns:

- Port utilities incrementally and keep old hidden vendor tools as aliases.
  Rollback disables the utility action manifest for a provider and routes calls
  through the old wrapper.

Risks:

- Vendor wrappers have provider-specific quirks. Keep connector adapters.

Acceptance criteria:

- At least OpenAI Images works as utility action.
- At least one web read/scrape action works as utility action.
- SEO is not required for utility usage.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_integrations tests/integration/test_mcp -q
```

Signoff notes:

- User confirms utilities are domain-neutral.

Commit message:

```text
utils: extract shared provider actions
```

## D15: Media Buying MVP Plugin

Goal:

Prove StackOS supports a non-SEO plugin with provider actions and generic UI.

Scope:

- Add `plugins/media-buying/plugin.yaml`.
- Define media buying resources.
- Add Meta auth provider manifest.
- Add read-only account discovery action.
- Add paused campaign create action.
- Add media campaign launch workflow template.
- Add generic resource-view configs.

Non-goals:

- No active spend by default.
- No automated optimization decisions.
- No broad provider coverage beyond Meta skeleton plus placeholders.

Dependencies:

- D02.
- D03.
- D04.
- D06.
- D07.
- D09.
- D10.
- D11.
- D14.

Affected files/modules:

- `plugins/media-buying/*`
- connector for Meta if not present
- action manifests
- auth provider manifest
- tests

Data model changes:

- Use generic resource/action/auth tables.

API/MCP changes:

- Media buying capabilities/actions visible through catalog/action tools.

UI changes:

- Media Buying nav appears only when plugin enabled.
- Campaigns/Creatives render through generic resource explorer.
- Campaign launch renders through generic workflow/run-plan views.

Tests:

- Plugin manifest validates.
- Auth no-secret tests.
- Action validate/execute with mocked Meta.
- Action execution denied without grants, budgets, auth scopes, and approval
  policy.
- Reject `ACTIVE` or otherwise spendful campaign create payloads unless a
  future task explicitly adds active-spend approval semantics.
- Workflow template validates.
- UI nav/resource renderer tests.

Docs:

- Media buying plugin docs.

Compatibility:

- Existing SEO unaffected.

Migration/rollback concerns:

- Use mocked provider tests and paused-only actions first. Rollback disables
  the media buying plugin manifest and leaves generic core/action/auth tables
  intact.

Risks:

- Paid media writes are high-risk. Default to paused objects and approval gates.

Acceptance criteria:

- Project can enable media buying plugin.
- Agent can discover Meta campaign create schema.
- Agent can execute paused campaign create with credential ref in a mocked test.
- UI renders media resources without custom workflow pages.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_mcp tests/integration/test_integrations -q
TPF_LLM_TOOL=codex tpf pnpm --dir ui test
```

Signoff notes:

- User confirms non-SEO plugin proves the architecture.

Commit message:

```text
media-buying: add Meta campaign MVP
```

## D16: Jobs And Schedules Plugin Ownership

Goal:

Move scheduled/maintenance work out of SEO-shaped core assumptions.

Scope:

- Generalize scheduled jobs to target plugin maintenance, workflow templates,
  run plan seeds, or actions.
- Move GSC pull and drift rollup under SEO plugin schedule ownership.
- Keep existing job ids as compatibility where needed.
- Ensure schedules cannot make strategic choices. Risky write schedules must
  use predeclared workflow templates or run-plan seeds with grants, budgets,
  auth scopes, and approval policy.

Non-goals:

- No full scheduler rewrite if current APScheduler foundation works.

Dependencies:

- D02.
- D04.
- D06.
- D07.
- D09.
- D10.
- D12.
- D13.

Affected files/modules:

- `content_stack/jobs/*`
- `content_stack/server.py`
- `plugins/seo/plugin.yaml`
- schedule repositories/tools
- tests

Data model changes:

- Extend `scheduled_jobs` or add generic schedule target fields.

API/MCP changes:

- Existing `schedule.*` may become plugin-aware.

UI changes:

- Schedule UI becomes generic schedule explorer.

Tests:

- Existing scheduled jobs still register.
- SEO schedules come from plugin manifest/config.
- Generic schedule target validation.
- Deny risky scheduled writes without predeclared run-plan policy, grants,
  budgets, auth scopes, and approval gates.

Docs:

- Schedule/plugin maintenance docs.

Compatibility:

- Existing project schedules keep working.

Migration/rollback concerns:

- Preserve existing job ids while adding plugin ownership metadata. Rollback
  ignores plugin schedule fields and registers the current hard-coded jobs.

Risks:

- Cron behavior can silently change. Preserve job ids and add tests.

Acceptance criteria:

- No SEO-only job is required by StackOS core startup.
- SEO jobs still run when SEO plugin is enabled.
- Schedules cannot execute risky writes without predeclared run-plan policy and
  approval gates.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/integration/test_jobs tests/integration/test_routes -q
```

Signoff notes:

- User confirms scheduling is plugin-aware.

Commit message:

```text
stackos: make schedules plugin-aware
```

## D17: Naming, Install, Docs, And Compatibility Cleanup

Goal:

Make StackOS the product surface while preserving old installs.

Scope:

- Add `stackos` CLI alias.
- Add StackOS agent plugin package.
- Update README and docs.
- Update app title/description.
- Update installer asset handling for plugin-owned assets.
- Keep `content-stack` command/data paths for compatibility.
- Add migration/deprecation docs.

Non-goals:

- No forced data-dir migration unless explicitly designed and tested.
- No removal of compatibility aliases yet.

Dependencies:

- D01.
- D02.
- D10.
- D11.
- D12.
- D13.
- D14.
- D15.
- D16.

Affected files/modules:

- `pyproject.toml`
- `content_stack/cli.py`
- `content_stack/config.py`
- `content_stack/install.py`
- `plugins/content-stack/*`
- `plugins/stackos-agent/*`
- README/docs
- tests

Data model changes:

- None unless migration metadata is added.

API/MCP changes:

- MCP server name/plugin package updated or aliased.

UI changes:

- Product copy and branding updated.

Tests:

- Install tests for old and new plugin names.
- CLI tests for `content-stack` and `stackos`.
- Plugin package tests.
- Docs link sanity where practical.

Docs:

- Migration docs.
- Compatibility policy.

Compatibility:

- Old users keep working.

Migration/rollback concerns:

- Do not migrate data directories by default. Rollback removes/ignores the
  `stackos` alias and keeps `content-stack` package names, commands, and paths
  serving existing installs.

Risks:

- Rename churn. Keep aliases and avoid breaking data paths.

Acceptance criteria:

- New installs present StackOS.
- Existing `content-stack` command and plugin compatibility remain.
- Docs no longer frame the product as SEO-only.

Verification commands:

```bash
TPF_LLM_TOOL=codex tpf pytest tests/unit tests/integration/test_install_scripts -q
```

Signoff notes:

- User confirms product surface has pivoted while compatibility remains.

Commit message:

```text
stackos: add product alias and compatibility docs
```

## Task Plan Self-Verification Checklist

Before implementation starts, verify this task plan against the vision:

- Does each task have one coherent deliverable boundary?
- Are dependencies explicit?
- Does every risky task include tests?
- Does every auth/action/context task include no-secret/redaction verification?
- Does every UI task avoid per-workflow bespoke screens?
- Does every domain-specific task place behavior under plugin ownership?
- Are procedures always legacy/compatibility after the transition begins?
- Does every task preserve existing SEO flows unless explicitly scoped?
- Does every migration preserve old tables/history unless a separately
  signed-off destructive cleanup ticket exists?
- Is each task small enough to commit independently after signoff?

## Open Questions Before D02

- Should the first plugin manifest format be YAML or JSON? YAML is friendlier
  for templates and docs; JSON is stricter for generated tooling.
- Should generic resources use one `resource_records` table immediately, or
  should D02 only register resource schemas while D03 adds storage?
- Should `plugins/content-stack` be renamed to `plugins/stackos-agent` early, or
  should we add the new plugin beside the old one later in D17?
- Should UI consume REST endpoints only, or should any UI route inspect MCP
  catalog metadata generated from the same registry?
- How long should `integration.set` remain callable through compatibility
  paths after `auth.*` exists?
