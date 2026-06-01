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
6. When a workflow or action might need vendor setup, call `toolbox.call` for
   `readiness.check` with the selected `workflow_key` or `action_ref` before
   broad `auth.status`. Do not ask the user to paste secrets into chat. Name
   only the scoped missing providers and send the operator to
   `http://localhost:5180/projects/{project_id}/connections`. After the user
   connects them in the UI, call `toolbox.call` for `auth.status` and
   `auth.test` before continuing.
7. Use `action.list` for normal "what can I use now?" discovery. It returns
   executable/current actions and hides disconnected, deferred, project-local,
   missing-connector, and otherwise non-executable external-provider actions.
   Use `integration.list` when setup or planning needs compact provider
   readiness and hidden-action counts; use
   `include_unavailable_integrations=true` only for deliberate setup/catalog
   inspection.
8. When a step requires a provider call, use `action.describe`,
   `action.validate`, and the step-granted `action.execute` path. The daemon
   resolves credentials inside the action process and returns only sanitized
   output.
9. When the user asks for one explicit action and no workflow state is needed,
   use `toolbox.call` for `action.run` with `confirm_direct=true`,
   `intent_summary`, and an `idempotency_key` for non-read actions. Provider
   side-effect operations return raw redacted provider output by default; do not
   request compact or ack shapes for them.

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
- Set up support/engineering/local agents: choose the workflow first. Use
  `communications.customer-feedback-intake` to normalize inbound feedback into
  one route-approved canonical Slack thread with media and refs preserved. Use
  `support.issue-investigation` to read the full thread, ask bounded
  clarifications, and post the evidence-backed support conclusion. Use
  `support.delivery-task-handoff` only after a same-thread operator instruction
  asks for task creation, then hand off to `engineering.tracked-delivery` for
  scoped implementation, verification, review, and release. Describe the
  selected workflow, resolve agents with `agentPreset.resolveForWorkflow`, then
  create/start a run plan when executing. Treat the referenced communications,
  support, and engineering presets as one curated project-adapted set. Adapt
  that subset to the host/project's local agent format only when local agents
  are requested. For Codex repos, inspect `.codex/config.toml` and existing
  `.codex/agents/*.toml` before proposing file creates or updates. Treat each
  preset's `recommended_tools` as StackOS operation refs. If those refs are not
  mounted as direct host tools, use `toolbox.describe` and `toolbox.call`.
  For non-Slack feedback, do not treat `communicationTarget.resolve` as route
  approval; use a matching route or current operator instruction. Forward every
  route-approved media item in the same canonical Slack handoff message when
  supported, or stop before partial handoff with an explicit media
  blocker/waiver. Before creating a run, inspect
  `workflowExtension.get`/`workflowTemplate.describe` for project defaults such
  as `communication_route_ref`, `canonical_slack_target_ref`, and
  `project_workflow_context`. If a project needs durable route refs or channel
  guidance, validate and save a project extension with
  `workflowExtension.validate` and `workflowExtension.upsert`. Put workflow
  field changes, including agent/skill requirements, contracts, approval gates,
  and steps, in `template_overrides_json`; StackOS applies that atomic patch to
  the base workflow and validates the effective template. Top-level workflow
  fields are replaced atomically, so pass the full desired
  `agent_requirements`, `skill_requirements`, or `steps` list when changing
  them. Do not invent a new context-sharing mechanism or duplicate the workflow
  unless a new reusable workflow identity is needed.
- Discover operations: if you do not know the exact operation name, call
  `toolbox.call` for `operation.list` first, then `operation.describe` for the
  few operations you intend to use. Keep `toolbox.describe` scoped to exact
  tool names.
- Repair denied tools: read `toolbox.describe.tool_statuses`. `unknown_tool`
  means the name is wrong or removed. `local_admin_required` means operator
  setup is needed. `run_plan_step_grant_required` means create/start the run
  plan, claim the step, then retry with `run_id`. `not_granted_to_active_step`
  means the active step exists but the grant snapshot does not cover this tool
  or argument shape.
