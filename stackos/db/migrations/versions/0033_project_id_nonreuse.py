"""Keep future project IDs from being reused after deletion.

Revision ID: 0033_project_id_nonreuse
Revises: 0032_shared_telegram_application

Existing numeric selections must be confirmed against current project metadata:
SQLite cannot reconstruct IDs deleted before this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_project_id_nonreuse"
down_revision = "0032_shared_telegram_application"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # A retry after the rebuild committed but before Alembic stamped the version
    # must retain the sequence, including IDs already deleted since the rebuild.
    ddl = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'")
    ).scalar_one()
    if "AUTOINCREMENT" in ddl.upper():
        return

    # Rebuilding a referenced parent with FK enforcement enabled cascades or
    # nulls child records. Disable it outside the transaction, then rebuild
    # atomically and check all references before committing. No child is rewritten.
    with op.get_context().autocommit_block():
        foreign_keys = bind.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
            with op.batch_alter_table(
                "projects", recreate="always", table_kwargs={"sqlite_autoincrement": True}
            ):
                pass
            if bind.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise RuntimeError("Project ID migration found invalid foreign keys")
            bind.exec_driver_sql("COMMIT")
        except Exception:
            bind.exec_driver_sql("ROLLBACK")
            raise
        finally:
            bind.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")


def downgrade() -> None:
    # Retain the stronger allocation invariant even if the runtime is rolled
    # back. Resetting it could redirect saved references to replacement projects.
    pass
