"""add StackOS auth method and credential profile columns

Revision ID: 0013_stackos_auth_method_profiles
Revises: 0012_stackos_action_calls
Create Date: 2026-05-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_stackos_auth_method_profiles"
down_revision: str | None = "0012_stackos_action_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("integration_credentials") as batch:
        batch.add_column(
            sa.Column(
                "profile_key",
                sa.String(length=160),
                nullable=False,
                server_default="default",
            )
        )
        batch.drop_constraint("uq_integration_credentials_project_kind", type_="unique")
        batch.create_unique_constraint(
            "uq_integration_credentials_project_kind_profile",
            ["project_id", "kind", "profile_key"],
        )
    op.create_index(
        "ix_integration_credentials_project_kind_profile",
        "integration_credentials",
        ["project_id", "kind", "profile_key"],
    )

    with op.batch_alter_table("credentials") as batch:
        batch.add_column(
            sa.Column(
                "auth_method_key",
                sa.String(length=160),
                nullable=False,
                server_default="default",
            )
        )
        batch.add_column(
            sa.Column(
                "profile_key",
                sa.String(length=160),
                nullable=False,
                server_default="default",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("profile_key")
        batch.drop_column("auth_method_key")

    op.drop_index(
        "ix_integration_credentials_project_kind_profile",
        table_name="integration_credentials",
    )
    with op.batch_alter_table("integration_credentials") as batch:
        batch.drop_constraint("uq_integration_credentials_project_kind_profile", type_="unique")
        batch.create_unique_constraint(
            "uq_integration_credentials_project_kind",
            ["project_id", "kind"],
        )
        batch.drop_column("profile_key")
