"""Write operation handlers for the tracker operation surface."""

from __future__ import annotations

from typing import Any

from sqlmodel import select

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
    TrackerReopenInput,
    TrackerUpdateTaskInput,
    TrackerUpdateTicketInput,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import (
    TrackerMutationOut,
    TrackerReopenOut,
    TrackerRepository,
)


def _safe_rejection_reason(reason: str) -> str:
    clean = redact_secret_text(str(reason or "")).strip()
    if not clean:
        raise ValidationError("reason is required to reject a task")
    return clean


def _safe_reopen_reason(reason: str) -> str:
    clean = redact_secret_text(str(reason or "")).strip()
    if not clean:
        raise ValidationError("reason is required to reopen a task or run plan")
    return clean


def _workflow_run_plan_id_from_task_key(task_key: str | None) -> int | None:
    if task_key is None or not task_key.startswith("workflow-"):
        return None
    raw = task_key.removeprefix("workflow-")
    try:
        return int(raw)
    except ValueError:
        return None


def _workflow_run_plan_id_from_run_id(
    ctx: MCPContext,
    *,
    project_id: int,
    run_id: int,
) -> int:
    plan = ctx.session.exec(
        select(RunPlan).where(RunPlan.project_id == project_id, RunPlan.run_id == run_id)
    ).first()
    if plan is None or plan.id is None:
        raise ValidationError(
            "run_id is not linked to a run plan in this project; pass task_key for a manual task",
            data={
                "project_id": project_id,
                "run_id": run_id,
                "next_operations": ["tracker.reopen", "runPlan.list"],
            },
        )
    return int(plan.id)


def _workflow_step_context(
    repo: TrackerRepository,
    inp: TrackerCreateTicketInput,
) -> dict[str, Any] | None:
    if (inp.run_plan_id is None) != (inp.step_id is None):
        raise ValidationError("run_plan_id and step_id must be provided together")
    if inp.run_plan_id is None:
        return None
    step_id = str(inp.step_id or "").strip()
    if not step_id:
        raise ValidationError("step_id is required when run_plan_id is provided")
    context = repo.workflow_step_ticket_context(
        project_id=inp.project_id,
        run_plan_id=inp.run_plan_id,
        step_id=step_id,
    )
    if inp.task_key is not None and inp.task_key != context["task_key"]:
        raise ValidationError(
            "task_key is resolved from run_plan_id and step_id; omit it or pass the workflow task",
            data={"task_key": inp.task_key, "resolved_task_key": context["task_key"]},
        )
    if inp.parent_ticket_key is not None and inp.parent_ticket_key != context["parent_ticket_key"]:
        raise ValidationError(
            "parent_ticket_key is resolved from run_plan_id and step_id; "
            "omit it or pass the workflow step ticket",
            data={
                "parent_ticket_key": inp.parent_ticket_key,
                "resolved_parent_ticket_key": context["parent_ticket_key"],
            },
        )
    return context


