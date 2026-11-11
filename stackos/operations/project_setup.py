"""Project setup, budgets, and schedule operation contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.config import Settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.streaming import ProgressEmitter
from stackos.mcp.tools.projects import (
    BudgetListInput,
    BudgetQueryProjectInput,
    BudgetSetInput,
    BudgetUpdateInput,
    ProjectDeleteInput,
    ProjectMoveInput,
    ProjectUpdateInput,
    ScheduleListInput,
    ScheduleRemoveInput,
    ScheduleRunsInput,
    ScheduleSetInput,
    ScheduleToggleInput,
    _budget_list,
    _budget_query_project,
    _budget_set,
    _budget_update,
    _project_delete,
    _project_move,
    _project_update,
    _schedule_list,
    _schedule_remove,
    _schedule_runs,
    _schedule_set,
    _schedule_toggle,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.project_cleanup import ProjectCleanupResult
from stackos.repositories.base import Page, RepositoryError, ValidationError
from stackos.repositories.projects import (
    IntegrationBudgetOut,
    ProjectOut,
    ProjectRepository,
    ScheduledJobOut,
    ScheduledRunOut,
    ScheduleRemovalOut,
)
from stackos.repositories.resources import ArtifactRepository


class ProjectFileOwnershipInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(strict=True, ge=1)
    project_slug: str = Field(min_length=1, max_length=80)
    artifact_id: int = Field(strict=True, ge=1)
    expected_uri: str = Field(min_length=1)
    expected_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    confirm_ownership: bool = Field(strict=True)
    response_mode: Literal["compact", "raw", "standard", "verbose"] | None = None


class ProjectFileOwnershipResult(BaseModel):
    project_id: int
    artifact_id: int
    operation_id: str
    reconciled: bool


async def _project_reconcile_file_ownership(
    inp: ProjectFileOwnershipInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ProjectFileOwnershipResult]:
    if not (
        ctx.extras.get("surface") == "rest"
        and ctx.extras.get("trusted_local_admin") is True
        and ctx.extras.get("auth_scope") == "daemon-admin"
    ):
        raise ToolNotGrantedError(
            "file ownership reconciliation requires local-admin REST authority"
        )
    settings = ctx.extras.get("settings")
    if not isinstance(settings, Settings):
        raise ValidationError("project cleanup settings are unavailable")
    try:
        ArtifactRepository(ctx.session).reconcile_generated_file(
            **inp.model_dump(
                include={
                    "project_id",
                    "project_slug",
                    "artifact_id",
                    "expected_uri",
                    "expected_sha256",
                    "confirm_ownership",
                }
            ),
            asset_root=settings.generated_assets_dir,
        )
    except RepositoryError:
        raise
    except Exception as exc:
        raise RepositoryError(
            "file ownership reconciliation could not complete",
            data={
                "project_id": inp.project_id,
                "artifact_id": inp.artifact_id,
                "error_code": type(exc).__name__,
            },
        ) from None
    project = ProjectRepository(ctx.session).get(inp.project_id)
    return WriteEnvelope[ProjectFileOwnershipResult](
        data=ProjectFileOwnershipResult(
            project_id=inp.project_id,
            artifact_id=inp.artifact_id,
            operation_id=(project.cleanup_json or {})["operation_id"],
            reconciled=True,
        ),
        project_id=inp.project_id,
    )


def operation_specs():
    return [
        operation_spec(
            name="project.update",
            browser_safe=True,
            summary="Patch safe project metadata or explicitly select its primary folder binding.",
            input_model=ProjectUpdateInput,
            output_model=WriteEnvelope[ProjectOut],
            handler=_project_update,
            purpose=(
                "Use this for explicit operator/admin project metadata updates, including "
                "renaming a project that was created with a poor inferred name."
            ),
            when_to_use=(
                "An operator wants to rename the bound/selected project.",
                "The operator wants to correct the business-facing project name without "
                "changing the workspace folder binding.",
            ),
            prerequisites=(
                "Requires an existing project_id and an explicit patch.",
                "Changing project metadata does not move or rebind workspace folders.",
                "Display names are trimmed, nonblank and at most 200 characters; "
                "duplicates are allowed.",
                "Agents and console callers may patch only name. Catalog scope is REST-admin only.",
                "primary_workspace_binding_id selects an existing same-project directory "
                "binding; null leaves the project without a local folder. Existing schedules "
                "and task/run execution snapshots retain their saved targets.",
            ),
            examples=(
                OperationExample(
                    title="Rename project",
                    arguments={"project_id": 1, "patch": {"name": "Acme Pro"}},
                ),
                OperationExample(
                    title="Select a previously confirmed project folder",
                    arguments={"project_id": 1, "patch": {"primary_workspace_binding_id": 2}},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="project.move",
            browser_safe=True,
            summary="Move one visible project to the top or before/after a visible anchor.",
            input_model=ProjectMoveInput,
            output_model=WriteEnvelope[ProjectOut],
            handler=_project_move,
            purpose="Persist one bounded project move without replacing a paginated catalog.",
            prerequisites=(
                "Use project_id as the target; names and slugs never select the move target.",
                "top omits anchor_id; before/after require an active visible anchor_id.",
                "Bound agents can move only their injected project; "
                "catalog scope requires local-admin REST authority.",
            ),
            returns=(
                "The moved project, including its stored order_index; "
                "no-op moves preserve all data.",
            ),
            examples=(
                OperationExample(
                    title="Move bound project to top",
                    arguments={"project_id": 1, "placement": "top"},
                ),
                OperationExample(
                    title="Move selected catalog project",
                    arguments={
                        "project_id": 2,
                        "placement": "after",
                        "anchor_id": 1,
                        "scope": "catalog",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="project.delete",
            summary="Permanently delete a confirmed project and its owned StackOS data.",
            input_model=ProjectDeleteInput,
            output_model=WriteEnvelope[ProjectCleanupResult],
            handler=_project_delete,
            purpose="Delete live project-owned state through the canonical cleanup service.",
            when_to_use=("An operator explicitly authorizes permanent project deletion.",),
            prerequisites=(
                "Confirm permanent=true, exact project_id and immutable project_slug.",
                "Stop owned work using its existing controls; deletion never aborts or kills work.",
                "Console requires catalog scope; bound agents can target only their project.",
            ),
            returns=(
                "Typed blocked/incomplete/deleted/missing outcome, blockers and retry context.",
                "Incomplete retains project ID and operation_id; retry this same operation.",
                "Ownership gaps require admin project.reconcileFileOwnership before retry.",
                "Repositories, shared data, external exports and existing backups are preserved.",
                "Retries revalidate confirmation and observe current state, including missing.",
            ),
            examples=(
                OperationExample(
                    title="Permanently delete confirmed project",
                    arguments={
                        "project_id": 2,
                        "confirmation": {
                            "permanent": True,
                            "project_id": 2,
                            "project_slug": "acme",
                        },
                    },
                ),
            ),
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True, browser_safe=True, path="/api/v1/operations/project.delete/call"
                ),
                cli=OperationSurface(enabled=True, command="ops call project.delete"),
            ),
            response_policy=OperationResponsePolicy(
                allowed_modes=("compact", "raw"),
                ack_safe=False,
                compact_notes=(
                    "Keep the bounded cleanup result, including blockers and ownership gaps.",
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            idempotency_policy="handler",
        ),
        OperationSpec(
            name="project.reconcileFileOwnership",
            summary="Confirm exact ownership of a legacy file blocking project cleanup.",
            input_model=ProjectFileOwnershipInput,
            output_model=WriteEnvelope[ProjectFileOwnershipResult],
            handler=_project_reconcile_file_ownership,
            purpose="Resolve a stopped-cleanup ownership gap through ArtifactRepository.",
            prerequisites=(
                "Requires daemon-admin REST/CLI authority; unavailable to MCP and browser console.",
                "Review exact project ID/slug, artifact ID/URI and current file SHA-256; "
                "then set confirm_ownership=true.",
                "Deletion confirmation and editable artifact metadata do not prove file ownership.",
            ),
            returns=(
                "Reconciled artifact and cleanup operation ID; retry project.delete separately.",
            ),
            examples=(
                OperationExample(
                    title="Reconcile reviewed legacy file",
                    arguments={
                        "project_id": 2,
                        "project_slug": "acme",
                        "artifact_id": 42,
                        "expected_uri": "/generated-assets/openai-images/legacy.png",
                        "expected_sha256": "0123456789abcdef" * 4,
                        "confirm_ownership": True,
                    },
                ),
            ),
            surfaces=OperationSurfaces(
                rest=OperationSurface(
                    enabled=True, path="/api/v1/operations/project.reconcileFileOwnership/call"
                ),
                cli=OperationSurface(
                    enabled=True, command="ops call project.reconcileFileOwnership"
                ),
            ),
            mutating=True,
            grant_policy="local-admin-project-write",
            idempotency_policy="handler",
            response_policy=OperationResponsePolicy(allowed_modes=("compact", "raw")),
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
            name="schedule.runs",
            summary="Page actual scheduled workflows and their canonical task usage.",
            input_model=ScheduleRunsInput,
            output_model=Page[ScheduledRunOut],
            handler=_schedule_runs,
            purpose="Inspect linked scheduled executions, including history after removal.",
            returns=(
                "Skipped ticks create no history row. Task status and usage remain canonical.",
            ),
            examples=(
                OperationExample(
                    title="Read scheduled runs", arguments={"project_id": 1, "limit": 50}
                ),
            ),
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
            browser_safe=True,
            summary="Create or replace a project scheduled job.",
            input_model=ScheduleSetInput,
            output_model=WriteEnvelope[ScheduledJobOut],
            handler=_schedule_set,
            purpose="Use this for explicit local-admin recurring job setup.",
            prerequisites=(
                "Use a stable kind/key; set replaces the complete configuration.",
                "Enabling requires name, IANA timezone, existing workflow, concrete inputs "
                "and execution_json with an existing project_dir and configured suitable roles.",
                "Use agents to bind declared workflow roles to project source_ref selections; "
                "step_agent_assignments maps template step IDs to roles when selection "
                "is ambiguous. "
                "Codex schedules store references, never copied source instructions.",
                "Strict preflight is read-only; it creates no request, run plan or task. "
                "Disabled incomplete drafts remain inert. Normal grants and approvals still apply.",
            ),
            examples=(
                OperationExample(
                    title="Schedule weekly review",
                    arguments={
                        "project_id": 1,
                        "kind": "weekly-review",
                        "cron_expr": "0 9 * * 1",
                        "name": "Weekly review",
                        "timezone": "America/Los_Angeles",
                        "template_key": "core.project-memory-review",
                        "inputs_json": {},
                        "execution_json": {
                            "project_dir": "/work/project",
                            "agents": {
                                "project-memory-review": {
                                    "source_ref": {
                                        "host_key": "codex",
                                        "native_name": "memory-reviewer",
                                        "source_path": ".codex/agents/memory-reviewer.toml",
                                    }
                                }
                            },
                        },
                        "enabled": False,
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="schedule.toggle",
            browser_safe=True,
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
            browser_safe=True,
            summary="Remove one scheduled job and its timer; preserve execution history.",
            input_model=ScheduleRemoveInput,
            output_model=WriteEnvelope[ScheduleRemovalOut],
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
