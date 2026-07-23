"""Daemon-owned opaque references for provider objects."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _utcnow


class ProviderObjectReference(SQLModel, table=True):
    """Account-bound mapping from an opaque StackOS ref to a provider object id."""

    __tablename__ = "provider_object_references"
    __table_args__ = (
        UniqueConstraint("safe_ref", name="uq_provider_object_references_safe_ref"),
        UniqueConstraint(
            "project_id",
            "provider_key",
            "provider_account_id",
            "object_type",
            "provider_object_id",
            name="uq_provider_object_references_identity",
        ),
        Index(
            "ix_provider_object_references_account_type",
            "project_id",
            "provider_key",
            "provider_account_id",
            "object_type",
        ),
        Index("ix_provider_object_references_safe_ref", "safe_ref"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    credential_id: int = Field(
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_key: str = Field(max_length=160)
    provider_account_id: str = Field(max_length=300)
    object_type: str = Field(max_length=200)
    provider_object_id: str = Field(max_length=500)
    safe_ref: str = Field(max_length=120)
    display_name: str | None = Field(default=None, max_length=500)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    stale_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


__all__ = ["ProviderObjectReference"]