def _workflow_source_json(
    source_json: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(source_json or {})
    merged.update(
        {
            key: value
            for key, value in {
                "run_plan_id": context["run_plan_id"],
                "run_plan_key": context["run_plan_key"],
                "run_plan_step_id": context["run_plan_step_id"],
                "step_id": context["step_id"],
                "template_key": context["template_key"],
                "workflow_parent_ticket_key": context["parent_ticket_key"],
            }.items()
            if value is not None
        }
    )
    return merged


def _workflow_ticket_json(raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ticket = dict(raw)
    parent_ticket_key = str(ticket.get("parent_ticket_key") or "").strip()
    if parent_ticket_key and parent_ticket_key != context["parent_ticket_key"]:
        raise ValidationError(
            "parent_ticket_key is resolved from run_plan_id and step_id for each "
            "workflow ticket list item; omit it or pass the workflow step ticket",
            data={
                "ticket_key": ticket.get("key"),
                "parent_ticket_key": parent_ticket_key,
                "resolved_parent_ticket_key": context["parent_ticket_key"],
            },
        )
    ticket["parent_ticket_key"] = context["parent_ticket_key"]
    ticket["run_plan_id"] = context["run_plan_id"]
    ticket["run_plan_step_id"] = context["run_plan_step_id"]
    source_json = ticket.get("source_json") if isinstance(ticket.get("source_json"), dict) else None
    ticket["source_json"] = _workflow_source_json(source_json, context)
    return ticket


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
    repo = TrackerRepository(ctx.session)
    workflow_context = _workflow_step_context(repo, inp)
    if inp.tickets_json is not None:
        tickets_json = (
            [_workflow_ticket_json(item, workflow_context) for item in inp.tickets_json]
            if workflow_context is not None
            else inp.tickets_json
        )
        ticket_list_json = {
            "task_key": workflow_context["task_key"] if workflow_context else inp.task_key,
            "tickets": tickets_json,
            "dependencies": inp.dependencies_json or [],
            "created_by": inp.created_by,
        }
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
    task_key = workflow_context["task_key"] if workflow_context else inp.task_key
    parent_ticket_key = (
        workflow_context["parent_ticket_key"] if workflow_context else inp.parent_ticket_key
    )
    source_json = (
        _workflow_source_json(inp.source_json, workflow_context)
        if workflow_context is not None
        else inp.source_json
    )
    if task_key is None or inp.key is None or inp.title is None:
        raise ValidationError("task_key, key, and title are required for single ticket creation")
    env = repo.create_ticket(
        project_id=inp.project_id,
        task_key=task_key,
        key=inp.key,
        title=inp.title,
        goal=inp.goal,
        status=inp.status,
        kind=inp.kind,
        assignee=inp.assignee,
        priority_key=inp.priority_key,
        lane_key=inp.lane_key,
        parent_ticket_key=parent_ticket_key,
        dependency_keys=inp.dependency_keys,
        blocker_reason=inp.blocker_reason,
        outcome=inp.outcome,
        effort=inp.effort,
        source_kind=inp.source_kind,
        source_json=source_json,
        definition_of_done_json=inp.definition_of_done_json,
        constraints_json=inp.constraints_json,
        expected_changes_json=inp.expected_changes_json,
        allowed_paths_json=inp.allowed_paths_json,
        references_json=inp.references_json,
        completion_evidence_json=inp.completion_evidence_json,
        context_json=inp.context_json,
        metadata_json=inp.metadata_json,
        run_plan_id=workflow_context["run_plan_id"] if workflow_context else None,
        run_plan_step_id=workflow_context["run_plan_step_id"] if workflow_context else None,
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
    workflow_run_plan_id = inp.run_plan_id or _workflow_run_plan_id_from_task_key(inp.task_key)
    if workflow_run_plan_id is not None:
        plan = ctx.session.get(RunPlan, workflow_run_plan_id)
        if plan is not None and plan.project_id == inp.project_id:
            if plan.status in {RunPlanStatus.DRAFT, RunPlanStatus.STARTED}:
                RunPlanRepository(ctx.session).abort(
                    project_id=inp.project_id,
                    run_plan_id=workflow_run_plan_id,
                    reason=reason,
                    actor=inp.actor,
                    commit=False,
                )
            elif plan.status in {RunPlanStatus.COMPLETED, RunPlanStatus.FAILED}:
                raise ConflictError(
                    "completed or failed workflow run plans cannot be rejected through "
                    "tracker state",
                    data={
                        "task_key": inp.task_key or f"workflow-{workflow_run_plan_id}",
                        "run_plan_id": workflow_run_plan_id,
                        "run_plan_status": plan.status.value,
                        "next_operations": ["runPlan.get", "runPlan.checkConsistency"],
                    },
                )
            else:
                # Already-aborted workflow plans can receive a rejection note in tracker
                # without changing canonical run-plan state.
                pass
        else:
            raise ValidationError(
                "workflow task run plan not found in project",
                data={
                    "task_key": inp.task_key or f"workflow-{workflow_run_plan_id}",
                    "run_plan_id": workflow_run_plan_id,
                    "project_id": inp.project_id,
                    "next_operations": ["runPlan.get", "runPlan.checkConsistency"],
                },
            )
    env = TrackerRepository(ctx.session).reject_task(
        project_id=inp.project_id,
        task_key=inp.task_key,
        run_plan_id=workflow_run_plan_id,
        reason=reason,
        actor=inp.actor,
        allow_workflow_reject=workflow_run_plan_id is not None,
    )
    return WriteEnvelope[TrackerMutationOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def tracker_reopen(
    inp: TrackerReopenInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TrackerReopenOut]:
    if inp.task_key is None and inp.run_plan_id is None and inp.run_id is None:
        raise ValidationError("task_key, run_plan_id, or run_id is required")
    task_run_plan_id = _workflow_run_plan_id_from_task_key(inp.task_key)
    if inp.task_key is not None and inp.run_id is not None and task_run_plan_id is None:
        raise ValidationError(
            "manual task_key cannot be combined with run_id; pass only task_key to reopen the task",
            data={"task_key": inp.task_key, "run_id": inp.run_id},
        )
    workflow_run_plan_id = inp.run_plan_id or task_run_plan_id
    if inp.run_id is not None:
        run_plan_id_from_run = _workflow_run_plan_id_from_run_id(
            ctx,
            project_id=inp.project_id,
            run_id=inp.run_id,
        )
        if workflow_run_plan_id is not None and workflow_run_plan_id != run_plan_id_from_run:
            raise ValidationError(
                "run_id and run_plan_id/task_key point at different workflows",
                data={
                    "task_key": inp.task_key,
                    "run_plan_id": inp.run_plan_id,
                    "run_id": inp.run_id,
                    "resolved_run_plan_id": run_plan_id_from_run,
                },
            )
        workflow_run_plan_id = run_plan_id_from_run
    if (
        inp.task_key is not None
        and inp.run_plan_id is not None
        and inp.task_key != f"workflow-{inp.run_plan_id}"
    ):
        raise ValidationError(
            "task_key must match workflow-{run_plan_id} when both reopen targets are provided",
            data={"task_key": inp.task_key, "run_plan_id": inp.run_plan_id},
        )
    reason = _safe_reopen_reason(inp.reason)
    if workflow_run_plan_id is not None:
        run_env = RunPlanRepository(ctx.session).reopen(
            project_id=inp.project_id,
            run_plan_id=workflow_run_plan_id,
            step_id=inp.step_id,
            reason=reason,
            actor=inp.actor,
        )
        tracker = TrackerRepository(ctx.session)
        snapshot = tracker.get(
            project_id=inp.project_id,
            task_key=f"workflow-{workflow_run_plan_id}",
            include_graph=False,
        )
        task = snapshot.tasks[0] if snapshot.tasks else None
        out = TrackerReopenOut(
            tracker=snapshot.tracker,
            task=task,
            rev=snapshot.tracker.rev,
            run_plan_id=workflow_run_plan_id,
            run_id=run_env.data.run_id,
            run_token=run_env.data.run_token,
            reopened_step_id=run_env.data.reopened_step_id,
            reset_step_ids=run_env.data.reset_step_ids,
            next_operations=run_env.data.next_operations,
        )
        return WriteEnvelope[TrackerReopenOut](
            data=out,
            run_id=run_env.run_id,
            project_id=run_env.project_id,
        )

    env = TrackerRepository(ctx.session).reopen_task(
        project_id=inp.project_id,
        task_key=str(inp.task_key),
        reason=reason,
        actor=inp.actor,
    )
    return WriteEnvelope[TrackerReopenOut](
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
    "tracker_reopen",
    "tracker_update_task",
    "tracker_update_ticket",
]
