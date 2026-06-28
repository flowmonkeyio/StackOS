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
| Plugins, resources, templates, runs, tasks, agent presets | [`docs/plugins.md`](./docs/plugins.md), [`docs/workflow-templates.md`](./docs/workflow-templates.md), [`docs/agent-presets.md`](./docs/agent-presets.md), [`docs/run-plans.md`](./docs/run-plans.md), [`docs/task-tracker.md`](./docs/task-tracker.md) |
| Provider contract reviews | [`docs/integration-contracts/AGENTS.md`](./docs/integration-contracts/AGENTS.md), [`docs/integration-contracts/`](./docs/integration-contracts/) |
| UI work | [`docs/ui-design-system.md`](./docs/ui-design-system.md), [`docs/ui-component-inventory.md`](./docs/ui-component-inventory.md) |
| Before-commit/release signoff | [`docs/release-signoff.md`](./docs/release-signoff.md) |

## Core Rules

- The runtime layers are project -> workflow template -> run plan. Projects
  store durable state, templates define reusable setup, and run plans are
  concrete execution instances with scoped grants and audit history.
- Core stays domain-agnostic. Engineering, SEO, media buying, GTM, publishing,
  communications, and utilities belong in plugins through manifests, resources,
  actions, and templates.
- Agents decide strategy. StackOS stores, validates, resolves daemon-held auth,
  executes explicit calls, and records audit. Tools/connectors must not invent
  workflow logic or business decisions.
- In StackOS docs and tool contracts, an agent is the MCP/tool consumer. A host
  may separately provide repository filesystem tools, but StackOS workspace
  binding only scopes project operations and does not grant file access.
- Agents are the primary users of run-plan execution mechanics. Humans and
  scripts bootstrap, inspect, approve, and administer; they should not require
  bespoke workflow UIs for each plugin domain.
- Task tracking is project-scoped work state. Workflow run plans mirror into
  tasks/tickets automatically; manual agent work should use `tracker.*`
  operations. The tracker stores lifecycle, dependencies, provenance, and
  verification context; agents still decide the work.
- Agent presets are generic MCP/tool-consumer role contracts. They must be
  adapted to project rules, stack, references, tracker workflow, and signoff
  before use. Workflow templates may recommend host-side skills such as
  `stackos:stackos` so the main agent knows how to operate StackOS MCP,
  workflows, run plans, tasks, tickets, dependencies, and evidence.
- Project-local Codex SDLC agents live in `.codex/agents/*.toml` and are
  adapted from `plugins/engineering/agent-presets/sdlc.yaml`. Main-agent
  orchestration for `engineering.tracked-delivery` lives in
  `.codex/orchestrator/sdlc-delivery-orchestrator.md` and is adapted from the
  `stackos.sdlc.delivery-orchestrator` skill preset; it is guidance for the
  main agent, not a subagent role.
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
- Normal agent sessions are scoped by the repository/directory identity that
  launched the StackOS bridge. Start with `workspace.startSession`: it creates
  or reuses one daemon-owned project and binding for the current workspace when
  reliable identity exists, then the bridge injects the resolved `project_id`
  into project-scoped calls. Use `workspace.resolve` for read-only diagnostics.
  The workspace-bound project is the source of truth; there is no global active
  project or last-used project fallback in the agent path.
- Workspace identity is directory-first, not Git-first. `--workspace-root` and
  `STACKOS_WORKSPACE_ROOT` are explicit workspace hints; Claude Code can pass a
  real project root through `CLAUDE_PROJECT_DIR`; process cwd is only a
  fallback hint. Git remote/top-level detection is optional enrichment and must
  not be required for non-technical users. Claude Desktop may launch StackOS
  from global app config with no repo context. A cwd or path fingerprint for the
  filesystem root `/` must be treated as missing workspace context, not as a
  project to bind or bootstrap.
- Project identity is user/business-facing metadata. StackOS may derive it from
  explicit workspace metadata, git remote basename, or chosen folder basename
  only when the candidate is reliable. Generic or app-internal names such as
  `Resources`, `Contents`, `MacOS`, `StackOS.app`, or `Project` must cause setup
  to ask for `project_name`, `project_slug`, or a deliberate `workspace_alias`
  instead of creating a bad project. For desktop/global hosts with no cwd/git
  signal, use `workspace.connect` to reuse an existing
  `candidate_workspaces` alias or selected existing project; use
  `workspace.bootstrap` only to create a new named workspace from explicit
  project metadata. Do not create a project separately and assume the current
  agent scope moved; the workspace binding must be created or verified by
  `workspace.bootstrap` or `workspace.connect` in the same setup flow.
  Caller-invented cwd/repo anchors are rejected by the
  bridge. Later display-name fixes use the explicit
  local-admin `project.update` flow and must not move the workspace binding.
- The agent-facing MCP bridge exposes only `workspace.startSession`,
  `workspace.resolve`, `toolbox.describe`, and `toolbox.call` directly. Project
  setup, workflows, run plans, tracker, auth, resources, communications, and
  actions are called through the scoped toolbox. Use `toolbox.describe` with
  exact `tool_names`; do not request broad schemas unless debugging.
- Agent-facing MCP setup/discovery responses are compact by default when the
  operation policy allows it. Use `response_mode=raw`,
  `response_mode=standard`, or `response_mode=verbose` only when full daemon
  payloads are needed. For direct write actions, pass `intent_id` when stable
  retry semantics matter; StackOS can derive a request-scoped idempotency key
  otherwise.
