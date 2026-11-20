"""Durable installation fence; per-task evidence stays with the tracker."""

import sqlalchemy as sa
from alembic import op

revision = "0050_managed_upgrades"
down_revision = "0049_project_task_capacity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("managed_upgrades"):
        # A lost stamp may be replayed after the additive postflight migration.
        columns = {item["name"] for item in inspector.get_columns("managed_upgrades")}
        if columns - {"postflight_json"} != {
            "upgrade_id",
            "active",
            "source_identity",
            "target_identity",
            "revision",
            "status",
            "checkpoint_refs_json",
            "created_at",
            "updated_at",
        }:
            raise RuntimeError("Existing managed_upgrades schema is incompatible")
        return  # Current metadata adoption or completed DDL with a lost stamp.
    op.create_table(
        "managed_upgrades",
        sa.Column("upgrade_id", sa.String(160), primary_key=True),
        sa.Column("active", sa.Integer(), unique=True),
        sa.Column("source_identity", sa.String(240), nullable=False),
        sa.Column("target_identity", sa.String(240), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("checkpoint_refs_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("active IS NULL OR active = 1", name="ck_managed_upgrade_active"),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM managed_upgrades LIMIT 1")).first():
        raise RuntimeError(
            "Managed-upgrade evidence must be retained; refusing destructive downgrade"
        )
    op.drop_table("managed_upgrades")
