# Agent Operating Model

In this document, an agent is the MCP/tool consumer that calls StackOS
operations and plugin actions. It may be a coding agent, automation script, or
other runtime, and it may separately receive repository filesystem tools from
its host. StackOS does not grant that filesystem capability. StackOS resolves
project scope, validates explicit inputs, executes daemon-side operations, and
records audit.

StackOS has two execution paths because agents do two different kinds of work:

1. **Workflow work**: multi-step, stateful, repeatable work that should be
   planned, granted, recorded, and reviewed.
2. **Direct action work**: one explicit tool action where a run plan would add
   ceremony without adding control.

The agent still decides what should happen. StackOS only resolves the current
project, validates static contracts, holds credentials inside the daemon,
executes explicit calls, stores durable state, and records audit.

## Decision Rule

Use a workflow template and run plan when the task has any of these traits:

- more than one meaningful step
- durable outputs, artifacts, resources, learnings, experiments, or decisions
- approvals, quality gates, retries, or branching
- repeated operational cadence, such as SEO reviews or media buying checks
- a need to grant specific write tools to a specific step

Use `toolbox.call` for `action.run` when the user asked for one concrete action:

- send one Telegram message
- poll the current bot updates during setup
- send one SMTP test email
- run one diagnostic provider call
- fetch one no-auth utility result

When unsure, prefer a run plan. Direct action is for simple execution, not a
shortcut around workflow memory or approval.

Use the project task tracker for work navigation in both paths. Workflow plans
mirror into tracker tasks/tickets automatically. Manual or ad hoc agent work
should be made explicit with `tracker.createTask` and `tracker.createTicket`
instead of living only in chat context.

Generic agent presets are setup contracts for MCP/tool consumers. They are not
daemon-run agents and they are not project-ready prompts. Before using one, the
caller must adapt it with project guidance, stack details, tracker workflow,
references, verification commands, and release expectations. Use
`agentPreset.resolveForWorkflow` to see the required/recommended agent roles and
host-side skill requirements for a workflow template.

Support and engineering roles run through the normal workflow-template path.
Customer feedback starts with `communications.customer-feedback-intake`: the
agent preflights cross-surface route approval and media forwarding, establishes
one canonical Slack thread, adds the intake reaction, and returns durable refs.
Investigation then runs through `support.issue-investigation`: the agent reads
the full thread, asks same-thread clarifications when needed, and posts the
support conclusion in that thread. Task creation is a separate instructed
handoff through `support.delivery-task-handoff`; it requires a same-thread
operator instruction, creates delivery-ready tracker work, posts the task
handoff in the thread, adds a task-created reaction, and then points to
`engineering.tracked-delivery`. Scoped implementation runs through
`engineering.tracked-delivery`. A resolvable Slack target is not route
approval, and source media must be forwarded in the same canonical Slack
handoff message when the provider path supports it, or explicitly
blocked/waived before the workflow proceeds. `agentPreset.resolveForWorkflow`
turns each workflow into the required and recommended role contracts; use those
communications, support, and engineering roles as one curated project-adapted
agent set without overlapping responsibilities.

Local agent installation is outside the StackOS daemon boundary. StackOS
describes generic presets and workflow role requirements, but the host/project
decides whether those become `.codex` agents, markdown-frontmatter agents,
plugin agent files, or session-only instructions. If a host does not expose a
recommended StackOS operation directly, the agent should inspect and invoke it
through the scoped toolbox.

For Codex-style hosts, inspect `.codex/config.toml` and existing
`.codex/agents/*.toml` before creating or updating local agents. StackOS does
not scan or register those files because repository filesystem access belongs
to the host agent, not the daemon. The workflow and preset responses should
tell the agent what roles are needed; the host-local format decides how those
roles are materialized.

Desktop project hosts such as Claude Desktop Cowork/Code should follow the
same split. StackOS install/repair owns neutral connectivity only: daemon
state, MCP registration, and the generic StackOS mechanics skill when the host
uses skills. Domain workflow behavior should be project-local and
agent-authored. The operator should open or create a real host project for the
business work, then ask the host agent to set up that project for a named
StackOS workflow. The agent must resolve the workflow template, skill preset,
and agent presets, adapt them to the project, and create the host-local
guidance, skill, or agent files that future sessions in that host project will
load. It must not write global domain guidance, create tracker-only recipe
tasks, or rely on a global active project.

For folderless or plain chat sessions, do not invent a project-local install
target. Bind by explicit `workspace_alias` or project identity, execute with
the resolved workflow and presets in the current session, and ask the operator
to move durable recurring work into a real host project when they want future
sessions to resume reliably.

