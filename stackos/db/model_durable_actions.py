"""Durable generic action scheduling, receipts, and shared delivery admission."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _enum_column, _utcnow
from stackos.db.model_enums import (
    DurableActionAttemptStatus,
    DurableActionItemStatus,
    DurableActionJobStatus,
)


class DurableActionJob(SQLModel, table=True):
    """One immutable queued action request linked to its normal ActionCall receipt."""

    __tablename__ = "durable_action_jobs"
    __table_args__ = (
        UniqueConstraint("action_call_id", name="uq_durable_action_jobs_action_call"),
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_durable_action_jobs_project_idempotency",
        ),
        Index("ix_durable_action_jobs_project_state_due", "project_id", "state", "due_at"),
        Index("ix_durable_action_jobs_credential_state", "credential_id", "state"),
        Index("ix_durable_action_jobs_run_plan_step", "run_plan_step_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    )
    action_call_id: int = Field(
        sa_column=Column(ForeignKey("action_calls.id", ondelete="CASCADE"), nullable=False)
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
    )
    run_plan_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("run_plans.id", ondelete="SET NULL"), nullable=True),
    )
    run_plan_step_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("run_plan_steps.id", ondelete="SET NULL"), nullable=True),
    )
    credential_id: int = Field(
        sa_column=Column(ForeignKey("credentials.id", ondelete="RESTRICT"), nullable=False)
    )
    credential_ref: str = Field(max_length=120)
    action_ref: str = Field(max_length=300)
    idempotency_key: str | None = Field(default=None, max_length=160)
    input_digest: str = Field(max_length=64)
    input_snapshot_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    pacing_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    state: DurableActionJobStatus = Field(
        default=DurableActionJobStatus.SCHEDULED,
        sa_column=_enum_column(DurableActionJobStatus),
    )
    due_at: datetime = Field(default_factory=_utcnow, nullable=False)
    expires_at: datetime | None = Field(default=None)
    paused_at: datetime | None = Field(default=None)
    cancelled_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class DurableActionItem(SQLModel, table=True):
    """One sealed destination payload within a durable action job."""

    __tablename__ = "durable_action_items"
    __table_args__ = (
        UniqueConstraint("job_id", "ordinal", name="uq_durable_action_items_job_ordinal"),
        UniqueConstraint(
            "job_id", "correlation_ref", name="uq_durable_action_items_job_correlation"
        ),
        Index("ix_durable_action_items_job_state_due", "job_id", "state", "next_eligible_at"),
        Index("ix_durable_action_items_lease", "lease_expires_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(
        sa_column=Column(ForeignKey("durable_action_jobs.id", ondelete="CASCADE"), nullable=False)
    )
    ordinal: int = Field(nullable=False)
    destination_ref: str = Field(max_length=300)
    correlation_ref: str = Field(max_length=300)
    input_digest: str = Field(max_length=64)
    input_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    state: DurableActionItemStatus = Field(
        default=DurableActionItemStatus.PENDING,
        sa_column=_enum_column(DurableActionItemStatus),
    )
    next_eligible_at: datetime = Field(default_factory=_utcnow, nullable=False)
    lease_ref: str | None = Field(default=None, max_length=120)
    lease_expires_at: datetime | None = Field(default=None)
    attempt_count: int = Field(default=0, nullable=False)
    first_attempted_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    result_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class DurableActionAttempt(SQLModel, table=True):
    """Pre-effect attempt correlation and terminal receipt for one durable item."""

    __tablename__ = "durable_action_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_ref", name="uq_durable_action_attempts_ref"),
        UniqueConstraint("item_id", "ordinal", name="uq_durable_action_attempts_item_ordinal"),
        Index("ix_durable_action_attempts_item", "item_id"),
        Index("ix_durable_action_attempts_state", "state"),
    )

    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(
        sa_column=Column(ForeignKey("durable_action_items.id", ondelete="CASCADE"), nullable=False)
    )
    ordinal: int = Field(nullable=False)
    attempt_ref: str = Field(max_length=120)
    state: DurableActionAttemptStatus = Field(
        default=DurableActionAttemptStatus.LEASED,
        sa_column=_enum_column(DurableActionAttemptStatus),
    )
    provider_sending_id: str | None = Field(default=None, max_length=300)
    temporary_message_ref: str | None = Field(default=None, max_length=300)
    final_message_ref: str | None = Field(default=None, max_length=300)
    progress_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    receipt_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    completed_at: datetime | None = Field(default=None)


class ActionDeliveryAdmission(SQLModel, table=True):
    """Global Account/destination lease and next-eligible state for dispatch admission."""

    __tablename__ = "action_delivery_admissions"
    __table_args__ = (
        UniqueConstraint("credential_id", "scope_key", name="uq_action_delivery_admissions_scope"),
        Index("ix_action_delivery_admissions_lease", "lease_expires_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int = Field(
        sa_column=Column(ForeignKey("credentials.id", ondelete="RESTRICT"), nullable=False)
    )
    scope_key: str = Field(max_length=400)
    next_eligible_at: datetime = Field(default_factory=_utcnow, nullable=False)
    lease_ref: str | None = Field(default=None, max_length=120)
    lease_expires_at: datetime | None = Field(default=None)
    quiesced_at: datetime | None = Field(default=None)
    quiesce_reason: str | None = Field(default=None, max_length=300)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class DurableActionArtifact(SQLModel, table=True):
    """One artifact retained while its sealed durable delivery can still use it."""

    __tablename__ = "durable_action_artifacts"
    __table_args__ = (
        UniqueConstraint("job_id", "artifact_id", name="uq_durable_action_artifacts_job_artifact"),
        Index("ix_durable_action_artifacts_artifact", "artifact_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(
        sa_column=Column(ForeignKey("durable_action_jobs.id", ondelete="CASCADE"), nullable=False)
    )
    artifact_id: int = Field(
        sa_column=Column(ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False)
    )
    artifact_uri: str = Field(max_length=2048)
    content_sha256: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


__all__ = [
    "ActionDeliveryAdmission",
    "DurableActionArtifact",
    "DurableActionAttempt",
    "DurableActionItem",
    "DurableActionJob",
]
