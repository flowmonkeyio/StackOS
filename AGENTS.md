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
| Architecture or execution model | [`docs/architecture.md`](./docs/architecture.md), [`docs/operations.md`](./docs/operations.md), [`docs/agent-operating-model.md`](./docs/agent-operating-model.md) |
| Callable operations or action execution | [`docs/operations.md`](./docs/operations.md), [`docs/action-executor.md`](./docs/action-executor.md), [`docs/extending.md`](./docs/extending.md) |
| Provider auth or credentials | [`docs/auth-providers.md`](./docs/auth-providers.md), [`docs/security.md`](./docs/security.md) |
| Plugins, resources, templates, runs, tasks | [`docs/plugins.md`](./docs/plugins.md), [`docs/workflow-templates.md`](./docs/workflow-templates.md), [`docs/run-plans.md`](./docs/run-plans.md), [`docs/task-tracker.md`](./docs/task-tracker.md) |
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
- Task tracking is project-scoped work state. Workflow run plans mirror into
  tasks/tickets automatically; manual agent work should use `tracker.*`
  operations. The tracker stores lifecycle, dependencies, provenance, and
  verification context; agents still decide the work.
- Agents never receive secrets. They receive safe provider/account refs,
  auth-method keys, status, scopes, diagnostics, and opaque `credential_ref`
  values. `action.run` and `action.execute` resolve credentials inside the
  daemon process.
- Agents should use `toolProfile.resolve` when they need one provider/profile
  execution target. It returns a compact safe tuple and avoids broad auth/profile
  discovery calls when the provider intent is already known.
- Communications are provider-neutral state, provider-neutral delivery
  operations, and explicit provider actions.
  Use `communicationProfile.*`, `communicationSurface.*`,
  `communicationContact.*`, `communicationMembership.*`,
  `communicationTarget.*`, `communicationRoute.*`, and
  `communicationContext.query` for identities, surfaces, contacts, memberships,
  named destinations, handoff routes, and stored history.
  Use `ingressEndpoint.*` for project-level public webhook setup; local tunnel
  providers such as ngrok are configured only under `driver_config`, while
  production uses a deployed HTTPS `public_base_url`.
  Use `communication.send` and `communication.reply` as the normal agent-facing
  delivery path. Agents provide intent-level actor/destination/content/context;
  StackOS resolves the profile, target, provider action, credential, policy,
  capabilities, idempotency, and audit. `communicationTarget.resolve` is a
  read-only planning/debug helper and provider actions through `action.run` are
  lower-level escape hatches for explicitly provider-specific work.
  Direct sends support simple non-workflow requests. Workflow sends should pass
  the run token and require an active step grant for `communication.send` with
  explicit target refs.
  Unsupported rich features or delivery options reject with model-readable
  repair context and no side effect; StackOS must not silently degrade buttons,
  attachments, privacy, threading, or notification semantics.
- Communication ingress follows one-brain processing: provider adapters verify
  signatures/secrets and normalize payloads; shared communication policy owns
  visibility, trigger matching, allowlisted invokers, storage, and request
  creation. Do not add provider-specific decision logic for when a bot should
  answer.
- Communication surfaces must carry intent and safety context when used for
  real work: audience, purpose, agent guidance, data-scope/share boundaries, and
  safe external customer/account/ticket refs. Treat these fields as guidance for
  agents, not as hidden workflow logic or secret storage.
- MCP is an adapter, not the core abstraction. Register callable behavior once
  as a StackOS operation, then expose it through allowed MCP, REST, CLI, and UI
  surfaces from that spec.
- Direct MCP tools are only for generic StackOS primitives. Provider/vendor
  operations must be plugin actions executed through `action.run` for one
  explicit direct action or `action.execute` inside a granted run-plan step,
  with manifest entries, connector tests, grants, and docs updated together.
- Workflow execution writes such as `resource.upsert`, `artifact.create`,
  `learning.create`, `experiment.*`, `decision.record`, and `action.execute`
  require a started run plan, one running step, and an explicit grant snapshot.
- Normal agent sessions are scoped by the repository that launched the
  StackOS bridge. The bridge injects the resolved `project_id`, refuses
  cross-project calls, and blocks project-scoped tools until the workspace is
  bound.
- Agent-facing MCP setup/discovery responses are compact by default. Use
  `response_mode=standard` or `response_mode=verbose` only when full daemon
  payloads are needed. For direct write actions, pass `intent_id` when stable
  retry semantics matter; StackOS can derive a request-scoped idempotency key
  otherwise.
- The UI should render generic StackOS objects: projects, plugins, workflow
  templates, run plans, resources, artifacts, auth status, action calls,
  context, learnings, experiments, decisions, and tracker tasks/tickets.

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

## StackOS MCP

Use the project StackOS MCP server for StackOS operations when it is available
in the Codex tool list:

- Codex MCP name: `stackos`
- Registration command: `bash scripts/register-mcp-codex.sh --force`
- Bridge command: `.venv/bin/python -m stackos mcp-bridge`
- Daemon URL used by the bridge: `http://127.0.0.1:5180/mcp`

The bridge reads the daemon token from local state and must keep it out of
prompts, logs, and tool arguments. After registering or changing this MCP entry,
restart the Codex session so native `mcp__stackos__...` tools are mounted.
Do not use custom JSON-RPC scripts for normal StackOS agent operations once the
native MCP tools are available.