Workflow infrastructure setup is not workflow execution. Select
`setup_existing` from `workflowTemplate.authoringGuide`, resolve the effective
workflow and exact role topology, materialize required roles and recommended
roles by default, check structural readiness, and use structural plus strict
read-only `runPlan.validate` calls. A strict missing-input result is the named
prerequisite gate. Do not call `runPlan.create` or create tracker work until a
concrete operation request supplies the required inputs and passes execution
readiness.

When a host supports skills, workflow templates may recommend `stackos:stackos`.
That skill teaches agents how to use StackOS MCP, operations, workflow
templates, run plans, tracker tasks/tickets, dependencies, and evidence. The
main agent decides whether to load it, but the workflow response makes the
expectation visible.

## Workflow Path

```text
agent intent
-> operation.list with surface=mcp, mode=grouped, response_mode=compact when operation names are not clear
-> operation.describe / action.list / action.describe when the contract is not already clear
-> integration.list when project integration inventory or hidden-action counts are needed
-> readiness.check when a selected workflow/action may need provider setup
-> agentPreset.resolveForWorkflow when setting up workflow-specific roles
-> workflow template or agent-authored run plan
-> runPlan.validate/create/start
-> tracker.brief or tracker.next for bounded work context
-> runPlan.claimStep
-> step-granted tools through toolbox.call
-> action.execute for provider actions
-> runPlan.recordStep
-> resources/artifacts/learnings/experiments/decisions as needed
```

`action.execute` remains run-plan-step gated. The active step and frozen grant
snapshot must both allow the exact `action_ref`. It returns the full redacted
action execution shape because workflow steps usually need complete audit data.

`tracker.*` operations are not a replacement for run-plan grants. They provide
work state, dependency readiness, and verification context. The step still
claims and records through `runPlan.*`, and provider execution still flows
through `action.execute`.

Use `readiness.check` when the agent already knows the selected workflow or
action. It reports only setup gaps for that scope, such as the credential or
budget needed by `seo.keyword-research`, instead of making the project look
blocked because unrelated providers are disconnected. Read the dimensions
explicitly:

- `structurally_ready`: the workflow and referenced action contracts exist
- `context_status`: required project context is ready, missing, or
  `not_evaluated`
- `required_providers_ready`: all required actions and every required choose-one route
  have an executable path; optional routes do not block preparation
- `execution_ready`: all evaluated dimensions needed to execute are ready

There is no catch-all `ready` alias. Use `execution_ready` for execution and
`structurally_ready` for planning when context or providers are incomplete.
Top-level `missing` contains blockers only.
Missing setup with `required_for: optional_action_execution`,
`alternative_route_not_selected`, or `optional_route:*` remains on the detailed
action or route so the agent can explain available branches without treating
them as prerequisites. `route_groups` describe choose-one provider paths; an
unavailable alternative does not block a ready route.

When `runPlan.claimStep` returns a running step, it includes the step purpose,
instructions, policies, approvals, success criteria, allowed tools, resolved
values for the step's declared input refs, bounded selected context, expected
output contracts, and bounded `direct_dependency_handoffs`. Use that packet
before rediscovering prior work. If an input, context, or prior result is
truncated, follow the included targeted raw `runPlan.getStep` recovery hint rather than
guessing.

Use `action.list` as the normal "what can I use now?" discovery path. It hides
external-provider actions whose required integration is not connected, and it
also hides deferred, project-local, missing-connector, or otherwise
non-executable external-provider actions. Executable local/no-credential
actions remain visible. Use `integration.list` for project setup or planning
when the agent needs compact provider readiness, hidden-action counts, and safe
connection links. Use `readiness.check` for the selected workflow/action when
the agent already knows the work scope. Broad `auth.status` is still available
for diagnostics, but it should not be the default first-run answer for one
concrete workflow/action.

## Direct Action Path

```text
agent receives one explicit user request
-> operation.describe / action.list / action.describe when the contract is not already clear
-> toolbox.call(readiness.check) for the selected action when setup is uncertain
-> toolbox.call(toolProfile.resolve) when provider/auth/profile selection is needed
-> toolbox.call(secret.set) once when a non-auth string payload value is sensitive
-> toolbox.call(action.validate) with the $secret_ref marker when validation is needed
-> toolbox.call(action.run)
-> daemon-side credential resolution
-> connector execution
-> compact response-file path for external provider output and action_calls audit row
```

Inspecting the schema can happen before `secret.set`, but concrete payload
validation must use the returned marker instead of the raw sensitive value.

`action.run` is available through the MCP toolbox, REST, and CLI. It still uses
the same connector, auth, redaction, idempotency, cost, and audit code as
`action.execute`.

Direct non-read actions require:

