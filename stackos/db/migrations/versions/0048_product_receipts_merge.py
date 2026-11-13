"""Join accepted product metrics, Patterns and setup without rewriting history."""

from alembic import op

revision = "0048_product_receipts_merge"
down_revision = ("0046_receipt_trend_bounds", "0047_patterns_setup_merge")
branch_labels = None
depends_on = None


def upgrade() -> None:
    from stackos.repositories.tracker.receipt_projection import initialize_receipt_projection

    # Existing metrics-head installations also need the cancellation-aware inputs
    # and classification. Raw receipts remain authoritative and unchanged.
    initialize_receipt_projection(op.get_bind())


def downgrade() -> None:
    pass
