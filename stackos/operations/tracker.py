"""Task tracker operation registrations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from stackos.agent_responses import (
    compact_tracker_brief,
    compact_tracker_changed,
    compact_tracker_history_page,
    compact_tracker_next,
    compact_tracker_search,
    compact_tracker_status,
    compact_tracker_verify,
)
from stackos.db.models import TrackerItemStatus, TrackerSourceKind, TrackerTicketKind
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.tracker import (
    TrackerBriefOut,
    TrackerChangedOut,
    TrackerHistoryOut,
    TrackerMutationOut,
    TrackerNextOut,
    TrackerRepository,
    TrackerSearchOut,
    TrackerSnapshotOut,
    TrackerStatusOut,
    TrackerVerifyOut,
)

TrackerResponseMode = Literal["compact", "standard", "verbose"]


class TrackerProjectInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "response_mode": "compact"}},
    )

    project_id: int
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "the full tracker rows for diagnostics."
        ),
    )


class TrackerGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"project_id": 1, "include_graph": True},
                {
                    "project_id": 1,
                    "task_key": "telegram-comms",
                    "ticket_keys": ["telegram-ingress", "telegram-reply"],
                    "include_graph": False,
                },
            ]
        },
    )

    project_id: int
    statuses: list[TrackerItemStatus] | None = None
    task_key: str | None = None
    ticket_keys: list[str] | None = None
    ticket_ids: list[int] | None = None
    block_state: Literal["blocked", "open"] | None = None
    dependency_ticket_key: str | None = None
    workflow_key: str | None = None
    run_plan_id: int | None = None
    assignee: str | None = None
    include_graph: bool = True


class TrackerNextInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "limit": 5, "response_mode": "compact"}},
    )

    project_id: int
    limit: int = Field(default=5, ge=1, le=50)
    assignee: str | None = None
    include_blocked: bool = True
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full tracker ticket rows for diagnostics."
        ),
    )


class TrackerTicketInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "ticket_key": "slack-ingress",
                "response_mode": "compact",
            }
        },
    )

    project_id: int
    ticket_key: str
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "the full tracker brief for diagnostics."
        ),
    )


class TrackerHistoryInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "limit": 50, "response_mode": "compact"}},
    )

    project_id: int
    limit: int | None = Field(default=None, ge=1, le=200)
    after_id: int | None = None
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full tracker history rows with before/after/patch payloads."
        ),
    )


class TrackerChangedInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "since_rev": 42, "response_mode": "compact"}
        },
    )

    project_id: int
    since_rev: int | None = None
    limit: int = Field(default=50, ge=1, le=200)
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full tracker history rows with before/after/patch payloads."
        ),
    )


class TrackerSearchInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "query": "telegram", "response_mode": "compact"}
        },
    )

    project_id: int
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    response_mode: TrackerResponseMode = Field(
        default="compact",
        description=(
            "Agent response shape. compact is default; standard/verbose returns "
            "full matching task and ticket rows for diagnostics."
        ),
    )


class TrackerCreateTaskInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "telegram-comms",
                "title": "Telegram communications flow",
                "goal": "Support message ingress and safe replies.",
                "created_by": "codex",
            }
        },
    )

    project_id: int
    key: str
    title: str
    goal: str = ""
    description: str = ""
    status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED
    priority_key: str = "p2"
    lane_key: str = "implementation"
    owner: str | None = None
    task_type: str = "task"
    source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL
    source_json: dict[str, Any] | None = None
    definition_of_done_json: list[str] | None = None
    constraints_json: list[str] | None = None
    expected_outcomes_json: list[str] | None = None
    completion_evidence_json: dict[str, Any] | None = None
    context_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    created_by: str | None = None
    create_default_ticket: bool = False


class TrackerCreateTicketInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "project_id": 1,
                    "task_key": "telegram-comms",
                    "key": "telegram-ingress",
                    "title": "Wire Telegram ingress",
                    "dependency_keys": [],
                },
                {
                    "project_id": 1,
                    "task_key": "telegram-comms",
                    "tickets_json": [
                        {"key": "telegram-ingress", "title": "Wire Telegram ingress"},
                        {
                            "key": "telegram-reply",
                            "title": "Send safe replies",
                            "dependency_keys": ["telegram-ingress"],
                        },
                    ],
                    "dry_run": True,
                },
            ]
        },
    )

    project_id: int
    task_key: str | None = None
    key: str | None = None
    title: str | None = None
    goal: str = ""
    status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED
    kind: TrackerTicketKind = TrackerTicketKind.TICKET
    assignee: str | None = None
    priority_key: str = "p2"
    lane_key: str = "implementation"
    parent_ticket_key: str | None = None
    dependency_keys: list[str] | None = None
    blocker_reason: str | None = None
    outcome: str | None = None
    effort: str | None = None
    source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL
    source_json: dict[str, Any] | None = None
    definition_of_done_json: list[str] | None = None
    constraints_json: list[str] | None = None
    expected_changes_json: list[str] | None = None
    allowed_paths_json: list[str] | None = None
    references_json: list[dict[str, Any]] | None = None
    completion_evidence_json: dict[str, Any] | None = None
    context_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    created_by: str | None = None
    tickets_json: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional ticket list import. When provided, task_key is shared by default "
            "and single-ticket key/title fields are ignored."
        ),
    )
    dependencies_json: list[dict[str, Any]] | None = None
    dry_run: bool = Field(
        default=False,
        description="When tickets_json is provided, validate and normalize without writing.",
    )


class TrackerUpdateTaskInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "task_key": "telegram-comms",
                "patch_json": {"status": "in-progress"},
            }
        },
    )

    project_id: int
    task_key: str
    patch_json: dict[str, Any]
    actor: str | None = None


class TrackerUpdateTicketInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "project_id": 1,
                    "ticket_key": "telegram-ingress",
                    "patch_json": {"status": "complete", "outcome": "verified"},
                },
                {
                    "project_id": 1,
                    "updates_json": [
                        {
                            "ticket_key": "telegram-ingress",
                            "patch_json": {
                                "status": "complete",
                                "completion_evidence_json": {"summary": "Webhook verified."},
                            },
                        },
                        {
                            "ticket_key": "telegram-reply",
                            "patch_json": {
                                "assignee": "codex",
                                "add_dependency_keys": ["telegram-policy"],
                                "remove_dependency_keys": ["telegram-ingress"],
                            },
                        },
                    ],
                },
                {
                    "project_id": 1,
                    "ticket_key": "telegram-reply",
                    "patch_json": {
                        "add_dependency_keys": ["telegram-policy"],
                        "remove_dependency_keys": ["telegram-ingress"],
                    },
                    "dry_run": True,
                },
            ]
        },
    )

    project_id: int
    ticket_key: str | None = None
    patch_json: dict[str, Any] | None = None
    updates_json: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional list of atomic ticket patch updates. Each item identifies a ticket "
            "by ticket_id or ticket_key and supplies patch_json. Use add_dependency_keys "
            "and remove_dependency_keys for small dependency edits; dependency_keys "
            "intentionally replaces the full dependency list."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Preview dependency-changing patch_json or updates_json without writing. "
            "Returns dependency diffs for dependency fields and leaves ordinary updates quiet."
        ),
    )
    actor: str | None = None


class TrackerPatchInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "patch_json": {
                    "tickets": {
                        "telegram-ingress": {
                            "status": "complete",
                            "outcome": "Webhook verified.",
                        }
                    }
                },
            }
        },
    )

    project_id: int
    patch_json: dict[str, Any]
    actor: str | None = None


class TrackerPickInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "assignee": "codex"}},
    )

    project_id: int
    assignee: str
    ticket_key: str | None = None


class TrackerReleaseInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "ticket_key": "telegram-ingress", "actor": "codex"}
        },
    )

    project_id: int
    ticket_key: str
    actor: str | None = None


class TrackerLinkRunPlanInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "task_key": "manual-review", "run_plan_id": 12}
        },
    )

    project_id: int
    task_key: str
    run_plan_id: int
    actor: str | None = None


async def tracker_status(
    inp: TrackerProjectInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerStatusOut | dict[str, Any]:
    status = TrackerRepository(ctx.session).status(project_id=inp.project_id)
    if inp.response_mode == "compact":
        return compact_tracker_status(status)
    return status


async def tracker_get(
    inp: TrackerGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerSnapshotOut:
    return TrackerRepository(ctx.session).get(
        project_id=inp.project_id,
        statuses=inp.statuses,
        task_key=inp.task_key,
        ticket_keys=inp.ticket_keys,
        ticket_ids=inp.ticket_ids,
        block_state=inp.block_state,
        dependency_ticket_key=inp.dependency_ticket_key,
        workflow_key=inp.workflow_key,
        run_plan_id=inp.run_plan_id,
        assignee=inp.assignee,
        include_graph=inp.include_graph,
    )


async def tracker_next(
    inp: TrackerNextInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerNextOut | dict[str, Any]:
    next_work = TrackerRepository(ctx.session).next(
        project_id=inp.project_id,
        limit=inp.limit,
        assignee=inp.assignee,
        include_blocked=inp.include_blocked,
    )
    if inp.response_mode == "compact":
        return compact_tracker_next(next_work)
    return next_work


async def tracker_blockers(
    inp: TrackerProjectInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerNextOut | dict[str, Any]:
    blockers = TrackerRepository(ctx.session).blockers(project_id=inp.project_id)
    if inp.response_mode == "compact":
        return compact_tracker_next(blockers)
    return blockers


async def tracker_brief(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerBriefOut | dict[str, Any]:
    return _brief_response(
        TrackerRepository(ctx.session).brief(
            project_id=inp.project_id,
            ticket_key=inp.ticket_key,
        ),
        response_mode=inp.response_mode,
    )


def _brief_response(
    brief: TrackerBriefOut,
    *,
    response_mode: TrackerResponseMode,
) -> TrackerBriefOut | dict[str, Any]:
    if response_mode == "compact":
        return compact_tracker_brief(brief)
    return brief


async def tracker_why(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerBriefOut | dict[str, Any]:
    return _brief_response(
        TrackerRepository(ctx.session).brief(
            project_id=inp.project_id,
            ticket_key=inp.ticket_key,
        ),
        response_mode=inp.response_mode,
    )


async def tracker_execute(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerBriefOut | dict[str, Any]:
    return _brief_response(
        TrackerRepository(ctx.session).brief(
            project_id=inp.project_id,
            ticket_key=inp.ticket_key,
        ),
        response_mode=inp.response_mode,
    )


async def tracker_verify(
    inp: TrackerTicketInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerVerifyOut | dict[str, Any]:
    verification = TrackerRepository(ctx.session).verify(
        project_id=inp.project_id,
        ticket_key=inp.ticket_key,
    )
    if inp.response_mode == "compact":
        return compact_tracker_verify(verification)
    return verification


async def tracker_history(
    inp: TrackerHistoryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[TrackerHistoryOut] | dict[str, Any]:
    history = TrackerRepository(ctx.session).history(
        project_id=inp.project_id,
        limit=inp.limit,
        after_id=inp.after_id,
    )
    if inp.response_mode == "compact":
        return compact_tracker_history_page(history)
    return history


async def tracker_changed(
    inp: TrackerChangedInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerChangedOut | dict[str, Any]:
    changed = TrackerRepository(ctx.session).changed(
        project_id=inp.project_id,
        since_rev=inp.since_rev,
        limit=inp.limit,
    )
    if inp.response_mode == "compact":
        return compact_tracker_changed(changed)
    return changed


async def tracker_search(
    inp: TrackerSearchInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TrackerSearchOut | dict[str, Any]:
    search = TrackerRepository(ctx.session).search(
        project_id=inp.project_id,
        query=inp.query,
        limit=inp.limit,
    )
    if inp.response_mode == "compact":
        return compact_tracker_search(search)
    return search


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


def _surfaces(name: str, command: str | None = None) -> OperationSurfaces:
    alias_commands = {
        "tracker.status": "tracker status",
        "tracker.get": "tracker get",
        "tracker.next": "tracker next",
        "tracker.brief": "tracker brief",
        "tracker.verify": "tracker verify",
        "tracker.createTask": "tracker create-task",
        "tracker.createTicket": "tracker create-ticket",
        "tracker.patch": "tracker patch",
        "tracker.pick": "tracker pick",
        "tracker.updateTask": "ops call tracker.updateTask",
        "tracker.updateTicket": "ops call tracker.updateTicket",
        "tracker.linkRunPlan": "ops call tracker.linkRunPlan",
    }
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=f"/api/v1/operations/{name}/call"),
        cli=OperationSurface(
            enabled=True,
            command=command or alias_commands.get(name) or f"ops call {name}",
        ),
    )


def _read_spec(
    *,
    name: str,
    summary: str,
    input_model: type[MCPInput],
    output_model: Any,
    handler: Any,
    purpose: str,
    examples: tuple[OperationExample, ...] = (),
) -> OperationSpec:
    return OperationSpec(
        name=name,
        summary=summary,
        input_model=input_model,
        output_model=output_model,
        handler=handler,
        surfaces=_surfaces(name),
        purpose=purpose,
        when_to_use=(
            "Use this when an agent needs bounded project work context without "
            "scanning run plans, history, or resources manually.",
        ),
        prerequisites=("Pass the project_id resolved for the current workspace.",),
        returns=("A compact tracker read model with no secrets.",),
        examples=examples,
        mutating=False,
        grant_policy="direct-read",
    )


def _write_spec(
    *,
    name: str,
    summary: str,
    input_model: type[MCPInput],
    handler: Any,
    purpose: str,
    examples: tuple[OperationExample, ...] = (),
    output_model: Any = WriteEnvelope[TrackerMutationOut],
) -> OperationSpec:
    return OperationSpec(
        name=name,
        summary=summary,
        input_model=input_model,
        output_model=output_model,
        handler=handler,
        surfaces=_surfaces(name),
        purpose=purpose,
        when_to_use=(
            "Use this when the agent has decided how tracker state should change.",
            "Do not put secrets in patch_json, metadata_json, context_json, "
            "completion_evidence_json, or references.",
        ),
        prerequisites=("Pass project_id and stable task/ticket keys or ticket ids.",),
        returns=("A WriteEnvelope with compact mutation output and the new tracker revision.",),
        examples=examples,
        mutating=True,
        grant_policy="direct-tracker-write",
    )


def operation_specs() -> list[OperationSpec]:
    return [
        _read_spec(
            name="tracker.status",
            summary="Summarize project tracker counts, ready work, blockers, and revision.",
            input_model=TrackerProjectInput,
            output_model=TrackerStatusOut | dict[str, Any],
            handler=tracker_status,
            purpose="Use this as the cheapest tracker health and progress check.",
            examples=(OperationExample(title="Get tracker status", arguments={"project_id": 1}),),
        ),
        _read_spec(
            name="tracker.get",
            summary=(
                "Fetch filtered tasks, tickets, dependencies, links, and optional graph projection."
            ),
            input_model=TrackerGetInput,
            output_model=TrackerSnapshotOut,
            handler=tracker_get,
            purpose=(
                "Use this for UI rendering, list review by task/ticket ids, or when "
                "the agent needs a bounded project work map."
            ),
            examples=(
                OperationExample(
                    title="Review selected tickets under one task",
                    arguments={
                        "project_id": 1,
                        "task_key": "core-tracker",
                        "ticket_keys": ["core-tracker-schema", "core-tracker-ui"],
                        "include_graph": False,
                    },
                ),
                OperationExample(
                    title="Fetch workflow-filtered tracker graph",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "core.review",
                        "include_graph": True,
                    },
                ),
            ),
        ),
        _read_spec(
            name="tracker.next",
            summary="List ready tickets ranked by priority and dependency state.",
            input_model=TrackerNextInput,
            output_model=TrackerNextOut | dict[str, Any],
            handler=tracker_next,
            purpose="Use this before picking work so the agent does not invent the next ticket.",
            examples=(OperationExample(title="List next work", arguments={"project_id": 1}),),
        ),
        _read_spec(
            name="tracker.blockers",
            summary="List open tickets blocked by explicit blockers or incomplete dependencies.",
            input_model=TrackerProjectInput,
            output_model=TrackerNextOut | dict[str, Any],
            handler=tracker_blockers,
            purpose="Use this to triage stuck project work.",
        ),
        _read_spec(
            name="tracker.brief",
            summary="Get one ticket with task, dependency, dependent, reference, and link context.",
            input_model=TrackerTicketInput,
            output_model=TrackerBriefOut | dict[str, Any],
            handler=tracker_brief,
            purpose=(
                "Use this before doing ticket work. It is the agent-friendly "
                "bounded context packet."
            ),
        ),
        _read_spec(
            name="tracker.why",
            summary="Explain why a ticket exists by returning its linked task and provenance.",
            input_model=TrackerTicketInput,
            output_model=TrackerBriefOut | dict[str, Any],
            handler=tracker_why,
            purpose="Use this when the agent or operator needs provenance and dependency context.",
        ),
        _read_spec(
            name="tracker.execute",
            summary="Return the ticket execution brief without changing state.",
            input_model=TrackerTicketInput,
            output_model=TrackerBriefOut | dict[str, Any],
            handler=tracker_execute,
            purpose=(
                "Use this as the llm-tracker-style execution context before "
                "making code/tool changes."
            ),
        ),
        _read_spec(
            name="tracker.verify",
            summary="Check whether a ticket is verification-ready and what remains.",
            input_model=TrackerTicketInput,
            output_model=TrackerVerifyOut | dict[str, Any],
            handler=tracker_verify,
            purpose="Use this before marking a ticket complete or asking a human for signoff.",
        ),
        _read_spec(
            name="tracker.history",
            summary="Read append-only tracker revisions.",
            input_model=TrackerHistoryInput,
            output_model=Page[TrackerHistoryOut] | dict[str, Any],
            handler=tracker_history,
            purpose="Use this for audit and context recovery across prior runs.",
        ),
        _read_spec(
            name="tracker.changed",
            summary="Read tracker changes after a known revision.",
            input_model=TrackerChangedInput,
            output_model=TrackerChangedOut | dict[str, Any],
            handler=tracker_changed,
            purpose="Use this for efficient context refresh without reloading the whole tracker.",
        ),
        _read_spec(
            name="tracker.search",
            summary="Search project tracker tasks and tickets.",
            input_model=TrackerSearchInput,
            output_model=TrackerSearchOut | dict[str, Any],
            handler=tracker_search,
            purpose="Use this when the agent has a keyword but not a task/ticket key.",
        ),
        _write_spec(
            name="tracker.createTask",
            summary="Create one durable project task.",
            input_model=TrackerCreateTaskInput,
            handler=tracker_create_task,
            purpose="Use this when an agent/human has a work objective that can own tickets.",
        ),
        _write_spec(
            name="tracker.createTicket",
            summary="Create one executable ticket or validate/create a ticket list under a task.",
            input_model=TrackerCreateTicketInput,
            handler=tracker_create_ticket,
            purpose=(
                "Use this to split a task into clear executable units and dependencies. "
                "Pass tickets_json with dry_run=true to draft/review a list before "
                "creating tickets."
            ),
            examples=(
                OperationExample(
                    title="Dry-run a ticket list",
                    arguments={
                        "project_id": 1,
                        "task_key": "core-tracker",
                        "tickets_json": [
                            {"key": "core-tracker-schema", "title": "Add tracker schema"},
                            {
                                "key": "core-tracker-ui",
                                "title": "Expose tracker UI",
                                "dependency_keys": ["core-tracker-schema"],
                            },
                        ],
                        "dry_run": True,
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.updateTask",
            summary="Update one task using an explicit patch.",
            input_model=TrackerUpdateTaskInput,
            handler=tracker_update_task,
            purpose="Use this for task status, ownership, lane, priority, and metadata updates.",
        ),
        _write_spec(
            name="tracker.updateTicket",
            summary="Update one ticket or apply a list of atomic ticket patches.",
            input_model=TrackerUpdateTicketInput,
            handler=tracker_update_ticket,
            purpose=(
                "Use this for ticket status, assignee, blockers, outcome, evidence, and "
                "dependencies. Pass updates_json when several tickets need independent "
                "patch-only updates by ticket_key or ticket_id. For dependency changes, "
                "prefer add_dependency_keys/remove_dependency_keys unless replacing the "
                "whole dependency list. Add dry_run=true to preview dependency diffs "
                "without writing before a larger cleanup."
            ),
            examples=(
                OperationExample(
                    title="Patch a ticket list with safe dependency edits",
                    arguments={
                        "project_id": 1,
                        "updates_json": [
                            {
                                "ticket_key": "core-tracker-schema",
                                "patch_json": {"status": "complete"},
                            },
                            {
                                "ticket_key": "core-tracker-ui",
                                "patch_json": {
                                    "assignee": "codex",
                                    "add_dependency_keys": ["core-tracker-api"],
                                    "remove_dependency_keys": ["core-tracker-schema"],
                                },
                            },
                        ],
                    },
                ),
                OperationExample(
                    title="Preview dependency changes before applying",
                    arguments={
                        "project_id": 1,
                        "ticket_key": "core-tracker-ui",
                        "patch_json": {
                            "add_dependency_keys": ["core-tracker-api"],
                            "remove_dependency_keys": ["core-tracker-schema"],
                        },
                        "dry_run": True,
                    },
                ),
            ),
        ),
        _write_spec(
            name="tracker.patch",
            summary="Apply a small multi-entity task/ticket patch.",
            input_model=TrackerPatchInput,
            handler=tracker_patch,
            purpose="Use this when several tracker updates are part of one agent decision.",
        ),
        _write_spec(
            name="tracker.pick",
            summary="Claim a ready ticket or one explicit ticket for an assignee.",
            input_model=TrackerPickInput,
            handler=tracker_pick,
            purpose="Use this before work starts so ownership and in-progress state are recorded.",
        ),
        _write_spec(
            name="tracker.release",
            summary="Release one ticket's assignee without changing its status.",
            input_model=TrackerReleaseInput,
            handler=tracker_release,
            purpose="Use this when an agent stops owning a ticket.",
        ),
        _write_spec(
            name="tracker.linkRunPlan",
            summary="Link an existing task to a run plan.",
            input_model=TrackerLinkRunPlanInput,
            handler=tracker_link_run_plan,
            purpose=(
                "Use this when a manually created task should point at workflow execution state."
            ),
        ),
    ]


__all__ = ["operation_specs"]