- Connect vendors: inspect the run plan's needed providers, share
  scoped readiness first with `toolbox.call` for `readiness.check` using the
  selected `workflow_key` or `action_ref`. Share
  `/projects/{project_id}/connections` only for the listed providers, wait for
  the operator to connect them in the UI, then run `toolbox.call` for
  `auth.status` and `auth.test`.
- Inspect integrations: call `toolbox.call` for `integration.list` when the
  agent needs a project-level provider inventory, connected counts, hidden
  external action counts, and safe setup links. Do not use broad `auth.status`
  as the first answer for one selected workflow/action unless diagnostics need
  all provider rows.
- Plan direct work: use tracker tasks/tickets when the agent is planning or
  delivering scoped work outside a concrete workflow run and the operator did
  not invoke a workflow. Create dependencies, blockers, definition of done, and
  completion evidence there.
- Tracker lifecycle rules: use `tracker.updateTask(status=...)` only for
  independent tasks. If an independent task has tickets, prefer updating the
  tickets and let StackOS aggregate the parent task. Terminal tracker statuses
  are `complete`, `deferred`, `aborted`, `failed`, and `skipped`; any terminal
  status moves the item to the done lane. Use `deferred` only for postponed
  resumable work, `aborted` for stopped/rejected/cancelled work, `failed` for
  attempted unsuccessful work, and `skipped` for intentionally not executed
  work. `tracker.rejectTask` is an operator rejection override: it marks the
  task and all child tickets `aborted`. For workflow-backed tasks, do not patch
  task or mirror-ticket lifecycle directly. `runPlan.create` creates the
  workflow task and step tickets; `runPlan.start` marks the task
  `in-progress`; `runPlan.claimStep` marks the step ticket `in-progress`;
  `runPlan.recordStep` marks the step ticket `complete`, `failed`, or
  `skipped`; `runPlan.abort` marks the workflow task and unfinished linked
  tickets `aborted`. Completed or failed workflow run plans cannot be rejected
  through tracker state. `tracker.linkRunPlan` is provenance only; it does not
  transfer lifecycle ownership from tracker to run plan.
- Execute workflow work: use a workflow template when work should follow a
  reusable contract or when the operator explicitly asks to use a workflow,
  engineering workflow, StackOS workflow, or "the workflow". Create or resolve
  the workflow-backed run plan before creating tracker tasks or tickets, then
  create discovery, design, delivery, verification, and closeout tickets under
  the workflow task/run plan from the start. Check the attached workflow
  extension first when the project has route refs, default inputs, selected
  context, guardrails, or workflow-field overrides. `runPlan.create` applies
  enabled extension defaults and the effective template, then turns it into
  concrete state; `runPlan.start` and step grants control which tools/actions
  are available. Mirror or link tracker tickets when human-visible
  sequencing/evidence matters.
- Execute a step: claim the run-plan step, follow the referenced guidance, call
  `toolbox.describe` for needed granted tools, invoke them with `toolbox.call`,
  then `runPlan.recordStep`. For long implementation stretches between claim
  and record, call `run.heartbeat` with the active `run_id`; claim and record
  refresh heartbeat automatically, but an agent doing local work for several
  minutes should keep the controller run alive explicitly.
- Execute one direct action: describe/validate when useful, call
  `toolbox.call` for `readiness.check` when setup is uncertain, call
  `toolbox.call` for `action.run`, and read the raw redacted provider result.
- Execute a workflow action: validate the manifest and input, let the daemon
  resolve credentials through `action.execute`, then store outputs as
  resources, artifacts, learnings, or run step summaries.
- Use engineering evidence/resources: read existing `engineering-decision` and
  `engineering-evidence` records with `resource.query`. Create durable evidence
  only inside a run-plan step with explicit grants such as `resource.upsert`,
  `artifact.create`, or `decision.record`.
