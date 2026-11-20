"""Additive migration and lost-stamp replay preserve installation fence evidence."""

import sqlite3

import pytest
from alembic import command

from stackos.config import Settings
from stackos.db.migrate import alembic_config, upgrade_to_head


def test_upgrade_migration_preserves_prior_schema_and_checkpoint_refs(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    config = alembic_config(settings)
    settings.ensure_dirs()
    command.upgrade(config, "0049_project_task_capacity")
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute(
            "INSERT INTO managed_upgrades (upgrade_id,active,source_identity,target_identity,"
            "revision,status,checkpoint_refs_json,created_at,updated_at) VALUES "
            "('fixture',1,'source','target',2,'quiesced','[]',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    command.stamp(config, "0049_project_task_capacity")
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute(
            "SELECT upgrade_id,active,source_identity,target_identity,revision,status "
            "FROM managed_upgrades"
        ).fetchall() == [("fixture", 1, "source", "target", 2, "quiesced")]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO managed_upgrades SELECT 'other',active,"
                "source_identity,target_identity,"
                "revision,status,checkpoint_refs_json,created_at,updated_at,postflight_json "
                "FROM managed_upgrades"
            )
    with pytest.raises(RuntimeError, match="evidence must be retained"):
        command.downgrade(config, "0049_project_task_capacity")
