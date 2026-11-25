"""Online roles must never enter the legacy whole-installation archive service."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from typer.testing import CliRunner

from stackos import backups
from stackos.backups import service
from stackos.cli import app, backup_commands
from stackos.config import Settings
from stackos.mcp.context import MCPContext
from stackos.operations import backups as operations
from stackos.repositories.base import ValidationError


@pytest.fixture(params=["server", "runtime"])
def online_settings(request, tmp_path):
    # Guard tests need no server/database: role selection alone must refuse
    # before opening any path. Settings target validation is proved separately.
    return Settings.model_construct(
        process_role=request.param, data_dir=tmp_path / "data", state_dir=tmp_path / "state"
    )


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("load_policy", ()),
        ("save_policy", (backups.BackupPolicy(),)),
        ("update_policy", ({"enabled": False},)),
        ("backup_status", ()),
        ("recovery_key_custody", ([Path("/unused/custody")],)),
        ("initialize_backups", ("not-a-key",)),
        ("generate_recovery_key", (Path("/unused/key"), Path("/unused/public"))),
        ("export_recovery_key", (Path("/unused/key"), Path("/unused/export"))),
        ("export_latest_backup", (Path("/unused/export"),)),
        ("create_backup", ()),
        ("run_due_backup", ()),
        ("restore_backup", (Path("/unused/archive"), Path("/unused/key"), Path("/unused/out"))),
    ],
)
def test_online_service_refuses_before_filesystem(online_settings, monkeypatch, name, args):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Online backup reached local state or archive processing")

    monkeypatch.setattr(service, "backup_lock", forbidden)
    monkeypatch.setattr(service, "read_json", forbidden)
    monkeypatch.setattr(backups.recovery, "canonical", forbidden)
    fn = getattr(backups, name, None) or getattr(service, name)
    kwargs = {}
    if name == "initialize_backups":
        kwargs = {
            "private_key_path": Path("/unused/key"),
            "protected_key_path": Path("/unused/cache"),
        }
    with pytest.raises(backups.BackupError) as raised:
        fn(online_settings, *args, **kwargs)
    assert raised.value.code == "unsupported_backup_backend"
    assert "Postgres" in raised.value.message
    assert not online_settings.state_dir.exists()
    assert not online_settings.data_dir.exists()


@pytest.mark.parametrize(
    "command",
    [
        ["backup"],
        ["backups", "status"],
        ["backups", "configure", "--enabled"],
        ["backups", "configure", "--public-key-file", "/unused/public"],
        ["backup", "--public-key-file", "/unused/public"],
        [
            "backups",
            "keygen",
            "--private-key-file",
            "/unused/key",
            "--public-key-file",
            "/unused/public",
        ],
        ["backups", "custody", "--reserve", "--protected-path", "/unused/custody"],
        [
            "backups",
            "initialize",
            "--public-key-file",
            "/unused/public",
            "--private-key-file",
            "/unused/key",
            "--protected-key-file",
            "/unused/cache",
        ],
        ["backups", "export", "--output", "/unused/export"],
        [
            "backups",
            "export-key",
            "--private-key-file",
            "/unused/key",
            "--output",
            "/unused/export",
        ],
        [
            "restore",
            "/unused/archive",
            "--private-key-file",
            "/unused/key",
            "--output-dir",
            "/unused/out",
        ],
    ],
)
def test_online_cli_explains_refusal(online_settings, monkeypatch, command):
    monkeypatch.setattr(backup_commands, "get_settings", lambda: online_settings)
    result = CliRunner().invoke(app, command)
    assert result.exit_code == 1
    assert "unsupported_backup_backend" in result.output
    assert "Postgres" in result.output
    assert not online_settings.state_dir.exists()


@pytest.mark.parametrize("name", ["backup.status", "backup.configure", "backup.create"])
def test_online_rest_refuses_before_dispatch_or_database(online_settings, name):
    from stackos.api.project_access import authorize_request

    request = Request(
        {"type": "http", "method": "POST", "path": f"/api/v1/operations/{name}/call", "headers": []}
    )
    # No engine, token file, SQLite path or database query is needed for refusal.
    with pytest.raises(HTTPException) as raised:
        authorize_request(SimpleNamespace(info={}), request, online_settings)
    assert raised.value.status_code == 403
    assert raised.value.detail["code"] == "unsupported_backup_backend"
    assert "Postgres" in raised.value.detail["message"]


def test_trusted_operation_adapter_also_refuses(online_settings):
    ctx = MCPContext(
        session=None,
        request_id="backup-backend-test",
        extras={"surface": "rest", "trusted_local_admin": True, "settings": online_settings},
    )
    with pytest.raises(ValidationError) as raised:
        operations._settings(operations.BackupStatusInput(), ctx)
    assert raised.value.data["code"] == "unsupported_backup_backend"
