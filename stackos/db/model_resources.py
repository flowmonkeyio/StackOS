"""SQLModel table declarations for resources, artifacts, and project events."""

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


class Resource(SQLModel, table=True):
    """Resource type declared by a StackOS plugin.

    Resources are static schemas/catalog metadata. They do not decide workflow
    behavior; agents and humans decide what to write, then StackOS validates,
    stores, filters, and retrieves the records.
    """

    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_resources_plugin_key"),
        Index("ix_resources_plugin", "plugin_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    schema_data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("schema_json", JSON),
    )
    ui_schema_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ResourceRecord(SQLModel, table=True):
    """Project-scoped record for a plugin-defined resource type."""

    __tablename__ = "resource_records"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "resource_id",
            "external_id",
            name="uq_resource_records_project_resource_external",
        ),
        Index("ix_resource_records_project_resource", "project_id", "resource_id"),
        Index("ix_resource_records_resource", "resource_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    resource_id: int = Field(
        sa_column=Column(
            ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    external_id: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=300)
    data_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    provenance_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Artifact(SQLModel, table=True):
    """Generic artifact storage reference.

    Artifacts point at files, generated media, screenshots, exports, or other
    blobs produced by tools. The daemon stores references and sanitized
    metadata only; provider auth and secret material stay outside agent reach.
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_project", "project_id"),
        Index("ix_artifacts_project_status", "project_id", "status"),
        Index("ix_artifacts_resource_record", "resource_record_id"),
        Index("ix_artifacts_plugin", "plugin_id"),
        Index("ix_artifacts_superseded_by", "superseded_by_artifact_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    plugin_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="SET NULL"),
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
    kind: str = Field(max_length=80)
    uri: str = Field(max_length=2048)
    status: str = Field(default="draft", max_length=40)
    name: str | None = Field(default=None, max_length=300)
    mime_type: str | None = Field(default=None, max_length=160)
    size_bytes: int | None = Field(default=None)
    superseded_by_artifact_id: int | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    provenance_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ProjectEvent(SQLModel, table=True):
    """Append-only project memory timeline event."""

    __tablename__ = "project_events"
    __table_args__ = (
        Index("ix_project_events_project_occurred", "project_id", "occurred_at"),
        Index("ix_project_events_source", "source_type", "source_id"),
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
    source_type: str = Field(max_length=80)
    source_id: int | None = Field(default=None)
    event_type: str = Field(max_length=120)
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    occurred_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


__all__ = [
    "Artifact",
    "ProjectEvent",
    "Resource",
    "ResourceRecord",
]
