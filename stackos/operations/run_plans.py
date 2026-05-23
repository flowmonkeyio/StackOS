"""StackOS run-plan operation registrations."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.db.models import (
    ApprovalRequestStatus,
    RunPlanStatus,
    RunPlanStepStatus,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page
from stackos.repositories.run_plans import (
    RunPlanOut,
    RunPlanRepository,
    RunPlanStartOut,
    RunPlanStepOut,
    RunPlanSummaryOut,
)
from stackos.workflows import RunPlanValidationOut


class RunPlanValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"template_key": "core.project-memory-review"}},
    )

    project_id: int | None = None
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None


class RunPlanCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "template_key": "core.project-memory-review"}
        },
    )

    project_id: int
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    key: str | None = None
    title: str | None = None
    inputs_json: dict[str, Any] | None = None
    context_snapshot_id: int | None = None
    selected_context_json: dict[str, Any] | None = None
    created_by: str | None = None
    metadata_json: dict[str, Any] | None = None


class RunPlanStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "run_plan_id": 1}},
    )

    project_id: int
    run_plan_id: int


class RunPlanGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"run_plan_id": 1}})

    run_plan_id: int


class RunPlanListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None
    status: RunPlanStatus | None = None
    template_key: str | None = None
    limit: int | None = None
    after_id: int | None = None


class RunPlanUpdateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "approval_key": "launch-review",
                "approval_status": "approved",
            }
        },
    )

    run_plan_id: int
    metadata_json: dict[str, Any] | None = None
    approval_key: str | None = None
    approval_status: ApprovalRequestStatus | None = None
    decided_by: str | None = None
    decision_json: dict[str, Any] | None = None


class RunPlanClaimStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1, "step_id": "review"}},
    )

    run_plan_id: int
    step_id: str | None = None
    claimed_by: str | None = None


class RunPlanRecordStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "step_id": "review",
                "status": "success",
                "result_json": {"summary": "done"},
            }
        },
    )

    run_plan_id: int
    step_id: str
    status: RunPlanStepStatus
    result_json: dict[str, Any] | None = None
    error: str | None = None


async def run_plan_validate(
    inp: RunPlanValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanValidationOut:
    return RunPlanRepository(ctx.session).validate_plan(
        run_plan_json=inp.run_plan_json,
        template_key=inp.template_key,
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )


async def run_plan_create(
    inp: RunPlanCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).create(
        project_id=inp.project_id,
        run_plan_json=inp.run_plan_json,
        template_key=inp.template_key,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
        key=inp.key,
        title=inp.title,
        inputs_json=inp.inputs_json,
        context_snapshot_id=inp.context_snapshot_id,
        selected_context_json=inp.selected_context_json,
        created_by=inp.created_by,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_start(
    inp: RunPlanStartInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanStartOut]:
    env = RunPlanRepository(ctx.session).start(inp.run_plan_id, project_id=inp.project_id)
    return WriteEnvelope[RunPlanStartOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


async def run_plan_get(
    inp: RunPlanGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanOut:
    return RunPlanRepository(ctx.session).get(inp.run_plan_id)


async def run_plan_list(
    inp: RunPlanListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[RunPlanSummaryOut]:
    return RunPlanRepository(ctx.session).list(
        project_id=inp.project_id,
        status=inp.status,
        template_key=inp.template_key,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def run_plan_update(
    inp: RunPlanUpdateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).update(
        run_plan_id=inp.run_plan_id,
        metadata_json=inp.metadata_json,
        approval_key=inp.approval_key,
        approval_status=inp.approval_status,
        decided_by=inp.decided_by,
        decision_json=inp.decision_json,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_claim_step(
    inp: RunPlanClaimStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanStepOut]:
    env = RunPlanRepository(ctx.session).claim_step(
        run_plan_id=inp.run_plan_id,
        run_id=ctx.run_id,
        step_id=inp.step_id,
        claimed_by=inp.claimed_by,
    )
    return WriteEnvelope[RunPlanStepOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


async def run_plan_record_step(
    inp: RunPlanRecordStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).record_step(
        run_plan_id=inp.run_plan_id,
        run_id=ctx.run_id,
        step_id=inp.step_id,
        status=inp.status,
        result_json=inp.result_json,
        error=inp.error,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


def _surfaces(name: str, command: str | None = None) -> OperationSurfaces:
    rest_path = f"/api/v1/operations/{name}/call"
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=rest_path),
        cli=OperationSurface(enabled=True, command=command or f"ops call {name}"),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="runPlan.validate",
            summary="Validate a concrete run plan or template-derived plan without saving it.",
            input_model=RunPlanValidateInput,
            output_model=RunPlanValidationOut,
            handler=run_plan_validate,
            surfaces=_surfaces("runPlan.validate", "run-plans validate"),
            purpose=(
                "Use this before creating a run plan to verify the workflow shape, "
                "grants, approvals, context filters, and secret hygiene."
            ),
            when_to_use=(
                "An agent has authored run_plan_json and wants a schema and policy check.",
                "A script wants to verify that a workflow template can become a run plan.",
            ),
            prerequisites=(
                "Pass run_plan_json for an explicit plan or template_key for a "
                "template-derived plan.",
                "Pass project_id when the validation depends on project templates.",
            ),
            returns=(
                "valid=true with the normalized plan when validation passes.",
                "Structured validation issues with paths and machine-readable codes.",
            ),
            examples=(
                OperationExample(
                    title="Validate a template-derived plan",
                    arguments={"project_id": 1, "template_key": "core.project-memory-review"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.create",
            summary="Create a durable run plan from a template or explicit plan JSON.",
            input_model=RunPlanCreateInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_create,
            surfaces=_surfaces("runPlan.create", "run-plans create"),
            purpose=(
                "Use this after selecting or authoring the workflow setup. Creation freezes "
                "the plan, step order, grants, context filters, approvals, and output contract."
            ),
            when_to_use=(
                "The agent is ready to turn reusable workflow setup into a specific execution.",
                "A script wants to create a repeatable run instance without starting it yet.",
            ),
            prerequisites=(
                "Pass project_id.",
                "Pass either run_plan_json or template_key.",
                "Do not place secrets in metadata_json, selected_context_json, or run_plan_json.",
            ),
            returns=(
                "A WriteEnvelope containing the saved run plan, steps, approval gates, "
                "and snapshots.",
                "The saved run plan remains draft until runPlan.start is called.",
            ),
            examples=(
                OperationExample(
                    title="Create a plan from a project template",
                    arguments={"project_id": 1, "template_key": "core.project-memory-review"},
                ),
                OperationExample(
                    title="Create a one-step explicit plan",
                    arguments={
                        "project_id": 1,
                        "run_plan_json": {
                            "schema_version": "stackos.run-plan.v1",
                            "key": "manual.review.run",
                            "title": "Manual review",
                            "steps": [{"id": "review", "title": "Review"}],
                        },
                    },
                ),
            ),
            grant_policy="direct-bootstrap-write",
        ),
        OperationSpec(
            name="runPlan.start",
            summary="Start a draft run plan and return the scoped run token.",
            input_model=RunPlanStartInput,
            output_model=WriteEnvelope[RunPlanStartOut],
            handler=run_plan_start,
            surfaces=_surfaces("runPlan.start", "run-plans start"),
            purpose=(
                "Use this to begin execution. The returned run_token is the only value an "
                "agent should pass to run-plan step tools; it is not a credential secret."
            ),
            when_to_use=(
                "A draft plan is approved enough to execute.",
                "A CLI or REST caller wants to hand an agent a scoped run token for "
                "step execution.",
            ),
            prerequisites=(
                "Pass project_id and run_plan_id.",
                "The run plan must be in draft status.",
            ),
            returns=(
                "The started plan.",
                "The linked run audit row.",
                "A run_token scoped to the run-plan controller.",
            ),
            examples=(
                OperationExample(
                    title="Start a run plan",
                    arguments={"project_id": 1, "run_plan_id": 42},
                ),
            ),
            grant_policy="direct-bootstrap-write",
        ),
        OperationSpec(
            name="runPlan.get",
            summary="Fetch one run plan with steps, approvals, grants, and snapshots.",
            input_model=RunPlanGetInput,
            output_model=RunPlanOut,
            handler=run_plan_get,
            surfaces=_surfaces("runPlan.get", "run-plans get"),
            purpose=(
                "Use this to inspect the current plan state before choosing the next tool call "
                "or to refresh context after a step changes status."
            ),
            when_to_use=(
                "The agent needs current step, approval, or output state.",
                "A human or script wants to inspect a saved plan.",
            ),
            prerequisites=("Pass run_plan_id.",),
            returns=("The full run plan object, including steps and approval requests.",),
            examples=(OperationExample(title="Fetch a run plan", arguments={"run_plan_id": 42}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.list",
            summary="List run plans with cursor pagination and optional filters.",
            input_model=RunPlanListInput,
            output_model=Page[RunPlanSummaryOut],
            handler=run_plan_list,
            surfaces=_surfaces("runPlan.list", "run-plans list"),
            purpose=(
                "Use this to find recent or relevant run instances for a project, template, "
                "or status without loading every plan into context."
            ),
            when_to_use=(
                "An agent needs recent history before creating or resuming work.",
                "A UI or script needs a paginated run-plan index.",
            ),
            prerequisites=(
                "Pass project_id when querying within a project.",
                "Use limit and after_id to keep responses small.",
            ),
            returns=("A cursor-paginated page of run-plan summaries.",),
            examples=(
                OperationExample(
                    title="List recent project run plans",
                    arguments={"project_id": 1, "limit": 10},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.update",
            summary="Update run-plan metadata or explicit approval-gate state.",
            input_model=RunPlanUpdateInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_update,
            surfaces=OperationSurfaces(mcp=OperationSurface(enabled=True)),
            purpose=(
                "Use this for controlled plan administration such as recording an approval "
                "decision or safe metadata. Normal agents are not granted this operation."
            ),
            when_to_use=(
                "A trusted controller has an approval decision to persist.",
                "A local admin path needs to attach non-secret metadata to a plan.",
            ),
            prerequisites=(
                "Pass run_plan_id.",
                "Pass approval_key and approval_status together when updating approvals.",
                "Never include secrets in metadata_json or decision_json.",
            ),
            returns=("A WriteEnvelope containing the updated run plan.",),
            examples=(
                OperationExample(
                    title="Approve a gate",
                    arguments={
                        "run_plan_id": 42,
                        "approval_key": "launch-review",
                        "approval_status": "approved",
                        "decided_by": "operator",
                    },
                ),
            ),
            grant_policy="admin-only",
        ),
        OperationSpec(
            name="runPlan.claimStep",
            summary="Claim the next eligible run-plan step for the active run token.",
            input_model=RunPlanClaimStepInput,
            output_model=WriteEnvelope[RunPlanStepOut],
            handler=run_plan_claim_step,
            surfaces=_surfaces("runPlan.claimStep", "run-plans claim-step"),
            purpose=(
                "Use this after runPlan.start to move one approved pending step into running "
                "state and activate its frozen tool grants."
            ),
            when_to_use=(
                "The agent has a run_token and is ready to execute the next step.",
                "A specific step_id should be claimed after its dependencies are complete.",
            ),
            prerequisites=(
                "Pass run_token from runPlan.start.",
                "Pass run_plan_id and optionally step_id.",
                "The plan must be started and any referenced approval gates must be satisfied.",
            ),
            returns=(
                "A WriteEnvelope containing the claimed running step.",
                "The step's allowed_tools list, derived from the run-plan grant snapshot.",
            ),
            examples=(
                OperationExample(
                    title="Claim a specific step",
                    arguments={"run_plan_id": 42, "step_id": "review", "run_token": "token"},
                ),
            ),
            grant_policy="run-plan-controller",
        ),
        OperationSpec(
            name="runPlan.recordStep",
            summary="Record a terminal result for the active running run-plan step.",
            input_model=RunPlanRecordStepInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_record_step,
            surfaces=_surfaces("runPlan.recordStep", "run-plans record-step"),
            purpose=(
                "Use this when the current step is done. Recording the final step closes the "
                "plan and linked run audit row."
            ),
            when_to_use=(
                "The agent completed, failed, or intentionally skipped the claimed step.",
                "A controller must persist structured step output for future context retrieval.",
            ),
            prerequisites=(
                "Pass run_token from runPlan.start.",
                "Pass run_plan_id, step_id, and status success, failed, or skipped.",
                "Keep result_json concise and free of secrets.",
            ),
            returns=(
                "A WriteEnvelope containing the updated run plan.",
                "Completed or failed plan status when the recorded step is terminal for the plan.",
            ),
            examples=(
                OperationExample(
                    title="Record step success",
                    arguments={
                        "run_plan_id": 42,
                        "step_id": "review",
                        "status": "success",
                        "result_json": {"summary": "done"},
                        "run_token": "token",
                    },
                ),
            ),
            grant_policy="run-plan-controller",
        ),
    ]


__all__ = [
    "RunPlanClaimStepInput",
    "RunPlanCreateInput",
    "RunPlanGetInput",
    "RunPlanListInput",
    "RunPlanRecordStepInput",
    "RunPlanStartInput",
    "RunPlanUpdateInput",
    "RunPlanValidateInput",
    "operation_specs",
    "run_plan_claim_step",
    "run_plan_create",
    "run_plan_get",
    "run_plan_list",
    "run_plan_record_step",
    "run_plan_start",
    "run_plan_update",
    "run_plan_validate",
]
