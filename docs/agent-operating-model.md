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

Use `action.run` when the user asked for one concrete action:

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

When a host supports skills, workflow templates may recommend `stackos:stackos`.
That skill teaches agents how to use StackOS MCP, operations, workflow
templates, run plans, tracker tasks/tickets, dependencies, and evidence. The
main agent decides whether to load it, but the workflow response makes the
expectation visible.

## Workflow Path

```text
agent intent
-> operation.describe / action.describe when the contract is not already clear
-> agentPreset.resolveForWorkflow when setting up workflow-specific roles
-> workflow template or agent-authored run plan
-> runPlan.validate/create/start
-> tracker.brief or tracker.next for bounded work context
-> runPlan.claimStep
-> step-granted tools through direct MCP or toolbox.call
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

## Direct Action Path

```text
agent receives one explicit user request
-> operation.describe / action.describe when the contract is not already clear
-> toolProfile.resolve when provider/auth/profile selection is needed
-> action.describe / action.validate when needed
-> action.run
-> daemon-side credential resolution
-> connector execution
-> compact result and action_calls audit row
```

`action.run` is available through MCP, REST, and CLI. It still uses the same
connector, auth, redaction, idempotency, cost, and audit code as
`action.execute`.

Direct non-read actions require:

- `confirm_direct=true`
- `intent_summary`
- an explicit `action_ref` or plugin/action pair
- only safe refs, such as `credential_ref`, never secret values

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
-> action.run or local work
-> tracker.patch / tracker.updateTicket
```

Agent-facing MCP responses are compact by default for noisy discovery/setup
tools. Pass `response_mode=standard` when the full normal daemon payload is
needed, or `response_mode=verbose`/`verbose=true` for diagnostics on tools that
support it. This keeps simple MCP calls from flooding the context window while
preserving the richer REST/UI contracts.

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

The agent-facing bridge hides broad project switching, deletion, and admin-only
changes, but exposes the narrow setup path agents need: `workspace.resolve`,
`workspace.bootstrap`, `workspace.connect`, `workspace.listBindings`, and safe
project discovery/creation tools. If a repo is already bound, the bridge
injects `project_id` and relaxes the advertised schemas so agents do not have to
keep repeating it. If a caller explicitly passes a different `project_id`, the
bridge refuses the call.

Workspace hints are also scoped. The bridge injects its current
`cwd`, `repo_fingerprint`, `git_remote_url`, `last_known_root`, runtime, and
session id where relevant. Calls that try to resolve or connect another
workspace are refused by the bridge.

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

1. Call `workspace.resolve`.
2. If it returns `needs_connect=true`, read its `next_step`; the normal next
   call is `workspace.bootstrap`.
3. Call `workspace.bootstrap` from the current repo/directory. It creates or
   reuses one project for that workspace root and stores the binding in the
   daemon DB. Repeating it for the same root/fingerprint returns the existing
   project and binding.
4. Use `project.list`, `project.create`, or `workspace.connect` only when the
   operator intentionally wants to choose a specific existing project or supply
   explicit project metadata.
5. Continue through `workspace.startSession`; future calls resolve the project
   automatically from the current workspace.

The bridge sends a path fingerprint by default:
`path:<sha256(workspace_root)[:24]>`. If git is unavailable, this path identity
is enough for a local directory. If git or a remote is added later, bootstrap can
attach that metadata to the existing binding when the same root is seen. A
moved non-git directory without a remote looks like a new workspace unless the
agent intentionally reconnects or rebinds it.

This keeps normal agents focused on the project attached to their workspace and
still gives them a complete MCP-native path when no binding exists yet.

## Design Review

This model keeps the current StackOS principles intact:

- **Agent decides**: workflows and direct actions are selected from user intent;
  StackOS does not infer strategy.
- **One execution substrate**: `action.run` and `action.execute` both call the
  same daemon-side action repository and connectors.
- **No secrets**: agents pass credential refs; credentials resolve only inside
  the daemon process.
- **Audited simple calls**: direct actions still write `action_calls` rows.
- **Workflow memory preserved**: multi-step work still uses templates, run
  plans, resources, artifacts, context, learnings, experiments, and decisions.
- **Project isolation**: bridge clients operate inside the project derived from
  the current workspace and cannot switch to another project by accident.

Known operational note: live provider behavior still needs real provider
credentials for production contract testing. Mock/local tests prove the action
registry, adapters, auth boundary, audit path, and compact response behavior.
