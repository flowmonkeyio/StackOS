"""Pydantic output models for StackOS project memory."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    source_type: str
    source_id: int | None
    event_type: str
    title: str | None
    summary: str | None
    tags: list[str]
    metadata_json: dict[str, Any] | None
    occurred_at: datetime
    created_at: datetime


class ContextIndexEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    source_type: str
    source_id: int | None
    title: str | None
    summary: str | None
    domain: str | None
    provider_key: str | None
    status: str | None
    tags: list[str]
    metadata_json: dict[str, Any] | None
    occurred_at: datetime
    created_at: datetime


class ContextSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    name: str | None
    query_json: dict[str, Any]
    selected_sources_json: list[dict[str, Any]]
    summary_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class ContextItemOut(BaseModel):
    source: str
    id: int
    project_id: int | None
    title: str | None
    occurred_at: datetime | None
    fields: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ContextQueryOut(BaseModel):
    project_id: int
    sources: list[str]
    fields: list[str]
    limit: int
    items: list[ContextItemOut]
    total_estimate: int


class ContextPageOut(BaseModel):
    items: list[ContextItemOut]
    next_cursor: int | None = None
    total_estimate: int = 0


class LearningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    source_snapshot_id: int | None
    supersedes_learning_id: int | None
    statement: str
    domain: str | None
    confidence: str
    status: str
    review_state: str
    created_by: str | None
    tags: list[str]
    applies_to_json: dict[str, Any] | None
    evidence_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ExperimentVariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: int
    key: str
    name: str | None
    resources_json: list[dict[str, Any]] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    key: str | None
    name: str | None
    domain: str | None
    hypothesis: str
    status: str
    linked_template_ids_json: list[str] | None
    linked_run_ids_json: list[int] | None
    metric_targets_json: dict[str, Any] | None
    decision_policy_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    variants: list[ExperimentVariantOut]
    created_at: datetime
    updated_at: datetime


class ExperimentObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    experiment_id: int
    run_id: int | None
    variant_key: str | None
    metrics_json: dict[str, Any]
    summary: str | None
    metadata_json: dict[str, Any] | None
    observed_at: datetime
    created_at: datetime


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    experiment_id: int | None
    run_id: int | None
    title: str | None
    decision: str
    rationale: str | None
    status: str
    decided_by: str | None
    tags: list[str]
    evidence_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime


class MetricSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    source_type: str | None
    source_id: int | None
    metric_key: str
    metric_value: float | None
    dimensions_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    captured_at: datetime
    created_at: datetime
