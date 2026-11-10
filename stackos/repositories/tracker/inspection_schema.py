"""Bounded task inspection contracts over existing canonical sources."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from stackos.context.repository.schema import DecisionOut, ExperimentOut, LearningOut
from stackos.repositories.resources import ArtifactOut, ResourceRecordOut

ResultSourceKind = Literal["resource_record", "artifact", "learning", "decision", "experiment"]


class TaskActivityItem(BaseModel):
    source_kind: Literal["project_event", "tracker_revision"]
    source_id: int
    occurred_at: datetime
    kind: str
    summary: str
    task_key: str
    ticket_key: str | None = None
    run_id: int | None = None
    run_plan_id: int | None = None
    phase: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TaskResultAssociation(BaseModel):
    kind: str
    ref: str
    path: str


class TaskResultItem(BaseModel):
    source_kind: ResultSourceKind
    source_id: int
    occurred_at: datetime
    associations: list[TaskResultAssociation]
    data: ResourceRecordOut | ArtifactOut | LearningOut | DecisionOut | ExperimentOut


class TaskActivityOut(BaseModel):
    project_id: int
    task_id: int
    task_key: str
    items: list[TaskActivityItem]
    next_cursor: str | None = None
    coverage: str = (
        "Persisted linked project events and structured delivery lifecycle revisions; "
        "not a complete internal audit."
    )
    warnings: list[str] = Field(default_factory=list)


class TaskResultsOut(BaseModel):
    project_id: int
    task_id: int
    task_key: str
    source_kind: ResultSourceKind | None = None
    items: list[TaskResultItem]
    next_cursor: str | None = None
    coverage: str = (
        "Explicit tracker, run, historical execution-context and typed provenance "
        "associations only; "
        "association does not imply approval or exclusive production. "
        "Unlinked content remains in Library."
    )
    warnings: list[str] = Field(default_factory=list)
