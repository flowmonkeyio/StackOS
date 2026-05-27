---
name: stackos
description: Use when working from any website repository to connect the repo to StackOS, resolve the current project, inspect workflow templates/run plans, and use daemon-managed resources/actions without writing setup files into the repo.
---

# StackOS Plugin Entrypoint

Use the current repository as source context and the local StackOS daemon as
durable state. The daemon owns projects, credentials, workflow templates, run
plans, resources, actions, context, learnings, experiments, decisions, and audit
trails.

Use StackOS tools for durable state and execution planning. The direct MCP
surface is intentionally small: call `workspace.startSession` or
`workspace.resolve` first, then use `toolbox.describe` and `toolbox.call` for
project setup, workflow templates, run plans, tracker, resources, context, and
actions.

The MCP bridge intentionally exposes a compact direct tool list. Do not try to
call hidden daemon tools directly. Use `toolbox.describe` with exact
`tool_names` to inspect only the tools needed for the current decision, then
`toolbox.call` to invoke exactly one hidden tool by name. When working inside a
run plan, pass `run_id` so the bridge can refresh step grants and inject the
run token.

## Operating Rules

1. Do not create `.env`, `.mcp.json`, `AGENTS.md`, `CLAUDE.md`, or
   `.stackos/*` in the current repository unless the user explicitly asks
   for checked-in hints.
2. Start with `workspace.startSession` using repo hints supplied by the plugin
   MCP bridge. For a new repo/directory it creates or reuses the local StackOS
   project and daemon-owned binding automatically.
3. Treat the workspace-bound `project_id` as the source of truth. Do not use a
   global active project concept.
4. Use `toolbox.describe`/`toolbox.call` for hidden setup tools and the current
   run-plan step grants that are not in the direct list.
5. Agent presets are contracts, not daemon-run local agents. When asked to set
   up local agents, adapt presets to the host/project's detected format
   (`.codex/agents/*.toml` referenced by `.codex/config.toml`, markdown
   frontmatter, plugin files, or session-only guidance). StackOS does not scan,
   write, or register host-local agent files; the host agent inspects repository
   files and adapts presets only when local-agent setup is requested.
6. When a run plan needs missing vendor credentials, do not ask the user to
   paste secrets into chat. Name the missing providers and send the operator to
   `http://localhost:5180/projects/{project_id}/connections`. After the user
   connects them in the UI, call `toolbox.call` for `auth.status` and
   `auth.test` before continuing.
7. When a step requires a provider call, use `action.describe`,
   `action.validate`, and the step-granted `action.execute` path. The daemon
   resolves credentials inside the action process and returns only sanitized
   output.
8. When the user asks for one explicit action and no workflow state is needed,
   use `toolbox.call` for `action.run` with `confirm_direct=true`,
   `intent_summary`, and an `idempotency_key` for non-read actions. Leave
   `verbose=false` unless the full redacted action payload is needed for
   debugging.

## Common Flows

- First run in a repo: call `workspace.startSession`. The bridge should create
  or reuse one daemon-owned project binding for that workspace and return UI
  links plus setup/profile state. Treat `workspace_bound=true` as "project tools
  are usable"; missing framework/content-model profile fields are adaptation
  hints, not blockers. After inspecting the repo's stack and content model, use
  `toolbox.call` for `workspace.updateProfile` when those hints should be
  durable for future agents.
- Ongoing repo session: call `workspace.startSession`, use the workspace-bound
  project id, then call only the scoped tools needed for the current task. Do
  not request broad schemas or catalog dumps unless debugging.
- Connect to a specific project: if the operator wants a known existing
  project, call `toolbox.call` for `workspace.connect` or
  `workspace.bootstrap` with that project identifier explicitly.
- Set up SDLC/local agents: use the normal workflow path with
  `engineering.tracked-delivery`. Describe the workflow, resolve agents with
  `agentPreset.resolveForWorkflow`, then create/start a run plan when
  executing. Adapt the referenced `stackos.sdlc.*` presets to the
  host/project's local agent format only when local agents are requested. For
  Codex repos, inspect `.codex/config.toml` and existing
  `.codex/agents/*.toml` before proposing file creates or updates.
  Treat each preset's `recommended_tools` as StackOS operation refs. If those
  refs are not mounted as direct host tools, use `toolbox.describe` and
  `toolbox.call`.
- Discover operations: if you do not know the exact operation name, call
  `toolbox.call` for `operation.list` first, then `operation.describe` for the
  few operations you intend to use. Keep `toolbox.describe` scoped to exact
  tool names.
- Connect vendors: inspect the run plan's needed providers, share
  `/projects/{project_id}/connections`, wait for the operator to connect them
  in the UI, then run `toolbox.call` for `auth.status` and `auth.test`.
- Plan direct work: use tracker tasks/tickets when the agent is planning or
  delivering scoped work outside a concrete workflow run. Create dependencies,
  blockers, definition of done, and completion evidence there.
- Execute workflow work: use a workflow template when work should follow a
  reusable contract. `runPlan.create` turns the template into concrete state;
  `runPlan.start` and step grants control which tools/actions are available.
  Mirror or link tracker tickets when human-visible sequencing/evidence matters.
- Execute a step: claim the run-plan step, follow the referenced guidance, call
  `toolbox.describe` for needed granted tools, invoke them with `toolbox.call`,
  then `runPlan.recordStep`.
- Execute one direct action: describe/validate when useful, call
  `toolbox.call` for `action.run`, and read the compact result.
- Execute a workflow action: validate the manifest and input, let the daemon
  resolve credentials through `action.execute`, then store outputs as
  resources, artifacts, learnings, or run step summaries.
- Use engineering evidence/resources: read existing `engineering-decision` and
  `engineering-evidence` records with `resource.query`. Create durable evidence
  only inside a run-plan step with explicit grants such as `resource.upsert`,
  `artifact.create`, or `decision.record`.
