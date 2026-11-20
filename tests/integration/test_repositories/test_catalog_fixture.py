"""Consumer setup reuses catalog rows; catalog tests retain a true empty start."""

import pytest
from sqlmodel import Session, select

from stackos.db.models import ActionCall, Credential, Plugin, Project
from stackos.repositories import plugins as plugin_repository
from stackos.repositories.plugins import PluginRepository


def test_consumer_fixture_has_only_initialized_catalog_state(session: Session) -> None:
    assert session.exec(select(Plugin)).first() is not None
    assert session.exec(select(Project)).all() == []
    assert session.exec(select(Credential)).all() == []
    assert session.exec(select(ActionCall)).all() == []
    assert PluginRepository._builtin_sync_generations[session.get_bind()] == (
        plugin_repository.get_builtin_plugin_manifest_snapshot().generation
    )


@pytest.mark.fresh_catalog
def test_fresh_catalog_marker_runs_real_bootstrap(session: Session) -> None:
    assert session.exec(select(Plugin)).all() == []
    assert session.get_bind() not in PluginRepository._builtin_sync_generations
    PluginRepository(session).sync_builtin_plugins()
    assert session.exec(select(Plugin)).first() is not None
