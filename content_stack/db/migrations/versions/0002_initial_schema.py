"""initial StackOS core schema

Revision ID: 0002_initial_schema
Revises: 0001_initial_empty
Create Date: 2026-05-06

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_initial_schema"
down_revision: str | None = "0001_initial_empty"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the clean StackOS core tables."""
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("niche", sa.String(length=200), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("schedule_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=True)

    op.create_table(
        "integration_budgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=120), nullable=False),
        sa.Column("monthly_budget_usd", sa.Float(), nullable=False),
        sa.Column("alert_threshold_pct", sa.Integer(), nullable=False),
        sa.Column("current_month_spend", sa.Float(), nullable=False),
        sa.Column("current_month_calls", sa.Integer(), nullable=False),
        sa.Column("qps", sa.Float(), nullable=False),
        sa.Column("last_reset", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "kind", name="uq_integration_budgets_project_kind"),
    )

    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=120), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "kind",
            name="uq_integration_credentials_project_kind",
        ),
    )
    op.create_index(
        "ix_integration_credentials_project",
        "integration_credentials",
        ["project_id"],
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "run-plan",
                "skill-run",
                "action",
                "scheduled-job",
                "maintenance",
                name="ck_runkind",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("parent_run_id", sa.Integer(), nullable=True),
        sa.Column("client_session_id", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "success",
                "failed",
                "aborted",
                name="ck_runstatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("last_step", sa.String(length=120), nullable=True),
        sa.Column("last_step_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["parent_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_runs_parent", "runs", ["parent_run_id"])
    op.create_index("idx_runs_project_started", "runs", ["project_id", "started_at"])
    op.execute(
        "CREATE INDEX idx_runs_running_heartbeat "
        "ON runs(status, heartbeat_at) WHERE status = 'running'"
    )

    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=120), nullable=False),
        sa.Column("cron_expr", sa.String(length=120), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_status", sa.String(length=32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_jobs_project", "scheduled_jobs", ["project_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "tool_name", "idempotency_key", name="uq_idempotency"),
    )

    op.create_table(
        "run_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("skill_name", sa.String(length=120), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "success",
                "failed",
                "skipped",
                name="ck_runstepstatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("input_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("output_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("integration_calls_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_run_steps_run", "run_steps", ["run_id", "step_index"])

    op.create_table(
        "run_step_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_step_id", sa.Integer(), nullable=False),
        sa.Column("mcp_tool", sa.String(length=120), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_step_id"], ["run_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_run_step_calls_step", "run_step_calls", ["run_step_id"])


def downgrade() -> None:
    """Drop the clean StackOS core tables in reverse FK order."""
    op.drop_index("idx_run_step_calls_step", table_name="run_step_calls")
    op.drop_table("run_step_calls")
    op.drop_index("idx_run_steps_run", table_name="run_steps")
    op.drop_table("run_steps")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_scheduled_jobs_project", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")
    op.execute("DROP INDEX IF EXISTS idx_runs_running_heartbeat")
    op.drop_index("idx_runs_project_started", table_name="runs")
    op.drop_index("idx_runs_parent", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_integration_credentials_project", table_name="integration_credentials")
    op.drop_table("integration_credentials")
    op.drop_table("integration_budgets")
    op.drop_index("ix_projects_slug", table_name="projects")
    op.drop_table("projects")