- `confirm_direct=true`
- `intent_summary`
- an explicit `action_ref` or plugin/action pair
- only safe refs in action input: use `credential_ref` for provider auth and an
  exact `{"$secret_ref":"secret_..."}` marker for a non-auth sensitive string

When an authorized secure input has already made a tenant/customer value
available to the agent, call `secret.set` once and retain only its returned ref.
Do not echo the value in prose, create a provider Connection for it, or pass it
as raw `input_json`. The same marker works through direct `action.run` and
granted workflow `action.execute`; the shared runtime resolves it only for the
connector request.

When the provider target is not already obvious, call `toolProfile.resolve`
once instead of chaining broad provider/profile/auth discovery calls. The
resolver returns a compact safe tuple: provider status, optional project tool
profile, the daemon-held `credential_ref` to use, `ready`, `missing`, and
`next_action`. It does not select business strategy or action payloads.

Agents may pass `intent_id` or `idempotency_key` when they need stable retry
semantics. If neither is supplied, StackOS derives a request-scoped
idempotency key so the agent does not have to invent dedupe strings for ordinary
single calls. Workflow `action.execute` derives a stable key from the active
run, step, action, input, and credential when the plan did not provide one.

For direct work that spans more than one update or needs visible follow-up,
create tracker state first:

```text
tracker.createTask
-> tracker.createTicket
-> tracker.pick
-> tracker.brief
-> toolbox.call(action.run) or local work
-> tracker.patch / tracker.updateTicket
-> tracker.updateTask/updateTicket(status=deferred) if the operator parks resumable work
-> tracker.rejectTask if the operator rejects or aborts the task/run
```

Agent-facing MCP responses are compact by default for noisy internal
discovery/setup tools when the operation policy allows it. Compact mode keeps
ids, refs, counts, warnings, schema summaries, bounded lists/text, and next-call
hints, while omitting file/content bodies. Pass `response_mode=raw` or
`response_mode=standard` when the full normal daemon payload is needed, and use
`response_mode=ack` only for safe internal writes. External provider actions
through `action.run` and `action.execute` return compact response-file paths,
`schema_ref`, and `schema_operation` by default; communication/browser
side-effect operations remain raw-only until they define provider-safe compact
contracts. CLI calls use the same dispatcher but default to raw inline action
output. This keeps simple MCP calls from flooding the context window while
preserving richer non-agent contracts.

Workflow evidence follows the same boundary. Read project resources with
`resource.query`; write reusable evidence from a started run-plan step using
the granted write operations such as `resource.upsert`, `artifact.create`,
`artifact.update`, `artifact.archive`, `artifact.supersede`, or
`decision.record`. Tracker ticket comments and evidence are good for work
state; resources/artifacts/decisions are for reusable project memory. Do not
turn StackOS artifacts into scratch files for normal iteration; preserve only
approved outputs, final packets, durable evidence, operator-approved drafts, or
other intentionally retained blobs.

## Project Scope

Normal agent sessions are scoped by the repository they are running from.

```text
stackos mcp-bridge
-> detects git root/current root
-> builds a stable path fingerprint
-> reads git remote when available
-> calls workspace.startSession
-> injects the resolved project_id into project-scoped tools
```

The agent-facing bridge exposes only `workspace.startSession`,
`workspace.resolve`, `toolbox.describe`, and `toolbox.call` directly. First-run
setup uses `workspace.startSession` to create or reuse one workspace binding.
Ongoing operations use the same call only to confirm the workspace-bound
project, then move to narrowly scoped `toolbox.describe` / `toolbox.call`
requests. Setup, workflow, tracker, auth, and run-plan tools are reached
through `toolbox.call`. If a repo is already bound, the bridge injects
`project_id` and relaxes the advertised schemas so agents do not have to keep
repeating it. Agents should omit injected fields in toolbox calls unless an
exact described schema says otherwise outside the bridge path. If a caller
explicitly passes a different `project_id`, the bridge refuses the call. There
is no global active project in the agent path; the workspace-bound project is
the source of truth.

Workspace hints are also scoped. The bridge injects its current
`cwd`, `repo_fingerprint`, `git_remote_url`, `last_known_root`, runtime, and
session id where relevant. Calls that try to resolve or connect another
workspace are refused by the bridge. If the bridge has no host-supplied
workspace identity, caller-supplied directory/repo anchors are refused too;
desktop/global sessions must bind by explicit `workspace_alias` or project
identity.

These workspace hints are identifiers for StackOS project binding only. They
help the daemon inject the correct `project_id` and reject cross-project calls.
They do not let StackOS read, write, or enumerate repository files. Any file
access an agent has comes from the host environment and should be reasoned
about separately from StackOS permissions.

## Setup Path

Project and workspace are separate concepts. A project is the durable StackOS
container for tasks, tickets, workflows, credentials refs, resources, run
plans, communications, and audit. A workspace is the daemon-owned binding from
the local repo/directory where the agent is running to one project.

