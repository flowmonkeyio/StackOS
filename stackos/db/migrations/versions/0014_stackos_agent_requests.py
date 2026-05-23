"""add StackOS generic agent request queue

Revision ID: 0014_stackos_agent_requests
Revises: 0013_stackos_auth_method_profiles
Create Date: 2026-05-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_stackos_agent_requests"
down_revision: str | None = "0013_stackos_auth_method_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body_preview", sa.Text(), nullable=False),
        sa.Column("source_provider", sa.String(length=160), nullable=True),
        sa.Column("source_kind", sa.String(length=120), nullable=True),
        sa.Column("source_resource_key", sa.String(length=160), nullable=True),
        sa.Column("source_resource_record_id", sa.Integer(), nullable=True),
        sa.Column("source_message_ref", sa.String(length=300), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "claimed",
                "run-created",
                "run-started",
                "responded",
                "resolved",
                "ignored",
                "failed",
                name="ck_agentrequeststatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column(
            "attention_status",
            sa.Enum(
                "unread",
                "read",
                "archived",
                name="ck_agentrequestattentionstatus",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(length=120), nullable=True),
        sa.Column("claim_token_hash", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=True),
        sa.Column("run_plan_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("ignored_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_resource_record_id"],
            ["resource_records.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["run_plan_id"], ["run_plans.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "request_key", name="uq_agent_requests_project_key"),
    )
    op.create_index(
        "ix_agent_requests_project_status",
        "agent_requests",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_agent_requests_project_attention",
        "agent_requests",
        ["project_id", "attention_status"],
    )
    op.create_index(
        "ix_agent_requests_project_created",
        "agent_requests",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_agent_requests_claim",
        "agent_requests",
        ["status", "claim_expires_at"],
    )
    op.create_index(
        "ix_agent_requests_source_record",
        "agent_requests",
        ["source_resource_record_id"],
    )
    op.create_index(
        "ix_agent_requests_run_plan",
        "agent_requests",
        ["run_plan_id"],
    )


def downgrade() -> None:
    # Downgrade removes only the table this revision owns.
    op.drop_index("ix_agent_requests_run_plan", table_name="agent_requests")
    op.drop_index("ix_agent_requests_source_record", table_name="agent_requests")
    op.drop_index("ix_agent_requests_claim", table_name="agent_requests")
    op.drop_index("ix_agent_requests_project_created", table_name="agent_requests")
    op.drop_index("ix_agent_requests_project_attention", table_name="agent_requests")
    op.drop_index("ix_agent_requests_project_status", table_name="agent_requests")
    op.drop_table("agent_requests")
