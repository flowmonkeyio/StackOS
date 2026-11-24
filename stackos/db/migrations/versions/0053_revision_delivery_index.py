"""Cover delivery transition reads without loading retained revision snapshots."""

from alembic import op
from sqlalchemy.schema import CreateIndex

revision = "0053_revision_delivery_index"
down_revision = "0052_upgrade_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from stackos.db.model_tracker import REVISION_DELIVERY_STATS_INDEX

    op.get_bind().execute(CreateIndex(REVISION_DELIVERY_STATS_INDEX, if_not_exists=True))


def downgrade() -> None:
    op.drop_index("ix_tracker_revisions_delivery_stats", table_name="tracker_revisions")
