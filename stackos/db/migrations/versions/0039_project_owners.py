"""Persist private project ownership without adopting existing projects."""

import sqlalchemy as sa
from alembic import op

revision = "0039_project_owners"
down_revision = "0038_user_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "environment_id" not in columns:
        op.add_column("projects", sa.Column("environment_id", sa.String(63), nullable=True))
    if "owner_user_id" not in columns:
        op.add_column("projects", sa.Column("owner_user_id", sa.String(36), nullable=True))
    op.create_index(
        "ix_projects_environment_id", "projects", ["environment_id"], if_not_exists=True
    )
    op.create_index("ix_projects_owner_user_id", "projects", ["owner_user_id"], if_not_exists=True)


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM projects WHERE owner_user_id IS NOT NULL "
                "OR environment_id IS NOT NULL LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError("Refusing to discard private project ownership")
    op.drop_index("ix_projects_owner_user_id", table_name="projects")
    op.drop_index("ix_projects_environment_id", table_name="projects")
    op.drop_column("projects", "owner_user_id")
    op.drop_column("projects", "environment_id")
