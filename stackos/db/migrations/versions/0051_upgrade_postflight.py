"""Retain the validated target postflight, including upgrades without tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0051_upgrade_postflight"
down_revision = "0050_managed_upgrades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "postflight_json" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("managed_upgrades")
    }:
        op.add_column("managed_upgrades", sa.Column("postflight_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM managed_upgrades WHERE postflight_json IS NOT NULL "
                "AND postflight_json != 'null' LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError(
            "Managed-upgrade evidence must be retained; refusing destructive downgrade"
        )
    op.drop_column("managed_upgrades", "postflight_json")
