# StackOS Agent Notes

StackOS is a project-scoped tool and plugin runtime. It stores configuration,
context, workflow templates, run plans, resources, artifacts, auth references,
and audit records. The agent decides what to do; StackOS persists the setup and
executes explicit tool calls.

## Product Boundaries

- **Core**: projects, plugins, capabilities, providers, auth provider refs,
  resources, artifacts, workflow templates, run plans, context, learnings,
  experiments, decisions, action calls, and audit runs.
- **Plugins**: domain packages such as SEO, media buying, GTM, and shared utils.
  Domain-specific resources and actions belong in plugin manifests and plugin
  workflow templates.
- **Tools**: static callable operations. Tools validate input, resolve auth
  server-side, call local or external systems, and return structured output.
  Tools must not decide strategy or invent workflow logic.
- **Agents**: plan, choose templates, create run plans, request context, select
  tools, interpret results, and record learnings.

## Execution Model

The runtime layers are:

1. **Project**: durable workspace, plugin enablement, credentials, resources,
   artifacts, history, learnings, experiments, and decisions.
2. **Workflow template**: reusable setup for a class of work. A template may
   include instructions, inputs, context requirements, policy boundaries,
   approval gates, tool/action requirements, expected outputs, and default run
   plan steps.
3. **Run plan**: a concrete execution instance created from a template or by an
   agent. It has ordered steps, scoped tool grants, status, output, and audit
   history.

Context retrieval is filtered. Templates and run plans should ask for the
minimum useful history, for example recent runs with selected fields, active
experiments for the same domain, accepted learnings by tag, and relevant
artifacts by resource key.

## Auth Boundary

Agents never receive secrets. Each provider owns its auth type and credential
storage:

- API keys, OAuth tokens, and account metadata are stored server-side.
- Agents receive provider/account ids, status, scopes, and safe diagnostics.
- `action.execute` and provider-specific tools resolve credentials inside the
  daemon process.
- Credential usage events should be recorded for auditability.

## MCP Surface

The agent-visible MCP surface should stay small and generic:

- bootstrap/setup: workspace/project selection and project config
- plugin/catalog/capability/provider/resource/artifact discovery
- safe auth status/test by reference
- workflow template list/describe/validate
- run plan create/validate/start/get/list
- context/query/timeline and learning/experiment/decision reads
- action describe/validate

Bootstrap/setup calls may exist before a run token so an agent can create a
project and start the first run plan. Workflow execution writes are different:
`resource.upsert`, `artifact.create`, `learning.create`, `experiment.*`,
`decision.record`, and `action.execute` require a started run plan, one running
step, and an explicit grant in the run-plan snapshot.

Hidden vendor operations stay behind grants and action execution. Do not expose
vendor-specific write operations directly to normal agents unless the operation
is intentionally part of the generic StackOS contract.

## UI Direction

The UI should render generic StackOS objects rather than one bespoke screen per
workflow:

- project dashboard
- plugin catalog and enabled plugins
- workflow template list/detail
- run plan list/detail with generic step rendering
- resource and artifact browsers
- auth providers and credential status
- action call history
- context, learnings, experiments, and decisions

SEO remains a first-party plugin domain, not the core product shape.

## Change Checklist

When changing an execution or tool flow, update these together:

1. data model and repository invariant
2. MCP tool schema and bridge visibility
3. permission grant and no-secret auth boundary
4. plugin manifest or workflow template metadata
5. generic UI rendering path
6. tests for direct visibility, grants, auth, and run-plan audit records
7. documentation that names the current StackOS model

Do not add support shims for removed flows. If a flow is replaced, delete the old
route, tool registration, docs, tests, and install asset in the same delivery.

## TPF Token Proxy Filter

Prefix shell commands with `TPF_LLM_TOOL=codex tpf` unless the command is one of:
`cd`, `echo`, `cat`, `head`, `tail`, `mkdir`, `rm`, `mv`, `cp`, `chmod`,
`pwd`, `export`, `source`, `set`, `unset`, `alias`, `read`, `printf`,
`test`, `true`, `false`, `which`, `touch`.

For piped commands, put the pipe in `TPF_PIPE`:

```bash
TPF_PIPE='head -20' TPF_LLM_TOOL=codex tpf git log --oneline
```

Do not wrap redirections, logical OR, background jobs, or subshells.

## Serena MCP

Use this project's dedicated Serena MCP server:

- Codex MCP name: `serena-content-stack`
- URL: `http://localhost:9123/mcp`
- launchd label: `com.oraios.serena-mcp.content-stack`
- project: `/Users/sergeyrura/Bin/content-stack`
- log: `~/Library/Logs/serena-mcp-content-stack.log`

Do not call `activate_project` on the shared/global `serena` MCP. Do not write,
rename, edit, or delete Serena memories unless the user explicitly asks.
