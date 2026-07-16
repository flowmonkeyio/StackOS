---
name: stackos
description: Use when working from any website repository to connect the repo to StackOS, resolve the current project, inspect workflow templates/run plans, and use daemon-managed resources/actions without writing setup files into the repo.
---

# StackOS Plugin Entrypoint

Use the current repository as source context and the local StackOS daemon as
durable state. The daemon owns projects, credentials, workflow templates, run
plans, resources, actions, context, learnings, experiments, decisions, and audit
trails.

Use StackOS tools for durable state, execution planning, and daemon-owned
browser automation. The direct MCP surface is intentionally focused: call
`workspace.startSession` or `workspace.resolve` first, use direct `browser.*`
tools for live browser sessions when needed, then use `toolbox.describe` and
`toolbox.call` for project setup, workflow templates, run plans, tracker,
resources, context, and actions.

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
5. Agent presets are contracts, not daemon-run local agents. A request to set
   up workflow infrastructure or local agents authorizes reviewed project-local
   host execution contracts when project guidance permits them; ordinary
   workflow execution does not. Inspect and preserve existing host files, then
   adapt presets to the host/project's detected format
   (`.codex/agents/*.toml` referenced by `.codex/config.toml`, markdown
   frontmatter, plugin files, or session-only guidance). StackOS does not scan,
   write, or register host-local agent files. Required roles must be
   materialized or receive an explicit session-only fallback; recommended roles
   are materialized by default unless the agent records a project/host/risk
   reason; optional roles require selection. Skill presets are main-agent
   guidance, never subagents.
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
   inspection. Generated provider inventories expose stable public action refs;
   do not call internal generated storage keys.
8. When a step requires a provider call, use `action.describe`,
   `action.validate`, and the step-granted `action.execute` path. When several
   provider calls share credential, provider scope, output policy, request
   budget, or reusable execution context, use `executionContext.discover`,
   `executionContext.resolve`, or `executionContext.create` and pass
   `context_ref` instead of repeating credential/provider context on every
   call. The daemon resolves credentials inside the action process and returns
   only sanitized output. External-provider action output is file-backed by
   default; inspect the returned response-file path before rerunning provider
   calls. If the envelope schema is needed, call `schema.get` with the returned
   `schema_ref`.
9. When the user asks for one explicit action and no workflow state is needed,
   use `toolbox.call` for `action.run` with `confirm_direct=true`,
   `intent_summary`, and an `idempotency_key` for non-read actions. External
   provider actions return compact response-file metadata by default: path,
   schema version, `schema_ref`, `schema_operation`, content type, byte size,
   checksum, and semantic name. The sanitized request/response envelope lives
   in the file. Fetch the schema through `schema.get` only when needed. Request
   raw only when the next step truly needs the full public audit shape.
   Pass `response_mode=raw` only when the bounded response-file content itself
   should enter the context window.
10. For browser automation, use the direct `browser.*` tools when mounted, or
    `toolbox.call` for the same operation names before the current Codex session
    is restarted. `browser.session.start` opens a persistent Playwright Chromium session
    (`headless=false` by default for operator login/inspection). Use
    `browser.page.call` and `browser.context.call` for public Playwright
    methods with raw args/kwargs or named `arguments`; prefer named
    `arguments` for manifest convenience methods such as `goto`, `click`, and
    `fill` so receipts and validation can identify fields like `url` and
    `selector`. Use `browser.handle.call` for
    returned locator/download/response-like object handles,
    `browser.script.run` and `browser.script.inject` for arbitrary JavaScript, and
    `browser.page.screenshot` for generated-assets evidence. Treat this as the
    same class of browser control as a normal Playwright/test browser session:
    this is local trusted-admin browser automation, not an externally exposed
    sandbox. StackOS records redacted receipts, but it does not maintain a
    restrictive browser-method allowlist. The daemon owns executable/profile
    paths and rejects launch options that try to override those controls. Treat
    immediate browser tool output as sensitive raw browser data.

## Common Flows

