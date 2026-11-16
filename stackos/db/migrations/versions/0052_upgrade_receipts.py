"""Index retained upgrade invocations without changing ticket attempts or raw receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0052_upgrade_receipts"
down_revision = "0051_upgrade_postflight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from stackos.repositories.tracker.receipt_projection import initialize_receipt_projection

    initialize_receipt_projection(op.get_bind())


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM tracker_receipt_projections WHERE observation_key != '' LIMIT 1")
        )
        .first()
    ):
        raise RuntimeError(
            "Retained invocation observations require the current receipt projection"
        )
    with op.batch_alter_table("tracker_receipt_projections", recreate="always") as batch:
        batch.drop_column("observation_key")
