"""Join online ownership and desktop project/UI histories without rewriting either.

Revision ID: 0042_server_main_consolidation
Revises: 0041_account_owners, 0041_ui804_consolidation
"""

revision = "0042_server_main_consolidation"
down_revision = ("0041_account_owners", "0041_ui804_consolidation")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
