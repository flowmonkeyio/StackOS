"""Operation handlers for the run-plan operation surface."""

from __future__ import annotations

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.run_plans.schemas import (
    RunPlanAbortInput,
    RunPlanCheckConsistencyInput,
    RunPlanClaimStepInput,
    RunPlanCreateInput,
    RunPlanGetInput,
    RunPlanGetStepInput,
    RunPlanListInput,
    RunPlanRecordStepInput,
    RunPlanRecoverInput,
    RunPlanReopenInput,
    RunPlanStartInput,
    RunPlanUpdateInput,
    RunPlanValidateInput,
)
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.run_plans import (
    RunPlanConsistencyOut,
    RunPlanOut,
    RunPlanReopenOut,
    RunPlanRepository,
    RunPlanStartOut,
    RunPlanStepOut,
    RunPlanSummaryOut,
)
from stackos.workflows import RunPlanValidationOut


def _template_key(template_key: str | None, workflow_key: str | None) -> str | None:
    if template_key is not None and workflow_key is not None and template_key != workflow_key:
        raise ValidationError(
            "template_key and workflow_key must match when both are provided",
            data={"template_key": template_key, "workflow_key": workflow_key},
        )
    return template_key if template_key is not None else workflow_key


async def run_plan_validate(
    inp: RunPlanValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanValidationOut:
    return RunPlanRepository(ctx.session).validate_plan(
        run_plan_json=inp.run_plan_json,
        template_key=_template_key(inp.template_key, inp.workflow_key),
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
        inputs_json=inp.inputs_json,
        selected_context_json=inp.selected_context_json,
        metadata_json=inp.metadata_json,
        enforce_required_inputs=inp.enforce_required_inputs,
    )


async def run_plan_create(
    inp: RunPlanCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).create(
        project_id=inp.project_id,
        run_plan_json=inp.run_plan_json,
        template_key=_template_key(inp.template_key, inp.workflow_key),
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
    return RunPlanRepository(ctx.session).get(inp.run_plan_id, project_id=inp.project_id)


async def run_plan_get_step(
    inp: RunPlanGetStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanStepOut:
    return RunPlanRepository(ctx.session).get_step(
        inp.run_plan_id,
        inp.step_id,
        project_id=inp.project_id,
    )


async def run_plan_check_consistency(
    inp: RunPlanCheckConsistencyInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanConsistencyOut:
    return RunPlanRepository(ctx.session).check_consistency(
        inp.run_plan_id,
        project_id=inp.project_id,
    )


async def run_plan_recover(
    inp: RunPlanRecoverInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).recover(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        step_id=inp.step_id,
        step_status=inp.step_status,
        reason=inp.reason,
        actor=inp.actor,
        result_json=inp.result_json,
        error=inp.error,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_reopen(
    inp: RunPlanReopenInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanReopenOut]:
    env = RunPlanRepository(ctx.session).reopen(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        step_id=inp.step_id,
        reason=inp.reason,
        actor=inp.actor,
    )
    return WriteEnvelope[RunPlanReopenOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


async def run_plan_list(
    inp: RunPlanListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[RunPlanSummaryOut]:
    return RunPlanRepository(ctx.session).list(
        project_id=inp.project_id,
        run_id=inp.run_id,
        status=inp.status,
        template_key=_template_key(inp.template_key, inp.workflow_key),
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
        project_id=inp.project_id,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_abort(
    inp: RunPlanAbortInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).abort(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        reason=inp.reason,
        actor=inp.actor,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_claim_step(
    inp: RunPlanClaimStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanStepOut]:
    env = RunPlanRepository(ctx.session).claim_step(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
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
        project_id=inp.project_id,
        run_id=ctx.run_id,
        step_id=inp.step_id,
        status=inp.status,
        result_json=inp.result_json,
        error=inp.error,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


__all__ = [
    "run_plan_abort",
    "run_plan_check_consistency",
    "run_plan_claim_step",
    "run_plan_create",
    "run_plan_get",
    "run_plan_get_step",
    "run_plan_list",
    "run_plan_record_step",
    "run_plan_recover",
    "run_plan_reopen",
    "run_plan_start",
    "run_plan_update",
    "run_plan_validate",
]
