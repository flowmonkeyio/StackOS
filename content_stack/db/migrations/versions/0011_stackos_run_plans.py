"""add StackOS run plan sidecar tables

Revision ID: 0011_stackos_run_plans
Revises: 0010_stackos_workflow_templates
Create Date: 2026-05-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_stackos_run_plans"
down_revision: str | None = "0010_stackos_workflow_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("template_version_id", sa.Integer(), nullable=True),
        sa.Column("context_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "started",
                "completed",
                "failed",
                "aborted",
                name="ck_runplanstatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("template_key", sa.String(length=160), nullable=True),
        sa.Column("template_version", sa.String(length=40), nullable=True),
        sa.Column("template_source", sa.String(length=40), nullable=True),
        sa.Column("template_origin_path", sa.String(length=1000), nullable=True),
        sa.Column("template_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("selected_context_json", sa.JSON(), nullable=True),
        sa.Column("context_filters_json", sa.JSON(), nullable=True),
        sa.Column("grant_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("budget_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("output_contract_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["workflow_templates.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["workflow_template_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["context_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("run_id", name="uq_run_plans_run"),
    )
    op.create_index(
        "ix_run_plans_project_status",
        "run_plans",
        ["project_id", "status"],
    )
    op.create_index("ix_run_plans_run", "run_plans", ["run_id"])
    op.create_index(
        "ix_run_plans_template",
        "run_plans",
        ["template_id", "template_version_id"],
    )
    op.create_index(
        "ix_run_plans_context_snapshot",
        "run_plans",
        ["context_snapshot_id"],
    )

    op.create_table(
        "run_plan_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_plan_id", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "success",
                "failed",
                "skipped",
                "blocked",
                name="ck_runplanstepstatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("depends_on_json", sa.JSON(), nullable=False),
        sa.Column("input_refs_json", sa.JSON(), nullable=False),
        sa.Column("context_refs_json", sa.JSON(), nullable=False),
        sa.Column("action_refs_json", sa.JSON(), nullable=False),
        sa.Column("resource_refs_json", sa.JSON(), nullable=False),
        sa.Column("policy_refs_json", sa.JSON(), nullable=False),
        sa.Column("approval_refs_json", sa.JSON(), nullable=False),
        sa.Column("output_refs_json", sa.JSON(), nullable=False),
        sa.Column("instructions_json", sa.JSON(), nullable=False),
        sa.Column("success_criteria_json", sa.JSON(), nullable=False),
        sa.Column("action_payloads_json", sa.JSON(), nullable=True),
        sa.Column("expected_outputs_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("claimed_by", sa.String(length=120), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_plan_id"], ["run_plans.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "run_plan_id",
            "step_id",
            name="uq_run_plan_steps_plan_step",
        ),
    )
    op.create_index(
        "ix_run_plan_steps_plan_position",
        "run_plan_steps",
        ["run_plan_id", "position"],
    )
    op.create_index(
        "ix_run_plan_steps_plan_status",
        "run_plan_steps",
        ["run_plan_id", "status"],
    )

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_plan_id", sa.Integer(), nullable=False),
        sa.Column("run_plan_step_id", sa.Integer(), nullable=True),
        sa.Column("approval_key", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("required_when", sa.String(length=160), nullable=False, server_default="always"),
        sa.Column("approver", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "cancelled",
                name="ck_approvalrequeststatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(length=120), nullable=True),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_plan_id"], ["run_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_plan_step_id"],
            ["run_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "run_plan_id",
            "approval_key",
            name="uq_approval_requests_plan_key",
        ),
    )
    op.create_index(
        "ix_approval_requests_project_status",
        "approval_requests",
        ["project_id", "status"],
    )
    op.create_index("ix_approval_requests_plan", "approval_requests", ["run_plan_id"])
    op.create_index("ix_approval_requests_step", "approval_requests", ["run_plan_step_id"])


def downgrade() -> None:
    # Clean-cut pivot rule: this downgrade removes only D07-owned sidecar
    # objects. It does not drop or rewrite legacy SEO/procedure/run tables.
    op.drop_index("ix_approval_requests_step", table_name="approval_requests")
    op.drop_index("ix_approval_requests_plan", table_name="approval_requests")
    op.drop_index(
        "ix_approval_requests_project_status",
        table_name="approval_requests",
    )
    op.drop_table("approval_requests")
    op.drop_index(
        "ix_run_plan_steps_plan_status",
        table_name="run_plan_steps",
    )
    op.drop_index(
        "ix_run_plan_steps_plan_position",
        table_name="run_plan_steps",
    )
    op.drop_table("run_plan_steps")
    op.drop_index(
        "ix_run_plans_context_snapshot",
        table_name="run_plans",
    )
    op.drop_index("ix_run_plans_template", table_name="run_plans")
    op.drop_index("ix_run_plans_run", table_name="run_plans")
    op.drop_index("ix_run_plans_project_status", table_name="run_plans")
    op.drop_table("run_plans")
