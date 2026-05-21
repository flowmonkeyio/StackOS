# Run Plans

A run plan is a concrete, auditable execution plan for one run. It is usually
derived from a workflow template, then authored by an agent for the project,
goal, context, and approval requirements at hand.

## Lifecycle

1. Use the bootstrap/setup MCP surface to choose or create the project and set
   non-secret configuration such as schedules and budgets.
2. Discover or describe a template with `workflowTemplate.*`.
3. Create a concrete plan with `runPlan.create`.
4. Validate it with `runPlan.validate`.
5. Start it with `runPlan.start`.
6. Claim and record steps through run-plan controller tools.
7. Inspect execution through `runPlan.get`, `run.get`, and `run.list`.

## Grants

Run-plan grants are frozen in `run_plans.grant_snapshot_json`.

The daemon dispatcher requires:

- a `stackos/run-plan-controller` token
- a started plan
- exactly one running step
- matching `project_id`
- an explicit grant for the requested tool/action/resource/context fields

Direct context reads use the safe field set. Advanced context fields,
resource/artifact mutations, memory writes, and `action.execute` require
step-scoped grants.

The run plan is not used for StackOS bootstrap itself. Creating a project,
selecting it, setting non-secret budget/schedule configuration, and creating or
starting the run plan are direct setup operations. Once execution begins, the
run-plan grant snapshot is the boundary for workflow writes and tool execution.

## Audit

Runs store top-level execution state. Run steps and tool calls store the
fine-grained audit trail for agent activity and daemon-side execution.

Use `run.insertStep`, `run.recordStepCall`, `run.listSteps`, and
`run.listStepCalls` for audit grain outside the run-plan controller path.

## UI

The Runs UI renders generic run and run-plan state. Workflow-specific views
should be plugin/resource renderers unless a signed-off task scopes a bespoke
page.
