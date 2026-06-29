"""Project setup, budgets, and schedule operation contracts."""

from __future__ import annotations

from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.projects import (
    BudgetListInput,
    BudgetQueryProjectInput,
    BudgetSetInput,
    BudgetUpdateInput,
    ProjectIdInput,
    ProjectUpdateInput,
    ScheduleListInput,
    ScheduleRemoveInput,
    ScheduleSetInput,
    ScheduleToggleInput,
    _budget_list,
    _budget_query_project,
    _budget_set,
    _budget_update,
    _project_delete,
    _project_update,
    _schedule_list,
    _schedule_remove,
    _schedule_set,
    _schedule_toggle,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample
from stackos.repositories.projects import IntegrationBudgetOut, ProjectOut, ScheduledJobOut


def operation_specs():
    return [
        operation_spec(
            name="project.update",
            summary="Patch safe project metadata such as display name.",
            input_model=ProjectUpdateInput,
            output_model=WriteEnvelope[ProjectOut],
            handler=_project_update,
            purpose=(
                "Use this for explicit operator/admin project metadata updates, including "
                "renaming a project that was created with a poor inferred name."
            ),
            when_to_use=(
                "A local admin needs to change safe project fields.",
                "The operator wants to correct the business-facing project name without "
                "changing the workspace folder binding.",
            ),
            prerequisites=(
                "Requires an existing project_id and an explicit patch.",
                "Changing project metadata does not move or rebind workspace folders.",
            ),
            examples=(
                OperationExample(
                    title="Rename project",
                    arguments={"project_id": 1, "patch": {"name": "Acme Pro"}},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-project-write",
        ),
        operation_spec(
            name="project.delete",
            summary="Archive one project.",
            input_model=ProjectIdInput,
            output_model=WriteEnvelope[ProjectOut],
            handler=_project_delete,
            purpose="Use this only for explicit local-admin project cleanup.",
            when_to_use=("An operator explicitly asks to archive a project.",),
            prerequisites=("Confirm the project identity before deleting.",),
            examples=(OperationExample(title="Archive project", arguments={"project_id": 2}),),
            mutating=True,
            grant_policy="local-admin-project-write",
        ),
        operation_spec(
            name="budget.list",
            summary="List integration budgets configured for one project.",
            input_model=BudgetListInput,
            output_model=list[IntegrationBudgetOut],
            handler=_budget_list,
            purpose="Use this during setup or workflow readiness checks to inspect spend limits.",
            when_to_use=("A workflow/action may use a budgeted provider.",),
            examples=(OperationExample(title="List budgets", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="budget.queryProject",
            summary="Read one project budget by provider kind.",
            input_model=BudgetQueryProjectInput,
            output_model=IntegrationBudgetOut,
            handler=_budget_query_project,
            purpose="Use this when an agent needs the exact limit for one budget kind.",
            when_to_use=("An action or workflow names a provider budget kind.",),
            examples=(
                OperationExample(
                    title="Read provider budget",
                    arguments={"project_id": 1, "kind": "openai"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="budget.set",
            summary="Create or replace a project integration budget.",
            input_model=BudgetSetInput,
            output_model=WriteEnvelope[IntegrationBudgetOut],
            handler=_budget_set,
            purpose="Use this for explicit setup of provider spend and QPS limits.",
            when_to_use=("An operator asks to configure project budget limits.",),
            prerequisites=("Requires explicit project_id, kind, and monthly budget.",),
            examples=(
                OperationExample(
                    title="Set monthly budget",
                    arguments={"project_id": 1, "kind": "openai", "monthly_budget_usd": 100},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="budget.update",
            summary="Update a project integration budget.",
            input_model=BudgetUpdateInput,
            output_model=WriteEnvelope[IntegrationBudgetOut],
            handler=_budget_update,
            purpose="Use this for explicit edits to provider spend and QPS limits.",
            when_to_use=("An operator asks to change existing project budget limits.",),
            prerequisites=("Requires explicit project_id, kind, and monthly budget.",),
            examples=(
                OperationExample(
                    title="Update monthly budget",
                    arguments={"project_id": 1, "kind": "openai", "monthly_budget_usd": 150},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="schedule.list",
            summary="List project scheduled jobs.",
            input_model=ScheduleListInput,
            output_model=list[ScheduledJobOut],
            handler=_schedule_list,
            purpose="Use this to inspect daemon-managed recurring project jobs.",
            examples=(OperationExample(title="List schedules", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="schedule.set",
            summary="Create or replace a project scheduled job.",
            input_model=ScheduleSetInput,
            output_model=WriteEnvelope[ScheduledJobOut],
            handler=_schedule_set,
            purpose="Use this for explicit local-admin recurring job setup.",
            prerequisites=("Requires a cron expression supplied by the operator or workflow.",),
            examples=(
                OperationExample(
                    title="Schedule weekly review",
                    arguments={
                        "project_id": 1,
                        "kind": "weekly-review",
                        "cron_expr": "0 9 * * 1",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="schedule.toggle",
            summary="Enable or disable one scheduled job.",
            input_model=ScheduleToggleInput,
            output_model=WriteEnvelope[ScheduledJobOut],
            handler=_schedule_toggle,
            purpose="Use this for explicit local-admin schedule activation changes.",
            examples=(
                OperationExample(
                    title="Disable job",
                    arguments={"project_id": 1, "job_id": 4, "enabled": False},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="schedule.remove",
            summary="Disable one scheduled job.",
            input_model=ScheduleRemoveInput,
            output_model=WriteEnvelope[ScheduledJobOut],
            handler=_schedule_remove,
            purpose="Use this when an operator explicitly removes a daemon schedule.",
            examples=(
                OperationExample(title="Remove job", arguments={"project_id": 1, "job_id": 4}),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
    ]


__all__ = ["operation_specs"]
