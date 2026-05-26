"""Project task tracker repository.

The tracker is StackOS-native state for agent work management. It deliberately
does not contain workflow/business logic: agents create, claim, update, and
link work; StackOS persists state, validates transitions, mirrors run-plan
lifecycle events, and exposes bounded read models for humans and agents.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, overload

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlmodel import Session, col, select

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.db.models import (
    TRACKER_ITEM_STATUS_TRANSITIONS,
    AgentRequest,
    Project,
    RunPlan,
    RunPlanStep,
    RunPlanStepStatus,
    TaskTracker,
    TaskTrackerLane,
    TaskTrackerPriority,
    TrackerItemStatus,
    TrackerLinkKind,
    TrackerRevision,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketDependency,
    TrackerTicketKind,
    TrackerTicketLink,
    TrackerTicketReference,
)
from stackos.repositories.base import (
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
    validate_transition,
)

DEFAULT_TRACKER_KEY = "default"
DEFAULT_LANES: tuple[tuple[str, str], ...] = (
    ("planning", "Planning"),
    ("implementation", "Implementation"),
    ("verification", "Verification"),
    ("done", "Done"),
)
DEFAULT_PRIORITIES: tuple[tuple[str, str, int], ...] = (
    ("p0", "P0", 0),
    ("p1", "P1", 10),
    ("p2", "P2", 20),
    ("p3", "P3", 30),
)
TERMINAL_TICKET_STATUSES = {
    TrackerItemStatus.COMPLETE,
    TrackerItemStatus.DEFERRED,
}
DEPENDENCY_PATCH_FIELDS = {
    "dependency_keys",
    "add_dependency_keys",
    "remove_dependency_keys",
}


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _clean_text(value: str | None) -> str:
    return redact_secret_text(str(value or "")).strip()


def _slug(value: str, *, fallback: str = "item", max_length: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length].strip("-") or fallback


def _status_value(value: TrackerItemStatus | str) -> TrackerItemStatus:
    if isinstance(value, TrackerItemStatus):
        return value
    return TrackerItemStatus(value)


def _required_id(value: int | None, entity: str) -> int:
    if value is None:
        raise RuntimeError(f"{entity} id is required after the row has been flushed")
    return value


class TrackerLaneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    position: int


class TrackerPriorityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    rank: int
    position: int


class TrackerSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    key: str
    name: str
    description: str
    rev: int
    created_at: datetime
    updated_at: datetime


class TrackerReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_id: int
    ref_type: str
    ref: str
    title: str | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class TrackerLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int | None
    ticket_id: int | None
    link_kind: TrackerLinkKind
    ref: str | None
    run_plan_id: int | None
    run_plan_step_id: int | None
    run_id: int | None
    agent_request_id: int | None
    resource_record_id: int | None
    artifact_id: int | None
    action_call_id: int | None
    title: str | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class TrackerTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    tracker_id: int
    key: str
    title: str
    goal: str
    description: str
    status: TrackerItemStatus
    priority_key: str
    lane_key: str
    owner: str | None
    task_type: str
    order_index: int
    source_kind: TrackerSourceKind
    source_json: dict[str, Any] | None
    definition_of_done_json: list[str]
    constraints_json: list[str]
    expected_outcomes_json: list[str]
    completion_evidence_json: dict[str, Any] | None
    context_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TrackerDependencyOut(BaseModel):
    id: int
    ticket_key: str
    depends_on_ticket_key: str
    dependency_type: str
    metadata_json: dict[str, Any] | None = None


class TrackerTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    tracker_id: int
    task_id: int
    task_key: str = ""
    parent_ticket_id: int | None
    parent_ticket_key: str | None = None
    run_plan_id: int | None
    run_plan_step_id: int | None
    run_id: int | None
    agent_request_id: int | None
    key: str
    title: str
    goal: str
    status: TrackerItemStatus
    kind: TrackerTicketKind
    assignee: str | None
    priority_key: str
    lane_key: str
    order_index: int
    blocker_reason: str | None
    outcome: str | None
    effort: str | None
    source_kind: TrackerSourceKind
    source_json: dict[str, Any] | None
    definition_of_done_json: list[str]
    constraints_json: list[str]
    expected_changes_json: list[str]
    allowed_paths_json: list[str]
    completion_evidence_json: dict[str, Any] | None
    context_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_by: str | None
    claimed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    dependency_keys: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    reference_count: int = 0
    link_count: int = 0


class TrackerGraphNodeOut(BaseModel):
    id: str
    type: Literal["task", "ticket", "group"]
    parent_id: str | None = None
    label: str
    status: str
    lane_key: str
    priority_key: str
    data: dict[str, Any] = Field(default_factory=dict)


class TrackerGraphEdgeOut(BaseModel):
    id: str
    type: Literal["contains", "dependency", "link"]
    source: str
    target: str
    label: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class TrackerGraphOut(BaseModel):
    nodes: list[TrackerGraphNodeOut] = Field(default_factory=list)
    edges: list[TrackerGraphEdgeOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    layout_hints: dict[str, Any] = Field(default_factory=dict)


class TrackerSnapshotOut(BaseModel):
    tracker: TrackerSummaryOut
    lanes: list[TrackerLaneOut]
    priorities: list[TrackerPriorityOut]
    tasks: list[TrackerTaskOut]
    tickets: list[TrackerTicketOut]
    dependencies: list[TrackerDependencyOut]
    links: list[TrackerLinkOut]
    graph: TrackerGraphOut | None = None


class TrackerStatusOut(BaseModel):
    tracker: TrackerSummaryOut
    task_counts: dict[str, int]
    ticket_counts: dict[str, int]
    blocked_ticket_count: int
    ready_ticket_count: int
    in_progress_ticket_count: int
    rev: int


class TrackerNextOut(BaseModel):
    tickets: list[TrackerTicketOut]
    blocked: list[TrackerTicketOut] = Field(default_factory=list)
    explanation: str


class TrackerBriefOut(BaseModel):
    ticket: TrackerTicketOut
    task: TrackerTaskOut
    dependencies: list[TrackerTicketOut]
    dependents: list[TrackerTicketOut]
    references: list[TrackerReferenceOut]
    links: list[TrackerLinkOut]
    suggested_next_actions: list[str]


class TrackerVerifyOut(BaseModel):
    ticket: TrackerTicketOut
    ready: bool
    checks: list[dict[str, Any]]
    suggested_next_actions: list[str]


class TrackerHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rev: int
    actor: str | None
    change_kind: str
    entity_kind: str
    entity_id: int | None
    entity_key: str | None
    summary: str
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    patch_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class TrackerChangedOut(BaseModel):
    since_rev: int | None
    current_rev: int
    changes: list[TrackerHistoryOut]


class TrackerSearchOut(BaseModel):
    tasks: list[TrackerTaskOut]
    tickets: list[TrackerTicketOut]


class TrackerListIssueOut(BaseModel):
    index: int | None = None
    key: str | None = None
    field: str | None = None
    message: str


class TrackerDependencyPreviewOut(BaseModel):
    ticket_key: str
    mode: Literal["none", "replace", "add-remove"]
    current_dependency_keys: list[str] = Field(default_factory=list)
    final_dependency_keys: list[str] = Field(default_factory=list)
    added_dependency_keys: list[str] = Field(default_factory=list)
    removed_dependency_keys: list[str] = Field(default_factory=list)
    kept_dependency_keys: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TrackerListItemResultOut(BaseModel):
    index: int
    action: Literal["validated", "created", "updated", "skipped", "noop", "error"]
    key: str | None = None
    id: int | None = None
    changed_fields: list[str] = Field(default_factory=list)
    dependency_preview: TrackerDependencyPreviewOut | None = None
    ticket: TrackerTicketOut | None = None
    error: str | None = None


class TrackerMutationOut(BaseModel):
    tracker: TrackerSummaryOut
    task: TrackerTaskOut | None = None
    ticket: TrackerTicketOut | None = None
    tickets: list[TrackerTicketOut] = Field(default_factory=list)
    dependencies: list[TrackerDependencyOut] = Field(default_factory=list)
    dependency_preview: TrackerDependencyPreviewOut | None = None
    results: list[TrackerListItemResultOut] = Field(default_factory=list)
    errors: list[TrackerListIssueOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    valid: bool = True
    dry_run: bool = False
    rev: int


class TrackerRepository:
    """Project-scoped task tracker state and run-plan mirroring."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _tracker_or_none(
        self,
        *,
        project_id: int,
        key: str = DEFAULT_TRACKER_KEY,
    ) -> TaskTracker | None:
        self._require_project(project_id)
        return self._s.exec(
            select(TaskTracker).where(TaskTracker.project_id == project_id, TaskTracker.key == key)
        ).first()

    def _empty_tracker_out(self, *, project_id: int) -> TrackerSummaryOut:
        now = _utcnow()
        return TrackerSummaryOut(
            id=0,
            project_id=project_id,
            key=DEFAULT_TRACKER_KEY,
            name="Project task tracker",
            description="Default project tracker for workflow and direct agent work.",
            rev=0,
            created_at=now,
            updated_at=now,
        )

    def _default_lane_out(self) -> list[TrackerLaneOut]:
        return [
            TrackerLaneOut(key=key, label=label, position=position)
            for position, (key, label) in enumerate(DEFAULT_LANES)
        ]

    def _default_priority_out(self) -> list[TrackerPriorityOut]:
        return [
            TrackerPriorityOut(key=key, label=label, rank=rank, position=position)
            for position, (key, label, rank) in enumerate(DEFAULT_PRIORITIES)
        ]

    def ensure_tracker(
        self,
        *,
        project_id: int,
        key: str = DEFAULT_TRACKER_KEY,
    ) -> TaskTracker:
        row = self._tracker_or_none(project_id=project_id, key=key)
        if row is not None:
            return row
        now = _utcnow()
        row = TaskTracker(
            project_id=project_id,
            key=key,
            name="Project task tracker",
            description="Default project tracker for workflow and direct agent work.",
            rev=0,
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        self._s.flush()
        assert row.id is not None
        for position, (lane_key, label) in enumerate(DEFAULT_LANES):
            self._s.add(
                TaskTrackerLane(
                    tracker_id=row.id,
                    key=lane_key,
                    label=label,
                    position=position,
                    created_at=now,
                    updated_at=now,
                )
            )
        for position, (priority_key, label, rank) in enumerate(DEFAULT_PRIORITIES):
            self._s.add(
                TaskTrackerPriority(
                    tracker_id=row.id,
                    key=priority_key,
                    label=label,
                    rank=rank,
                    position=position,
                    created_at=now,
                    updated_at=now,
                )
            )
        self._record_revision(
            row,
            actor="system",
            change_kind="create",
            entity_kind="tracker",
            entity_id=row.id,
            entity_key=row.key,
            summary="Created default project tracker.",
            commit=False,
        )
        self._s.flush()
        return row

    def status(self, *, project_id: int) -> TrackerStatusOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerStatusOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                task_counts=self._count_statuses([]),
                ticket_counts=self._count_statuses([]),
                blocked_ticket_count=0,
                ready_ticket_count=0,
                in_progress_ticket_count=0,
                rev=0,
            )
        tasks = self._task_rows(tracker.id)
        tickets = self._ticket_rows(tracker.id)
        return TrackerStatusOut(
            tracker=self._tracker_out(tracker),
            task_counts=self._count_statuses([task.status for task in tasks]),
            ticket_counts=self._count_statuses([ticket.status for ticket in tickets]),
            blocked_ticket_count=sum(
                1 for ticket in tickets if self._ticket_blocks_active_work(tracker.id, ticket)
            ),
            ready_ticket_count=len(self._ready_ticket_rows(tracker.id, tickets=tickets)),
            in_progress_ticket_count=sum(
                1 for ticket in tickets if ticket.status == TrackerItemStatus.IN_PROGRESS
            ),
            rev=tracker.rev,
        )

    def get(
        self,
        *,
        project_id: int,
        statuses: list[TrackerItemStatus] | None = None,
        task_key: str | None = None,
        ticket_keys: list[str] | None = None,
        ticket_ids: list[int] | None = None,
        block_state: Literal["blocked", "open"] | None = None,
        dependency_ticket_key: str | None = None,
        workflow_key: str | None = None,
        run_plan_id: int | None = None,
        assignee: str | None = None,
        include_graph: bool = True,
    ) -> TrackerSnapshotOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerSnapshotOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                lanes=self._default_lane_out(),
                priorities=self._default_priority_out(),
                tasks=[],
                tickets=[],
                dependencies=[],
                links=[],
                graph=self._graph_out([], [], [], []) if include_graph else None,
            )
        tasks = self._task_rows(tracker.id)
        tickets = self._ticket_rows(tracker.id)
        if task_key is not None:
            task = self._task_by_key(tracker.id, task_key)
            tasks = [task]
            tickets = [ticket for ticket in tickets if ticket.task_id == task.id]
        if ticket_keys:
            key_set = {str(key) for key in ticket_keys}
            tickets = [ticket for ticket in tickets if ticket.key in key_set]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if ticket_ids:
            id_set = set(ticket_ids)
            tickets = [ticket for ticket in tickets if ticket.id in id_set]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if dependency_ticket_key is not None:
            focus = self._ticket_by_key(tracker.id, dependency_ticket_key)
            related_ids = {focus.id}
            related_ids.update(
                dep.depends_on_ticket_id for dep in self._dependency_rows_for_ticket(focus.id)
            )
            related_ids.update(dep.ticket_id for dep in self._dependent_rows_for_ticket(focus.id))
            tickets = [ticket for ticket in tickets if ticket.id in related_ids]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if statuses:
            status_set = set(statuses)
            tickets = [ticket for ticket in tickets if ticket.status in status_set]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids or task.status in status_set]
        if block_state:
            want_blocked = block_state == "blocked"
            tickets = [
                ticket
                for ticket in tickets
                if self._ticket_blocks_active_work(tracker.id, ticket) == want_blocked
            ]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if workflow_key is not None:
            tasks = [
                task
                for task in tasks
                if (task.source_json or {}).get("template_key") == workflow_key
                or (task.source_json or {}).get("run_plan_key") == workflow_key
            ]
            task_ids = {task.id for task in tasks if task.id is not None}
            tickets = [ticket for ticket in tickets if ticket.task_id in task_ids]
        if run_plan_id is not None:
            tickets = [ticket for ticket in tickets if ticket.run_plan_id == run_plan_id]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if assignee is not None:
            tickets = [ticket for ticket in tickets if ticket.assignee == assignee]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]

        task_out = [self._task_out(task) for task in tasks]
        ticket_out = self._ticket_out_many(tickets)
        dependencies = self._dependency_out_for_tickets(tickets)
        links = self._link_out_for_scope(
            tracker.id,
            {task.id for task in tasks},
            {ticket.id for ticket in tickets},
        )
        graph = (
            self._graph_out(task_out, ticket_out, dependencies, links) if include_graph else None
        )
        return TrackerSnapshotOut(
            tracker=self._tracker_out(tracker),
            lanes=self._lane_out(tracker.id),
            priorities=self._priority_out(tracker.id),
            tasks=task_out,
            tickets=ticket_out,
            dependencies=dependencies,
            links=links,
            graph=graph,
        )

    def create_task(
        self,
        *,
        project_id: int,
        key: str,
        title: str,
        goal: str = "",
        description: str = "",
        status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED,
        priority_key: str = "p2",
        lane_key: str = "implementation",
        owner: str | None = None,
        task_type: str = "task",
        source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL,
        source_json: dict[str, Any] | None = None,
        definition_of_done_json: list[str] | None = None,
        constraints_json: list[str] | None = None,
        expected_outcomes_json: list[str] | None = None,
        completion_evidence_json: dict[str, Any] | None = None,
        context_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        created_by: str | None = None,
        create_default_ticket: bool = False,
        commit: bool = True,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        key = _slug(key, fallback="task", max_length=180)
        if self._task_by_key(tracker.id, key, missing_ok=True) is not None:
            raise ConflictError("tracker task key already exists", data={"task_key": key})
        now = _utcnow()
        row = TrackerTask(
            tracker_id=tracker.id,
            project_id=project_id,
            key=key,
            title=_clean_text(title) or key,
            goal=_clean_text(goal),
            description=_clean_text(description),
            status=status,
            priority_key=priority_key,
            lane_key=lane_key,
            owner=owner,
            task_type=task_type,
            order_index=self._next_task_position(tracker.id),
            source_kind=source_kind,
            source_json=redact_secrets(source_json) if source_json is not None else None,
            definition_of_done_json=definition_of_done_json or [],
            constraints_json=constraints_json or [],
            expected_outcomes_json=expected_outcomes_json or [],
            completion_evidence_json=(
                redact_secrets(completion_evidence_json)
                if completion_evidence_json is not None
                else None
            ),
            context_json=redact_secrets(context_json) if context_json is not None else None,
            metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            started_at=now if status == TrackerItemStatus.IN_PROGRESS else None,
            completed_at=now if status == TrackerItemStatus.COMPLETE else None,
        )
        self._s.add(row)
        self._s.flush()
        assert row.id is not None
        created_tickets: list[TrackerTicket] = []
        if create_default_ticket:
            created_tickets.append(
                self._create_ticket_row(
                    tracker=tracker,
                    task=row,
                    key=f"{key}-work",
                    title=title,
                    goal=goal,
                    status=status,
                    priority_key=priority_key,
                    lane_key=lane_key,
                    assignee=owner,
                    source_kind=source_kind,
                    source_json=source_json,
                    definition_of_done_json=definition_of_done_json,
                    constraints_json=constraints_json,
                    expected_changes_json=expected_outcomes_json,
                    completion_evidence_json=completion_evidence_json,
                    created_by=created_by,
                    now=now,
                )
            )
        self._record_revision(
            tracker,
            actor=created_by,
            change_kind="create",
            entity_kind="task",
            entity_id=row.id,
            entity_key=row.key,
            summary=f"Created task {row.key}.",
            after_json=self._task_snapshot(row),
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(row)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(row),
                tickets=self._ticket_out_many(created_tickets),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def create_ticket(
        self,
        *,
        project_id: int,
        task_key: str,
        key: str,
        title: str,
        goal: str = "",
        status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED,
        kind: TrackerTicketKind = TrackerTicketKind.TICKET,
        assignee: str | None = None,
        priority_key: str = "p2",
        lane_key: str = "implementation",
        parent_ticket_key: str | None = None,
        dependency_keys: list[str] | None = None,
        blocker_reason: str | None = None,
        outcome: str | None = None,
        effort: str | None = None,
        source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL,
        source_json: dict[str, Any] | None = None,
        definition_of_done_json: list[str] | None = None,
        constraints_json: list[str] | None = None,
        expected_changes_json: list[str] | None = None,
        allowed_paths_json: list[str] | None = None,
        references_json: list[dict[str, Any]] | None = None,
        completion_evidence_json: dict[str, Any] | None = None,
        context_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        created_by: str | None = None,
        commit: bool = True,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        task = self._task_by_key(tracker.id, task_key)
        key = _slug(key, fallback="ticket", max_length=180)
        if self._ticket_by_key(tracker.id, key, missing_ok=True) is not None:
            raise ConflictError("tracker ticket key already exists", data={"ticket_key": key})
        parent_id: int | None = None
        if parent_ticket_key:
            parent = self._ticket_by_key(tracker.id, parent_ticket_key)
            parent_id = parent.id
        now = _utcnow()
        ticket = self._create_ticket_row(
            tracker=tracker,
            task=task,
            key=key,
            title=title,
            goal=goal,
            status=status,
            kind=kind,
            assignee=assignee,
            priority_key=priority_key,
            lane_key=lane_key,
            parent_ticket_id=parent_id,
            blocker_reason=blocker_reason,
            outcome=outcome,
            effort=effort,
            source_kind=source_kind,
            source_json=source_json,
            definition_of_done_json=definition_of_done_json,
            constraints_json=constraints_json,
            expected_changes_json=expected_changes_json,
            allowed_paths_json=allowed_paths_json,
            completion_evidence_json=completion_evidence_json,
            context_json=context_json,
            metadata_json=metadata_json,
            created_by=created_by,
            now=now,
        )
        for dependency_key in dependency_keys or []:
            dependency = self._ticket_by_key(tracker.id, dependency_key)
            self._add_dependency(tracker, ticket, dependency)
        for reference in references_json or []:
            self._add_reference(tracker, ticket, reference)
        self._sync_task_status(task, now=now)
        self._record_revision(
            tracker,
            actor=created_by,
            change_kind="create",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Created ticket {ticket.key}.",
            after_json=self._ticket_snapshot(ticket),
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(ticket)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                ticket=self._ticket_out(ticket),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def update_task(
        self,
        *,
        project_id: int,
        task_key: str,
        patch_json: dict[str, Any],
        actor: str | None = None,
        commit: bool = True,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        task = self._task_by_key(tracker.id, task_key)
        before = self._task_snapshot(task)
        self._apply_task_patch(task, patch_json)
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="update",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Updated task {task.key}.",
            before_json=before,
            after_json=self._task_snapshot(task),
            patch_json=patch_json,
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(task)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def update_ticket(
        self,
        *,
        project_id: int,
        ticket_key: str,
        patch_json: dict[str, Any],
        actor: str | None = None,
        commit: bool = True,
        dry_run: bool = False,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        ticket = self._ticket_by_key(tracker.id, ticket_key)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is None:
            raise NotFoundError("ticket task not found", data={"ticket_key": ticket_key})
        if dry_run:
            dependency_preview = self._preview_dependency_patch(tracker, ticket, patch_json)
            warnings = dependency_preview.warnings if dependency_preview is not None else []
            return Envelope(
                data=TrackerMutationOut(
                    tracker=self._tracker_out(tracker),
                    task=self._task_out(task),
                    ticket=self._ticket_out(ticket),
                    dependency_preview=dependency_preview,
                    results=[
                        TrackerListItemResultOut(
                            index=0,
                            action="validated",
                            key=ticket.key,
                            id=ticket.id,
                            changed_fields=list(patch_json.keys()),
                            dependency_preview=dependency_preview,
                            ticket=self._ticket_out(ticket),
                        )
                    ],
                    warnings=warnings,
                    dry_run=True,
                    rev=tracker.rev,
                ),
                project_id=project_id,
            )
        before = self._ticket_snapshot(ticket)
        self._apply_ticket_patch(tracker, ticket, patch_json)
        self._sync_task_status(task, now=_utcnow())
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="update",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Updated ticket {ticket.key}.",
            before_json=before,
            after_json=self._ticket_snapshot(ticket),
            patch_json=patch_json,
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(ticket)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                ticket=self._ticket_out(ticket),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def patch(
        self,
        *,
        project_id: int,
        patch_json: dict[str, Any],
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        changed_tickets: list[TrackerTicket] = []
        changed_task: TrackerTask | None = None
        tasks_patch = patch_json.get("tasks")
        tickets_patch = patch_json.get("tickets")
        if "tasks" not in patch_json and "tickets" not in patch_json:
            raise ValidationError("patch_json must include tasks or tickets")
        if tasks_patch is not None and not isinstance(tasks_patch, dict):
            raise ValidationError("patch_json.tasks must be an object keyed by task key")
        if tickets_patch is not None and not isinstance(tickets_patch, dict):
            raise ValidationError("patch_json.tickets must be an object keyed by ticket key")
        if isinstance(tasks_patch, dict):
            for task_key, task_patch in tasks_patch.items():
                if not isinstance(task_patch, dict):
                    raise ValidationError("task patch entries must be objects")
                task = self._task_by_key(tracker.id, str(task_key))
                before = self._task_snapshot(task)
                self._apply_task_patch(task, task_patch)
                changed_task = task
                self._record_revision(
                    tracker,
                    actor=actor,
                    change_kind="update",
                    entity_kind="task",
                    entity_id=task.id,
                    entity_key=task.key,
                    summary=f"Patched task {task.key}.",
                    before_json=before,
                    after_json=self._task_snapshot(task),
                    patch_json=task_patch,
                    commit=False,
                )
        if isinstance(tickets_patch, dict):
            for ticket_key, ticket_patch in tickets_patch.items():
                if not isinstance(ticket_patch, dict):
                    raise ValidationError("ticket patch entries must be objects")
                ticket = self._ticket_by_key(tracker.id, str(ticket_key))
                ticket_task = self._s.get(TrackerTask, ticket.task_id)
                if ticket_task is None:
                    raise NotFoundError("ticket task not found", data={"ticket_key": ticket.key})
                before = self._ticket_snapshot(ticket)
                self._apply_ticket_patch(tracker, ticket, ticket_patch)
                self._sync_task_status(ticket_task, now=_utcnow())
                changed_task = ticket_task
                changed_tickets.append(ticket)
                self._record_revision(
                    tracker,
                    actor=actor,
                    change_kind="update",
                    entity_kind="ticket",
                    entity_id=ticket.id,
                    entity_key=ticket.key,
                    summary=f"Patched ticket {ticket.key}.",
                    before_json=before,
                    after_json=self._ticket_snapshot(ticket),
                    patch_json=ticket_patch,
                    commit=False,
                )
        self._s.commit()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(changed_task) if changed_task is not None else None,
                tickets=self._ticket_out_many(changed_tickets),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def validate_ticket_list(
        self,
        *,
        project_id: int,
        ticket_list_json: dict[str, Any],
    ) -> TrackerMutationOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerMutationOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                errors=[
                    TrackerListIssueOut(
                        field="task_key",
                        message="tracker task not found for list validation",
                    )
                ],
                valid=False,
                dry_run=True,
                rev=0,
            )
        task, specs, errors, warnings = self._prepare_ticket_list(tracker, ticket_list_json)
        return TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            task=self._task_out(task) if task is not None else None,
            results=[
                TrackerListItemResultOut(index=spec["index"], action="validated", key=spec["key"])
                for spec in specs
            ],
            errors=errors,
            warnings=warnings,
            valid=not errors,
            dry_run=True,
            rev=tracker.rev,
        )

    def create_ticket_list(
        self,
        *,
        project_id: int,
        ticket_list_json: dict[str, Any],
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            out = TrackerMutationOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                errors=[
                    TrackerListIssueOut(
                        field="task_key",
                        message="tracker task not found for list create",
                    )
                ],
                valid=False,
                rev=0,
            )
            return Envelope(data=out, project_id=project_id)
        task, specs, errors, warnings = self._prepare_ticket_list(tracker, ticket_list_json)
        if task is None or errors:
            out = TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task) if task is not None else None,
                results=[
                    TrackerListItemResultOut(index=spec["index"], action="error", key=spec["key"])
                    for spec in specs
                ],
                errors=errors,
                warnings=warnings,
                valid=False,
                rev=tracker.rev,
            )
            return Envelope(data=out, project_id=project_id)

        now = _utcnow()
        created: list[TrackerTicket] = []
        created_by_key: dict[str, TrackerTicket] = {}
        for spec in specs:
            ticket = self._create_ticket_row(
                tracker=tracker,
                task=task,
                key=spec["key"],
                title=spec["title"],
                goal=spec["goal"],
                status=spec["status"],
                kind=spec["kind"],
                assignee=spec["assignee"],
                priority_key=spec["priority_key"],
                lane_key=spec["lane_key"],
                blocker_reason=spec["blocker_reason"],
                outcome=spec["outcome"],
                effort=spec["effort"],
                source_kind=spec["source_kind"],
                source_json=spec["source_json"],
                definition_of_done_json=spec["definition_of_done_json"],
                constraints_json=spec["constraints_json"],
                expected_changes_json=spec["expected_changes_json"],
                allowed_paths_json=spec["allowed_paths_json"],
                completion_evidence_json=spec["completion_evidence_json"],
                context_json=spec["context_json"],
                metadata_json=spec["metadata_json"],
                created_by=spec["created_by"] or actor,
                now=now,
            )
            created.append(ticket)
            created_by_key[ticket.key] = ticket

        for spec in specs:
            ticket = created_by_key[spec["key"]]
            parent_key = spec["parent_ticket_key"]
            if parent_key:
                parent = created_by_key.get(parent_key) or self._ticket_by_key(
                    tracker.id, parent_key
                )
                ticket.parent_ticket_id = parent.id
                self._s.add(ticket)
            for dependency_key in spec["dependency_keys"]:
                dependency = created_by_key.get(dependency_key) or self._ticket_by_key(
                    tracker.id, dependency_key
                )
                self._add_dependency(tracker, ticket, dependency)
            for reference in spec["references_json"]:
                self._add_reference(tracker, ticket, reference)
            self._record_revision(
                tracker,
                actor=actor or spec["created_by"],
                change_kind="list-create",
                entity_kind="ticket",
                entity_id=ticket.id,
                entity_key=ticket.key,
                summary=f"Created ticket {ticket.key} from list.",
                after_json=self._ticket_snapshot(ticket),
                commit=False,
            )

        self._sync_task_status(task, now=now)
        self._s.commit()
        for ticket in created:
            self._s.refresh(ticket)
        out = TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            task=self._task_out(task),
            tickets=self._ticket_out_many(created),
            dependencies=self._dependency_out_for_tickets(created),
            results=[
                TrackerListItemResultOut(
                    index=spec["index"],
                    action="created",
                    key=spec["key"],
                    id=created_by_key[spec["key"]].id,
                    ticket=self._ticket_out(created_by_key[spec["key"]]),
                )
                for spec in specs
            ],
            warnings=warnings,
            valid=True,
            rev=tracker.rev,
        )
        return Envelope(data=out, project_id=project_id)

    def update_ticket_list(
        self,
        *,
        project_id: int,
        updates_json: list[dict[str, Any]],
        actor: str | None = None,
        dry_run: bool = False,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            out = TrackerMutationOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                errors=[TrackerListIssueOut(message="tracker not found for ticket list update")],
                valid=False,
                rev=0,
            )
            return Envelope(data=out, project_id=project_id)
        if not isinstance(updates_json, list):
            raise ValidationError("updates_json must be a list")
        if dry_run:
            return self._preview_ticket_list_update(
                project_id=project_id,
                tracker=tracker,
                updates_json=updates_json,
            )

        results: list[TrackerListItemResultOut] = []
        changed: list[TrackerTicket] = []
        for index, item in enumerate(updates_json):
            if not isinstance(item, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        error="update entries must be objects",
                    )
                )
                continue
            patch_json = item.get("patch_json", item.get("patch"))
            if not isinstance(patch_json, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error="update entry patch_json must be an object",
                    )
                )
                continue
            if not patch_json:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="noop",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                    )
                )
                continue
            try:
                with self._s.begin_nested():
                    ticket = self._ticket_from_list_update(tracker.id, item)
                    task = self._s.get(TrackerTask, ticket.task_id)
                    if task is None:
                        raise NotFoundError(
                            "ticket task not found", data={"ticket_key": ticket.key}
                        )
                    before = self._ticket_snapshot(ticket)
                    self._apply_ticket_patch(tracker, ticket, patch_json)
                    self._sync_task_status(task, now=_utcnow())
                    self._record_revision(
                        tracker,
                        actor=actor,
                        change_kind="list-update",
                        entity_kind="ticket",
                        entity_id=ticket.id,
                        entity_key=ticket.key,
                        summary=f"Updated ticket {ticket.key} from list.",
                        before_json=before,
                        after_json=self._ticket_snapshot(ticket),
                        patch_json=patch_json,
                        commit=False,
                    )
                changed.append(ticket)
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="updated",
                        key=ticket.key,
                        id=ticket.id,
                        changed_fields=list(patch_json.keys()),
                        ticket=self._ticket_out(ticket),
                    )
                )
            except (ConflictError, NotFoundError, ValidationError, ValueError) as exc:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error=str(exc),
                    )
                )

        if changed:
            self._s.commit()
            for ticket in changed:
                self._s.refresh(ticket)
        errors = [
            TrackerListIssueOut(
                index=result.index,
                key=result.key,
                message=result.error or "ticket update failed",
            )
            for result in results
            if result.action == "error"
        ]
        out = TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            tickets=self._ticket_out_many(changed),
            dependencies=self._dependency_out_for_tickets(changed),
            results=results,
            errors=errors,
            valid=not errors,
            rev=tracker.rev,
        )
        return Envelope(data=out, project_id=project_id)

    def next(
        self,
        *,
        project_id: int,
        limit: int = 5,
        assignee: str | None = None,
        include_blocked: bool = True,
    ) -> TrackerNextOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerNextOut(
                tickets=[],
                blocked=[],
                explanation="No tracker work exists for this project yet.",
            )
        tickets = self._ticket_rows(tracker.id)
        if assignee is not None:
            tickets = [ticket for ticket in tickets if ticket.assignee in {None, assignee}]
        ready = self._ready_ticket_rows(tracker.id, tickets=tickets)
        ready = sorted(
            ready,
            key=lambda ticket: (
                self._priority_rank(tracker.id, ticket.priority_key),
                ticket.order_index,
                ticket.id or 0,
            ),
        )[: max(1, min(limit, 50))]
        blocked = []
        if include_blocked:
            blocked = [
                ticket
                for ticket in tickets
                if ticket.status not in TERMINAL_TICKET_STATUSES
                and ticket.key not in {item.key for item in ready}
                and self._ticket_blocks_active_work(tracker.id, ticket)
            ][: max(1, min(limit, 50))]
        return TrackerNextOut(
            tickets=self._ticket_out_many(ready),
            blocked=self._ticket_out_many(blocked),
            explanation=(
                "Ready tickets are non-terminal tickets without incomplete dependencies, "
                "ranked by priority then tracker order."
            ),
        )

    def pick(
        self,
        *,
        project_id: int,
        ticket_key: str | None = None,
        assignee: str,
    ) -> Envelope[TrackerMutationOut]:
        if not assignee.strip():
            raise ValidationError("assignee is required")
        tracker = self.ensure_tracker(project_id=project_id)
        ticket = (
            self._ticket_by_key(tracker.id, ticket_key)
            if ticket_key is not None
            else next(iter(self._ready_ticket_rows(tracker.id)), None)
        )
        if ticket is None:
            raise NotFoundError("no ready tracker ticket found")
        patch = {"status": TrackerItemStatus.IN_PROGRESS.value, "assignee": assignee}
        return self.update_ticket(
            project_id=project_id,
            ticket_key=ticket.key,
            patch_json=patch,
            actor=assignee,
        )

    def release(
        self,
        *,
        project_id: int,
        ticket_key: str,
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        return self.update_ticket(
            project_id=project_id,
            ticket_key=ticket_key,
            patch_json={"assignee": None},
            actor=actor,
        )

    def blockers(self, *, project_id: int) -> TrackerNextOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerNextOut(
                tickets=[],
                blocked=[],
                explanation="No tracker work exists for this project yet.",
            )
        tickets = [
            ticket
            for ticket in self._ticket_rows(tracker.id)
            if self._ticket_blocks_active_work(tracker.id, ticket)
        ]
        return TrackerNextOut(
            tickets=[],
            blocked=self._ticket_out_many(tickets),
            explanation=(
                "Blocked tickets have an explicit blocker_reason or incomplete dependencies."
            ),
        )

    def brief(self, *, project_id: int, ticket_key: str) -> TrackerBriefOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            raise NotFoundError("tracker ticket not found", data={"ticket_key": ticket_key})
        ticket = self._ticket_by_key(tracker.id, ticket_key)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is None:
            raise NotFoundError("ticket task not found", data={"ticket_key": ticket.key})
        dependencies = [
            self._s.get(TrackerTicket, dep.depends_on_ticket_id)
            for dep in self._dependency_rows_for_ticket(ticket.id)
        ]
        dependents = [
            self._s.get(TrackerTicket, dep.ticket_id)
            for dep in self._dependent_rows_for_ticket(ticket.id)
        ]
        return TrackerBriefOut(
            ticket=self._ticket_out(ticket),
            task=self._task_out(task),
            dependencies=self._ticket_out_many([item for item in dependencies if item is not None]),
            dependents=self._ticket_out_many([item for item in dependents if item is not None]),
            references=[
                TrackerReferenceOut.model_validate(row)
                for row in self._reference_rows_for_ticket(ticket.id)
            ],
            links=[
                TrackerLinkOut.model_validate(row) for row in self._link_rows_for_ticket(ticket.id)
            ],
            suggested_next_actions=self._suggest_next_actions(ticket),
        )

    def verify(self, *, project_id: int, ticket_key: str) -> TrackerVerifyOut:
        brief = self.brief(project_id=project_id, ticket_key=ticket_key)
        ticket = brief.ticket
        checks: list[dict[str, Any]] = []
        checks.append(
            {
                "key": "dependencies-complete",
                "passed": all(
                    dep.status == TrackerItemStatus.COMPLETE for dep in brief.dependencies
                ),
                "detail": "All dependency tickets are complete.",
            }
        )
        checks.append(
            {
                "key": "definition-of-done-present",
                "passed": bool(ticket.definition_of_done_json),
                "detail": "Ticket has definition_of_done_json for verification.",
            }
        )
        checks.append(
            {
                "key": "no-open-blocker",
                "passed": ticket.blocker_reason is None,
                "detail": "Ticket has no explicit blocker_reason.",
            }
        )
        return TrackerVerifyOut(
            ticket=ticket,
            ready=all(item["passed"] for item in checks),
            checks=checks,
            suggested_next_actions=self._suggest_next_actions_raw(ticket, checks),
        )

    def history(
        self,
        *,
        project_id: int,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[TrackerHistoryOut]:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return Page(items=[], next_cursor=None, total_estimate=0)
        stmt = select(TrackerRevision).where(TrackerRevision.tracker_id == tracker.id)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=TrackerRevision.id,
            limit=limit,
            after_id=after_id,
            converter=TrackerHistoryOut.model_validate,
        )

    def changed(
        self,
        *,
        project_id: int,
        since_rev: int | None = None,
        limit: int = 50,
    ) -> TrackerChangedOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerChangedOut(since_rev=since_rev, current_rev=0, changes=[])
        stmt = select(TrackerRevision).where(TrackerRevision.tracker_id == tracker.id)
        if since_rev is not None:
            stmt = stmt.where(TrackerRevision.rev > since_rev)
        stmt = stmt.order_by(col(TrackerRevision.rev).asc()).limit(max(1, min(limit, 200)))
        rows = list(self._s.exec(stmt))
        return TrackerChangedOut(
            since_rev=since_rev,
            current_rev=tracker.rev,
            changes=[TrackerHistoryOut.model_validate(row) for row in rows],
        )

    def search(
        self,
        *,
        project_id: int,
        query: str,
        limit: int = 20,
    ) -> TrackerSearchOut:
        needle = f"%{query.strip()}%"
        if not query.strip():
            raise ValidationError("query is required")
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerSearchOut(tasks=[], tickets=[])
        tasks = list(
            self._s.exec(
                select(TrackerTask)
                .where(
                    TrackerTask.tracker_id == tracker.id,
                    or_(
                        col(TrackerTask.key).like(needle),
                        col(TrackerTask.title).like(needle),
                        col(TrackerTask.goal).like(needle),
                        col(TrackerTask.description).like(needle),
                    ),
                )
                .limit(max(1, min(limit, 100)))
            )
        )
        tickets = list(
            self._s.exec(
                select(TrackerTicket)
                .where(
                    TrackerTicket.tracker_id == tracker.id,
                    or_(
                        col(TrackerTicket.key).like(needle),
                        col(TrackerTicket.title).like(needle),
                        col(TrackerTicket.goal).like(needle),
                        col(TrackerTicket.outcome).like(needle),
                    ),
                )
                .limit(max(1, min(limit, 100)))
            )
        )
        return TrackerSearchOut(
            tasks=[self._task_out(row) for row in tasks],
            tickets=self._ticket_out_many(tickets),
        )

    def link_run_plan(
        self,
        *,
        project_id: int,
        task_key: str,
        run_plan_id: int,
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        plan = self._s.get(RunPlan, run_plan_id)
        if plan is None or plan.project_id != project_id:
            raise NotFoundError(
                "run plan not found in project",
                data={"project_id": project_id, "run_plan_id": run_plan_id},
            )
        task = self._task_by_key(tracker.id, task_key)
        self._add_link(
            tracker,
            task_id=task.id,
            link_kind=TrackerLinkKind.RUN_PLAN,
            run_plan_id=run_plan_id,
            ref=f"run-plan:{run_plan_id}",
            title=plan.title,
        )
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="link",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Linked task {task.key} to run plan {run_plan_id}.",
            commit=False,
        )
        self._s.commit()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def mirror_run_plan_created(
        self,
        *,
        plan: RunPlan,
        steps: list[RunPlanStep],
        created_by: str | None = None,
    ) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        task_key = f"workflow-{plan.id}"
        task = self._task_by_key(tracker.id, task_key, missing_ok=True)
        now = _utcnow()
        if task is None:
            task = TrackerTask(
                tracker_id=tracker.id,
                project_id=plan.project_id,
                key=task_key,
                title=_clean_text(plan.title) or task_key,
                goal=_clean_text(plan.goal),
                description=f"Workflow run plan {plan.key}",
                status=TrackerItemStatus.NOT_STARTED,
                priority_key="p1",
                lane_key="planning",
                owner=plan.created_by,
                task_type="workflow",
                order_index=self._next_task_position(tracker.id),
                source_kind=TrackerSourceKind.WORKFLOW,
                source_json={
                    "run_plan_id": plan.id,
                    "run_plan_key": plan.key,
                    "template_key": plan.template_key,
                    "template_version": plan.template_version,
                },
                definition_of_done_json=["All run-plan step tickets reach complete or deferred."],
                constraints_json=[],
                expected_outcomes_json=[],
                context_json=plan.selected_context_json,
                metadata_json={"mirrored_from": "runPlan.create"},
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            self._s.add(task)
            self._s.flush()
            self._add_link(
                tracker,
                task_id=task.id,
                link_kind=TrackerLinkKind.RUN_PLAN,
                run_plan_id=plan.id,
                ref=f"run-plan:{plan.id}",
                title=plan.title,
            )
            self._record_revision(
                tracker,
                actor=created_by,
                change_kind="mirror",
                entity_kind="task",
                entity_id=task.id,
                entity_key=task.key,
                summary=f"Mirrored run plan {plan.id} into tracker task {task.key}.",
                after_json=self._task_snapshot(task),
                commit=False,
            )
        step_by_id = {step.step_id: step for step in steps}
        ticket_by_step: dict[str, TrackerTicket] = {}
        for step in sorted(steps, key=lambda item: (item.position, item.id or 0)):
            ticket_key = f"workflow-{plan.id}-{_slug(step.step_id, fallback='step', max_length=80)}"
            ticket = self._ticket_by_key(tracker.id, ticket_key, missing_ok=True)
            if ticket is None:
                ticket = self._create_ticket_row(
                    tracker=tracker,
                    task=task,
                    key=ticket_key,
                    title=step.title,
                    goal=step.purpose,
                    status=self._ticket_status_from_step(step.status),
                    priority_key="p1",
                    lane_key="planning",
                    source_kind=TrackerSourceKind.WORKFLOW,
                    source_json={
                        "run_plan_id": plan.id,
                        "run_plan_key": plan.key,
                        "run_plan_step_id": step.id,
                        "step_id": step.step_id,
                        "template_key": plan.template_key,
                    },
                    definition_of_done_json=step.success_criteria_json or [],
                    constraints_json=step.policy_refs_json or [],
                    expected_changes_json=step.output_refs_json or [],
                    metadata_json={
                        "action_refs": step.action_refs_json or [],
                        "resource_refs": step.resource_refs_json or [],
                    },
                    run_plan_id=plan.id,
                    run_plan_step_id=step.id,
                    created_by=created_by,
                    now=now,
                    order_index=step.position,
                )
                self._add_link(
                    tracker,
                    task_id=task.id,
                    ticket_id=ticket.id,
                    link_kind=TrackerLinkKind.RUN_PLAN_STEP,
                    run_plan_id=plan.id,
                    run_plan_step_id=step.id,
                    ref=f"run-plan-step:{plan.id}:{step.step_id}",
                    title=step.title,
                )
                self._record_revision(
                    tracker,
                    actor=created_by,
                    change_kind="mirror",
                    entity_kind="ticket",
                    entity_id=ticket.id,
                    entity_key=ticket.key,
                    summary=f"Mirrored run-plan step {step.step_id} into ticket {ticket.key}.",
                    after_json=self._ticket_snapshot(ticket),
                    commit=False,
                )
            ticket_by_step[step.step_id] = ticket
        for step in steps:
            ticket = ticket_by_step[step.step_id]
            for dependency_step_id in step.depends_on_json or []:
                dependency_step = step_by_id.get(str(dependency_step_id))
                if dependency_step is None:
                    continue
                dependency_ticket = ticket_by_step.get(dependency_step.step_id)
                if dependency_ticket is not None:
                    self._add_dependency(tracker, ticket, dependency_ticket)
        plan.metadata_json = {
            **(plan.metadata_json or {}),
            "tracker_task_key": task.key,
            "tracker_ticket_keys": [ticket.key for ticket in ticket_by_step.values()],
        }
        plan.updated_at = now
        self._s.add(plan)
        self._sync_task_status(task, now=now)

    def mirror_run_plan_started(self, *, plan: RunPlan) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        task = self._task_by_key(tracker.id, f"workflow-{plan.id}", missing_ok=True)
        if task is None:
            return
        now = _utcnow()
        task.status = TrackerItemStatus.IN_PROGRESS
        task.started_at = task.started_at or now
        task.updated_at = now
        tickets = self._ticket_rows_for_run_plan(tracker.id, plan.id)
        for ticket in tickets:
            ticket.run_id = plan.run_id
            ticket.updated_at = now
            self._s.add(ticket)
        self._add_link(
            tracker,
            task_id=task.id,
            link_kind=TrackerLinkKind.RUN,
            run_plan_id=plan.id,
            run_id=plan.run_id,
            ref=f"run:{plan.run_id}",
            title=f"Run for {plan.title}",
        )
        self._record_revision(
            tracker,
            actor="system",
            change_kind="workflow-start",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Run plan {plan.id} started.",
            commit=False,
        )

    def mirror_run_plan_step_claimed(self, *, plan: RunPlan, step: RunPlanStep) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        ticket = self._ticket_for_step(tracker.id, plan.id, step.step_id)
        if ticket is None:
            return
        now = _utcnow()
        before = self._ticket_snapshot(ticket)
        ticket.status = TrackerItemStatus.IN_PROGRESS
        ticket.assignee = step.claimed_by
        ticket.claimed_at = step.claimed_at or now
        ticket.started_at = ticket.started_at or now
        ticket.run_id = plan.run_id
        ticket.lane_key = "implementation"
        ticket.updated_at = now
        self._s.add(ticket)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is not None:
            self._sync_task_status(task, now=now)
        self._record_revision(
            tracker,
            actor=step.claimed_by,
            change_kind="workflow-step-claim",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Run-plan step {step.step_id} claimed.",
            before_json=before,
            after_json=self._ticket_snapshot(ticket),
            commit=False,
        )

    def mirror_run_plan_step_recorded(self, *, plan: RunPlan, step: RunPlanStep) -> None:
        if plan.id is None:
            return
        tracker = self.ensure_tracker(project_id=plan.project_id)
        ticket = self._ticket_for_step(tracker.id, plan.id, step.step_id)
        if ticket is None:
            return
        now = _utcnow()
        before = self._ticket_snapshot(ticket)
        ticket.status = self._ticket_status_from_step(step.status)
        ticket.outcome = self._step_outcome(step)
        ticket.blocker_reason = step.error if step.status == RunPlanStepStatus.FAILED else None
        ticket.completed_at = now if ticket.status in TERMINAL_TICKET_STATUSES else None
        ticket.lane_key = "done" if ticket.status == TrackerItemStatus.COMPLETE else ticket.lane_key
        ticket.run_id = plan.run_id
        ticket.updated_at = now
        self._s.add(ticket)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is not None:
            self._sync_task_status(task, now=now)
        self._record_revision(
            tracker,
            actor=step.claimed_by,
            change_kind="workflow-step-record",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Run-plan step {step.step_id} recorded as {step.status.value}.",
            before_json=before,
            after_json=self._ticket_snapshot(ticket),
            commit=False,
        )

    def link_agent_request_to_task(
        self,
        *,
        request: AgentRequest,
        task_key: str,
        actor: str | None = None,
    ) -> None:
        tracker = self.ensure_tracker(project_id=request.project_id)
        task = self._task_by_key(tracker.id, task_key, missing_ok=True)
        if task is None:
            return
        self._add_link(
            tracker,
            task_id=task.id,
            link_kind=TrackerLinkKind.AGENT_REQUEST,
            agent_request_id=request.id,
            ref=f"agent-request:{request.id}",
            title=request.title,
        )
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="link",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Linked agent request {request.id} to tracker task {task.key}.",
            commit=False,
        )

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _tracker_out(self, row: TaskTracker) -> TrackerSummaryOut:
        return TrackerSummaryOut.model_validate(row)

    def _lane_out(self, tracker_id: int | None) -> list[TrackerLaneOut]:
        tracker_id = _required_id(tracker_id, "tracker")
        return [
            TrackerLaneOut.model_validate(row)
            for row in self._s.exec(
                select(TaskTrackerLane)
                .where(TaskTrackerLane.tracker_id == tracker_id)
                .order_by(col(TaskTrackerLane.position).asc())
            )
        ]

    def _priority_out(self, tracker_id: int | None) -> list[TrackerPriorityOut]:
        tracker_id = _required_id(tracker_id, "tracker")
        return [
            TrackerPriorityOut.model_validate(row)
            for row in self._s.exec(
                select(TaskTrackerPriority)
                .where(TaskTrackerPriority.tracker_id == tracker_id)
                .order_by(col(TaskTrackerPriority.position).asc())
            )
        ]

    def _task_out(self, row: TrackerTask) -> TrackerTaskOut:
        return TrackerTaskOut.model_validate(row)

    def _ticket_out(self, row: TrackerTicket) -> TrackerTicketOut:
        task = self._s.get(TrackerTask, row.task_id)
        parent = self._s.get(TrackerTicket, row.parent_ticket_id) if row.parent_ticket_id else None
        dependencies = self._dependency_rows_for_ticket(row.id)
        dependency_keys = [
            item.key
            for item in [
                self._s.get(TrackerTicket, dep.depends_on_ticket_id) for dep in dependencies
            ]
            if item is not None
        ]
        blocked_by = [
            item.key
            for item in [
                self._s.get(TrackerTicket, dep.depends_on_ticket_id) for dep in dependencies
            ]
            if item is not None and item.status != TrackerItemStatus.COMPLETE
        ]
        base = TrackerTicketOut.model_validate(row).model_dump(
            exclude={
                "task_key",
                "parent_ticket_key",
                "dependency_keys",
                "blocked_by",
                "reference_count",
                "link_count",
            }
        )
        return TrackerTicketOut(
            **base,
            task_key=task.key if task is not None else "",
            parent_ticket_key=parent.key if parent is not None else None,
            dependency_keys=dependency_keys,
            blocked_by=blocked_by,
            reference_count=len(self._reference_rows_for_ticket(row.id)),
            link_count=len(self._link_rows_for_ticket(row.id)),
        )

    def _ticket_out_many(self, rows: list[TrackerTicket]) -> list[TrackerTicketOut]:
        return [self._ticket_out(row) for row in rows]

    def _task_rows(self, tracker_id: int | None) -> list[TrackerTask]:
        tracker_id = _required_id(tracker_id, "tracker")
        return list(
            self._s.exec(
                select(TrackerTask)
                .where(TrackerTask.tracker_id == tracker_id)
                .order_by(col(TrackerTask.order_index).asc(), col(TrackerTask.id).asc())
            )
        )

    def _ticket_rows(self, tracker_id: int | None) -> list[TrackerTicket]:
        tracker_id = _required_id(tracker_id, "tracker")
        return list(
            self._s.exec(
                select(TrackerTicket)
                .where(TrackerTicket.tracker_id == tracker_id)
                .order_by(col(TrackerTicket.order_index).asc(), col(TrackerTicket.id).asc())
            )
        )

    def _ticket_rows_for_run_plan(
        self, tracker_id: int | None, run_plan_id: int
    ) -> list[TrackerTicket]:
        tracker_id = _required_id(tracker_id, "tracker")
        return list(
            self._s.exec(
                select(TrackerTicket).where(
                    TrackerTicket.tracker_id == tracker_id,
                    TrackerTicket.run_plan_id == run_plan_id,
                )
            )
        )

    @overload
    def _task_by_key(
        self,
        tracker_id: int | None,
        key: str,
        *,
        missing_ok: Literal[False] = False,
    ) -> TrackerTask: ...

    @overload
    def _task_by_key(
        self,
        tracker_id: int | None,
        key: str,
        *,
        missing_ok: Literal[True],
    ) -> TrackerTask | None: ...

    def _task_by_key(
        self,
        tracker_id: int | None,
        key: str,
        *,
        missing_ok: bool = False,
    ) -> TrackerTask | None:
        tracker_id = _required_id(tracker_id, "tracker")
        row = self._s.exec(
            select(TrackerTask).where(TrackerTask.tracker_id == tracker_id, TrackerTask.key == key)
        ).first()
        if row is None and not missing_ok:
            raise NotFoundError("tracker task not found", data={"task_key": key})
        return row

    @overload
    def _ticket_by_key(
        self,
        tracker_id: int | None,
        key: str | None,
        *,
        missing_ok: Literal[False] = False,
    ) -> TrackerTicket: ...

    @overload
    def _ticket_by_key(
        self,
        tracker_id: int | None,
        key: str | None,
        *,
        missing_ok: Literal[True],
    ) -> TrackerTicket | None: ...

    def _ticket_by_key(
        self,
        tracker_id: int | None,
        key: str | None,
        *,
        missing_ok: bool = False,
    ) -> TrackerTicket | None:
        tracker_id = _required_id(tracker_id, "tracker")
        if key is None:
            if missing_ok:
                return None
            raise ValidationError("ticket_key is required")
        row = self._s.exec(
            select(TrackerTicket).where(
                TrackerTicket.tracker_id == tracker_id,
                TrackerTicket.key == key,
            )
        ).first()
        if row is None and not missing_ok:
            raise NotFoundError("tracker ticket not found", data={"ticket_key": key})
        return row

    def _ticket_for_step(
        self,
        tracker_id: int | None,
        run_plan_id: int,
        step_id: str,
    ) -> TrackerTicket | None:
        tracker_id = _required_id(tracker_id, "tracker")
        step = self._s.exec(
            select(RunPlanStep).where(
                RunPlanStep.run_plan_id == run_plan_id,
                RunPlanStep.step_id == step_id,
            )
        ).first()
        if step is None:
            return None
        return self._s.exec(
            select(TrackerTicket).where(
                TrackerTicket.tracker_id == tracker_id,
                TrackerTicket.run_plan_id == run_plan_id,
                TrackerTicket.run_plan_step_id == step.id,
            )
        ).first()

    def _dependency_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketDependency]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    TrackerTicketDependency.ticket_id == ticket_id
                )
            )
        )

    def _dependent_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketDependency]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    TrackerTicketDependency.depends_on_ticket_id == ticket_id
                )
            )
        )

    def _reference_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketReference]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(
                select(TrackerTicketReference).where(TrackerTicketReference.ticket_id == ticket_id)
            )
        )

    def _link_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketLink]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(select(TrackerTicketLink).where(TrackerTicketLink.ticket_id == ticket_id))
        )

    def _link_out_for_scope(
        self,
        tracker_id: int | None,
        task_ids: set[int | None],
        ticket_ids: set[int | None],
    ) -> list[TrackerLinkOut]:
        tracker_id = _required_id(tracker_id, "tracker")
        rows = list(
            self._s.exec(
                select(TrackerTicketLink).where(TrackerTicketLink.tracker_id == tracker_id)
            )
        )
        return [
            TrackerLinkOut.model_validate(row)
            for row in rows
            if row.task_id in task_ids or row.ticket_id in ticket_ids
        ]

    def _dependency_out_for_tickets(
        self,
        tickets: list[TrackerTicket],
    ) -> list[TrackerDependencyOut]:
        ids = {ticket.id for ticket in tickets}
        if not ids:
            return []
        rows = list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    col(TrackerTicketDependency.ticket_id).in_(ids)
                )
            )
        )
        by_id = {ticket.id: ticket.key for ticket in tickets}
        for dependency in rows:
            if dependency.depends_on_ticket_id not in by_id:
                dep_ticket = self._s.get(TrackerTicket, dependency.depends_on_ticket_id)
                if dep_ticket is not None:
                    by_id[dep_ticket.id] = dep_ticket.key
        return [
            TrackerDependencyOut(
                id=row.id or 0,
                ticket_key=by_id.get(row.ticket_id, ""),
                depends_on_ticket_key=by_id.get(row.depends_on_ticket_id, ""),
                dependency_type=row.dependency_type,
                metadata_json=row.metadata_json,
            )
            for row in rows
        ]

    def _prepare_ticket_list(
        self,
        tracker: TaskTracker,
        ticket_list_json: dict[str, Any],
    ) -> tuple[TrackerTask | None, list[dict[str, Any]], list[TrackerListIssueOut], list[str]]:
        errors: list[TrackerListIssueOut] = []
        warnings: list[str] = []
        specs: list[dict[str, Any]] = []
        if not isinstance(ticket_list_json, dict):
            return (
                None,
                [],
                [TrackerListIssueOut(message="ticket_list_json must be an object")],
                warnings,
            )
        task_key = str(ticket_list_json.get("task_key") or "").strip()
        if not task_key:
            errors.append(TrackerListIssueOut(field="task_key", message="task_key is required"))
            return None, specs, errors, warnings
        task = self._task_by_key(tracker.id, task_key, missing_ok=True)
        if task is None:
            errors.append(
                TrackerListIssueOut(
                    field="task_key",
                    key=task_key,
                    message="tracker task not found",
                )
            )
            return None, specs, errors, warnings

        raw_tickets = ticket_list_json.get("tickets")
        if not isinstance(raw_tickets, list) or not raw_tickets:
            errors.append(
                TrackerListIssueOut(field="tickets", message="tickets must be a non-empty list")
            )
            return task, specs, errors, warnings

        seen_keys: set[str] = set()
        for index, raw in enumerate(raw_tickets):
            if not isinstance(raw, dict):
                errors.append(
                    TrackerListIssueOut(
                        index=index, field="tickets", message="ticket must be an object"
                    )
                )
                continue
            key = _slug(str(raw.get("key") or ""), fallback="", max_length=180)
            if not key:
                errors.append(
                    TrackerListIssueOut(index=index, field="key", message="ticket key is required")
                )
                continue
            if key in seen_keys:
                errors.append(
                    TrackerListIssueOut(
                        index=index, key=key, field="key", message="duplicate ticket key"
                    )
                )
            seen_keys.add(key)
            if self._ticket_by_key(tracker.id, key, missing_ok=True) is not None:
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="key",
                        message="tracker ticket key already exists",
                    )
                )
            title = _clean_text(raw.get("title")) if raw.get("title") is not None else ""
            if not title:
                title = key
                warnings.append(f"ticket {key} has no title; key will be used as title")
            status = self._list_enum(
                raw.get("status", TrackerItemStatus.NOT_STARTED.value),
                TrackerItemStatus,
                errors,
                index=index,
                key=key,
                field="status",
            )
            kind = self._list_enum(
                raw.get("kind", TrackerTicketKind.TICKET.value),
                TrackerTicketKind,
                errors,
                index=index,
                key=key,
                field="kind",
            )
            source_kind = self._list_enum(
                raw.get("source_kind", TrackerSourceKind.MANUAL.value),
                TrackerSourceKind,
                errors,
                index=index,
                key=key,
                field="source_kind",
            )
            dependency_keys = self._json_string_list(
                raw.get("dependency_keys", []),
                errors,
                index=index,
                key=key,
                field="dependency_keys",
            )
            references_json = raw.get("references_json", [])
            if not isinstance(references_json, list):
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="references_json",
                        message="references_json must be a list",
                    )
                )
                references_json = []
            elif any(not isinstance(item, dict) for item in references_json):
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="references_json",
                        message="references_json entries must be objects",
                    )
                )
                references_json = []
            completion_evidence_json = raw.get("completion_evidence_json")
            if completion_evidence_json is not None and not isinstance(
                completion_evidence_json, dict
            ):
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="completion_evidence_json",
                        message="completion_evidence_json must be an object",
                    )
                )
                completion_evidence_json = None
            specs.append(
                {
                    "index": index,
                    "key": key,
                    "title": title,
                    "goal": _clean_text(raw.get("goal")),
                    "status": status or TrackerItemStatus.NOT_STARTED,
                    "kind": kind or TrackerTicketKind.TICKET,
                    "assignee": raw.get("assignee"),
                    "priority_key": str(raw.get("priority_key") or "p2"),
                    "lane_key": str(raw.get("lane_key") or "implementation"),
                    "parent_ticket_key": str(raw.get("parent_ticket_key") or "").strip() or None,
                    "dependency_keys": dependency_keys,
                    "blocker_reason": raw.get("blocker_reason"),
                    "outcome": raw.get("outcome"),
                    "effort": raw.get("effort"),
                    "source_kind": source_kind or TrackerSourceKind.MANUAL,
                    "source_json": raw.get("source_json")
                    if isinstance(raw.get("source_json"), dict)
                    else None,
                    "definition_of_done_json": self._json_string_list(
                        raw.get("definition_of_done_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="definition_of_done_json",
                    ),
                    "constraints_json": self._json_string_list(
                        raw.get("constraints_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="constraints_json",
                    ),
                    "expected_changes_json": self._json_string_list(
                        raw.get("expected_changes_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="expected_changes_json",
                    ),
                    "allowed_paths_json": self._json_string_list(
                        raw.get("allowed_paths_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="allowed_paths_json",
                    ),
                    "references_json": references_json,
                    "completion_evidence_json": completion_evidence_json,
                    "context_json": raw.get("context_json")
                    if isinstance(raw.get("context_json"), dict)
                    else None,
                    "metadata_json": raw.get("metadata_json")
                    if isinstance(raw.get("metadata_json"), dict)
                    else None,
                    "created_by": raw.get("created_by") or ticket_list_json.get("created_by"),
                }
            )

        by_key = {spec["key"]: spec for spec in specs}
        raw_dependencies = ticket_list_json.get("dependencies", [])
        if raw_dependencies is not None and not isinstance(raw_dependencies, list):
            errors.append(
                TrackerListIssueOut(field="dependencies", message="dependencies must be a list")
            )
        elif isinstance(raw_dependencies, list):
            for dep_index, raw_dep in enumerate(raw_dependencies):
                if not isinstance(raw_dep, dict):
                    errors.append(
                        TrackerListIssueOut(
                            index=dep_index,
                            field="dependencies",
                            message="dependency must be an object",
                        )
                    )
                    continue
                ticket_key = _slug(
                    str(raw_dep.get("ticket_key") or ""), fallback="", max_length=180
                )
                dependency_key = _slug(
                    str(
                        raw_dep.get("depends_on_ticket_key") or raw_dep.get("dependency_key") or ""
                    ),
                    fallback="",
                    max_length=180,
                )
                if not ticket_key or not dependency_key:
                    errors.append(
                        TrackerListIssueOut(
                            index=dep_index,
                            field="dependencies",
                            message="dependency requires ticket_key and depends_on_ticket_key",
                        )
                    )
                    continue
                target = by_key.get(ticket_key)
                if target is None:
                    errors.append(
                        TrackerListIssueOut(
                            index=dep_index,
                            key=ticket_key,
                            field="dependencies",
                            message="dependency ticket_key must refer to a list ticket",
                        )
                    )
                    continue
                if dependency_key not in target["dependency_keys"]:
                    target["dependency_keys"].append(dependency_key)

        for spec in specs:
            if spec["parent_ticket_key"]:
                self._validate_ticket_list_ref(
                    tracker, spec["parent_ticket_key"], by_key, errors, spec, "parent_ticket_key"
                )
            for dependency_key in spec["dependency_keys"]:
                self._validate_ticket_list_ref(
                    tracker, dependency_key, by_key, errors, spec, "dependency_keys"
                )
            if spec["key"] in spec["dependency_keys"]:
                errors.append(
                    TrackerListIssueOut(
                        index=spec["index"],
                        key=spec["key"],
                        field="dependency_keys",
                        message="ticket cannot depend on itself",
                    )
                )
        return task, specs, errors, warnings

    def _list_enum(
        self,
        value: Any,
        enum_cls: Any,
        errors: list[TrackerListIssueOut],
        *,
        index: int,
        key: str,
        field: str,
    ) -> Any:
        try:
            return enum_cls(str(value))
        except ValueError:
            errors.append(
                TrackerListIssueOut(
                    index=index,
                    key=key,
                    field=field,
                    message=f"unsupported {field}: {value}",
                )
            )
            return None

    def _json_string_list(
        self,
        value: Any,
        errors: list[TrackerListIssueOut],
        *,
        index: int,
        key: str,
        field: str,
    ) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            errors.append(
                TrackerListIssueOut(
                    index=index,
                    key=key,
                    field=field,
                    message=f"{field} must be a list",
                )
            )
            return []
        return [str(item) for item in value]

    def _validate_ticket_list_ref(
        self,
        tracker: TaskTracker,
        ticket_key: str,
        list_specs: dict[str, dict[str, Any]],
        errors: list[TrackerListIssueOut],
        spec: dict[str, Any],
        field: str,
    ) -> None:
        if ticket_key in list_specs:
            return
        if self._ticket_by_key(tracker.id, ticket_key, missing_ok=True) is not None:
            return
        errors.append(
            TrackerListIssueOut(
                index=spec["index"],
                key=spec["key"],
                field=field,
                message=f"referenced ticket {ticket_key!r} does not exist",
            )
        )

    def _preview_ticket_list_update(
        self,
        *,
        project_id: int,
        tracker: TaskTracker,
        updates_json: list[dict[str, Any]],
    ) -> Envelope[TrackerMutationOut]:
        results: list[TrackerListItemResultOut] = []
        tickets: list[TrackerTicket] = []
        total_added = 0
        total_removed = 0
        for index, item in enumerate(updates_json):
            if not isinstance(item, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        error="update entries must be objects",
                    )
                )
                continue
            patch_json = item.get("patch_json", item.get("patch"))
            if not isinstance(patch_json, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error="update entry patch_json must be an object",
                    )
                )
                continue
            if not patch_json:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="noop",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                    )
                )
                continue
            try:
                ticket = self._ticket_from_list_update(tracker.id, item)
                dependency_preview = self._preview_dependency_patch(tracker, ticket, patch_json)
                if dependency_preview is not None:
                    total_added += len(dependency_preview.added_dependency_keys)
                    total_removed += len(dependency_preview.removed_dependency_keys)
                tickets.append(ticket)
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="validated",
                        key=ticket.key,
                        id=ticket.id,
                        changed_fields=list(patch_json.keys()),
                        dependency_preview=dependency_preview,
                        ticket=self._ticket_out(ticket),
                    )
                )
            except (ConflictError, NotFoundError, ValidationError, ValueError) as exc:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error=str(exc),
                    )
                )

        errors = [
            TrackerListIssueOut(
                index=result.index,
                key=result.key,
                message=result.error or "ticket update preview failed",
            )
            for result in results
            if result.action == "error"
        ]
        warnings: list[str] = []
        if total_removed >= 3 and total_removed > max(total_added * 2, total_added + 2):
            warnings.append(
                "dependency preview removes "
                f"{total_removed} edges and adds {total_added}; review direction before applying"
            )
        out = TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            tickets=self._ticket_out_many(tickets),
            results=results,
            errors=errors,
            warnings=warnings,
            valid=not errors,
            dry_run=True,
            rev=tracker.rev,
        )
        return Envelope(data=out, project_id=project_id)

    def _preview_dependency_patch(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        patch_json: dict[str, Any],
    ) -> TrackerDependencyPreviewOut | None:
        if not any(field in patch_json for field in DEPENDENCY_PATCH_FIELDS):
            return None
        if "dependency_keys" in patch_json and (
            "add_dependency_keys" in patch_json or "remove_dependency_keys" in patch_json
        ):
            raise ValidationError(
                "dependency_keys cannot be combined with add_dependency_keys or "
                "remove_dependency_keys"
            )

        current_rows = self._dependency_rows_for_ticket(ticket.id)
        current_ids = [
            row.depends_on_ticket_id for row in current_rows if row.depends_on_ticket_id is not None
        ]
        current_keys = self._dependency_keys_from_ids(current_ids)
        final_ids = list(current_ids)
        requested_by_id: dict[int, str] = {
            dependency_id: dependency_key
            for dependency_id, dependency_key in zip(current_ids, current_keys, strict=False)
        }

        if "dependency_keys" in patch_json:
            mode: Literal["replace", "add-remove"] = "replace"
            final_ids = []
            for dependency_key in self._dependency_patch_keys(patch_json, "dependency_keys"):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                dependency_id = _required_id(dependency.id, "ticket")
                if dependency_id not in final_ids:
                    final_ids.append(dependency_id)
                requested_by_id[dependency_id] = dependency.key
        else:
            mode = "add-remove"
            for dependency_key in self._dependency_patch_keys(patch_json, "remove_dependency_keys"):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                dependency_id = _required_id(dependency.id, "ticket")
                if dependency_id not in final_ids:
                    raise ValidationError(
                        "ticket dependency edge does not exist",
                        data={"ticket_key": ticket.key, "depends_on": dependency.key},
                    )
                final_ids = [item for item in final_ids if item != dependency_id]
                requested_by_id[dependency_id] = dependency.key
            for dependency_key in self._dependency_patch_keys(patch_json, "add_dependency_keys"):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                dependency_id = _required_id(dependency.id, "ticket")
                if dependency_id not in final_ids:
                    final_ids.append(dependency_id)
                requested_by_id[dependency_id] = dependency.key

        self._validate_dependency_preview_graph(tracker, ticket, final_ids)
        final_keys = [
            requested_by_id.get(dependency_id) or self._dependency_keys_from_ids([dependency_id])[0]
            for dependency_id in final_ids
        ]
        current_id_set = set(current_ids)
        final_id_set = set(final_ids)
        added_ids = [
            dependency_id for dependency_id in final_ids if dependency_id not in current_id_set
        ]
        removed_ids = [
            dependency_id for dependency_id in current_ids if dependency_id not in final_id_set
        ]
        kept_ids = [dependency_id for dependency_id in final_ids if dependency_id in current_id_set]
        warnings: list[str] = []
        if current_ids and not final_ids:
            warnings.append("dependency preview removes all existing dependency edges")
        if len(removed_ids) >= 3 and len(removed_ids) > max(len(added_ids) * 2, len(added_ids) + 2):
            warnings.append(
                "dependency preview removes "
                f"{len(removed_ids)} edges and adds {len(added_ids)}; review direction"
            )
        return TrackerDependencyPreviewOut(
            ticket_key=ticket.key,
            mode=mode,
            current_dependency_keys=current_keys,
            final_dependency_keys=final_keys,
            added_dependency_keys=[
                requested_by_id.get(dependency_id)
                or self._dependency_keys_from_ids([dependency_id])[0]
                for dependency_id in added_ids
            ],
            removed_dependency_keys=[
                requested_by_id.get(dependency_id)
                or self._dependency_keys_from_ids([dependency_id])[0]
                for dependency_id in removed_ids
            ],
            kept_dependency_keys=[
                requested_by_id.get(dependency_id)
                or self._dependency_keys_from_ids([dependency_id])[0]
                for dependency_id in kept_ids
            ],
            warnings=warnings,
        )

    def _dependency_patch_keys(self, patch_json: dict[str, Any], field: str) -> list[str]:
        if field not in patch_json:
            return []
        if not isinstance(patch_json[field], list):
            raise ValidationError(f"{field} must be a list")
        return list(dict.fromkeys(str(key) for key in patch_json[field]))

    def _dependency_keys_from_ids(self, dependency_ids: list[int]) -> list[str]:
        keys: list[str] = []
        for dependency_id in dependency_ids:
            dependency = self._s.get(TrackerTicket, dependency_id)
            if dependency is not None:
                keys.append(dependency.key)
        return keys

    def _validate_dependency_preview_graph(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        final_dependency_ids: list[int],
    ) -> None:
        ticket_id = _required_id(ticket.id, "ticket")
        if ticket_id in final_dependency_ids:
            raise ValidationError("ticket cannot depend on itself", data={"ticket_key": ticket.key})
        rows = list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    TrackerTicketDependency.tracker_id == tracker.id
                )
            )
        )
        adjacency: dict[int, set[int]] = {}
        for row in rows:
            row_ticket_id = row.ticket_id
            depends_on_id = row.depends_on_ticket_id
            if row_ticket_id is None or depends_on_id is None or row_ticket_id == ticket_id:
                continue
            adjacency.setdefault(row_ticket_id, set()).add(depends_on_id)
        adjacency[ticket_id] = set(final_dependency_ids)
        for dependency_id in final_dependency_ids:
            if self._dependency_graph_reaches(adjacency, dependency_id, ticket_id):
                dependency_key = self._dependency_keys_from_ids([dependency_id])[0]
                raise ConflictError(
                    "ticket dependency would create a cycle",
                    data={"ticket_key": ticket.key, "depends_on": dependency_key},
                )

    def _dependency_graph_reaches(
        self,
        adjacency: dict[int, set[int]],
        start_id: int,
        target_id: int,
    ) -> bool:
        stack = [start_id]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            if current == target_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adjacency.get(current, set()))
        return False

    def _ticket_from_list_update(
        self,
        tracker_id: int | None,
        item: dict[str, Any],
    ) -> TrackerTicket:
        if isinstance(item.get("ticket_id"), int):
            ticket = self._s.get(TrackerTicket, item["ticket_id"])
            if ticket is not None and ticket.tracker_id == tracker_id:
                return ticket
            raise NotFoundError("tracker ticket not found", data={"ticket_id": item["ticket_id"]})
        return self._ticket_by_key(tracker_id, str(item.get("ticket_key") or ""))

    def _graph_out(
        self,
        tasks: list[TrackerTaskOut],
        tickets: list[TrackerTicketOut],
        dependencies: list[TrackerDependencyOut],
        links: list[TrackerLinkOut],
    ) -> TrackerGraphOut:
        nodes: list[TrackerGraphNodeOut] = []
        edges: list[TrackerGraphEdgeOut] = []
        task_node_ids: set[str] = set()
        ticket_node_ids: set[str] = set()
        for task in tasks:
            node_id = f"task:{task.key}"
            task_node_ids.add(node_id)
            nodes.append(
                TrackerGraphNodeOut(
                    id=node_id,
                    type="task",
                    label=task.title,
                    status=task.status.value,
                    lane_key=task.lane_key,
                    priority_key=task.priority_key,
                    data=task.model_dump(mode="json"),
                )
            )
        for ticket in tickets:
            node_id = f"ticket:{ticket.key}"
            ticket_node_ids.add(node_id)
            task_node = f"task:{ticket.task_key}"
            nodes.append(
                TrackerGraphNodeOut(
                    id=node_id,
                    type="group" if ticket.kind == TrackerTicketKind.GROUP else "ticket",
                    parent_id=task_node if task_node in task_node_ids else None,
                    label=ticket.title,
                    status=ticket.status.value,
                    lane_key=ticket.lane_key,
                    priority_key=ticket.priority_key,
                    data=ticket.model_dump(mode="json"),
                )
            )
            if task_node in task_node_ids:
                edges.append(
                    TrackerGraphEdgeOut(
                        id=f"contains:{ticket.task_key}:{ticket.key}",
                        type="contains",
                        source=task_node,
                        target=node_id,
                    )
                )
        for dependency in dependencies:
            source = f"ticket:{dependency.depends_on_ticket_key}"
            target = f"ticket:{dependency.ticket_key}"
            if source in ticket_node_ids and target in ticket_node_ids:
                edges.append(
                    TrackerGraphEdgeOut(
                        id=f"dependency:{dependency.depends_on_ticket_key}:{dependency.ticket_key}",
                        type="dependency",
                        source=source,
                        target=target,
                        label=dependency.dependency_type,
                        data={"dependency_id": dependency.id},
                    )
                )
        for link in links:
            if link.ticket_id is None and link.task_id is None:
                continue
            if link.link_kind in {TrackerLinkKind.RUN_PLAN, TrackerLinkKind.RUN_PLAN_STEP}:
                continue
            link_target: str | None = None
            if link.ticket_id is not None:
                linked_ticket = next((item for item in tickets if item.id == link.ticket_id), None)
                link_target = f"ticket:{linked_ticket.key}" if linked_ticket is not None else None
            if link_target is None and link.task_id is not None:
                linked_task = next((item for item in tasks if item.id == link.task_id), None)
                link_target = f"task:{linked_task.key}" if linked_task is not None else None
            if link_target is not None:
                source = f"link:{link.id}"
                nodes.append(
                    TrackerGraphNodeOut(
                        id=source,
                        type="ticket",
                        label=link.title or link.ref or link.link_kind.value,
                        status="link",
                        lane_key="external",
                        priority_key="p3",
                        data=link.model_dump(mode="json"),
                    )
                )
                edges.append(
                    TrackerGraphEdgeOut(
                        id=f"link:{link.id}:{link_target}",
                        type="link",
                        source=source,
                        target=link_target,
                        label=link.link_kind.value,
                    )
                )
        return TrackerGraphOut(
            nodes=nodes,
            edges=edges,
            warnings=self._graph_advisory_warnings(tasks, tickets, dependencies),
            layout_hints={"direction": "LR", "group_by": "task"},
        )

    def _graph_advisory_warnings(
        self,
        tasks: list[TrackerTaskOut],
        tickets: list[TrackerTicketOut],
        dependencies: list[TrackerDependencyOut],
    ) -> list[str]:
        warnings: list[str] = []
        tickets_by_task: dict[str, list[TrackerTicketOut]] = {}
        for ticket in tickets:
            tickets_by_task.setdefault(ticket.task_key, []).append(ticket)
        dependency_keys_by_task: dict[str, set[tuple[str, str]]] = {}
        for task_key, task_tickets in tickets_by_task.items():
            task_ticket_keys = {ticket.key for ticket in task_tickets}
            dependency_keys_by_task[task_key] = {
                (dependency.ticket_key, dependency.depends_on_ticket_key)
                for dependency in dependencies
                if dependency.ticket_key in task_ticket_keys
                and dependency.depends_on_ticket_key in task_ticket_keys
            }

        task_title_by_key = {task.key: task.title for task in tasks}
        terminal_statuses = {
            TrackerItemStatus.COMPLETE.value,
            TrackerItemStatus.DEFERRED.value,
        }
        for task_key, task_tickets in tickets_by_task.items():
            active_tickets = [
                ticket for ticket in task_tickets if ticket.status.value not in terminal_statuses
            ]
            if len(active_tickets) < 5:
                continue
            edges = dependency_keys_by_task.get(task_key, set())
            minimum_edges = max(2, len(active_tickets) // 2)
            if len(edges) < minimum_edges:
                warnings.append(
                    "Task "
                    f"{task_key} has {len(active_tickets)} nonterminal tickets but only "
                    f"{len(edges)} dependency relations; review whether the plan is missing "
                    "blocking edges."
                )
            linked_ticket_keys = {ticket_key for edge in edges for ticket_key in edge}
            isolated = [
                ticket.key for ticket in active_tickets if ticket.key not in linked_ticket_keys
            ]
            if len(isolated) >= 3 and len(isolated) >= (len(active_tickets) + 1) // 2:
                sample = ", ".join(isolated[:3])
                suffix = "..." if len(isolated) > 3 else ""
                warnings.append(
                    "Task "
                    f"{task_key} has {len(isolated)} nonterminal tickets without dependency "
                    f"links ({sample}{suffix}); review isolated work before implementation."
                )

        dependencies_by_ticket = {
            ticket_key: [
                dependency for dependency in dependencies if dependency.ticket_key == ticket_key
            ]
            for ticket_key in {ticket.key for ticket in tickets}
        }
        for ticket in tickets:
            if ticket.status.value in terminal_statuses:
                continue
            text = " ".join(
                [
                    ticket.key,
                    ticket.title,
                    ticket.goal or "",
                    task_title_by_key.get(ticket.task_key, ""),
                ]
            ).lower()
            looks_like_pre_gate = ("gate" in text or "review" in text) and (
                "before" in text or "pre-" in text or "pre " in text or ticket.lane_key == "review"
            )
            dependency_count = len(dependencies_by_ticket.get(ticket.key, []))
            if looks_like_pre_gate and dependency_count >= 2:
                warnings.append(
                    "Review/gate ticket "
                    f"{ticket.key} depends on {dependency_count} tickets; confirm dependency "
                    "direction if this gate should unblock implementation work."
                )
        return warnings

    def _create_ticket_row(
        self,
        *,
        tracker: TaskTracker,
        task: TrackerTask,
        key: str,
        title: str,
        goal: str = "",
        status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED,
        kind: TrackerTicketKind = TrackerTicketKind.TICKET,
        assignee: str | None = None,
        priority_key: str = "p2",
        lane_key: str = "implementation",
        parent_ticket_id: int | None = None,
        blocker_reason: str | None = None,
        outcome: str | None = None,
        effort: str | None = None,
        source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL,
        source_json: dict[str, Any] | None = None,
        definition_of_done_json: list[str] | None = None,
        constraints_json: list[str] | None = None,
        expected_changes_json: list[str] | None = None,
        allowed_paths_json: list[str] | None = None,
        completion_evidence_json: dict[str, Any] | None = None,
        context_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        run_id: int | None = None,
        agent_request_id: int | None = None,
        created_by: str | None = None,
        now: datetime | None = None,
        order_index: int | None = None,
    ) -> TrackerTicket:
        now = now or _utcnow()
        row = TrackerTicket(
            tracker_id=tracker.id,
            project_id=tracker.project_id,
            task_id=task.id,
            parent_ticket_id=parent_ticket_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
            run_id=run_id,
            agent_request_id=agent_request_id,
            key=key,
            title=_clean_text(title) or key,
            goal=_clean_text(goal),
            status=status,
            kind=kind,
            assignee=assignee,
            priority_key=priority_key,
            lane_key=lane_key,
            order_index=(
                order_index if order_index is not None else self._next_ticket_position(task.id)
            ),
            blocker_reason=_clean_text(blocker_reason) if blocker_reason else None,
            outcome=_clean_text(outcome) if outcome else None,
            effort=effort,
            source_kind=source_kind,
            source_json=redact_secrets(source_json) if source_json is not None else None,
            definition_of_done_json=definition_of_done_json or [],
            constraints_json=constraints_json or [],
            expected_changes_json=expected_changes_json or [],
            allowed_paths_json=allowed_paths_json or [],
            completion_evidence_json=(
                redact_secrets(completion_evidence_json)
                if completion_evidence_json is not None
                else None
            ),
            context_json=redact_secrets(context_json) if context_json is not None else None,
            metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            created_by=created_by,
            claimed_at=now if assignee and status == TrackerItemStatus.IN_PROGRESS else None,
            created_at=now,
            updated_at=now,
            started_at=now if status == TrackerItemStatus.IN_PROGRESS else None,
            completed_at=now if status == TrackerItemStatus.COMPLETE else None,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def _add_dependency(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        dependency_ticket: TrackerTicket,
    ) -> None:
        if ticket.id == dependency_ticket.id:
            raise ValidationError("ticket cannot depend on itself", data={"ticket_key": ticket.key})
        existing = self._s.exec(
            select(TrackerTicketDependency).where(
                TrackerTicketDependency.ticket_id == ticket.id,
                TrackerTicketDependency.depends_on_ticket_id == dependency_ticket.id,
            )
        ).first()
        if existing is not None:
            return
        if self._would_create_dependency_cycle(ticket, dependency_ticket):
            raise ConflictError(
                "ticket dependency would create a cycle",
                data={"ticket_key": ticket.key, "depends_on": dependency_ticket.key},
            )
        self._s.add(
            TrackerTicketDependency(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                ticket_id=ticket.id,
                depends_on_ticket_id=dependency_ticket.id,
                dependency_type="blocks",
                created_at=_utcnow(),
            )
        )

    def _add_reference(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        reference: dict[str, Any],
    ) -> None:
        ref_type = str(reference.get("ref_type") or reference.get("type") or "note")
        ref = str(reference.get("ref") or "")
        if not ref:
            raise ValidationError("ticket reference ref is required")
        self._s.add(
            TrackerTicketReference(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                ticket_id=ticket.id,
                ref_type=ref_type,
                ref=redact_secret_text(ref),
                title=_clean_text(reference.get("title")) or None,
                metadata_json=redact_secrets(reference.get("metadata_json"))
                if isinstance(reference.get("metadata_json"), dict)
                else None,
                created_at=_utcnow(),
            )
        )

    def _add_link(
        self,
        tracker: TaskTracker,
        *,
        link_kind: TrackerLinkKind,
        task_id: int | None = None,
        ticket_id: int | None = None,
        ref: str | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        run_id: int | None = None,
        agent_request_id: int | None = None,
        resource_record_id: int | None = None,
        artifact_id: int | None = None,
        action_call_id: int | None = None,
        title: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        existing = self._s.exec(
            select(TrackerTicketLink).where(
                TrackerTicketLink.tracker_id == tracker.id,
                TrackerTicketLink.task_id == task_id,
                TrackerTicketLink.ticket_id == ticket_id,
                TrackerTicketLink.link_kind == link_kind,
                TrackerTicketLink.ref == ref,
                TrackerTicketLink.run_plan_id == run_plan_id,
                TrackerTicketLink.run_plan_step_id == run_plan_step_id,
                TrackerTicketLink.agent_request_id == agent_request_id,
            )
        ).first()
        if existing is not None:
            return
        self._s.add(
            TrackerTicketLink(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                task_id=task_id,
                ticket_id=ticket_id,
                link_kind=link_kind,
                ref=redact_secret_text(ref) if ref else None,
                run_plan_id=run_plan_id,
                run_plan_step_id=run_plan_step_id,
                run_id=run_id,
                agent_request_id=agent_request_id,
                resource_record_id=resource_record_id,
                artifact_id=artifact_id,
                action_call_id=action_call_id,
                title=_clean_text(title) or None,
                metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
                created_at=_utcnow(),
            )
        )

    def _apply_task_patch(self, task: TrackerTask, patch_json: dict[str, Any]) -> None:
        now = _utcnow()
        if "status" in patch_json:
            new_status = _status_value(str(patch_json["status"]))
            if task.status != new_status:
                validate_transition(
                    task.status,
                    new_status,
                    TRACKER_ITEM_STATUS_TRANSITIONS,
                    label="tracker_task.status",
                )
                task.status = new_status
                if new_status == TrackerItemStatus.IN_PROGRESS:
                    task.started_at = task.started_at or now
                    task.completed_at = None
                elif new_status in TERMINAL_TICKET_STATUSES:
                    task.completed_at = now
                else:
                    task.completed_at = None
        for field in (
            "title",
            "goal",
            "description",
            "owner",
            "task_type",
            "priority_key",
            "lane_key",
        ):
            if field in patch_json:
                value = patch_json[field]
                setattr(task, field, _clean_text(value) if isinstance(value, str) else value)
        for field in (
            "source_json",
            "definition_of_done_json",
            "constraints_json",
            "expected_outcomes_json",
            "completion_evidence_json",
            "context_json",
            "metadata_json",
        ):
            if field in patch_json:
                setattr(task, field, redact_secrets(_jsonable(patch_json[field])))
        task.updated_at = now
        self._s.add(task)

    def _apply_ticket_patch(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        patch_json: dict[str, Any],
    ) -> None:
        now = _utcnow()
        if "status" in patch_json:
            new_status = _status_value(str(patch_json["status"]))
            if ticket.status != new_status:
                validate_transition(
                    ticket.status,
                    new_status,
                    TRACKER_ITEM_STATUS_TRANSITIONS,
                    label="tracker_ticket.status",
                )
                ticket.status = new_status
                if new_status == TrackerItemStatus.IN_PROGRESS:
                    ticket.started_at = ticket.started_at or now
                    ticket.completed_at = None
                    ticket.claimed_at = (
                        ticket.claimed_at or now if ticket.assignee else ticket.claimed_at
                    )
                elif new_status in TERMINAL_TICKET_STATUSES:
                    ticket.completed_at = now
                    if new_status == TrackerItemStatus.COMPLETE:
                        ticket.blocker_reason = None
                else:
                    ticket.completed_at = None
        for field in (
            "title",
            "goal",
            "assignee",
            "priority_key",
            "lane_key",
            "blocker_reason",
            "outcome",
            "effort",
        ):
            if field in patch_json:
                value = patch_json[field]
                setattr(ticket, field, _clean_text(value) if isinstance(value, str) else value)
                if field == "assignee" and value:
                    ticket.claimed_at = ticket.claimed_at or now
        if "kind" in patch_json:
            ticket.kind = TrackerTicketKind(str(patch_json["kind"]))
        if "parent_ticket_key" in patch_json:
            parent_key = patch_json["parent_ticket_key"]
            parent = self._ticket_by_key(tracker.id, str(parent_key)) if parent_key else None
            ticket.parent_ticket_id = parent.id if parent is not None else None
        for field in (
            "source_json",
            "definition_of_done_json",
            "constraints_json",
            "expected_changes_json",
            "allowed_paths_json",
            "completion_evidence_json",
            "context_json",
            "metadata_json",
        ):
            if field in patch_json:
                setattr(ticket, field, redact_secrets(_jsonable(patch_json[field])))
        if "dependency_keys" in patch_json:
            if "add_dependency_keys" in patch_json or "remove_dependency_keys" in patch_json:
                raise ValidationError(
                    "dependency_keys cannot be combined with add_dependency_keys or "
                    "remove_dependency_keys"
                )
            if not isinstance(patch_json["dependency_keys"], list):
                raise ValidationError("dependency_keys must be a list")
            for dep in self._dependency_rows_for_ticket(ticket.id):
                self._s.delete(dep)
            self._s.flush()
            for dependency_key in patch_json["dependency_keys"]:
                dependency = self._ticket_by_key(tracker.id, str(dependency_key))
                self._add_dependency(tracker, ticket, dependency)
        if "add_dependency_keys" in patch_json:
            if not isinstance(patch_json["add_dependency_keys"], list):
                raise ValidationError("add_dependency_keys must be a list")
            for dependency_key in dict.fromkeys(
                str(key) for key in patch_json["add_dependency_keys"]
            ):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                self._add_dependency(tracker, ticket, dependency)
        if "remove_dependency_keys" in patch_json:
            if not isinstance(patch_json["remove_dependency_keys"], list):
                raise ValidationError("remove_dependency_keys must be a list")
            for dependency_key in dict.fromkeys(
                str(key) for key in patch_json["remove_dependency_keys"]
            ):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                edge = self._s.exec(
                    select(TrackerTicketDependency).where(
                        TrackerTicketDependency.ticket_id == ticket.id,
                        TrackerTicketDependency.depends_on_ticket_id == dependency.id,
                    )
                ).first()
                if edge is None:
                    raise ValidationError(
                        "ticket dependency edge does not exist",
                        data={"ticket_key": ticket.key, "depends_on": dependency.key},
                    )
                self._s.delete(edge)
        if "references_json" in patch_json:
            if not isinstance(patch_json["references_json"], list):
                raise ValidationError("references_json must be a list")
            for reference in patch_json["references_json"]:
                if not isinstance(reference, dict):
                    raise ValidationError("references_json entries must be objects")
                self._add_reference(tracker, ticket, reference)
        ticket.updated_at = now
        self._s.add(ticket)

    def _sync_task_status(self, task: TrackerTask, *, now: datetime) -> None:
        tickets = list(self._s.exec(select(TrackerTicket).where(TrackerTicket.task_id == task.id)))
        if not tickets:
            return
        old = task.status
        if all(ticket.status == TrackerItemStatus.COMPLETE for ticket in tickets):
            task.status = TrackerItemStatus.COMPLETE
            task.completed_at = task.completed_at if old == task.status else now
            task.lane_key = "done"
        elif all(ticket.status == TrackerItemStatus.DEFERRED for ticket in tickets):
            task.status = TrackerItemStatus.DEFERRED
            task.completed_at = task.completed_at if old == task.status else now
        elif all(ticket.status in TERMINAL_TICKET_STATUSES for ticket in tickets):
            task.status = TrackerItemStatus.COMPLETE
            task.completed_at = task.completed_at if old == task.status else now
            task.lane_key = "done"
        elif any(
            ticket.status in (TrackerItemStatus.IN_PROGRESS, *TERMINAL_TICKET_STATUSES)
            for ticket in tickets
        ):
            task.status = TrackerItemStatus.IN_PROGRESS
            task.started_at = task.started_at or now
            task.completed_at = None
        else:
            task.status = TrackerItemStatus.NOT_STARTED
            task.completed_at = None
        if task.status != old:
            task.updated_at = now
            self._s.add(task)

    def _ready_ticket_rows(
        self,
        tracker_id: int | None,
        *,
        tickets: list[TrackerTicket] | None = None,
    ) -> list[TrackerTicket]:
        rows = tickets if tickets is not None else self._ticket_rows(tracker_id)
        return [
            ticket
            for ticket in rows
            if ticket.status not in TERMINAL_TICKET_STATUSES
            and not ticket.blocker_reason
            and not self._blocked_by_incomplete(tracker_id, ticket)
        ]

    def _ticket_blocks_active_work(self, tracker_id: int | None, ticket: TrackerTicket) -> bool:
        return ticket.status not in TERMINAL_TICKET_STATUSES and bool(
            ticket.blocker_reason or self._blocked_by_incomplete(tracker_id, ticket)
        )

    def _blocked_by_incomplete(self, tracker_id: int | None, ticket: TrackerTicket) -> list[str]:
        blockers: list[str] = []
        for dep in self._dependency_rows_for_ticket(ticket.id):
            dependency = self._s.get(TrackerTicket, dep.depends_on_ticket_id)
            if dependency is not None and dependency.status != TrackerItemStatus.COMPLETE:
                blockers.append(dependency.key)
        return blockers

    def _priority_rank(self, tracker_id: int | None, key: str) -> int:
        tracker_id = _required_id(tracker_id, "tracker")
        row = self._s.exec(
            select(TaskTrackerPriority).where(
                TaskTrackerPriority.tracker_id == tracker_id,
                TaskTrackerPriority.key == key,
            )
        ).first()
        return row.rank if row is not None else 100

    def _would_create_dependency_cycle(
        self,
        ticket: TrackerTicket,
        dependency_ticket: TrackerTicket,
    ) -> bool:
        target = ticket.id
        stack = [dependency_ticket.id]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current is None or current in seen:
                continue
            seen.add(current)
            rows = list(
                self._s.exec(
                    select(TrackerTicketDependency).where(
                        TrackerTicketDependency.ticket_id == current
                    )
                )
            )
            stack.extend(row.depends_on_ticket_id for row in rows)
        return False

    def _next_task_position(self, tracker_id: int | None) -> int:
        rows = self._task_rows(tracker_id)
        return max((row.order_index for row in rows), default=-1) + 1

    def _next_ticket_position(self, task_id: int | None) -> int:
        task_id = _required_id(task_id, "task")
        rows = list(self._s.exec(select(TrackerTicket).where(TrackerTicket.task_id == task_id)))
        return max((row.order_index for row in rows), default=-1) + 1

    def _record_revision(
        self,
        tracker: TaskTracker,
        *,
        actor: str | None,
        change_kind: str,
        entity_kind: str,
        entity_id: int | None,
        entity_key: str | None,
        summary: str,
        before_json: dict[str, Any] | None = None,
        after_json: dict[str, Any] | None = None,
        patch_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        commit: bool = False,
    ) -> None:
        now = _utcnow()
        tracker.rev += 1
        tracker.updated_at = now
        self._s.add(tracker)
        self._s.add(
            TrackerRevision(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                rev=tracker.rev,
                actor=actor,
                change_kind=change_kind,
                entity_kind=entity_kind,
                entity_id=entity_id,
                entity_key=entity_key,
                summary=summary,
                before_json=redact_secrets(before_json) if before_json is not None else None,
                after_json=redact_secrets(after_json) if after_json is not None else None,
                patch_json=redact_secrets(patch_json) if patch_json is not None else None,
                metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
                created_at=now,
            )
        )
        if commit:
            self._s.commit()
        else:
            self._s.flush()

    def _task_snapshot(self, task: TrackerTask) -> dict[str, Any]:
        return self._task_out(task).model_dump(mode="json")

    def _ticket_snapshot(self, ticket: TrackerTicket) -> dict[str, Any]:
        return self._ticket_out(ticket).model_dump(mode="json")

    def _count_statuses(self, statuses: list[TrackerItemStatus]) -> dict[str, int]:
        counts = {status.value: 0 for status in TrackerItemStatus}
        for status in statuses:
            counts[status.value] = counts.get(status.value, 0) + 1
        return counts

    def _ticket_status_from_step(self, status: RunPlanStepStatus) -> TrackerItemStatus:
        if status == RunPlanStepStatus.RUNNING:
            return TrackerItemStatus.IN_PROGRESS
        if status == RunPlanStepStatus.SUCCESS:
            return TrackerItemStatus.COMPLETE
        if status == RunPlanStepStatus.SKIPPED:
            return TrackerItemStatus.DEFERRED
        if status == RunPlanStepStatus.FAILED:
            return TrackerItemStatus.IN_PROGRESS
        return TrackerItemStatus.NOT_STARTED

    def _step_outcome(self, step: RunPlanStep) -> str | None:
        if step.error:
            return f"failed: {redact_secret_text(step.error)}"
        if isinstance(step.result_json, dict):
            for key in ("summary", "message", "status"):
                value = step.result_json.get(key)
                if value:
                    return redact_secret_text(str(value))
        return step.status.value

    def _suggest_next_actions(self, ticket: TrackerTicket) -> list[str]:
        checks = []
        if ticket.status == TrackerItemStatus.COMPLETE:
            return ["Review dependent tickets or start the next ready item."]
        blockers = self._blocked_by_incomplete(ticket.tracker_id, ticket)
        if blockers:
            checks.append(f"Complete dependencies first: {', '.join(blockers)}.")
        if ticket.blocker_reason:
            checks.append(f"Resolve blocker_reason: {ticket.blocker_reason}.")
        if not checks:
            checks.append("Claim or continue the ticket, then update status/outcome when done.")
        return checks

    def _suggest_next_actions_raw(
        self,
        ticket: TrackerTicketOut,
        checks: list[dict[str, Any]],
    ) -> list[str]:
        failed = [item for item in checks if not item["passed"]]
        if not failed:
            return ["Ticket is verification-ready. Mark complete after final human/agent review."]
        return [str(item["detail"]) for item in failed]


__all__ = [
    "DEFAULT_TRACKER_KEY",
    "TrackerBriefOut",
    "TrackerChangedOut",
    "TrackerDependencyOut",
    "TrackerDependencyPreviewOut",
    "TrackerGraphEdgeOut",
    "TrackerGraphNodeOut",
    "TrackerGraphOut",
    "TrackerHistoryOut",
    "TrackerLaneOut",
    "TrackerLinkOut",
    "TrackerMutationOut",
    "TrackerNextOut",
    "TrackerPriorityOut",
    "TrackerReferenceOut",
    "TrackerRepository",
    "TrackerSearchOut",
    "TrackerSnapshotOut",
    "TrackerStatusOut",
    "TrackerSummaryOut",
    "TrackerTaskOut",
    "TrackerTicketOut",
    "TrackerVerifyOut",
]
