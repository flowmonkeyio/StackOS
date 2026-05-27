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
5. When a run plan needs missing vendor credentials, do not ask the user to
   paste secrets into chat. Name the missing providers and send the operator to
   `http://localhost:5180/projects/{project_id}/connections`. After the user
   connects them in the UI, call `toolbox.call` for `auth.status` and
   `auth.test` before continuing.
6. When a step requires a provider call, use `action.describe`,
   `action.validate`, and the step-granted `action.execute` path. The daemon
   resolves credentials inside the action process and returns only sanitized
   output.
7. When the user asks for one explicit action and no workflow state is needed,
   use `toolbox.call` for `action.run` with `confirm_direct=true`,
   `intent_summary`, and an `idempotency_key` for non-read actions. Leave
   `verbose=false` unless the full redacted action payload is needed for
   debugging.

## Common Flows

- Connect repo: call `workspace.startSession`; the bridge should return a
  project-bound session and inject the resolved project on later calls. If the
  operator wants a specific existing project, call `toolbox.call` for
  `workspace.connect` or `workspace.bootstrap` with that project identifier
  explicitly.
- Connect vendors: inspect the run plan's needed providers, share
  `/projects/{project_id}/connections`, wait for the operator to connect them
  in the UI, then run `toolbox.call` for `auth.status` and `auth.test`.
- Continue work: call `workspace.startSession`, use the workspace-bound
  project id, then call `toolbox.call` for tracker/run-plan/workflow tools.
- Execute a step: claim the run-plan step, follow the referenced guidance, call
  `toolbox.describe` for needed granted tools, invoke them with `toolbox.call`,
  then `runPlan.recordStep`.
- Execute one direct action: describe/validate when useful, call
  `toolbox.call` for `action.run`, and read the compact result.
- Execute a workflow action: validate the manifest and input, let the daemon
  resolve credentials through `action.execute`, then store outputs as
  resources, artifacts, learnings, or run step summaries.
