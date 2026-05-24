---
name: stackos
description: Use when working from any website repository to connect the repo to StackOS, resolve the current project, inspect workflow templates/run plans, and use daemon-managed resources/actions without writing setup files into the repo.
---

# StackOS Plugin Entrypoint

Use the current repository as source context and the local StackOS daemon as
durable state. The daemon owns projects, credentials, workflow templates, run
plans, resources, actions, context, learnings, experiments, decisions, and audit
trails.

Use the daemon's `workflowTemplate.*`, `runPlan.*`, `workspace.*`,
`resource.*`, `context.*`, and `action.*` tools for durable state and execution
planning.

The MCP bridge intentionally exposes a compact direct tool list. Use direct
tools for workspace/project/workflow/run-plan control. Use `toolbox.describe`
to inspect hidden setup or active run-plan step tools, then `toolbox.call` to
invoke exactly one hidden tool by name. Do not try to call hidden daemon tools
directly.

## Operating Rules

1. Do not create `.env`, `.mcp.json`, `AGENTS.md`, `CLAUDE.md`, or
   `.stackos/*` in the current repository unless the user explicitly asks
   for checked-in hints.
2. Start by resolving the current workspace with `workspace.startSession` or
   `workspace.resolve` using repo hints supplied by the plugin MCP bridge.
3. If no binding exists, guide the user through `workspace.connect`; the binding
   is stored in the daemon DB, not in the website repo.
4. Use `toolbox.describe`/`toolbox.call` for hidden setup tools and the current
   run-plan step grants that are not in the direct list.
5. When a run plan needs missing vendor credentials, do not ask the user to
   paste secrets into chat. Name the missing providers and send the operator to
   `http://localhost:5180/projects/{project_id}/connections`. After the user
   connects them in the UI, call `auth.test` through the daemon before
   continuing.
6. When a step requires a provider call, use `action.describe`,
   `action.validate`, and the step-granted `action.execute` path. The daemon
   resolves credentials inside the action process and returns only sanitized
   output.
7. When the user asks for one explicit action and no workflow state is needed,
   use `action.run` with `confirm_direct=true`, `intent_summary`, and an
   `idempotency_key` for non-read actions. Leave `verbose=false` unless the
   full redacted action payload is needed for debugging.

## Common Flows

- Connect repo: resolve workspace; if it is unbound, have the operator choose
  the project through UI/CLI/admin flow, call `workspace.connect`, then inspect
  content conventions. The bridge will inject the resolved project on later
  calls.
- Connect vendors: inspect the run plan's needed providers, share
  `/projects/{project_id}/connections`, wait for the operator to connect them
  in the UI, then run the relevant health probes.
- Continue work: call `workspace.startSession`, resolve the project, then fetch
  the active run plan.
- Execute a step: claim the run-plan step, follow the referenced guidance, call
  `toolbox.describe` for needed granted tools, invoke them with `toolbox.call`,
  then `runPlan.recordStep`.
- Execute one direct action: describe/validate when useful, call `action.run`,
  and read the compact result.
- Execute a workflow action: validate the manifest and input, let the daemon
  resolve credentials through `action.execute`, then store outputs as
  resources, artifacts, learnings, or run step summaries.