If a repository or directory is not bound yet, setup should be explicit and
idempotent:

1. Call `workspace.startSession` from the current repo/directory. It creates or
   reuses one project for that workspace root and stores the binding in the
   daemon DB when reliable directory identity exists. It does not write files
   into the repo. Desktop/global hosts with no cwd/git signal stay unbound and
   receive `candidate_workspaces`/project-selection guidance instead.
2. Read the returned setup state carefully: `workspace_bound` or
   `project_scoped_tools_usable` means project-scoped tools can run. Missing
   `framework` or `content_model_json` means the project profile is
   under-described for adaptation, not that the workspace is unusable.
3. Continue with project-scoped tools immediately; the bridge injects the
   resolved `project_id`.
4. Use `workspace.resolve` when the caller needs a read-only diagnostic before
   setup.
5. Use `toolbox.call` for `project.list`, `project.create`,
   `workspace.bootstrap`, or `workspace.connect` only when the operator or user
   intent intentionally chooses project identity. For desktop/global sessions
   with no directory identity, use `workspace.connect` to reuse a selected
   existing project or named `workspace_alias`; use `workspace.bootstrap` to
   create a new named workspace from explicit `project_name`, `project_slug`,
   or `workspace_alias`. Do not call `project.create` and assume the current
   agent session moved; bind or verify the workspace with `workspace.bootstrap`
   or `workspace.connect` before project-scoped work. If the current repo
   fingerprint or alias is already bound to a different project,
   `workspace.connect` rejects the move unless the caller passes
   `rebind_existing=true`.

The bridge sends a path fingerprint by default:
`path:<sha256(workspace_root)[:24]>`. The `workspace_root` is the directory the
host or operator supplied, not necessarily a Git repository root. If git is
unavailable, this path identity is enough for a local directory. If git or a
remote is added later, bootstrap can attach that metadata to the existing
binding when the same root is seen. A moved non-git directory without a remote
looks like a new workspace unless the agent intentionally reconnects or rebinds
it.

The bridge only creates path fingerprints for usable workspace roots. Host app
process cwd is a hint, not proof of project context: explicit `--workspace-root`
or `STACKOS_WORKSPACE_ROOT` values win, Claude Code's `CLAUDE_PROJECT_DIR` is
preferred when present, and a global desktop host launched at `/` returns
`needs_workspace_binding` instead of binding to a generic root project.

This keeps normal agents focused on the project attached to their workspace and
still gives them a complete MCP-native path when no binding exists yet.

## Toolbox Repair

The bridge keeps the first-level MCP surface small on purpose. If a tool is not
listed directly, that usually means it is a scoped toolbox tool rather than a
missing capability.

- `denied_tool_names` means the daemon knows the tool, but it is not callable in
  the current bridge scope.
- `tool_statuses[].reason_code == "local_admin_required"` means the tool is a
  setup/admin control; use an explicit operator flow rather than trying to
  repair it with run-plan grants.
- `run_plan_step_grant_required` means the agent needs a started run plan, a
  claimed running step, and an `mcp_tool_grants` entry for that tool.
- `not_granted_to_active_step` means the current step exists, but its grant
  snapshot does not cover the requested tool or arguments.
- `unknown_tool` means the name is wrong or the flow was removed; call
  `operation.list` through `toolbox.call` with `surface: "mcp"`,
  `mode: "grouped"`, `response_mode: "compact"`, and optionally a narrow
  `query` to find the current operation name.

When a tool is operation-backed, `toolbox.describe` includes the operation name,
category, grant policy, and a pointer to `operation.describe`. Agents should ask
for exact tool names and escalate to `operation.describe` only when they need
the full schema or examples.

## Design Review

This model keeps the current StackOS principles intact:

- **Agent decides**: workflows and direct actions are selected from user intent;
  StackOS does not infer strategy.
- **One execution substrate**: `action.run` and `action.execute` both call the
  same daemon-side action repository and connectors.
- **Separated secret boundaries**: provider credentials remain daemon-held
  behind `credential_ref`; authorized payload values use one write-only
  `secret.set` call and symbolic action markers, then resolve only at connector
  dispatch.
- **Audited simple calls**: direct actions still write `action_calls` rows.
- **Workflow memory preserved**: multi-step work still uses templates, run
  plans, resources, artifacts, context, learnings, experiments, and decisions.
- **Project isolation**: bridge clients operate inside the project derived from
  the current workspace and cannot switch to another project by accident.

Known operational note: live provider behavior still needs real provider
credentials for production contract testing. Mock/local tests prove the action
registry, adapters, auth boundary, audit path, and compact response behavior.
