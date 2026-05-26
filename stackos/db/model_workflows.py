"""SQLModel table declarations for workflow templates, run plans, and approvals."""

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
    ApprovalRequestStatus,
    RunPlanStatus,
    RunPlanStepStatus,
)


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


__all__ = [
    "ApprovalRequest",
    "ProjectWorkflowTemplate",
    "RunPlan",
    "RunPlanStep",
    "WorkflowTemplate",
    "WorkflowTemplateVersion",
]
