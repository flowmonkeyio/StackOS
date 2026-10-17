"""Migration settings binding without seed-file or database access."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from unittest.mock import Mock

import pytest
from alembic.config import Config

from stackos import config
from stackos.config import Settings
from stackos.db.migrate import alembic_config, migration_seed_path


def test_alembic_config_binds_database_and_explicit_seed(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "explicit-data",
        state_dir=tmp_path / "explicit-state",
        _env_file=None,
    )

    cfg = alembic_config(settings)

    assert cfg.get_main_option("sqlalchemy.url") == f"sqlite:///{settings.db_path}"
    assert cfg.attributes["stackos_seed_path"] == settings.seed_path
    assert isinstance(cfg.attributes["stackos_seed_path"], Path)
    assert not settings.data_dir.exists()
    assert not settings.state_dir.exists()


def test_migration_seed_path_preserves_explicit_path_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback = Mock(side_effect=AssertionError("explicit binding must not consult defaults"))
    monkeypatch.setattr(config, "get_settings", fallback)
    seed_path = tmp_path / "unresolved" / ".." / "explicit-seed.bin"
    cfg = Config()
    cfg.attributes["stackos_seed_path"] = seed_path

    assert migration_seed_path(cfg) is seed_path
    fallback.assert_not_called()
    assert not (tmp_path / "unresolved").exists()


@pytest.mark.parametrize("with_config", [False, True], ids=["no-config", "direct-alembic"])
def test_migration_seed_path_keeps_existing_default_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_config: bool
) -> None:
    fallback_settings = Settings(
        data_dir=tmp_path / "fallback-data",
        state_dir=tmp_path / "fallback-state",
        _env_file=None,
    )
    fallback = Mock(return_value=fallback_settings)
    monkeypatch.setattr(config, "get_settings", fallback)

    assert migration_seed_path(Config() if with_config else None) == fallback_settings.seed_path
    fallback.assert_called_once_with()
    assert not fallback_settings.state_dir.exists()


@pytest.mark.parametrize(
    "invalid",
    [None, "private-path-must-not-be-echoed", b"seed.bin", 1, False, PurePosixPath("seed.bin")],
)
def test_migration_seed_path_rejects_invalid_explicit_binding_without_fallback(
    monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    fallback = Mock(side_effect=AssertionError("invalid explicit binding must not fall back"))
    monkeypatch.setattr(config, "get_settings", fallback)
    cfg = Config()
    cfg.attributes["stackos_seed_path"] = invalid

    with pytest.raises(ValueError, match=r"^migration seed path must be a Path$"):
        migration_seed_path(cfg)

    fallback.assert_not_called()
