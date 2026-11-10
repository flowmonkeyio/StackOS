"""Keep private Accounts and OAuth transactions with their creating user."""

from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision = "0041_account_owners"
down_revision = "0040_local_runtime_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null owners preserve local legacy custody; signup never adopts them.
    # SQLite's rebuild commits before the Alembic version stamp. A retry of
    # that narrow window must not rebuild or duplicate already-added columns.
    inspector = sa.inspect(op.get_bind())
    if "owner_user_id" in {c["name"] for c in inspector.get_columns("credentials")}:
        if {"owner_user_id", "environment_id"} <= {
            c["name"] for c in inspector.get_columns("oauth_states")
        } and "uq_credentials_local_provider_display_name" in {
            index["name"] for index in inspector.get_indexes("credentials")
        }:
            return
        raise RuntimeError(
            "Account ownership migration is incomplete; inspect the selected database"
        )
    with _rebuild_boundary():
        _upgrade()


def _upgrade() -> None:
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("environment_id", sa.String(63), nullable=True))
        batch.drop_constraint("uq_credentials_provider_display_name", type_="unique")
        batch.create_unique_constraint(
            "uq_credentials_provider_display_name",
            ["environment_id", "owner_user_id", "provider_key", "display_name_key"],
        )
        batch.create_index("ix_credentials_owner_user_id", ["owner_user_id"])
        batch.create_index("ix_credentials_environment_id", ["environment_id"])
    predicate = sa.text("owner_user_id IS NULL AND environment_id IS NULL")
    op.create_index(
        "uq_credentials_local_provider_display_name",
        "credentials",
        ["provider_key", "display_name_key"],
        unique=True,
        sqlite_where=predicate,
        postgresql_where=predicate,
    )
    op.add_column("oauth_states", sa.Column("owner_user_id", sa.String(36), nullable=True))
    op.add_column("oauth_states", sa.Column("environment_id", sa.String(63), nullable=True))


def downgrade() -> None:
    for table in ("credentials", "oauth_states"):
        if (
            op.get_bind()
            .execute(
                sa.text(
                    f"SELECT 1 FROM {table} WHERE owner_user_id IS NOT NULL "
                    "OR environment_id IS NOT NULL LIMIT 1"
                )
            )
            .first()
        ):
            raise RuntimeError("Refusing to discard private Account ownership")
    with _rebuild_boundary():
        _downgrade()


def _downgrade() -> None:
    op.drop_index("uq_credentials_local_provider_display_name", table_name="credentials")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_constraint("uq_credentials_provider_display_name", type_="unique")
        batch.create_unique_constraint(
            "uq_credentials_provider_display_name", ["provider_key", "display_name_key"]
        )
        batch.drop_index("ix_credentials_owner_user_id")
        batch.drop_index("ix_credentials_environment_id")
        batch.drop_column("owner_user_id")
        batch.drop_column("environment_id")
    with op.batch_alter_table("oauth_states") as batch:
        batch.drop_column("owner_user_id")
        batch.drop_column("environment_id")


@contextmanager
def _rebuild_boundary():
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        yield
        return
    # Reuse the parent-rebuild approach from 0033: foreign-key actions must
    # not null/cascade child records while the old parent table is replaced.
    with op.get_context().autocommit_block():
        foreign_keys = bind.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            bind.exec_driver_sql("BEGIN IMMEDIATE")
            yield
            if bind.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise RuntimeError("Account ownership migration found invalid foreign keys")
            bind.exec_driver_sql("COMMIT")
        except Exception:
            bind.exec_driver_sql("ROLLBACK")
            raise
        finally:
            bind.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
