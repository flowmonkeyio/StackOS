"""Pydantic response models for the tracker repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.db.models import (
    TrackerItemStatus,
    TrackerLinkKind,
    TrackerSourceKind,
    TrackerTicketKind,
)


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


class TrackerWorkflowHandoffOut(BaseModel):
    run_plan_id: int
    run_plan_step_id: int | None = None
    run_id: int | None = None
    step_id: str | None = None
    run_plan_key: str | None = None
    template_key: str | None = None
    next_operations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    workflow_handoff: TrackerWorkflowHandoffOut | None = None
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
    project_id: int
    query: str
    tasks: list[TrackerTaskOut]
    tickets: list[TrackerTicketOut]
    task_count: int = 0
    ticket_count: int = 0


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
    action: Literal["validated", "created", "updated", "rejected", "skipped", "noop", "error"]
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


class TrackerReopenOut(BaseModel):
    tracker: TrackerSummaryOut
    task: TrackerTaskOut | None = None
    rev: int
    run_plan_id: int | None = None
    run_id: int | None = None
    run_token: str | None = None
    reopened_step_id: str | None = None
    reset_step_ids: list[str] = Field(default_factory=list)
    next_operations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


__all__ = [
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
    "TrackerListIssueOut",
    "TrackerListItemResultOut",
    "TrackerMutationOut",
    "TrackerNextOut",
    "TrackerPriorityOut",
    "TrackerReferenceOut",
    "TrackerReopenOut",
    "TrackerSearchOut",
    "TrackerSnapshotOut",
    "TrackerStatusOut",
    "TrackerSummaryOut",
    "TrackerTaskOut",
    "TrackerTicketOut",
    "TrackerVerifyOut",
    "TrackerWorkflowHandoffOut",
]
