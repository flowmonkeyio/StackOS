"""Add project-scoped encrypted action payload values.

Revision ID: 0023_payload_secrets
Revises: 0022_artifact_lifecycle
Create Date: 2026-07-18

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_payload_secrets"
down_revision: str | None = "0022_artifact_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payload_secrets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("secret_ref", sa.String(length=120), nullable=False),
        sa.Column("value_type", sa.String(length=40), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secret_ref", name="uq_payload_secrets_ref"),
    )
    op.create_index("ix_payload_secrets_project", "payload_secrets", ["project_id"])
    op.create_index(
        "ix_payload_secrets_project_ref",
        "payload_secrets",
        ["project_id", "secret_ref"],
    )


def downgrade() -> None:
    op.drop_index("ix_payload_secrets_project_ref", table_name="payload_secrets")
    op.drop_index("ix_payload_secrets_project", table_name="payload_secrets")
    op.drop_table("payload_secrets")