- Person asks what to do after installation: call `toolbox.call` for
  `guide.gettingStarted` with `response_mode=raw`. This global read is allowed
  before workspace binding. Answer from the returned content and always link
  the person to `guide_url`, the designed website page. Treat `markdown_url` as
  the agent/tool source, not the primary human destination. Do not copy the
  walkthrough into the skill or improvise a parallel guide when the fetch is
  unavailable; share `guide_url` and use the returned warning.
- First run in a repo: call `workspace.startSession`. The bridge should create
  or reuse one daemon-owned project binding for that workspace and return UI
  links plus setup/profile state. Treat `workspace_bound=true` as "project tools
  are usable"; missing framework/content-model profile fields are adaptation
  hints, not blockers. After inspecting the repo's stack and content model, use
  `toolbox.call` for `workspace.updateProfile` when those hints should be
  durable for future agents.
- Read-only workspace diagnostics: call `workspace.resolve` when the agent only
  needs to inspect whether the current repo is already bound, what project it
  maps to, or why setup is unavailable. Do not use it as the normal start path
  for work that will create, update, execute, or track anything; switch to
  `workspace.startSession` before mutating project state.
- Ongoing repo session: call `workspace.startSession`, use the workspace-bound
  project id, then call only the scoped tools needed for the current task. Do
  not request broad schemas or catalog dumps unless debugging.
- Connect to a specific project: if the operator wants a known existing
  project, call `toolbox.call` for `workspace.connect` with that project
  identifier explicitly.
- Bind desktop/global hosts to a project: if StackOS says project identity is
  required, inspect `candidate_workspaces` and user intent. Reuse a known
  `workspace_alias` or selected existing project with `workspace.connect`, or
  ask for a business `project_name`/`project_slug` and call
  `workspace.bootstrap` to create a new named workspace. Do not let app/runtime
  folder names become projects, do not invent cwd/repo anchors, do not call
  `project.create` as a substitute for binding, and do not use
  last-used/global fallback binding.
- Set up a workflow project only after selecting one canonical intent mode:
  `setup_existing`, `customize_existing`, `author_project`, `publish_plugin`,
  `one_off_run`, or `execute`. Call `workflowTemplate.authoringGuide` for the
  structured mode boundaries and completion contract. A setup request does not
  authorize a new template, plugin change, or workflow execution.
  Phase 1 is infrastructure setup: bind the authorized workspace, select and
  describe the effective workflow, inspect the existing extension, save only a
  reviewed non-empty overlay when needed, and re-describe after any overlay
  change. Call `agentPreset.resolveForWorkflow` once; it already includes
  installed skill requirements and resolved main-agent skill presets. Inspect
  host files, materialize required roles, materialize recommended roles by
  default unless a reason is recorded, and materialize optional roles only when
  selected. Keep skill presets as main-agent guidance. Check the full readiness
  tuple, then run structural `runPlan.validate` with
  `enforce_required_inputs=false` and strict validation with
  `enforce_required_inputs=true` to expose exact deferred prerequisite keys.
  Do not call `runPlan.create`, create tracker work, collect domain
  prerequisites, or produce workflow output in Phase 1. Report the canonical
  setup completion contract, including versions and exact preset-to-target
  mapping.
  Phase 2 is prerequisite setup: classify every missing value as
  infrastructure, durable prerequisite, or per-run input; collect only durable
  prerequisites; and, when the workflow explicitly delegates one to another
  workflow, return that exact safe handoff instead of silently executing it.
  For operator-approved workflow-family setup, resolve each selected workflow
  and materialize the deduplicated union of required and recommended roles.
  Persist each prerequisite through its declared owner, omit unresolved values
  instead of storing placeholders, and stop before recurring output.
  Phase 3 is operation: bind the existing project, collect concrete run inputs,
  require selected-route execution readiness, strictly validate before
  `runPlan.create`, then start/claim/record through step grants and approvals.
