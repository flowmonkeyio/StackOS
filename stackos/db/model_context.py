"""SQLModel table declarations for context, learning, experiments, decisions, and metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _utcnow


class ContextIndexEntry(SQLModel, table=True):
    """Compact, searchable pointer into project memory."""

    __tablename__ = "context_index_entries"
    __table_args__ = (
        Index("ix_context_index_project_source", "project_id", "source_type", "source_id"),
        Index("ix_context_index_project_occurred", "project_id", "occurred_at"),
        Index("ix_context_index_domain_status", "domain", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    source_type: str = Field(max_length=80)
    source_id: int | None = Field(default=None)
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None)
    domain: str | None = Field(default=None, max_length=120)
    provider_key: str | None = Field(default=None, max_length=160)
    status: str | None = Field(default=None, max_length=80)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    occurred_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ContextSnapshot(SQLModel, table=True):
    """Immutable record of context loaded for a run or operator review."""

    __tablename__ = "context_snapshots"
    __table_args__ = (
        Index("ix_context_snapshots_project", "project_id"),
        Index("ix_context_snapshots_run", "run_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    name: str | None = Field(default=None, max_length=300)
    query_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    selected_sources_json: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    summary_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Learning(SQLModel, table=True):
    """Project-level learning candidate or accepted learning."""

    __tablename__ = "learnings"
    __table_args__ = (
        Index("ix_learnings_project_status", "project_id", "status", "review_state"),
        Index("ix_learnings_domain", "domain"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    source_snapshot_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("context_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    supersedes_learning_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("learnings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    statement: str
    domain: str | None = Field(default=None, max_length=120)
    confidence: str = Field(default="unknown", max_length=40)
    status: str = Field(default="active", max_length=40)
    review_state: str = Field(default="proposed", max_length=40)
    created_by: str | None = Field(default=None, max_length=120)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    applies_to_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    evidence_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Experiment(SQLModel, table=True):
    """Project-level experiment that may span multiple runs/providers."""

    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_experiments_project_key"),
        Index("ix_experiments_project_status", "project_id", "status"),
        Index("ix_experiments_domain", "domain"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str | None = Field(default=None, max_length=160)
    name: str | None = Field(default=None, max_length=300)
    domain: str | None = Field(default=None, max_length=120)
    hypothesis: str
    status: str = Field(default="planned", max_length=60)
    linked_template_ids_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    linked_run_ids_json: list[int] | None = Field(default=None, sa_column=Column(JSON))
    metric_targets_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    decision_policy_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ExperimentVariant(SQLModel, table=True):
    """Experiment arm metadata supplied by an agent or human."""

    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "key", name="uq_experiment_variants_key"),
        Index("ix_experiment_variants_experiment", "experiment_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    experiment_id: int = Field(
        sa_column=Column(
            ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=160)
    name: str | None = Field(default=None, max_length=300)
    resources_json: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ExperimentObservation(SQLModel, table=True):
    """Observed experiment data supplied by a tool, agent, or human."""

    __tablename__ = "experiment_observations"
    __table_args__ = (
        Index("ix_experiment_observations_experiment", "experiment_id", "observed_at"),
        Index("ix_experiment_observations_project", "project_id", "observed_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    experiment_id: int = Field(
        sa_column=Column(
            ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    variant_key: str | None = Field(default=None, max_length=160)
    metrics_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    summary: str | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    observed_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Decision(SQLModel, table=True):
    """Explicit decision record supplied by an agent or human."""

    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_project", "project_id", "created_at"),
        Index("ix_decisions_experiment", "experiment_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    experiment_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("experiments.id", ondelete="SET NULL"),
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
    title: str | None = Field(default=None, max_length=300)
    decision: str
    rationale: str | None = Field(default=None)
    status: str = Field(default="recorded", max_length=60)
    decided_by: str | None = Field(default=None, max_length=120)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    evidence_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class MetricSnapshot(SQLModel, table=True):
    """Point-in-time metric value linked to a project/source."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_project_metric", "project_id", "metric_key", "captured_at"),
        Index("ix_metric_snapshots_source", "source_type", "source_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    source_type: str | None = Field(default=None, max_length=80)
    source_id: int | None = Field(default=None)
    metric_key: str = Field(max_length=160)
    metric_value: float | None = Field(default=None)
    dimensions_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    captured_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


__all__ = [
    "ContextIndexEntry",
    "ContextSnapshot",
    "Decision",
    "Experiment",
    "ExperimentObservation",
    "ExperimentVariant",
    "Learning",
    "MetricSnapshot",
]
