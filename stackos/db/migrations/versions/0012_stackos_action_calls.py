"""add StackOS action-call audit sidecar

Revision ID: 0012_stackos_action_calls
Revises: 0011_stackos_run_plans
Create Date: 2026-05-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_stackos_action_calls"
down_revision: str | None = "0011_stackos_run_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("run_plan_id", sa.Integer(), nullable=True),
        sa.Column("run_plan_step_id", sa.Integer(), nullable=True),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("action_key", sa.String(length=160), nullable=False),
        sa.Column("plugin_slug", sa.String(length=120), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=True),
        sa.Column("connector_key", sa.String(length=160), nullable=True),
        sa.Column("operation", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "dry-run",
                "success",
                "failed",
                name="ck_actioncallstatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("credential_ref", sa.String(length=120), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_plan_id"], ["run_plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["run_plan_step_id"],
            ["run_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["action_id"], ["actions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_action_calls_project_created",
        "action_calls",
        ["project_id", "created_at"],
    )
    op.create_index("ix_action_calls_run", "action_calls", ["run_id"])
    op.create_index(
        "ix_action_calls_run_plan_step",
        "action_calls",
        ["run_plan_step_id"],
    )
    op.create_index("ix_action_calls_action", "action_calls", ["action_id"])
    op.create_index(
        "ix_action_calls_project_idempotency",
        "action_calls",
        ["project_id", "idempotency_key"],
    )


def downgrade() -> None:
    # Downgrade removes only the tables this revision owns.
    op.drop_index("ix_action_calls_project_idempotency", table_name="action_calls")
    op.drop_index("ix_action_calls_action", table_name="action_calls")
    op.drop_index("ix_action_calls_run_plan_step", table_name="action_calls")
    op.drop_index("ix_action_calls_run", table_name="action_calls")
    op.drop_index("ix_action_calls_project_created", table_name="action_calls")
    op.drop_table("action_calls")
