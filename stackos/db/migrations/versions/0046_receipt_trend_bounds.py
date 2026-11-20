"""Bound finish-dated review and overlapping work-time reads at the receipt owner."""

from alembic import op

revision = "0046_receipt_trend_bounds"
down_revision = "0045_tracker_stream_cursor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from stackos.repositories.tracker.receipt_projection import initialize_receipt_projection

    initialize_receipt_projection(op.get_bind())


def downgrade() -> None:
    for name in ("ix_tracker_receipts_review_finish", "ix_tracker_receipts_finish"):
        op.drop_index(name, table_name="tracker_receipt_projections")
    with op.batch_alter_table("tracker_receipt_projections") as batch:
        for name in ("review_outcome", "closed", "finished_at"):
            batch.drop_column(name)
