"""SQLModel declarations for the StackOS core tables.

Persistence rules:

- Enums are stored as TEXT (`native_enum=False` so SQLite gets the
  string verbatim, not a numeric ordinal). The `values_callable`
  hook ensures we ship the canonical hyphenated PLAN.md spelling
  (e.g. ``aborted-publish``) and not the Python identifier.
- Foreign keys are declared at the SQLModel `Field(foreign_key=...)`
  level so SQLModel/SQLAlchemy emit them; ON DELETE behaviour is
  attached via an explicit `sa.Column(ForeignKey(..., ondelete=...))`
  where it differs from the default RESTRICT.
- Composite and unique indexes are declared on the model when SQLAlchemy can
  express them, so introspection of ``SQLModel.metadata`` matches the on-disk
  database after an autogenerate diff.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_enums import (
    ACTION_CALL_STATUS_TRANSITIONS,
    AGENT_REQUEST_STATUS_TRANSITIONS,
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    TRACKER_ITEM_STATUS_TRANSITIONS,
    ActionCallStatus,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    ApprovalRequestStatus,
    PluginSource,
    RunKind,
    RunPlanStatus,
    RunPlanStepStatus,
    RunStatus,
    RunStepStatus,
    TrackerItemStatus,
    TrackerLinkKind,
    TrackerSourceKind,
    TrackerTicketKind,
)

# ---------------------------------------------------------------------------
# Enum helper
# ---------------------------------------------------------------------------


def _enum_column(
    enum_cls: type[enum.Enum],
    *,
    nullable: bool = False,
    name: str | None = None,
) -> Column[Any]:
    """Build a portable TEXT-backed Enum column.

    SQLite has no native ENUM type; using ``native_enum=False`` forces a
    CHECK-constrained TEXT column, which matches PLAN.md L383
    ("string columns, validated by pydantic"). ``values_callable``
    returns the canonical hyphenated value string (PLAN.md spellings
    like ``aborted-publish`` cannot be Python identifiers, so we keep
    the Python member ``ABORTED_PUBLISH`` while persisting the value).
    """
    return Column(
        SAEnum(
            enum_cls,
            native_enum=False,
            length=64,
            values_callable=lambda cls: [m.value for m in cls],
            name=name or f"ck_{enum_cls.__name__.lower()}",
        ),
        nullable=nullable,
    )


def _utcnow() -> datetime:
    """Naive UTC default for ``created_at`` / ``updated_at`` columns.

    SQLite stores datetimes as ISO-8601 text; we keep the value naive but
    explicitly UTC for consistency. Tests rely on a callable default so
    rows inserted in the same transaction don't share a frozen timestamp.
    """
    return datetime.now(tz=UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Tables — order chosen so referenced parents are declared before children.
# ---------------------------------------------------------------------------


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


class WorkflowTemplate(SQLModel, table=True):
    """Reusable workflow template catalog/storage row.

    Templates are inert configuration and instruction contracts. They do not
    execute actions or decide provider payloads; agents turn them into concrete
    run plans later.
    """

    __tablename__ = "workflow_templates"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "key",
            "source",
            name="uq_workflow_templates_project_key_source",
        ),
        Index("ix_workflow_templates_key", "key"),
        Index("ix_workflow_templates_project", "project_id"),
        Index("ix_workflow_templates_plugin", "plugin_id"),
        Index("ix_workflow_templates_source", "source"),
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
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    source: str = Field(max_length=40)
    origin_path: str | None = Field(default=None, max_length=1000)
    status: str = Field(default="active", max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class WorkflowTemplateVersion(SQLModel, table=True):
    """Immutable version snapshot for a workflow template."""

    __tablename__ = "workflow_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "version",
            name="uq_workflow_template_versions_template_version",
        ),
        Index("ix_workflow_template_versions_template", "template_id"),
        Index("ix_workflow_template_versions_checksum", "checksum"),
    )

    id: int | None = Field(default=None, primary_key=True)
    template_id: int = Field(
        sa_column=Column(
            ForeignKey("workflow_templates.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    version: str = Field(max_length=40)
    spec_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    checksum: str = Field(max_length=64)
    created_by: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ProjectWorkflowTemplate(SQLModel, table=True):
    """Project-level enablement/current pointer for stored templates."""

    __tablename__ = "project_workflow_templates"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "template_id",
            name="uq_project_workflow_templates_project_template",
        ),
        Index("ix_project_workflow_templates_project", "project_id"),
        Index("ix_project_workflow_templates_template", "template_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    template_id: int = Field(
        sa_column=Column(
            ForeignKey("workflow_templates.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    active_version_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workflow_template_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    enabled: bool = Field(default=True)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class RunPlan(SQLModel, table=True):
    """Concrete agent-authored execution plan derived from a workflow template.

    Run plans freeze setup, inputs, steps, approvals, context references, and
    audit links. The linked ``runs`` row is opened only when the plan starts.
    """

    __tablename__ = "run_plans"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_plans_run"),
        Index("ix_run_plans_project_status", "project_id", "status"),
        Index("ix_run_plans_run", "run_id"),
        Index("ix_run_plans_template", "template_id", "template_version_id"),
        Index("ix_run_plans_context_snapshot", "context_snapshot_id"),
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
    template_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workflow_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    template_version_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workflow_template_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    context_snapshot_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("context_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    title: str = Field(max_length=300)
    goal: str = Field(default="")
    status: RunPlanStatus = Field(sa_column=_enum_column(RunPlanStatus))
    template_key: str | None = Field(default=None, max_length=160)
    template_version: str | None = Field(default=None, max_length=40)
    template_source: str | None = Field(default=None, max_length=40)
    template_origin_path: str | None = Field(default=None, max_length=1000)
    template_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    inputs_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    selected_context_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    context_filters_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    grant_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    budget_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    policy_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    output_contract_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_by: str | None = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)


class RunPlanStep(SQLModel, table=True):
    """Concrete step state for a run plan.

    Step rows store the action/resource/context references and caller-supplied
    payload snapshots. StackOS only validates and records them; the agent owns
    the decisions behind the plan.
    """

    __tablename__ = "run_plan_steps"
    __table_args__ = (
        UniqueConstraint("run_plan_id", "step_id", name="uq_run_plan_steps_plan_step"),
        Index("ix_run_plan_steps_plan_position", "run_plan_id", "position"),
        Index("ix_run_plan_steps_plan_status", "run_plan_id", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_plan_id: int = Field(
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    step_id: str = Field(max_length=160)
    title: str = Field(max_length=300)
    purpose: str = Field(default="")
    position: int = Field(nullable=False)
    status: RunPlanStepStatus = Field(sa_column=_enum_column(RunPlanStepStatus))
    depends_on_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    input_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    context_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    action_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    resource_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    policy_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    approval_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    output_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    instructions_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    success_criteria_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    action_payloads_json: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    expected_outputs_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    result_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    claimed_by: str | None = Field(default=None, max_length=120)
    claimed_at: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ApprovalRequest(SQLModel, table=True):
    """Explicit approval gate state for a run plan or one of its steps."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("run_plan_id", "approval_key", name="uq_approval_requests_plan_key"),
        Index("ix_approval_requests_project_status", "project_id", "status"),
        Index("ix_approval_requests_plan", "run_plan_id"),
        Index("ix_approval_requests_step", "run_plan_step_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_plan_id: int = Field(
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_plan_step_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plan_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    approval_key: str = Field(max_length=160)
    title: str = Field(max_length=300)
    description: str = Field(default="")
    required_when: str = Field(default="always", max_length=160)
    approver: str | None = Field(default=None, max_length=200)
    status: ApprovalRequestStatus = Field(sa_column=_enum_column(ApprovalRequestStatus))
    requested_by: str | None = Field(default=None, max_length=120)
    decided_by: str | None = Field(default=None, max_length=120)
    requested_at: datetime = Field(default_factory=_utcnow, nullable=False)
    decided_at: datetime | None = Field(default=None)
    decision_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


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


class AgentRequest(SQLModel, table=True):
    """Generic project inbox item that agents can claim and turn into runs.

    Provider plugins may feed this queue through trusted ingestion or granted
    run-plan steps. The table remains core and provider-agnostic so Telegram,
    IMAP, webhooks, schedules, CI events, and future triggers share one claim
    contract instead of inventing provider-specific queues.
    """

    __tablename__ = "agent_requests"
    __table_args__ = (
        UniqueConstraint("project_id", "request_key", name="uq_agent_requests_project_key"),
        Index("ix_agent_requests_project_status", "project_id", "status"),
        Index("ix_agent_requests_project_attention", "project_id", "attention_status"),
        Index("ix_agent_requests_project_created", "project_id", "created_at"),
        Index("ix_agent_requests_claim", "status", "claim_expires_at"),
        Index("ix_agent_requests_source_record", "source_resource_record_id"),
        Index("ix_agent_requests_run_plan", "run_plan_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    request_key: str = Field(max_length=200)
    title: str = Field(max_length=300)
    body_preview: str = Field(default="", sa_column=Column(Text, nullable=False))
    source_provider: str | None = Field(default=None, max_length=160)
    source_kind: str | None = Field(default=None, max_length=120)
    source_resource_key: str | None = Field(default=None, max_length=160)
    source_resource_record_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("resource_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    source_message_ref: str | None = Field(default=None, max_length=300)
    priority: int = Field(default=0, nullable=False)
    status: AgentRequestStatus = Field(sa_column=_enum_column(AgentRequestStatus))
    attention_status: AgentRequestAttentionStatus = Field(
        sa_column=_enum_column(AgentRequestAttentionStatus)
    )
    claimed_by: str | None = Field(default=None, max_length=120)
    claim_token_hash: str | None = Field(default=None, max_length=128)
    claimed_at: datetime | None = Field(default=None)
    claim_expires_at: datetime | None = Field(default=None)
    run_plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    completed_at: datetime | None = Field(default=None)
    ignored_at: datetime | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


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


class Artifact(SQLModel, table=True):
    """Generic artifact storage reference.

    Artifacts point at files, generated media, screenshots, exports, or other
    blobs produced by tools. The daemon stores references and sanitized
    metadata only; provider auth and secret material stay outside agent reach.
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_project", "project_id"),
        Index("ix_artifacts_resource_record", "resource_record_id"),
        Index("ix_artifacts_plugin", "plugin_id"),
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
    name: str | None = Field(default=None, max_length=300)
    mime_type: str | None = Field(default=None, max_length=160)
    size_bytes: int | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    provenance_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class AuthProvider(SQLModel, table=True):
    """Auth provider metadata derived from plugin provider declarations."""

    __tablename__ = "auth_providers"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_auth_providers_plugin_key"),
        Index("ix_auth_providers_plugin", "plugin_id"),
        Index("ix_auth_providers_key", "key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    auth_type: str = Field(default="none", max_length=80)
    scopes_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Credential(SQLModel, table=True):
    """Opaque credential reference over encrypted integration credential rows."""

    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("credential_ref", name="uq_credentials_ref"),
        UniqueConstraint(
            "integration_credential_id",
            name="uq_credentials_integration_credential",
        ),
        Index("ix_credentials_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    auth_provider_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("auth_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    integration_credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("integration_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    credential_ref: str = Field(max_length=120)
    provider_key: str = Field(max_length=160)
    auth_type: str = Field(default="none", max_length=80)
    auth_method_key: str = Field(default="default", max_length=160)
    profile_key: str = Field(default="default", max_length=160)
    status: str = Field(default="connected", max_length=40)
    expires_at: datetime | None = Field(default=None)
    last_tested_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialScope(SQLModel, table=True):
    """Scope granted to an opaque credential reference."""

    __tablename__ = "credential_scopes"
    __table_args__ = (
        UniqueConstraint("credential_id", "scope", name="uq_credential_scopes_scope"),
        Index("ix_credential_scopes_credential", "credential_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int = Field(
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    scope: str = Field(max_length=200)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialAccount(SQLModel, table=True):
    """Provider account metadata linked to a credential reference."""

    __tablename__ = "credential_accounts"
    __table_args__ = (
        Index("ix_credential_accounts_credential", "credential_id"),
        Index("ix_credential_accounts_provider_account", "provider_account_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int = Field(
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_account_id: str | None = Field(default=None, max_length=300)
    display_name: str | None = Field(default=None, max_length=300)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class OAuthState(SQLModel, table=True):
    """OAuth state nonce for local human setup flows."""

    __tablename__ = "oauth_states"
    __table_args__ = (
        UniqueConstraint("state", name="uq_oauth_states_state"),
        Index("ix_oauth_states_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_key: str = Field(max_length=160)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    integration_credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("integration_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    state: str = Field(max_length=200)
    redirect_uri: str | None = Field(default=None, max_length=2048)
    expires_at: datetime | None = Field(default=None)
    consumed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialUsageEvent(SQLModel, table=True):
    """Redacted audit event for credential use or health probes."""

    __tablename__ = "credential_usage_events"
    __table_args__ = (
        Index("ix_credential_usage_events_credential", "credential_id"),
        Index("ix_credential_usage_events_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: int | None = Field(default=None)
    provider_key: str = Field(max_length=160)
    operation: str = Field(max_length=120)
    status: str = Field(max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialRefreshEvent(SQLModel, table=True):
    """Redacted audit event for credential refresh attempts."""

    __tablename__ = "credential_refresh_events"
    __table_args__ = (
        Index("ix_credential_refresh_events_credential", "credential_id"),
        Index("ix_credential_refresh_events_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: int | None = Field(default=None)
    provider_key: str = Field(max_length=160)
    status: str = Field(max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


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


class WorkspaceBinding(SQLModel, table=True):
    """Daemon-owned mapping from an external repo/workspace to a project.

    Plugin-provided MCP bridges run from arbitrary site repositories. They send
    repo fingerprints and framework hints to the singleton daemon; this table
    is the durable, non-invasive binding back to ``projects``. No required
    ``.env`` / ``.mcp.json`` / repo-local StackOS file is needed.
    """

    __tablename__ = "workspace_bindings"
    __table_args__ = (
        Index("ix_workspace_bindings_project", "project_id"),
        Index("ix_workspace_bindings_git_remote", "git_remote_url"),
        UniqueConstraint("repo_fingerprint", name="uq_workspace_bindings_fingerprint"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    repo_fingerprint: str = Field(max_length=128)
    git_remote_url: str | None = Field(default=None, max_length=500)
    normalized_repo_name: str | None = Field(default=None, max_length=200)
    last_known_root: str | None = Field(default=None, max_length=1000)
    framework: str | None = Field(default=None, max_length=120)
    content_model_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime | None = Field(default=None)


class AgentSession(SQLModel, table=True):
    """Ephemeral-ish record for a plugin MCP bridge connected to the daemon."""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_agent_sessions_project", "project_id"),
        Index("ix_agent_sessions_fingerprint", "repo_fingerprint"),
        Index("ix_agent_sessions_last_seen", "last_seen_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    workspace_binding_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workspace_bindings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    runtime: str = Field(default="unknown", max_length=40)
    cwd: str | None = Field(default=None, max_length=1000)
    repo_fingerprint: str | None = Field(default=None, max_length=128)
    git_remote_url: str | None = Field(default=None, max_length=500)
    thread_id: str | None = Field(default=None, max_length=160)
    client_session_id: str | None = Field(default=None, max_length=160)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Run(SQLModel, table=True):
    """Top-level pipeline audit (PLAN.md L374).

    ``parent_run_id`` enables ``run.children`` / cascade abort; ``heartbeat_at``
    is the daemon-restart-orphan signal.
    """

    __tablename__ = "runs"
    __table_args__ = (
        # Primary look-ups per PLAN.md L472-L474.
        Index("idx_runs_project_started", "project_id", "started_at"),
        Index("idx_runs_parent", "parent_run_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    kind: RunKind = Field(sa_column=_enum_column(RunKind))
    parent_run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    client_session_id: str | None = Field(default=None, max_length=120)
    started_at: datetime = Field(default_factory=_utcnow, nullable=False)
    ended_at: datetime | None = Field(default=None)
    status: RunStatus = Field(sa_column=_enum_column(RunStatus))
    error: str | None = Field(default=None)
    heartbeat_at: datetime | None = Field(default=None)
    last_step: str | None = Field(default=None, max_length=120)
    last_step_at: datetime | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class IntegrationCredential(SQLModel, table=True):
    """Encrypted provider credential profile backing daemon-side execution.

    ``project_id`` is nullable for global credentials. ``encrypted_payload`` +
    ``nonce`` are AES-256-GCM ciphertext; AAD is composed at the repository
    layer (M5).
    """

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "kind",
            "profile_key",
            name="uq_integration_credentials_project_kind_profile",
        ),
        Index("ix_integration_credentials_project", "project_id"),
        Index(
            "ix_integration_credentials_project_kind_profile",
            "project_id",
            "kind",
            "profile_key",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    kind: str = Field(max_length=120)
    profile_key: str = Field(default="default", max_length=160)
    encrypted_payload: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    nonce: bytes = Field(sa_column=Column(LargeBinary(12), nullable=False))
    expires_at: datetime | None = Field(default=None)
    last_refreshed_at: datetime | None = Field(default=None)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class IntegrationBudget(SQLModel, table=True):
    """Pre-emptive cost cap + rate limit (PLAN.md L368)."""

    __tablename__ = "integration_budgets"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "kind",
            name="uq_integration_budgets_project_kind",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: str = Field(max_length=120)
    monthly_budget_usd: float = Field(default=50.0)
    alert_threshold_pct: int = Field(default=80)
    current_month_spend: float = Field(default=0.0)
    current_month_calls: int = Field(default=0)
    qps: float = Field(default=1.0)
    last_reset: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class RunStep(SQLModel, table=True):
    """Per-skill audit grain (PLAN.md L376).

    ``cost_cents`` is the cost-of-truth (PLAN.md L376); ``runs.metadata_json.cost``
    is denormalised for fast UI display.
    """

    __tablename__ = "run_steps"
    __table_args__ = (Index("idx_run_steps_run", "run_id", "step_index"),)

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(
        sa_column=Column(
            ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    step_index: int = Field(nullable=False)
    skill_name: str = Field(max_length=120)
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    status: RunStepStatus = Field(sa_column=_enum_column(RunStepStatus))
    input_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    output_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    cost_cents: int = Field(default=0)
    integration_calls_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class RunStepCall(SQLModel, table=True):
    """Per-MCP-call audit grain inside a skill step (PLAN.md L377)."""

    __tablename__ = "run_step_calls"
    __table_args__ = (Index("idx_run_step_calls_step", "run_step_id"),)

    id: int | None = Field(default=None, primary_key=True)
    run_step_id: int = Field(
        sa_column=Column(
            ForeignKey("run_steps.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    mcp_tool: str = Field(max_length=120)
    request_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    duration_ms: int | None = Field(default=None)
    error: str | None = Field(default=None)
    cost_cents: int = Field(default=0)


class IdempotencyKey(SQLModel, table=True):
    """Mutating-tool dedup (PLAN.md L378).

    UNIQUE ``(project_id, tool_name, idempotency_key)``; replays within the
    24 h window short-circuit to ``response_json``.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "tool_name",
            "idempotency_key",
            name="uq_idempotency",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    tool_name: str = Field(max_length=120)
    idempotency_key: str = Field(max_length=120)
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ScheduledJob(SQLModel, table=True):
    """Per-project schedules (PLAN.md L379)."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_scheduled_jobs_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: str = Field(max_length=120)
    cron_expr: str = Field(max_length=120)
    next_run_at: datetime | None = Field(default=None)
    last_run_at: datetime | None = Field(default=None)
    last_run_status: str | None = Field(default=None, max_length=32)
    enabled: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Public re-exports — used by tests + future repository layer.
# ---------------------------------------------------------------------------


__all__ = [
    "ACTION_CALL_STATUS_TRANSITIONS",
    "AGENT_REQUEST_STATUS_TRANSITIONS",
    "APPROVAL_REQUEST_STATUS_TRANSITIONS",
    "RUN_PLAN_STATUS_TRANSITIONS",
    "RUN_PLAN_STEP_STATUS_TRANSITIONS",
    "RUN_STATUS_TRANSITIONS",
    "TRACKER_ITEM_STATUS_TRANSITIONS",
    "ActionCall",
    "ActionCallStatus",
    "AgentSession",
    "ApprovalRequest",
    "ApprovalRequestStatus",
    "IdempotencyKey",
    "IntegrationBudget",
    "IntegrationCredential",
    "Project",
    "Run",
    "RunKind",
    "RunPlan",
    "RunPlanStatus",
    "RunPlanStep",
    "RunPlanStepStatus",
    "RunStatus",
    "RunStep",
    "RunStepCall",
    "RunStepStatus",
    "ScheduledJob",
    "TaskTracker",
    "TaskTrackerLane",
    "TaskTrackerPriority",
    "TrackerItemStatus",
    "TrackerLinkKind",
    "TrackerRevision",
    "TrackerSourceKind",
    "TrackerTask",
    "TrackerTicket",
    "TrackerTicketDependency",
    "TrackerTicketKind",
    "TrackerTicketLink",
    "TrackerTicketReference",
    "TrackerTombstone",
    "WorkspaceBinding",
]
