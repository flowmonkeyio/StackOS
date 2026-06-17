# StackOS Architecture

StackOS is a storage, execution, and audit layer for agent-operated tools. It is
not a strategy engine. Agents make decisions; StackOS provides durable project
state, plugin catalogs, auth boundaries, run plans, and tool execution.

## Core Principles

1. **Agent-owned decisions**: workflow judgment, campaign structure, content
   strategy, vendor selection, and next actions live with the agent.
2. **Static tools**: tools expose configuration, validation, execution, and
   persistence. They do not encode business strategy.
3. **No-secret agents**: credentials stay in the daemon process. Agents pass
   provider and account references.
4. **Generic product surface**: core UI/API/MCP/CLI render templates, run
   plans, resources, artifacts, auth, actions, and operations. Domain UX comes
   from plugin config.
5. **Project memory by filters**: context retrieval is explicit and bounded, so
   agents can learn from history without flooding the prompt.

## Layers

### Project

The project is the top-level operating context. It owns plugin enablement,
credential accounts, resources, artifacts, run history, context snapshots,
learnings, experiments, and decisions.

### Plugin

Plugins package domain behavior as metadata:

- capabilities
- providers
- resources
- actions
- workflow templates
- UI navigation contributions

Engineering, SEO, media buying, GTM, and utilities should all fit this shape.

### Workflow Template

A workflow template is reusable setup for work. It can define:

- goal and domain
- input schema
- instructions and constraints
- context requirements with filters and field selection
- capability/action requirements
- approval gates
- default steps
- expected outputs and quality checks

Templates should be generic enough for agents to adapt in a project while still
giving them a strong starting structure.

### Run Plan

A run plan is a specific execution instance. It has concrete inputs, steps,
statuses, scoped tool grants, outputs, approval state, and audit records. Run
plans can be created from templates or authored directly by an agent.

### Direct Action

A direct action is a single explicit action call that does not need a workflow
template or run plan. In MCP sessions it is executed through `toolbox.call` for
`action.run`; it still resolves credentials inside the daemon, returns raw
redacted provider output for retry-safe agent follow-up, and writes an
`action_calls` audit row.

### Action Call

An action call is one validated tool invocation. It records input, output,
provider, credential reference, status, errors, and cost metadata. External
credentials resolve inside the daemon.

### Operation

An operation is the protocol-neutral callable contract above repositories and
below adapters. It defines input/output schemas, handler, surface policy,
grant policy, examples, and agent-facing guidance once. MCP tools, generic REST
operation calls, and CLI `ops` commands are adapters over the same operation
spec.

## Data Model

Core StackOS tables include:

- `projects`
- `plugins`, `project_plugins`
- `capabilities`, `providers`, `actions`, `action_versions`, `action_calls`
- `auth_providers`, `credential_accounts`, `credentials`,
  `credential_scopes`, `credential_usage_events`, `credential_refresh_events`
- `resources`, `resource_records`
- `artifacts`
- `workflow_templates`, `workflow_template_versions`,
  `project_workflow_templates`
- `run_plans`, `run_plan_steps`
- `runs`, `run_steps`, `run_step_calls`
- `context_snapshots`, `context_index_entries`
- `learnings`, `experiments`, `experiment_variants`,
  `experiment_observations`, `decisions`
- `approval_requests`
- `agent_requests`

Plugin tables may exist for first-party domains when they provide durable
domain resources. Those tables are plugin-owned and should not drive core UI
or MCP design.

## MCP And Permissions

MCP is an adapter, not the core callable abstraction. New callables should be
registered as StackOS operations when they need MCP, REST, or CLI exposure. The
operation registry emits agent-readable docs through `/api/v1/operations` and
`stackos ops describe`, and MCP tool schemas are generated from the same
specs when the MCP surface is enabled.

The agent-facing MCP bridge surface is intentionally small:

- discovery: the current workspace/project, plugin, catalog, capability,
  provider, resources, artifacts, and project memory
- bootstrap/setup: workspace binding, budgets, schedules, safe auth
  status/test, workflow-template discovery, and run-plan creation/start
- direct execution: `action.run` through `toolbox.call` for one explicit action
  with compact response-file paths plus `schema_ref`/`schema_operation` for
  external-provider output by default
- workflow execution: run-plan controller tools, `action.execute`, resource and
  artifact writes, memory writes, and run audit tools
- memory: context, learnings, experiments, decisions

The bridge derives the workspace-bound project from the repository that
launched it. It injects that `project_id` into project-scoped calls and refuses
explicit cross-project calls. It also injects current workspace hints and
refuses calls that try to resolve, bootstrap, or connect another workspace.
Safe project listing/creation and read-only binding diagnostics are available
through `toolbox.call` during intentional setup, so an unbound agent can finish
project bootstrap from MCP alone. Project switching, archiving, and broad admin
mutations remain daemon/admin capabilities; they are not advertised to normal
agent bridge clients.

Bootstrap/setup calls are intentionally available before a run token exists so
an agent can set up the current project and create the first run plan. They are
not a workflow execution path. Project-memory writes (`learning.create`,
`experiment.*`, `decision.record`), generic resource/artifact mutations, and
`action.execute` require a started run plan with a running step and an explicit
tool grant.

Provider operations are modeled as plugin actions. Agents use
`toolbox.call` for `action.describe`, `action.validate`, and `action.run` on
one explicit action, or for step-granted `action.execute` during workflow
steps. Vendor wrappers remain inside daemon-side integrations where auth,
budget checks, retries, and output normalization can be enforced; they are not
registered as provider-specific MCP tools.

## Auth Flow

1. A provider declares an auth type and scopes.
2. A project connects an account through `auth.start` or a local credential
   ingestion path.
3. Tokens are encrypted and stored server-side.
4. Agents query `auth.status` and `auth.test` through `toolbox.call` for safe
   state.
5. Tools resolve credentials by provider/account reference at execution time.
6. Every use writes credential usage metadata.

No API key or OAuth token should appear in prompts, run-plan inputs, action
arguments, UI JSON, or MCP responses.

## UI Architecture

The UI renders StackOS primitives:

- project overview
- plugin catalog
- workflow template detail
- run plan detail
- generic resource record pages
- artifact preview
- provider/auth status
- action-call audit ledger
- context and learning views

Plugin nav points to generic renderers with plugin/resource keys. New domains
should not require bespoke top-level pages unless a specialized editor is
clearly justified.

## Delivery Invariants

- A new domain starts as a plugin manifest plus templates and actions.
- A new external service starts as a provider, auth type, action definitions,
  and a daemon-side wrapper.
- A new callable operation starts as one operation spec with schemas, examples,
  surface policy, grant policy, and agent guidance.
- A new workflow starts as a template and run-plan tests.
- A new UI surface should render generic StackOS data.
- Removed flows must be deleted from routes, MCP registration, bridge exposure,
  tests, docs, install assets, and generated API types.
