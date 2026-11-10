"""Selected local environments must never consume installed or mixed targets."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from stackos.config import Settings, get_settings, get_state_dir

SCRIPT = Path(__file__).parents[2] / "scripts/prepare-local-supabase.py"
spec = importlib.util.spec_from_file_location("prepare_local_supabase", SCRIPT)
assert spec and spec.loader
prepare_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_module)


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Only Docker discovery and free-port probes are substituted. The actual
    # script, secret generation, CLI config and dotenv consumers run unchanged.
    monkeypatch.setattr(prepare_module.subprocess, "run", Mock(return_value=Mock(stdout="")))
    monkeypatch.setattr(prepare_module.socket, "socket", MagicMock())
    root = tmp_path / "alpha"
    prepare_module.prepare(
        root,
        "alpha",
        {
            "server": 18510,
            "runtime": 18511,
            "api": 18521,
            "db": 18522,
            "shadow": 18520,
            "mail": 18524,
        },
    )
    return root


def _settings(root: Path, role: str = "server", **changes: object) -> Settings:
    values = {
        key.removeprefix("STACKOS_").lower(): value
        for key, value in dotenv_values(root / f"{role}.env").items()
    }
    return Settings(_env_file=None, **(values | changes))


def test_server_and_runtime_share_only_explicit_target(prepared: Path) -> None:
    server, runtime = _settings(prepared), _settings(prepared, "runtime")
    assert server.environment_id == runtime.environment_id == "alpha"
    assert server.server_url == runtime.server_url == "http://127.0.0.1:18510"
    assert server.data_dir != runtime.data_dir
    assert server.state_dir != runtime.state_dir
    assert runtime.database_url is runtime.supabase_service_role_key is None
    assert server.database_url and "postgres:postgres" not in repr(server)
    for selected in (server, runtime):
        with pytest.raises(ValueError, match=r"cannot use.*SQLite"):
            _ = selected.db_path
    assert (prepared / "server.env").stat().st_mode & 0o777 == 0o600
    assert (prepared / "supabase/config.toml").stat().st_mode & 0o777 == 0o600
    signing_path = prepared / "supabase/signing_keys.json"
    assert signing_path.stat().st_mode & 0o777 == 0o600
    first = json.loads(signing_path.read_text())[0]
    second = json.loads(prepare_module._signing_key())[0]
    assert first["kid"] != second["kid"] and first["x"] != second["x"]
    assert prepared.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("tamper", ["disable_confirmation", "direct_provider_link"])
def test_auth_templates_cannot_bypass_stackos(prepared: Path, tamper: str) -> None:
    if tamper == "disable_confirmation":
        path = prepared / "supabase/config.toml"
        path.write_text(
            path.read_text().replace("enable_confirmations = true", "enable_confirmations = false")
        )
    else:
        path = prepared / "supabase/templates/confirmation.html"
        path.write_text('<a href="http://127.0.0.1:18521/auth/v1/verify">Confirm</a>')
    with pytest.raises(ValidationError):
        _settings(prepared)


@pytest.mark.parametrize(
    "changes",
    [
        {"environment_id": "beta"},
        {"server_url": "http://127.0.0.1:18512"},
        {"supabase_url": "http://127.0.0.1:19521"},
        {"database_url": "postgresql://postgres:private-password@127.0.0.1:19522/postgres"},
        {"database_url": "sqlite:////tmp/fallback.db"},
        {"supabase_anon_key": "foreign-key"},
        {"supabase_service_role_key": "foreign-key"},
        {"host": "localhost"},
        {"port": 5180},
        {"data_dir": Path.home() / ".local/share/stackos"},
        {"state_dir": Path.home() / ".local/state/stackos"},
        {"data_dir": Path("relative")},
        {"state_dir": Path("/tmp/another-environment")},
        {"process_role": "standalone"},
    ],
)
def test_mixed_targets_refuse_without_secret_disclosure(prepared: Path, changes: dict) -> None:
    with pytest.raises(ValidationError) as error:
        _settings(prepared, **changes)
    assert "private-password" not in str(error.value)
    assert "foreign-key" not in str(error.value)


@pytest.mark.parametrize(
    "missing",
    [
        "environment_id",
        "server_url",
        "host",
        "port",
        "data_dir",
        "state_dir",
        "supabase_workdir",
        "supabase_url",
        "database_url",
        "supabase_anon_key",
        "supabase_service_role_key",
    ],
)
def test_missing_server_setting_never_uses_defaults(prepared: Path, missing: str) -> None:
    values = {
        key.removeprefix("STACKOS_").lower(): value
        for key, value in dotenv_values(prepared / "server.env").items()
    }
    values.pop(missing)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_runtime_refuses_server_credentials_and_same_listener(prepared: Path) -> None:
    for changes in ({"database_url": "postgresql://private"}, {"port": 18510}):
        with pytest.raises(ValidationError):
            _settings(prepared, "runtime", **changes)


def test_selected_config_missing_or_changed_refuses(prepared: Path) -> None:
    config = prepared / "supabase/config.toml"
    original = config.read_text()
    for changed in (
        original.replace('project_id = "alpha"', 'project_id = "beta"'),
        original.replace('signing_keys_path = "./signing_keys.json"', 'signing_keys_path = ""'),
        original.replace("port = 18524", "port = 18521"),
        original.replace("enabled = false", "enabled = true", 1),  # pooler has no port
    ):
        config.write_text(changed)
        with pytest.raises(ValidationError):
            _settings(prepared)
    config.unlink()
    with pytest.raises(ValidationError):
        _settings(prepared)


def test_real_settings_entrypoint_observes_environment(prepared: Path, monkeypatch) -> None:
    for key, value in dotenv_values(prepared / "server.env").items():
        assert value is not None
        monkeypatch.setenv(key, value)
    assert get_settings().environment_id == "alpha"
    monkeypatch.setenv("STACKOS_SUPABASE_URL", "http://127.0.0.1:19521")
    with pytest.raises(ValidationError):
        get_settings()


def test_explicit_file_ignores_cwd_and_refuses_overlay(prepared: Path, monkeypatch) -> None:
    monkeypatch.chdir(prepared)
    (prepared / ".env").write_text("STACKOS_PORT=5180\nSTACKOS_DATA_DIR=/installed\n")
    monkeypatch.setenv("STACKOS_ENV_FILE", str(prepared / "server.env"))
    assert get_settings().environment_id == "alpha"
    assert get_state_dir() == prepared / "server-state"
    monkeypatch.setenv("STACKOS_PORT", "19510")
    with pytest.raises(ValueError, match="cannot be mixed"):
        get_settings()
    monkeypatch.delenv("STACKOS_PORT")
    (prepared / "server.env").chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        get_settings()
    monkeypatch.setenv("STACKOS_ENV_FILE", str(prepared / "absent.env"))
    with pytest.raises(ValueError, match="existing absolute"):
        get_settings()


def test_incomplete_selected_file_cannot_redirect_startup_logs(prepared: Path, monkeypatch) -> None:
    path = prepared / "server.env"
    path.write_text("STACKOS_PROCESS_ROLE=server\nSTACKOS_ENVIRONMENT_ID=alpha\n")
    monkeypatch.setenv("STACKOS_ENV_FILE", str(path))
    with pytest.raises(ValidationError):
        get_state_dir()
    path.write_text("STACKOS_STATE_DIR=/tmp/isolated-state\n")
    with pytest.raises(ValueError, match="explicitly select"):
        get_settings()


def test_prepare_refuses_existing_custody_and_docker_resources(prepared: Path, tmp_path) -> None:
    ports = {
        "server": 18510,
        "runtime": 18511,
        "api": 18521,
        "db": 18522,
        "shadow": 18520,
        "mail": 18524,
    }
    with pytest.raises(ValueError, match="fresh"):
        prepare_module.prepare(prepared, "alpha", ports)
    prepare_module.subprocess.run.return_value.stdout = "supabase_db_alpha\n"
    with pytest.raises(ValueError, match="already owns"):
        prepare_module.prepare(tmp_path / "new", "alpha", ports)
    assert not (tmp_path / "new").exists()
    prepare_module.subprocess.run.side_effect = subprocess.CalledProcessError(1, "docker")
    with pytest.raises(subprocess.CalledProcessError):
        prepare_module.prepare(tmp_path / "new", "alpha", ports)


def test_standalone_fixture_remains_separate(settings: Settings) -> None:
    assert settings.process_role == "standalone"
    assert settings.database_url is None
    assert settings.db_path == settings.data_dir / "stackos.db"
