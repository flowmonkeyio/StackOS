# Run Plans

Run plans are concrete StackOS execution records. A workflow template is the
reusable base; a run plan is the agent-authored, one-run plan that freezes what
will be attempted for this project at this moment.

The boundary is strict:

- Agents and humans choose strategy, providers, variants, payloads, approvals,
  and next actions.
- StackOS stores the plan, validates shape, gates approvals, links context,
  opens the audit run, records step results, and redacts secrets.
- StackOS does not decide winners, optimize campaigns, pick topics, or execute
  generic actions in D07.

## Layers

```text
Project
  durable context, resources, artifacts, learnings, experiments, decisions

Workflow Template
  reusable setup/instructions/contracts for a class of work

Run Plan
  concrete steps, selected context, grants, approval gates, payload config

Run
  existing audit row opened when the run plan starts
```

Run plans are additive sidecars. They link to `runs`, `context_snapshots`, and
workflow template versions, but they do not replace or delete old
`procedure_run_steps`, article, SEO, or integration tables.

## What A Run Plan Stores

Run plans use `schema_version: stackos.run-plan.v1` and can store:

- concrete step order and dependency refs
- selected template snapshot and template source
- selected context snapshot id plus compact context filters
- input values and expected output contracts
- capability/action/resource refs
- concrete action payload configuration authored by the agent
- opaque `credential_ref` values
- approval gates and approval decisions
- step results, errors, and audit timestamps

Run plans must not store API keys, OAuth tokens, bearer strings, passwords, or
refresh tokens. Agents pass opaque credential refs; the daemon resolves actual
secrets inside the auth provider layer.

## Lifecycle

`runPlan.create` creates a draft run plan from either:

- an existing workflow template such as `core.project-memory-review`
- a complete `run_plan_json` object supplied by the agent

`runPlan.start` requires both `project_id` and `run_plan_id`, freezes the plan
into execution state, and opens a linked `runs` audit row. It returns the
controller `run_token` only on the first successful start; repeat calls are
rejected so tokens cannot be recovered by probing an existing plan id. The
linked run currently uses the existing run-kind surface instead of altering the
legacy `runs.kind` enum; the run metadata records `stackos_type=run-plan` and
`run_plan_id`.

`runPlan.claimStep` and `runPlan.recordStep` move concrete step state. They do
not execute actions. The agent remains responsible for calling the appropriate
tools and then recording the outcome.

When all steps are successful or skipped, the run plan is marked `completed`
and the linked run is finished as `success`. A failed step marks the run plan
and linked run failed. These are mechanical lifecycle transitions, not business
decisions.

## Approval Gates

Approvals are explicit records in `approval_requests`. A step with
`approval_refs` cannot be claimed until the referenced approval records are
approved.

`runPlan.update` is intentionally narrow in D07. It updates run-plan metadata
and approval gate state, but it is admin/human-scoped rather than granted to the
started run-plan controller token. A run-plan agent cannot approve its own
gates. The tool does not mutate the concrete step list after start, and it does
not execute external actions.

## Context Linkage

Agents can create or select a `context_snapshot` before creating a run plan.
When the run plan starts, StackOS links that snapshot to the new audit run if it
was not already linked. This lets future agents retrieve the relevant past
context without loading every prior run into the prompt.

## MCP Surface

Direct agent-visible tools:

- `runPlan.create`
- `runPlan.validate`
- `runPlan.start`
- `runPlan.get`
- `runPlan.list`

Run-token-scoped tools:

- `runPlan.claimStep`
- `runPlan.recordStep`

Admin/human-scoped tool:

- `runPlan.update`

The scoped tools are granted to the internal `stackos/run-plan-controller`
skill bound to the run token returned by `runPlan.start`. This keeps normal
system calls from mutating started plan steps without a run context, while
keeping approval decisions outside the executing agent's grant.
The controller token is also bound to the exact linked `runs.id`: a token from
one started plan cannot claim or record another plan, even inside the same
project.

## Clean Cut

D07 creates only these sidecar tables:

- `run_plans`
- `run_plan_steps`
- `approval_requests`

It does not drop, rewrite, or clean up prior SEO/procedure/content-stack
tables. If a destructive cleanup is ever needed, it needs a separate signed-off
ticket with backup/restore and verification steps.
