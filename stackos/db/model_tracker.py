"""SQLModel table declarations for tracker tasks, tickets, and relations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _enum_column, _utcnow
from stackos.db.model_enums import (
    TrackerItemStatus,
    TrackerLinkKind,
    TrackerSourceKind,
    TrackerTicketKind,
)


class TaskTracker(SQLModel, table=True):
    """Project-scoped tracker container.

    StackOS keeps one default tracker per project today, but the table stays
    explicit so future archived/imported trackers can coexist without changing
    the task/ticket contract.
    """

    __tablename__ = "task_trackers"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_task_trackers_project_key"),
        Index("ix_task_trackers_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=240)
    description: str = Field(default="")
    rev: int = Field(default=0, nullable=False)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class TaskTrackerLane(SQLModel, table=True):
    """Named tracker lane used by agents and UI filters."""

    __tablename__ = "task_tracker_lanes"
    __table_args__ = (
        UniqueConstraint("tracker_id", "key", name="uq_task_tracker_lanes_tracker_key"),
        Index("ix_task_tracker_lanes_tracker", "tracker_id", "position"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=80)
    label: str = Field(max_length=160)
    position: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class TaskTrackerPriority(SQLModel, table=True):
    """Named tracker priority used by agents and UI filters."""

    __tablename__ = "task_tracker_priorities"
    __table_args__ = (
        UniqueConstraint("tracker_id", "key", name="uq_task_tracker_priorities_tracker_key"),
        Index("ix_task_tracker_priorities_tracker", "tracker_id", "position"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=40)
    label: str = Field(max_length=120)
    rank: int = Field(default=100, nullable=False)
    position: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class TrackerTask(SQLModel, table=True):
    """Durable work objective that owns executable tickets."""

    __tablename__ = "tracker_tasks"
    __table_args__ = (
        UniqueConstraint("tracker_id", "key", name="uq_tracker_tasks_tracker_key"),
        Index("ix_tracker_tasks_project_status", "project_id", "status"),
        Index("ix_tracker_tasks_project_lane", "project_id", "lane_key"),
        Index("ix_tracker_tasks_project_priority", "project_id", "priority_key"),
        Index("ix_tracker_tasks_project_source", "project_id", "source_kind"),
        Index("ix_tracker_tasks_tracker_position", "tracker_id", "order_index"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=200)
    title: str = Field(max_length=300)
    goal: str = Field(default="", sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: TrackerItemStatus = Field(sa_column=_enum_column(TrackerItemStatus))
    priority_key: str = Field(default="p2", max_length=40)
    lane_key: str = Field(default="implementation", max_length=80)
    owner: str | None = Field(default=None, max_length=120)
    task_type: str = Field(default="task", max_length=80)
    order_index: int = Field(default=0, nullable=False)
    source_kind: TrackerSourceKind = Field(sa_column=_enum_column(TrackerSourceKind))
    source_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    definition_of_done_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    constraints_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    expected_outcomes_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    completion_evidence_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    context_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_by: str | None = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)


class TrackerTicket(SQLModel, table=True):
    """Executable tracker unit, close to an llm-tracker card."""

    __tablename__ = "tracker_tickets"
    __table_args__ = (
        UniqueConstraint("tracker_id", "key", name="uq_tracker_tickets_tracker_key"),
        Index("ix_tracker_tickets_project_status", "project_id", "status"),
        Index("ix_tracker_tickets_project_task", "project_id", "task_id"),
        Index("ix_tracker_tickets_project_lane", "project_id", "lane_key"),
        Index("ix_tracker_tickets_project_priority", "project_id", "priority_key"),
        Index("ix_tracker_tickets_project_assignee", "project_id", "assignee"),
        Index("ix_tracker_tickets_parent", "parent_ticket_id"),
        Index("ix_tracker_tickets_run_plan", "run_plan_id"),
        Index("ix_tracker_tickets_step", "run_plan_step_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    task_id: int = Field(
        sa_column=Column(
            ForeignKey("tracker_tasks.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    parent_ticket_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("tracker_tickets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    run_plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    run_plan_step_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plan_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    agent_request_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("agent_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=200)
    title: str = Field(max_length=300)
    goal: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: TrackerItemStatus = Field(sa_column=_enum_column(TrackerItemStatus))
    kind: TrackerTicketKind = Field(sa_column=_enum_column(TrackerTicketKind))
    assignee: str | None = Field(default=None, max_length=120)
    priority_key: str = Field(default="p2", max_length=40)
    lane_key: str = Field(default="implementation", max_length=80)
    order_index: int = Field(default=0, nullable=False)
    blocker_reason: str | None = Field(default=None, sa_column=Column(Text))
    outcome: str | None = Field(default=None, sa_column=Column(Text))
    effort: str | None = Field(default=None, max_length=40)
    source_kind: TrackerSourceKind = Field(sa_column=_enum_column(TrackerSourceKind))
    source_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    definition_of_done_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    constraints_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    expected_changes_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    allowed_paths_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    completion_evidence_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    context_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_by: str | None = Field(default=None, max_length=120)
    claimed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)


class TrackerTicketDependency(SQLModel, table=True):
    """Ticket dependency edge for graph/readiness calculations."""

    __tablename__ = "tracker_ticket_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id",
            "depends_on_ticket_id",
            name="uq_tracker_ticket_dependencies_pair",
        ),
        Index("ix_tracker_ticket_dependencies_project", "project_id"),
        Index("ix_tracker_ticket_dependencies_ticket", "ticket_id"),
        Index("ix_tracker_ticket_dependencies_depends_on", "depends_on_ticket_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    ticket_id: int = Field(
        sa_column=Column(
            ForeignKey("tracker_tickets.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    depends_on_ticket_id: int = Field(
        sa_column=Column(
            ForeignKey("tracker_tickets.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    dependency_type: str = Field(default="blocks", max_length=80)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class TrackerTicketReference(SQLModel, table=True):
    """Human/agent references attached to a ticket."""

    __tablename__ = "tracker_ticket_references"
    __table_args__ = (
        Index("ix_tracker_ticket_references_project", "project_id"),
        Index("ix_tracker_ticket_references_ticket", "ticket_id"),
        Index("ix_tracker_ticket_references_ref", "ref_type", "ref"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    ticket_id: int = Field(
        sa_column=Column(
            ForeignKey("tracker_tickets.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    ref_type: str = Field(max_length=80)
    ref: str = Field(max_length=1000)
    title: str | None = Field(default=None, max_length=300)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class TrackerTicketLink(SQLModel, table=True):
    """Typed link from tracker work to StackOS or external objects."""

    __tablename__ = "tracker_ticket_links"
    __table_args__ = (
        Index("ix_tracker_ticket_links_project", "project_id"),
        Index("ix_tracker_ticket_links_task", "task_id"),
        Index("ix_tracker_ticket_links_ticket", "ticket_id"),
        Index("ix_tracker_ticket_links_kind", "link_kind"),
        Index("ix_tracker_ticket_links_run_plan", "run_plan_id"),
        Index("ix_tracker_ticket_links_run_plan_step", "run_plan_step_id"),
        Index("ix_tracker_ticket_links_agent_request", "agent_request_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    task_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("tracker_tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    ticket_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("tracker_tickets.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    link_kind: TrackerLinkKind = Field(sa_column=_enum_column(TrackerLinkKind))
    ref: str | None = Field(default=None, max_length=1000)
    run_plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    run_plan_step_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plan_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    agent_request_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("agent_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    resource_record_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("resource_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    artifact_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    action_call_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("action_calls.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    title: str | None = Field(default=None, max_length=300)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class TrackerRevision(SQLModel, table=True):
    """Append-only tracker change history."""

    __tablename__ = "tracker_revisions"
    __table_args__ = (
        UniqueConstraint("tracker_id", "rev", name="uq_tracker_revisions_tracker_rev"),
        Index("ix_tracker_revisions_project", "project_id", "created_at"),
        Index("ix_tracker_revisions_entity", "entity_kind", "entity_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    rev: int = Field(nullable=False)
    actor: str | None = Field(default=None, max_length=120)
    change_kind: str = Field(max_length=80)
    entity_kind: str = Field(max_length=80)
    entity_id: int | None = Field(default=None)
    entity_key: str | None = Field(default=None, max_length=200)
    summary: str = Field(default="", sa_column=Column(Text, nullable=False))
    before_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    after_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    patch_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class TrackerTombstone(SQLModel, table=True):
    """Deleted tracker entity marker for history and external sync."""

    __tablename__ = "tracker_tombstones"
    __table_args__ = (
        UniqueConstraint(
            "tracker_id",
            "entity_kind",
            "entity_key",
            name="uq_tracker_tombstones_tracker_entity",
        ),
        Index("ix_tracker_tombstones_project", "project_id", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tracker_id: int = Field(
        sa_column=Column(
            ForeignKey("task_trackers.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    entity_kind: str = Field(max_length=80)
    entity_id: int | None = Field(default=None)
    entity_key: str = Field(max_length=200)
    deleted_by: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, sa_column=Column(Text))
    snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    deleted_at: datetime = Field(default_factory=_utcnow, nullable=False)


__all__ = [
    "TaskTracker",
    "TaskTrackerLane",
    "TaskTrackerPriority",
    "TrackerRevision",
    "TrackerTask",
    "TrackerTicket",
    "TrackerTicketDependency",
    "TrackerTicketLink",
    "TrackerTicketReference",
    "TrackerTombstone",
]
