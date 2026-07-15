"""Read operation handlers for the tracker operation surface."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.tracker.schemas import (
    TrackerChangedInput,
    TrackerGetInput,
    TrackerHistoryInput,
    TrackerNextInput,
    TrackerProjectInput,
    TrackerSearchInput,
    TrackerTicketInput,
)
from stackos.repositories.base import Page
from stackos.repositories.tracker import (
    TrackerBriefOut,
    TrackerChangedOut,
    TrackerHistoryOut,
    TrackerNextOut,
    TrackerRepository,
    TrackerSearchOut,
    TrackerSnapshotOut,
    TrackerStatusOut,
    TrackerVerifyOut,
)


async def tracker_status(
    inp: TrackerProjectInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerStatusOut:
    repository = TrackerRepository(ctx.session)
    return await run_in_threadpool(repository.status, project_id=inp.project_id)


async def tracker_get(
    inp: TrackerGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerSnapshotOut:
    repository = TrackerRepository(ctx.session)
    return await run_in_threadpool(
        repository.get,
        project_id=inp.project_id,
        statuses=inp.statuses,
        task_statuses=inp.task_statuses,
        task_key=inp.task_key,
        ticket_keys=inp.ticket_keys,
        ticket_ids=inp.ticket_ids,
        block_state=inp.block_state,
        dependency_ticket_key=inp.dependency_ticket_key,
        workflow_key=inp.workflow_key,
        run_plan_id=inp.run_plan_id,
        assignee=inp.assignee,
        include_graph=inp.include_graph,
        task_index_only=inp.task_index_only,
    )


async def tracker_next(
    inp: TrackerNextInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerNextOut:
    return TrackerRepository(ctx.session).next(
        project_id=inp.project_id,
        limit=inp.limit,
        assignee=inp.assignee,
        include_blocked=inp.include_blocked,
    )


async def tracker_blockers(
    inp: TrackerProjectInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerNextOut:
    return TrackerRepository(ctx.session).blockers(project_id=inp.project_id)


async def tracker_brief(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerBriefOut:
    return TrackerRepository(ctx.session).brief(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
    )


async def tracker_why(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerBriefOut:
    return TrackerRepository(ctx.session).brief(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
    )


async def tracker_execute(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerBriefOut:
    return TrackerRepository(ctx.session).brief(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
    )


async def tracker_verify(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerVerifyOut:
    return TrackerRepository(ctx.session).verify(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
    )


async def tracker_history(
    inp: TrackerHistoryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[TrackerHistoryOut]:
    return TrackerRepository(ctx.session).history(
        project_id=inp.project_id,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def tracker_changed(
    inp: TrackerChangedInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerChangedOut:
    return TrackerRepository(ctx.session).changed(
        project_id=inp.project_id,
        since_rev=inp.since_rev,
        limit=inp.limit,
    )


async def tracker_search(
    inp: TrackerSearchInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerSearchOut:
    return TrackerRepository(ctx.session).search(
        project_id=inp.project_id,
        query=inp.query,
        limit=inp.limit,
    )


__all__ = [
    "tracker_blockers",
    "tracker_brief",
    "tracker_changed",
    "tracker_execute",
    "tracker_get",
    "tracker_history",
    "tracker_next",
    "tracker_search",
    "tracker_status",
    "tracker_verify",
    "tracker_why",
]
