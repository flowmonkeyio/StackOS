"""add workspace bindings and agent sessions

Revision ID: 0004_workspace_bindings
Revises: 0002_initial_schema
Create Date: 2026-05-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_workspace_bindings"
down_revision: str | None = "0002_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("repo_fingerprint", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("git_remote_url", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("normalized_repo_name", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("last_known_root", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column("framework", sqlmodel.sql.sqltypes.AutoString(length=120), nullable=True),
        sa.Column("content_model_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_fingerprint", name="uq_workspace_bindings_fingerprint"),
    )
    op.create_index("ix_workspace_bindings_project", "workspace_bindings", ["project_id"])
    op.create_index("ix_workspace_bindings_git_remote", "workspace_bindings", ["git_remote_url"])

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("workspace_binding_id", sa.Integer(), nullable=True),
        sa.Column("runtime", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("cwd", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column("repo_fingerprint", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("git_remote_url", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("thread_id", sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
        sa.Column("client_session_id", sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_binding_id"], ["workspace_bindings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_sessions_project", "agent_sessions", ["project_id"])
    op.create_index("ix_agent_sessions_fingerprint", "agent_sessions", ["repo_fingerprint"])
    op.create_index("ix_agent_sessions_last_seen", "agent_sessions", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_sessions_last_seen", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_fingerprint", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_project", table_name="agent_sessions")
    op.drop_table("agent_sessions")
    op.drop_index("ix_workspace_bindings_git_remote", table_name="workspace_bindings")
    op.drop_index("ix_workspace_bindings_project", table_name="workspace_bindings")
    op.drop_table("workspace_bindings")
