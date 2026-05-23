# StackOS Agent Notes

StackOS is a project-scoped tool and plugin runtime. It stores configuration,
context, workflow templates, run plans, resources, artifacts, auth references,
and audit records. The agent decides what to do; StackOS persists the setup and
executes explicit tool calls.

## Read First

Use [`docs/README.md`](./docs/README.md) as the documentation router. For common
work, start here:

| Work | Read |
| --- | --- |
| Setup, local start, autostart, or repair | [`docs/setup.md`](./docs/setup.md), [`docs/upgrade.md`](./docs/upgrade.md), [`docs/security.md`](./docs/security.md) |
| Architecture or execution model | [`docs/architecture.md`](./docs/architecture.md), [`docs/operations.md`](./docs/operations.md) |
| Callable operations or action execution | [`docs/operations.md`](./docs/operations.md), [`docs/action-executor.md`](./docs/action-executor.md), [`docs/extending.md`](./docs/extending.md) |
| Provider auth or credentials | [`docs/auth-providers.md`](./docs/auth-providers.md), [`docs/security.md`](./docs/security.md) |
| Plugins, resources, templates, runs | [`docs/plugins.md`](./docs/plugins.md), [`docs/workflow-templates.md`](./docs/workflow-templates.md), [`docs/run-plans.md`](./docs/run-plans.md) |
| Provider contract reviews | [`docs/integration-contracts/AGENTS.md`](./docs/integration-contracts/AGENTS.md), [`docs/integration-contracts/`](./docs/integration-contracts/) |
| UI work | [`docs/ui-design-system.md`](./docs/ui-design-system.md), [`docs/ui-component-inventory.md`](./docs/ui-component-inventory.md) |
| Before-commit/release signoff | [`docs/release-signoff.md`](./docs/release-signoff.md) |

## Core Rules

- The runtime layers are project -> workflow template -> run plan. Projects
  store durable state, templates define reusable setup, and run plans are
  concrete execution instances with scoped grants and audit history.
- Core stays domain-agnostic. SEO, media buying, GTM, publishing, and utilities
  belong in plugins through manifests, resources, actions, and templates.
- Agents decide strategy. StackOS stores, validates, resolves daemon-held auth,
  executes explicit calls, and records audit. Tools/connectors must not invent
  workflow logic or business decisions.
- Agents are the primary users of run-plan execution mechanics. Humans and
  scripts bootstrap, inspect, approve, and administer; they should not require
  bespoke workflow UIs for each plugin domain.
- Agents never receive secrets. They receive safe provider/account refs,
  auth-method keys, status, scopes, diagnostics, and opaque `credential_ref`
  values. `action.execute` resolves credentials inside the daemon process.
- MCP is an adapter, not the core abstraction. Register callable behavior once
  as a StackOS operation, then expose it through allowed MCP, REST, CLI, and UI
  surfaces from that spec.
- Direct MCP tools are only for generic StackOS primitives. Provider/vendor
  operations must be plugin actions executed through `action.execute`, with
  manifest entries, connector tests, grants, and docs updated together.
- Workflow execution writes such as `resource.upsert`, `artifact.create`,
  `learning.create`, `experiment.*`, `decision.record`, and `action.execute`
  require a started run plan, one running step, and an explicit grant snapshot.
- The UI should render generic StackOS objects: projects, plugins, workflow
  templates, run plans, resources, artifacts, auth status, action calls,
  context, learnings, experiments, and decisions.

## Local Ports

- StackOS daemon and committed UI bundle: `http://127.0.0.1:5180/`
- Vue/Vite dev UI: `http://127.0.0.1:5173/`, proxying `/api` and `/mcp` to
  `http://127.0.0.1:5180`

Do not assume another live localhost port belongs to this project. For example,
`3030` is commonly used by other local apps and is not the StackOS UI.

## Change Checklist

When changing an execution or tool flow, update these together:

1. data model and repository invariant
2. operation spec, schemas, surface policy, examples, and agent guidance
3. MCP/REST/CLI adapter visibility generated from the operation registry
4. permission grant and no-secret auth boundary
5. plugin manifest or workflow template metadata
6. generic UI rendering path
7. tests for direct visibility, grants, auth, and run-plan audit records
8. documentation that names the current StackOS model

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
