"""Generic ActionCall durable-job inspection and operator lifecycle controls."""

from __future__ import annotations

from typing import Any

from stackos.actions import ActionRepository
from stackos.actions.repository.durable import DurableActionJobOut
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.permissions import active_run_plan_step
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.workflows.run_plan_grants import parse_run_plan_mcp_tool_grants

from .execution import _check_direct_action_policy, _ensure_action_contract_approval
from .schemas import (
    ActionCallControlInput,
    ActionCallDurableItemsOut,
    ActionCallItemsInput,
    ActionCallResumeInput,
    ActionCallRetryInput,
)


async def action_call_items(
    inp: ActionCallItemsInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionCallDurableItemsOut:
    project_id = _project_id(inp.project_id, ctx)
    repo = ActionRepository(ctx.session)
    job = _job_for_call(repo, project_id=project_id, action_call_id=inp.action_call_id)
    items = repo.list_durable_action_items(project_id=project_id, job_id=job.id)
    return ActionCallDurableItemsOut(
        action_call_id=inp.action_call_id,
        job=job,
        items=items,
        count=len(items),
    )


async def action_call_pause(
    inp: ActionCallControlInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[DurableActionJobOut]:
    project_id = _project_id(inp.project_id, ctx)
    repo = ActionRepository(ctx.session)
    job = _job_for_call(repo, project_id=project_id, action_call_id=inp.action_call_id)
    result = repo.pause_durable_action_job(project_id=project_id, job_id=job.id)
    return WriteEnvelope(data=result, project_id=project_id, run_id=ctx.run_id)


async def action_call_resume(
    inp: ActionCallResumeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[DurableActionJobOut]:
    project_id = _project_id(inp.project_id, ctx)
    repo = ActionRepository(ctx.session)
    job = _job_for_call(repo, project_id=project_id, action_call_id=inp.action_call_id)
    call = repo.get_call(project_id=project_id, action_call_id=inp.action_call_id)
    if ctx.run_token:
        _authorize_workflow_restart(ctx, job=job, call=call, tool_name="actionCall.resume")
    else:
        manifest = repo.describe(project_id=project_id, action_ref=job.action_ref).manifest
        _check_direct_action_policy(
            risk_level=manifest.risk_level,
            config_json=manifest.config_json,
            dry_run=False,
            confirm_direct=inp.confirm_direct,
            intent_summary=inp.intent_summary,
        )
    result = repo.resume_durable_action_job(project_id=project_id, job_id=job.id)
    return WriteEnvelope(data=result, project_id=project_id, run_id=ctx.run_id)


async def action_call_retry(
    inp: ActionCallRetryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[DurableActionJobOut]:
    project_id = _project_id(inp.project_id, ctx)
    settings = ctx.extras.get("settings")
    asset_dir = getattr(settings, "generated_assets_dir", None)
    repo = ActionRepository(ctx.session, asset_dir=asset_dir)
    job = _job_for_call(repo, project_id=project_id, action_call_id=inp.action_call_id)
    call = repo.get_call(project_id=project_id, action_call_id=inp.action_call_id)
    if ctx.run_token:
        _authorize_workflow_restart(ctx, job=job, call=call, tool_name="actionCall.retry")
    else:
        manifest = repo.describe(project_id=project_id, action_ref=job.action_ref).manifest
        _check_direct_action_policy(
            risk_level=manifest.risk_level,
            config_json=manifest.config_json,
            dry_run=False,
            confirm_direct=inp.confirm_direct,
            intent_summary=inp.intent_summary,
        )
    result = repo.retry_durable_action_items(
        project_id=project_id,
        job_id=job.id,
        item_ids=inp.item_ids,
    )
    return WriteEnvelope(data=result, project_id=project_id, run_id=ctx.run_id)


async def action_call_cancel(
    inp: ActionCallControlInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[DurableActionJobOut]:
    project_id = _project_id(inp.project_id, ctx)
    repo = ActionRepository(ctx.session)
    job = _job_for_call(repo, project_id=project_id, action_call_id=inp.action_call_id)
    result = repo.cancel_durable_action_job(project_id=project_id, job_id=job.id)
    return WriteEnvelope(data=result, project_id=project_id, run_id=ctx.run_id)


def _project_id(value: int | None, ctx: MCPContext) -> int:
    project_id = value if value is not None else ctx.project_id
    if project_id is None:
        raise ValidationError(
            "project_id is required unless the agent bridge resolved the workspace project"
        )
    if ctx.project_id is not None and project_id != ctx.project_id:
        raise ValidationError("action call controls must remain in the workspace project")
    return project_id


def _job_for_call(
    repo: ActionRepository,
    *,
    project_id: int,
    action_call_id: int,
) -> DurableActionJobOut:
    job = repo.get_durable_action_job_for_action_call(
        project_id=project_id,
        action_call_id=action_call_id,
    )
    if job is None:
        raise NotFoundError(
            "action call has no durable delivery job",
            data={"project_id": project_id, "action_call_id": action_call_id},
        )
    return job


def _authorize_workflow_restart(
    ctx: MCPContext,
    *,
    job: DurableActionJobOut,
    call: Any,
    tool_name: str,
) -> None:
    """Recheck the original live step grant before a paused effect can resume."""

    plan, step = active_run_plan_step(ctx, tool_name)
    if call.run_id != ctx.run_id or call.run_plan_id != plan.id or call.run_plan_step_id != step.id:
        _deny_resume_grant(
            action_call_id=call.id,
            action_ref=job.action_ref,
            tool_name=tool_name,
            reason="resume must use the action call's original active run-plan step",
        )
    try:
        grants = parse_run_plan_mcp_tool_grants(plan.grant_snapshot_json)
    except ValueError as exc:
        _deny_resume_grant(
            action_call_id=call.id,
            action_ref=job.action_ref,
            tool_name=tool_name,
            reason=f"invalid run-plan grant snapshot: {exc}",
        )
    step_grants = [grant for grant in grants if grant.step_id == step.step_id]
    action_granted = job.action_ref in set(step.action_refs_json or []) and any(
        grant.tool_name == "action.execute" and job.action_ref in set(grant.action_refs)
        for grant in step_grants
    )
    communication_granted = _communication_resume_granted(
        metadata_json=call.metadata_json,
        grants=step_grants,
    )
    if not action_granted and not communication_granted:
        _deny_resume_grant(
            action_call_id=call.id,
            action_ref=job.action_ref,
            tool_name=tool_name,
            reason=(
                "resume requires the original step's action.execute grant for this action_ref "
                "or its matching communication.send/sendBatch target grant"
            ),
        )
    _ensure_action_contract_approval(
        ctx,
        plan_id=plan.id,
        grant_snapshot=plan.grant_snapshot_json,
        action_ref=job.action_ref,
    )


def _communication_resume_granted(*, metadata_json: Any, grants: list[Any]) -> bool:
    """Match only a sealed send's original communication grant.

    ``communication.reply`` has no stable target grant to reconstruct from a
    durable action call, so it deliberately requires the action.execute path.
    """

    metadata = metadata_json if isinstance(metadata_json, dict) else {}
    operation = metadata.get("operation")
    target_ref = metadata.get("target_ref")
    if operation not in {"communication.send", "communication.sendBatch"}:
        return False
    if not isinstance(target_ref, str) or not target_ref.strip():
        return False
    normalized_target = target_ref.strip()
    target_candidates = {normalized_target}
    if normalized_target.startswith("communication-target:"):
        target_candidates.add(normalized_target.removeprefix("communication-target:"))
    return any(
        grant.tool_name == operation and not target_candidates.isdisjoint(set(grant.targets))
        for grant in grants
    )


def _deny_resume_grant(
    *,
    action_call_id: int,
    action_ref: str,
    tool_name: str,
    reason: str,
) -> None:
    raise ToolNotGrantedError(
        reason,
        data={
            "tool": tool_name,
            "action_call_id": action_call_id,
            "action_ref": action_ref,
            "repair": "restart from the original active step with its matching grant",
        },
    )


__all__ = [
    "action_call_cancel",
    "action_call_items",
    "action_call_pause",
    "action_call_resume",
    "action_call_retry",
]