- Response shaping is an agent contract, not cosmetic truncation. New
  operations must declare whether `compact`, `raw`, and `ack` are allowed.
  `compact` must keep the ids, refs, counts, warnings, grant hints, and next
  safe-action fields an agent needs for the next tool call. `ack` is only for
  successful internal writes where a minimal response can still preserve retry
  safety. Errors, validation failures, grant denials, auth/setup diagnostics,
  and provider-side partial failures must always return structured repair
  context, regardless of requested response mode. External action operations
  such as `action.run` and `action.execute` support compact/raw response modes:
  MCP and REST default external provider output to response files, while CLI
  defaults to raw inline output unless an explicit output policy says otherwise.
  Provider side-effect operations such as `communication.send` and
  `communication.reply` remain raw-only until a provider-safe compact contract
  exists; never hide external message ids, file ids, provider request ids,
  idempotency status, partial success state, or retry guidance after a
  side-effect attempt. Idempotency rows must store canonical
  raw responses first, then shape the returned payload per request so a later
  raw replay can recover full details after an earlier compact or ack response.
- The UI should render generic StackOS objects: projects, plugins, workflow
  templates, run plans, resources, artifacts, auth status, action calls,
  context, learnings, experiments, decisions, and tracker tasks/tickets.

## Local Ports

- StackOS daemon and committed UI bundle: `http://127.0.0.1:5180/`
- Vue/Vite dev UI: `http://127.0.0.1:5173/`, proxying `/api` and `/mcp` to
  `http://127.0.0.1:5180`

Do not assume another live localhost port belongs to this project. For example,
`3030` is commonly used by other local apps and is not the StackOS UI.

## Install Lifecycle Rules

Install, repair, upgrade, restart, uninstall, and desktop packaging are one
product lifecycle. When changing any of them, do not stop at a unit test or a
fresh install happy path. Verify the lifecycle state machine end to end:

- install/repair writes or refreshes launchd state, starts the daemon, reaches
  `/api/v1/health`, and then runs doctor;
- restart handles loaded launchd jobs, missing launchd jobs, stale pid files,
  zombie/defunct daemon children, non-StackOS port blockers, and wedged live
  daemons without leaving launchd booted out;
- upgrade preserves `~/.local/share/stackos/stackos.db`, state, skills,
  plugins, MCP registrations, and existing auth references unless an explicit
  migration says otherwise;
- uninstall removes app/autostart/runtime wiring but preserves the project
  database by default;
- repair messages must be operator-readable and actionable. Do not surface raw
  JSON as the primary desktop failure experience.
- Host MCP registration is one lifecycle surface owned by
  `stackos.host_mcp`: Codex CLI, Claude Code, Claude Desktop, and Gemini CLI
  should use the shared service/adapters, command matcher, CLI discovery, and
  result contract. Do not add host-specific install logic that bypasses this
  layer.
- Saved host MCP commands must run the local stdio `mcp-bridge`, include the
  host-specific `--runtime`, and preserve non-default StackOS host/port,
  data-dir, and state-dir context. Stale registrations that point at an old
  package/app path must be repaired rather than reported healthy.
- Desktop package replacement is part of upgrade. Same-version or same-payload
  app moves must still refresh launchd and host MCP registrations when the
  packaged command path changes.
- Optional agent hosts are advisory when absent or unsupported. They should not
  block install/repair unless StackOS owns an unsafe/stale entry that requires
  repair. Claude Desktop config writes require restarting Claude Desktop before
  that app can see the updated MCP server.

Before signing off desktop or install changes, run the focused daemon CLI tests,
the desktop doctor, a packaged install/repair smoke, and a manual app restart
from the installed `/Applications/StackOS.app`. If a lifecycle bug is found,
add a regression that recreates the broken state, not just the expected final
state.

## Change Checklist

When changing an execution or tool flow, update these together:

1. data model and repository invariant
2. operation spec, schemas, surface policy, examples, and agent guidance
   including response policy, compact fields, raw-only side effects, and ack
   safety
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

Serena is a repo-singleton Streamable HTTP MCP server on `127.0.0.1:9133`, reached through `.mcp/serena-mcp-wrapper.py` from `.codex/config.toml` and `.mcp.json`. The wrapper is stdio-facing for MCP clients, starts the repo's Serena HTTP process when a client connects, shares that process across local clients/subagents, and stops it after 3600 seconds with no active requests. Do not replace this with direct HTTP-only MCP config; Serena itself will not auto-start without the wrapper.

- Codex MCP name: `serena`
- Project selection: fixed to `/Users/sergeyrura/Bin/content-stack` by the wrapper env `SERENA_PROJECT`
- Lifecycle commands: `python3 .mcp/serena-mcp-wrapper.py --status`, `python3 .mcp/serena-mcp-wrapper.py --ensure`, `python3 .mcp/serena-mcp-wrapper.py --stop`

On a fresh agent session, the first Serena interaction should be `initial_instructions` once if the MCP client did not already surface it. This confirms the active project, context, modes, languages, and operational tool surface for the session.

Never call `activate_project`; there is no global active project to switch.

Serena is operational tooling only. Use it for code navigation, symbol lookup, references, diagnostics, and precise symbol edits; do not use it for project knowledge, notes, onboarding, task tracking, or management. `.serena/project.yml` activates `no-memories` and explicitly excludes Serena's memory/onboarding tools.

Use `get_symbols_overview`, `find_symbol`, and `find_referencing_symbols` for navigation instead of reading whole files. The first cross-reference query after startup may cold-start the language server.

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
