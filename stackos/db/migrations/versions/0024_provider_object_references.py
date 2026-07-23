"""Add opaque account-scoped provider object references.

Revision ID: 0024_provider_object_references
Revises: 0023_payload_secrets
Create Date: 2026-07-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_provider_object_references"
down_revision: str | None = "0023_payload_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_object_references",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("provider_account_id", sa.String(length=300), nullable=False),
        sa.Column("object_type", sa.String(length=200), nullable=False),
        sa.Column("provider_object_id", sa.String(length=500), nullable=False),
        sa.Column("safe_ref", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("stale_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("safe_ref", name="uq_provider_object_references_safe_ref"),
        sa.UniqueConstraint(
            "project_id",
            "provider_key",
            "provider_account_id",
            "object_type",
            "provider_object_id",
            name="uq_provider_object_references_identity",
        ),
    )
    op.create_index(
        "ix_provider_object_references_account_type",
        "provider_object_references",
        ["project_id", "provider_key", "provider_account_id", "object_type"],
    )
    op.create_index(
        "ix_provider_object_references_safe_ref",
        "provider_object_references",
        ["safe_ref"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_object_references_safe_ref",
        table_name="provider_object_references",
    )
    op.drop_index(
        "ix_provider_object_references_account_type",
        table_name="provider_object_references",
    )
    op.drop_table("provider_object_references")
