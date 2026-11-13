"""Join accepted Patterns and project setup histories without rewriting either.

Revision ID: 0047_patterns_setup_merge
Revises: 0046_pattern_key_index, 0044_project_setup_receipts
"""

revision = "0047_patterns_setup_merge"
down_revision = ("0046_pattern_key_index", "0044_project_setup_receipts")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
