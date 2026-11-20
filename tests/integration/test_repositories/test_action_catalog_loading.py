"""Current catalog relationships and project visibility survive batched loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlmodel import Session

from stackos.db.connection import make_engine
from stackos.db.models import Action, Plugin, PluginSource, Provider
from stackos.repositories.base import NotFoundError
from stackos.repositories.plugins import PluginRepository
from tests.helpers.sqlite_schema import builtin_catalog_snapshot, restore_database_snapshot


def test_action_catalog_loads_shared_and_absent_providers_with_fresh_metadata(
    session: Session, project_id: int
) -> None:
    plugin = Plugin(
        slug="test-catalog",
        name="Catalog test",
        source=PluginSource.PROJECT,
        manifest_json={},
    )
    session.add(plugin)
    session.flush()
    provider = Provider(plugin_id=plugin.id, key="catalog-provider", name="Catalog provider")
    session.add(provider)
    session.flush()
    actions = [
        Action(
            plugin_id=plugin.id,
            provider_id=provider.id if key != "gamma" else None,
            key=key,
            name=key.title(),
            description=f"Description of {key}",
            risk_level="read",
            input_schema_json={"type": "object", "additionalProperties": False},
            output_schema_json={"type": "object"},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "http",
                "operation": "request",
                "requires_credential": False,
            },
        )
        for key in ("gamma", "beta", "alpha")
    ]
    session.add_all(actions)
    session.commit()
    repo = PluginRepository(session)
    repo.enable(project_id=project_id, plugin_slug="test-catalog")

    listed = repo.list_actions(plugin_slug=plugin.slug, project_id=project_id)

    assert [action.action_ref for action in listed] == [
        "test-catalog.alpha",
        "test-catalog.beta",
        "test-catalog.gamma",
    ]
    assert [action.provider_key for action in listed] == [
        "catalog-provider",
        "catalog-provider",
        None,
    ]
    assert [action.provider_id for action in listed] == [provider.id, provider.id, None]
    assert all(action.availability.status == "ready" for action in listed)
    assert listed[0].input_schema_json == {"type": "object", "additionalProperties": False}
    assert repo.list_actions(plugin_slug="missing-catalog") == []

    provider.key = "renamed-provider"
    provider.config_json = {"enabled": False}
    actions[-1].description = "Current description"
    session.add(provider)
    session.add(actions[-1])
    session.commit()
    session.expire_all()

    current = repo.list_actions(plugin_slug="test-catalog", project_id=project_id)

    assert [action.provider_key for action in current] == [
        "renamed-provider",
        "renamed-provider",
        None,
    ]
    assert [action.availability.status for action in current] == [
        "provider_disabled",
        "provider_disabled",
        "ready",
    ]
    assert current[0].description == "Current description"
    assert current[0].availability.executable is False

    session.delete(provider)
    session.commit()
    session.expire_all()
    without_provider = repo.list_actions(plugin_slug="test-catalog", project_id=project_id)
    assert len(without_provider) == 3
    assert all(action.provider_id is None for action in without_provider)
    assert all(action.provider_key is None for action in without_provider)

    repo.disable(project_id=project_id, plugin_slug="test-catalog")
    assert repo.list_actions(plugin_slug="test-catalog", project_id=project_id) == []
    assert len(repo.list_actions(plugin_slug="test-catalog")) == 3
    with pytest.raises(NotFoundError):
        repo.list_actions(project_id=project_id + 100_000)


def test_action_catalog_omits_parent_deleted_between_reads(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    engine = make_engine(path)
    writer_engine = make_engine(path)
    deleted = False

    def delete_parent_before_load(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal deleted
        if deleted or not statement.lstrip().startswith("SELECT plugins."):
            return
        deleted = True
        # A separate committed writer can run between SQLite legacy-mode SELECTs.
        # Keep foreign keys enabled and delete through the real repository tables.
        with Session(writer_engine) as writer:
            assert writer.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            action = writer.get(Action, action_id)
            plugin = writer.get(Plugin, plugin_id)
            assert action is not None and plugin is not None
            writer.delete(action)
            writer.flush()
            writer.delete(plugin)
            writer.commit()

    try:
        restore_database_snapshot(engine, builtin_catalog_snapshot())
        with Session(engine) as setup:
            plugin = Plugin(
                slug="test-deleted-catalog",
                name="Deleted catalog",
                source=PluginSource.PROJECT,
            )
            setup.add(plugin)
            setup.flush()
            action = Action(plugin_id=plugin.id, key="example", name="Example")
            setup.add(action)
            setup.flush()
            plugin_id, action_id = plugin.id, action.id
            setup.commit()

        event.listen(engine, "before_cursor_execute", delete_parent_before_load)
        try:
            with Session(engine) as reader:
                assert (
                    PluginRepository(reader).list_actions(plugin_slug="test-deleted-catalog") == []
                )
        finally:
            event.remove(engine, "before_cursor_execute", delete_parent_before_load)

        assert deleted
        with Session(writer_engine) as writer:
            assert writer.get(Plugin, plugin_id) is None
            assert writer.get(Action, action_id) is None
            assert writer.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
        writer_engine.dispose()
