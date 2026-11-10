"""Scoped local computer authorization; never store a local folder on the server."""

import sqlalchemy as sa
from alembic import op

revision = "0040_local_runtime_connections"
down_revision = "0039_project_owners"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_runtime_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("environment_id", sa.String(63), nullable=False),
        sa.Column("server_origin", sa.String(500), nullable=False),
        sa.Column("credential_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM local_runtime_connections LIMIT 1")).first():
        raise RuntimeError("Refusing to discard computer authorizations")
    op.drop_table("local_runtime_connections")
