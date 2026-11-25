"""Server-held, environment-bound Supabase user sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0038_user_sessions"
down_revision = "0037_schedule_id_nonreuse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("user_sessions"):
        return  # Standalone create_all adoption or interrupted DDL with a lost stamp.
    op.create_table(
        "user_sessions",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("environment_id", sa.String(63), nullable=False),
        sa.Column("server_origin", sa.String(200), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("csrf_digest", sa.String(64), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("recovery", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_sessions")
