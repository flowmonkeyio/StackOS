# Run Plans

A run plan is a concrete, auditable execution plan for one run. It is usually
derived from a workflow template, then authored by an agent for the project,
goal, context, and approval requirements at hand.

The mechanics are intentionally agent-first. Humans and scripts can bootstrap,
inspect, approve, and administer through UI, CLI, or REST, but the normal
execution user is an agent creating a precise run plan, claiming steps, calling
granted actions, and recording results.

## Lifecycle

1. Use the bootstrap/setup MCP surface to choose or create the project and set
   non-secret configuration such as schedules and budgets.
2. Discover or describe a template with `workflowTemplate.*`.
3. Draft a concrete plan from the template or an agent-authored object.
4. Validate it with `runPlan.validate`.
5. Create it with `runPlan.create` after reviewing errors and warnings.
6. Start it with `runPlan.start` and keep the returned `run_token`.
7. Claim and record steps through run-plan controller tools using that
   `run_token`.
8. Inspect execution through `runPlan.get`, `run.get`, and `run.list`.

`runPlan.create` also mirrors the plan into the project task tracker: one task
for the run plan and one ticket per concrete step. Agents can use
`tracker.next`, `tracker.brief`, and `tracker.verify` to navigate work without
scanning the full run-plan payload every time.

## Grants

Run-plan grants are frozen in `run_plans.grant_snapshot_json`.

The daemon dispatcher requires:

- the `run_token` returned by `runPlan.start`
- a started plan
- exactly one running step
- matching `project_id`
- an explicit grant for the requested tool/action/resource/context fields

## Validation Warnings

`runPlan.validate` separates structural validity from executable readiness.
`valid=true` means the plan shape, refs, grants, approvals, and secret hygiene
are acceptable. The `warnings` array tells the agent where a valid plan may
still fail during execution because a step names work contracts but lacks the
MCP grants needed by `runPlan.claimStep` and later tool calls.

Common warning codes:

- `missing_action_execute_grant`: a step declares `action_refs`, but no
  `action.execute` grant covers the same concrete action refs.
- `missing_resource_upsert_grant`: a step declares `resource_refs`, but no
  `resource.upsert` grant exists for that step.
- `missing_context_query_grant`: a step declares `context_refs`, but no
  `context.query` grant with explicit `sources` and `fields` exists.
- `missing_artifact_create_grant`: a step declares `output_refs`, but no
  `artifact.create` grant exists for persisted output artifacts.

Template-derived plans often start with planning contract refs, such as
`send_email`, before the agent resolves them to executable action refs, such as
`communications.smtp.email.send`. Treat warnings as the review checklist before
`runPlan.create`: resolve contract refs, add the exact `mcp_tool_grants`, then
validate again.

Direct context reads use the safe field set. Advanced context fields,
resource/artifact mutations, memory writes, workflow-scoped communication sends,
and `action.execute` require step-scoped grants.

For `action.execute`, the active step must declare the target action ref and
the matching `mcp_tool_grants` entry must include that exact ref in
`action_refs`. A step that declares `utils.image.generate` cannot execute a
different action, and a grant snapshot that names a different action cannot be
used to satisfy the step.

For `communication.send`, the matching `mcp_tool_grants` entry must include
`targets` such as `communication-target:ops-alerts`. This keeps workflow
notifications and handoffs explicit while still allowing simple non-workflow
messages through the direct `communication.send` path.

For `communication.reply`, the matching `mcp_tool_grants` entry must include
`sources` such as `telegram-bot`, `slack-bot`, or a specific stored source
surface. The daemon checks the request origin before allowing the reply.

The run plan is not used for StackOS bootstrap itself. Creating a project,
selecting it, setting non-secret budget/schedule configuration, and creating or
starting the run plan are direct setup operations. Once execution begins, the
run-plan grant snapshot is the boundary for workflow writes and tool execution.

## Audit

Runs store top-level execution state. Run steps and tool calls store the
fine-grained audit trail for agent activity and daemon-side execution.

Use `run.insertStep`, `run.recordStepCall`, `run.listSteps`, and
`run.listStepCalls` for audit grain outside the run-plan controller path.

Use [`task-tracker.md`](./task-tracker.md) for project work state. The tracker
is not the grant boundary; it is the navigation and lifecycle mirror for tasks,
tickets, dependencies, and verification.

## UI

The Runs UI renders generic run and run-plan state. The Tasks UI renders the
project tracker with workflow filters, status filters, dependency graph, and
ticket detail navigation. Workflow-specific views should be plugin/resource
renderers unless a signed-off task scopes a bespoke page.
