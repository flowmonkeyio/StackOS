"""Write operation handlers for the tracker operation surface."""

from __future__ import annotations

from datetime import UTC, datetime

from stackos.artifacts import redact_secret_text
from stackos.db.models import RunPlan, RunPlanStatus
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.tracker.schemas import (
    TrackerCreateTaskInput,
    TrackerCreateTicketInput,
    TrackerLinkRunPlanInput,
    TrackerPatchInput,
    TrackerPickInput,
    TrackerRejectTaskInput,
    TrackerReleaseInput,
    TrackerUpdateTaskInput,
    TrackerUpdateTicketInput,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import (
    TrackerMutationOut,
    TrackerRepository,
)


def _safe_rejection_reason(reason: str) -> str:
    clean = redact_secret_text(str(reason or "")).strip()
    if not clean:
        raise ValidationError("reason is required to reject a task")
    return clean


async def tracker_create_task(
    inp: TrackerCreateTaskInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    env = TrackerRepository(ctx.session).create_task(
        project_id=inp.project_id,
        key=inp.key,
        title=inp.title,
        goal=inp.goal,
        description=inp.description,
        status=inp.status,
        priority_key=inp.priority_key,
        lane_key=inp.lane_key,
        owner=inp.owner,
        task_type=inp.task_type,
        source_kind=inp.source_kind,
        source_json=inp.source_json,
        definition_of_done_json=inp.definition_of_done_json,
        constraints_json=inp.constraints_json,
        expected_outcomes_json=inp.expected_outcomes_json,
        completion_evidence_json=inp.completion_evidence_json,
        context_json=inp.context_json,
        metadata_json=inp.metadata_json,
        created_by=inp.created_by,
        create_default_ticket=inp.create_default_ticket,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_create_ticket(
    inp: TrackerCreateTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    if inp.tickets_json is not None:
        ticket_list_json = {
            "task_key": inp.task_key,
            "tickets": inp.tickets_json,
            "dependencies": inp.dependencies_json or [],
            "created_by": inp.created_by,
        }
        repo = TrackerRepository(ctx.session)
        if inp.dry_run:
            out = repo.validate_ticket_list(
                project_id=inp.project_id, ticket_list_json=ticket_list_json
            )
            return WriteEnvelope[TrackerMutationOut](
                data=out,
                run_id=ctx.run_id,
                project_id=inp.project_id,
            )
        env = repo.create_ticket_list(
            project_id=inp.project_id,
            ticket_list_json=ticket_list_json,
            actor=inp.created_by,
        )
        return WriteEnvelope[TrackerMutationOut](
            data=env.data,
            run_id=ctx.run_id,
            project_id=env.project_id,
        )
    if inp.task_key is None or inp.key is None or inp.title is None:
        raise ValidationError("task_key, key, and title are required for single ticket creation")
    env = TrackerRepository(ctx.session).create_ticket(
        project_id=inp.project_id,
        task_key=inp.task_key,
        key=inp.key,
        title=inp.title,
        goal=inp.goal,
        status=inp.status,
        kind=inp.kind,
        assignee=inp.assignee,
        priority_key=inp.priority_key,
        lane_key=inp.lane_key,
        parent_ticket_key=inp.parent_ticket_key,
        dependency_keys=inp.dependency_keys,
        blocker_reason=inp.blocker_reason,
        outcome=inp.outcome,
        effort=inp.effort,
        source_kind=inp.source_kind,
        source_json=inp.source_json,
        definition_of_done_json=inp.definition_of_done_json,
        constraints_json=inp.constraints_json,
        expected_changes_json=inp.expected_changes_json,
        allowed_paths_json=inp.allowed_paths_json,
        references_json=inp.references_json,
        completion_evidence_json=inp.completion_evidence_json,
        context_json=inp.context_json,
        metadata_json=inp.metadata_json,
        created_by=inp.created_by,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_update_task(
    inp: TrackerUpdateTaskInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    env = TrackerRepository(ctx.session).update_task(
        project_id=inp.project_id,
        task_key=inp.task_key,
        patch_json=inp.patch_json,
        actor=inp.actor,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_reject_task(
    inp: TrackerRejectTaskInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    if inp.task_key is None and inp.run_plan_id is None:
        raise ValidationError("task_key or run_plan_id is required")
    if (
        inp.task_key is not None
        and inp.run_plan_id is not None
        and inp.task_key != f"workflow-{inp.run_plan_id}"
    ):
        raise ValidationError(
            "task_key must match workflow-{run_plan_id} when both rejection targets are provided",
            data={"task_key": inp.task_key, "run_plan_id": inp.run_plan_id},
        )
    reason = _safe_rejection_reason(inp.reason)
    if inp.run_plan_id is not None:
        plan = ctx.session.get(RunPlan, inp.run_plan_id)
        if plan is not None and plan.project_id == inp.project_id:
            if plan.status in {RunPlanStatus.DRAFT, RunPlanStatus.STARTED}:
                RunPlanRepository(ctx.session).abort(
                    project_id=inp.project_id,
                    run_plan_id=inp.run_plan_id,
                    reason=reason,
                    actor=inp.actor,
                    commit=False,
                )
            else:
                now = datetime.now(tz=UTC).replace(tzinfo=None)
                plan.metadata_json = {
                    **(plan.metadata_json or {}),
                    "rejected": True,
                    "rejection_reason": reason,
                    "rejected_at": now.isoformat(),
                    **({"rejected_by": inp.actor} if inp.actor else {}),
                }
                plan.updated_at = now
                ctx.session.add(plan)
    env = TrackerRepository(ctx.session).reject_task(
        project_id=inp.project_id,
        task_key=inp.task_key,
        run_plan_id=inp.run_plan_id,
        reason=reason,
        actor=inp.actor,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_update_ticket(
    inp: TrackerUpdateTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    if inp.updates_json is not None:
        env = TrackerRepository(ctx.session).update_ticket_list(
            project_id=inp.project_id,
            updates_json=inp.updates_json,
            actor=inp.actor,
            dry_run=inp.dry_run,
        )
        return WriteEnvelope[TrackerMutationOut](
            data=env.data,
            run_id=ctx.run_id,
            project_id=env.project_id,
        )
    if inp.ticket_key is None or inp.patch_json is None:
        raise ValidationError("ticket_key and patch_json are required for single ticket update")
    env = TrackerRepository(ctx.session).update_ticket(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
        patch_json=inp.patch_json,
        actor=inp.actor,
        dry_run=inp.dry_run,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_patch(
    inp: TrackerPatchInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    env = TrackerRepository(ctx.session).patch(
        project_id=inp.project_id,
        patch_json=inp.patch_json,
        actor=inp.actor,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_pick(
    inp: TrackerPickInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    env = TrackerRepository(ctx.session).pick(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
        assignee=inp.assignee,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_release(
    inp: TrackerReleaseInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    env = TrackerRepository(ctx.session).release(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
        actor=inp.actor,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_link_run_plan(
    inp: TrackerLinkRunPlanInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerMutationOut]:
    env = TrackerRepository(ctx.session).link_run_plan(
        project_id=inp.project_id,
        task_key=inp.task_key,
        run_plan_id=inp.run_plan_id,
        actor=inp.actor,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


__all__ = [
    "tracker_create_task",
    "tracker_create_ticket",
    "tracker_link_run_plan",
    "tracker_patch",
    "tracker_pick",
    "tracker_reject_task",
    "tracker_release",
    "tracker_update_task",
    "tracker_update_ticket",
]