- Set up support/engineering/local agents: choose the workflow first. Use
  `communications.customer-feedback-intake` to normalize inbound feedback into
  one route-approved canonical Slack thread with media and refs preserved. Use
  `support.issue-investigation` to read the full thread, ask bounded
  clarifications, and post the evidence-backed support conclusion. Use
  `support.delivery-task-handoff` only after a same-thread operator instruction
  asks for task creation, then hand off to `engineering.tracked-delivery` for
  scoped implementation, verification, review, and release. Describe the
  selected workflow and resolve agents plus main-agent skill presets once with
  `agentPreset.resolveForWorkflow`; call `skillPreset.resolveForWorkflow`
  separately only when a standalone skill packet is specifically needed. Then
  create/start a run plan only when executing. Treat the referenced communications,
  support, engineering, and skill presets as one curated project-adapted set.
  Adapt agent presets to the host/project's local agent format when workflow
  infrastructure or local agents are requested. Adapt skill presets into
  session/project guidance for the main agent; they are not installed host
  skills and they do not create subagent roles. For Codex repos, inspect
  `.codex/config.toml` and existing
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
  field changes, including agent requirements, installed skill requirements,
  skill preset requirements, contracts, approval gates, and steps, in
  `template_overrides_json`; StackOS applies that atomic patch to the base
  workflow and validates the effective template. Top-level workflow fields are
  replaced atomically, so pass the full desired `agent_requirements`,
  `skill_requirements`, `skill_preset_requirements`, or `steps` list when
  changing them. Do not invent a new context-sharing mechanism or duplicate the
  workflow unless a new reusable workflow identity is needed.
- Discover operations: if you do not know the exact operation name, call
  `toolbox.call` for `operation.list` with
  `{"surface":"mcp","mode":"grouped","response_mode":"compact"}`, then use
  `toolbox.describe` or `operation.describe` for the few exact operations you
  intend to call. Keep `toolbox.describe` scoped to exact tool names.
- Use browser automation: call `browser.runtime.status`, then
  `browser.session.start`. If the platform needs login, pause and let the
  operator complete login in the opened browser session, then continue with
  `browser.page.call`, `browser.context.call`, `browser.handle.call`,
  `browser.script.run`, or `browser.script.inject`. Use
  `browser.page.snapshot` for text state and `browser.page.screenshot` for
  visual proof or publication evidence. Stop the session with
  `browser.session.stop` when done. Treat the browser method
  manifest as guidance, not an allowlist: public page/context methods may be
  called through raw args/kwargs, while manifest convenience methods should use
  named `arguments` when possible, for example
  `arguments: {"url": "https://example.com"}` for `goto`.
- Repair denied tools: read `toolbox.describe.tool_statuses`. `unknown_tool`
  means the name is wrong or removed. `local_admin_required` means operator
  setup is needed. `run_plan_step_grant_required` means create/start the run
  plan, claim the step, then retry with `run_id`. `not_granted_to_active_step`
  means the active step exists but the grant snapshot does not cover this tool
  or argument shape.
- Connect/setup vendors: inspect scoped readiness first with
  `toolbox.call` for `readiness.check` using the selected `workflow_key` or
  `action_ref`; use `integration.list` for catalog/setup questions and
  `action.describe` for one action. Read returned `setup`/`provider_setup`
  fields for StackOS connect, register, API-key, billing, docs, and fallback
  URLs. Share exact URLs from those fields; when `url_confidence` is
  `directional`, say it is the closest official destination. Never ask for
  secrets in chat. After the operator connects a provider, run `auth.status`
  and `auth.test`.
- Inspect integrations: call `toolbox.call` for `integration.list` when the
  agent needs a project-level provider inventory, connected counts, hidden
  external action counts, and safe setup links. Do not use broad `auth.status`
  as the first answer for one selected workflow/action unless diagnostics need
  all provider rows.
