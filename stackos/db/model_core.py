"""SQLModel table declarations for projects, plugins, actions, and providers."""

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

from stackos.db.model_base import _enum_column, _utcnow
from stackos.db.model_enums import (
    ActionCallStatus,
    PluginSource,
)


class Project(SQLModel, table=True):
    """Site registrations (PLAN.md L347).

    ``slug`` is globally unique (no ``project_id`` prefix). ``locale`` is
    singular per D3; multi-locale = separate row.
    """

    __tablename__ = "projects"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=80, unique=True, index=True)
    name: str = Field(max_length=200)
    domain: str = Field(max_length=255)
    niche: str | None = Field(default=None, max_length=200)
    locale: str = Field(max_length=16)
    is_active: bool = Field(default=False)
    schedule_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Plugin(SQLModel, table=True):
    """Installed StackOS plugin manifest metadata.

    Plugin rows are catalog state only. They describe capabilities, providers,
    actions, resources, and UI contributions; domain execution remains in
    plugin-owned manifests/connectors and grant-gated tools.
    """

    __tablename__ = "plugins"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=120, unique=True, index=True)
    name: str = Field(max_length=200)
    version: str = Field(default="0.1.0", max_length=40)
    description: str = Field(default="")
    source: PluginSource = Field(sa_column=_enum_column(PluginSource))
    manifest_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ProjectPlugin(SQLModel, table=True):
    """Project-level plugin enablement state."""

    __tablename__ = "project_plugins"
    __table_args__ = (
        UniqueConstraint("project_id", "plugin_id", name="uq_project_plugins_project_plugin"),
        Index("ix_project_plugins_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    enabled: bool = Field(default=True)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    enabled_at: datetime | None = Field(default_factory=_utcnow)
    disabled_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Capability(SQLModel, table=True):
    """Capability contributed by a plugin, such as SEO publishing or images."""

    __tablename__ = "capabilities"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_capabilities_plugin_key"),
        Index("ix_capabilities_plugin", "plugin_id"),
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
    kind: str = Field(default="domain", max_length=80)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Provider(SQLModel, table=True):
    """External or internal provider declared by a plugin."""

    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_providers_plugin_key"),
        Index("ix_providers_plugin", "plugin_id"),
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
    auth_type: str = Field(default="none", max_length=80)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Action(SQLModel, table=True):
    """Generic action declared by a plugin/provider.

    D02 stores schema/catalog metadata only. Execution lands in later action
    deliverables and must remain grant-gated.
    """

    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_actions_plugin_key"),
        Index("ix_actions_plugin", "plugin_id"),
        Index("ix_actions_provider", "provider_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    capability_key: str | None = Field(default=None, max_length=160)
    risk_level: str = Field(default="read", max_length=40)
    input_schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    output_schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ActionVersion(SQLModel, table=True):
    """Versioned action manifest snapshots."""

    __tablename__ = "action_versions"
    __table_args__ = (
        UniqueConstraint("action_id", "version", name="uq_action_versions_action_version"),
        Index("ix_action_versions_action", "action_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    action_id: int = Field(
        sa_column=Column(
            ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    version: str = Field(max_length=40)
    manifest_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ActionCall(SQLModel, table=True):
    """Redacted audit row for internal generic action execution.

    Action calls are StackOS sidecars. They record what the daemon executed,
    which credential ref was used, and the sanitized request/response envelope;
    plaintext secrets stay inside the connector boundary.
    """

    __tablename__ = "action_calls"
    __table_args__ = (
        Index("ix_action_calls_project_created", "project_id", "created_at"),
        Index("ix_action_calls_run", "run_id"),
        Index("ix_action_calls_run_plan_step", "run_plan_step_id"),
        Index("ix_action_calls_action", "action_id"),
        Index("ix_action_calls_project_idempotency", "project_id", "idempotency_key"),
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
    action_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    action_key: str = Field(max_length=160)
    plugin_slug: str = Field(max_length=120)
    provider_key: str | None = Field(default=None, max_length=160)
    connector_key: str | None = Field(default=None, max_length=160)
    operation: str = Field(max_length=160)
    status: ActionCallStatus = Field(sa_column=_enum_column(ActionCallStatus))
    dry_run: bool = Field(default=False)
    idempotency_key: str | None = Field(default=None, max_length=160)
    credential_ref: str | None = Field(default=None, max_length=120)
    request_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    cost_cents: int = Field(default=0)
    duration_ms: int | None = Field(default=None)
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    completed_at: datetime | None = Field(default=None)


__all__ = [
    "Action",
    "ActionCall",
    "ActionVersion",
    "Capability",
    "Plugin",
    "Project",
    "ProjectPlugin",
    "Provider",
]
