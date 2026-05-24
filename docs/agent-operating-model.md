# Agent Operating Model

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

## Workflow Path

```text
agent intent
-> workflow template or agent-authored run plan
-> runPlan.validate/create/start
-> runPlan.claimStep
-> step-granted tools through direct MCP or toolbox.call
-> action.execute for provider actions
-> runPlan.recordStep
-> resources/artifacts/learnings/experiments/decisions as needed
```

`action.execute` remains run-plan-step gated. The active step and frozen grant
snapshot must both allow the exact `action_ref`. It returns the full redacted
action execution shape because workflow steps usually need complete audit data.

## Direct Action Path

```text
agent receives one explicit user request
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
- `idempotency_key`
- an explicit `action_ref` or plugin/action pair
- only safe refs, such as `credential_ref`, never secret values

Responses are compact by default. Agents should request `verbose=true` only
when they need the full redacted action call and output payload for debugging or
handoff. This keeps simple MCP calls from flooding the context window.

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

The agent-facing bridge hides broad project listing, creation, switching, and
deletion. If a repo is already bound, the bridge injects `project_id` and
relaxes the advertised schemas so agents do not have to keep repeating it. If a
caller explicitly passes a different `project_id`, the bridge refuses the call.

Workspace hints are also scoped. The bridge injects its current
`cwd`, `repo_fingerprint`, `git_remote_url`, `last_known_root`, runtime, and
session id where relevant. Calls that try to resolve or connect another
workspace are refused by the bridge.

## Setup Path

If a repository is not bound yet, setup should be explicit:

1. Create or choose the StackOS project through the UI, CLI, REST-admin path, or
   another operator-approved setup flow.
2. From the repository, call `workspace.connect` with that project id.
3. Continue through `workspace.startSession`; future calls resolve the project
   automatically from the current repo.

This keeps normal agents focused on the project attached to their workspace
instead of browsing unrelated projects.

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
