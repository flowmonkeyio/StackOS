"""Retained PostgreSQL folder references use portable schema reflection."""

import importlib
from types import SimpleNamespace

import pytest
import sqlalchemy as sa


@pytest.mark.parametrize("ondelete", ["SET NULL", "CASCADE", None])
def test_postgres_folder_recovery_checks_reference_without_sqlite_pragma(monkeypatch, ondelete):
    migration = importlib.import_module(
        "stackos.db.migrations.versions.0038_project_primary_workspace"
    )
    # This PostgreSQL-shaped connection deliberately has no SQLite SQL method.
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    inspector = SimpleNamespace(
        get_columns=lambda table: [
            {"name": "primary_workspace_binding_id", "type": sa.Integer(), "nullable": True}
        ],
        get_foreign_keys=lambda table: [
            {
                "constrained_columns": ["primary_workspace_binding_id"],
                "referred_table": "workspace_bindings",
                "referred_columns": ["id"],
                "options": {"ondelete": ondelete},
            }
        ],
    )
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda connection: inspector)
    if ondelete == "SET NULL":
        migration.upgrade()
    else:
        with pytest.raises(RuntimeError, match="incompatible schema"):
            migration.upgrade()