- Author workflows from any repo: call `toolbox.call` for
  `workflowTemplate.authoringGuide` and treat that response as the canonical
  contract. Use the default compact response for mode selection and setup; after
  selecting `author_project` or `publish_plugin`, request `response_mode=raw`
  once for the complete authoring contract. Select one returned intent mode
  before mutation. Use
  `author_project` for a new no-code project/user template and
  `publish_plugin` only for distributed package behavior. Do not duplicate
  workflow-authoring rules into repo docs or long skill text.
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
  work. Terminal child-ticket updates can aggregate the parent task status, but
  ticket evidence is not copied to the task; read compact `task_rollup` and
  `completion_evidence_present` fields and patch task-level evidence explicitly
  when task closeout evidence matters. `tracker.rejectTask` is an operator
  rejection override: it marks the
  task and all child tickets `aborted`. For workflow-backed tasks, do not patch
  task or mirror-ticket lifecycle directly. Mirror tickets are the generated
  `workflow-{run_plan_id}-{step_id}` tickets; attached child tickets such as
  implementation, docs, verification, and audit tickets remain tracker-owned
  work items and should be updated with `tracker.updateTicket` when their
  evidence/status changes. `runPlan.create` creates the
  workflow task and step tickets; `runPlan.start` marks the task
  `in-progress`; `runPlan.claimStep` marks the step ticket `in-progress`;
  `runPlan.recordStep` marks the step ticket `complete`, `failed`, `skipped`,
  or `blocked`. `runPlan.recordStep(success)` enforces lifecycle, approvals,
  and transitive run-plan step dependencies. It does not hard-block on tracker
  graph warnings. Treat graph warnings as planning/audit signals: repair them
  when they hide required work or affect the current definition of done, record
  them as follow-up cleanup when they do not, and keep moving. `blocked` is
  recoverable and keeps the run plan started until the blocker is repaired and
  the same step is reclaimed. If an old daemon or controller bug already made a
  recoverable blocker terminal, call `runPlan.get` or
  `runPlan.checkConsistency`, then use `runPlan.recover` only when the
  diagnostics/history show a system-recoverable failed, aborted, or safely
  recoverable live workflow. Do not create a duplicate replacement plan just to
  escape stale lifecycle state. When the operator wants normal follow-up work
  after closeout, call `tracker.reopen` with `task_key`, `run_plan_id`, or
  linked `run_id` and a reason; StackOS reopens the task or the mirrored
  plan/run/task lifecycle as needed. `runPlan.abort` marks the workflow task and unfinished linked tickets
  `aborted`. Completed or failed workflow run plans cannot be rejected through
  tracker state.
  `tracker.linkRunPlan` is provenance only; it does not transfer lifecycle
  ownership from tracker to run plan.
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
  sequencing/evidence matters. For workflow-backed child tickets,
  pass `run_plan_id` and `step_id` at creation time. They are
  attachment/provenance only, not lifecycle ownership transfer. The workflow
  graph should have exactly one root: the first generated workflow step mirror
  ticket. Use `tracker.updateTicket` for those child tickets;
  use `runPlan.claimStep`/`runPlan.recordStep` only for the generated workflow
  step mirror tickets. Add dependency edges into the mirrored workflow
  spine, then immediately call `tracker.get` with `run_plan_id` and
  `include_graph=true`. Review workflow-spine warnings, repair material issues,
  and record non-blocking cleanup explicitly instead of treating every warning
  as an execution blocker.
- Execute a step: claim the run-plan step, follow the referenced guidance, call
  `toolbox.describe` for needed granted tools, invoke them with `toolbox.call`,
  then `runPlan.recordStep`. Claim and record refresh the linked audit heartbeat,
  and the stale-run reaper preserves normal long-running workflow work. Do not add
  periodic `run.heartbeat` calls unless a workflow explicitly asks for audit
  liveness evidence.
- Execute one direct action: describe/validate when useful, call
  `toolbox.call` for `readiness.check` when setup is uncertain, resolve an
  `executionContext.*` ref when the provider scope will be reused, call
  `toolbox.call` for `action.run`, then inspect the returned response-file path
  before making another paid or side-effecting provider call. Call `schema.get`
  with `schema_ref` only when the file envelope schema is needed.
- Execute a workflow action: validate the manifest and input, let the daemon
  resolve credentials through `action.execute`, then store synthesized outputs
  as resources, learnings, or run step summaries. Keep endpoint
  payload in `input_json`; keep provider scope such as acting-on-behalf options
  in provider context or an execution context. External provider outputs are
  file-backed by default; inspect the returned response-file path before
  rerunning production calls. Call `schema.get` with `schema_ref` only when the
  file envelope schema is needed.
- Use engineering evidence/resources: read existing `engineering-decision` and
  `engineering-evidence` records with `resource.query`. Create durable evidence
  only inside a run-plan step with explicit grants such as `resource.upsert`
  or `decision.record`.
