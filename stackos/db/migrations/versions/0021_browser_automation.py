"""Add browser automation state.

Revision ID: 0021_browser_automation
Revises: 0020_execution_contexts
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0021_browser_automation"
down_revision: str | None = "0020_execution_contexts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "browser_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("profile_key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("profile_ref", sa.String(length=220), nullable=False),
        sa.Column("allowed_origins_json", sa.JSON(), nullable=True),
        sa.Column("launch_options_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "profile_key", name="uq_browser_profiles_project_key"),
    )
    op.create_index(
        "ix_browser_profiles_project_status",
        "browser_profiles",
        ["project_id", "status"],
    )

    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("session_ref", sa.String(length=220), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("headless", sa.Boolean(), nullable=False),
        sa.Column("page_refs_json", sa.JSON(), nullable=True),
        sa.Column("current_url", sa.String(length=2048), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["browser_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "session_ref", name="uq_browser_sessions_project_ref"),
    )
    op.create_index(
        "ix_browser_sessions_profile",
        "browser_sessions",
        ["profile_id"],
    )
    op.create_index(
        "ix_browser_sessions_project_status",
        "browser_sessions",
        ["project_id", "status"],
    )

    op.create_table(
        "browser_action_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("artifact_id", sa.Integer(), nullable=True),
        sa.Column("session_ref", sa.String(length=220), nullable=True),
        sa.Column("page_ref", sa.String(length=220), nullable=True),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=120), nullable=False),
        sa.Column("side_effect_class", sa.String(length=80), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=True),
        sa.Column("target_origin", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_summary_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["profile_id"], ["browser_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["browser_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_browser_action_receipts_artifact",
        "browser_action_receipts",
        ["artifact_id"],
    )
    op.create_index(
        "ix_browser_action_receipts_profile",
        "browser_action_receipts",
        ["profile_id"],
    )
    op.create_index(
        "ix_browser_action_receipts_project",
        "browser_action_receipts",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_browser_action_receipts_session",
        "browser_action_receipts",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_browser_action_receipts_session", table_name="browser_action_receipts")
    op.drop_index("ix_browser_action_receipts_project", table_name="browser_action_receipts")
    op.drop_index("ix_browser_action_receipts_profile", table_name="browser_action_receipts")
    op.drop_index("ix_browser_action_receipts_artifact", table_name="browser_action_receipts")
    op.drop_table("browser_action_receipts")
    op.drop_index("ix_browser_sessions_project_status", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_profile", table_name="browser_sessions")
    op.drop_table("browser_sessions")
    op.drop_index("ix_browser_profiles_project_status", table_name="browser_profiles")
    op.drop_table("browser_profiles")
