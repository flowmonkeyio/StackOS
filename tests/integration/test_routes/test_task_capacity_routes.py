"""Capacity's real local-admin REST boundary and existing backup custody."""

import sqlite3
from pathlib import Path

import pytest

from stackos import backups
from stackos.auth import derive_ui_token
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import NotFoundError


def call(api, name, arguments=None, headers=None):
    return api.post(
        f"/api/v1/operations/tracker.{name}/call",
        json={"arguments": arguments or {}},
        headers=headers,
    )


def test_capacity_contract_and_local_operator_save_readback(api, settings):
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    default = call(api, "deliveryCapacity", headers=ui)
    assert default.status_code == 200, default.text
    assert default.json() == {
        "scope": "local-user-daemon",
        "configured_limit": 3,
        "running_count": 0,
        "queued_count": 0,
        "supported_min": 1,
        "supported_max": 32,
    }
    assert call(api, "deliveryCapacity", {"response_mode": "raw"}, ui).json() == default.json()
    for value in (32, 1, 6):
        saved = call(api, "setDeliveryCapacity", {"concurrent_tasks": value}, ui)
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["configured_limit"] == value
        raw = call(
            api, "setDeliveryCapacity", {"concurrent_tasks": value, "response_mode": "raw"}, ui
        )
        assert raw.status_code == 200 and raw.json()["data"] == saved.json()["data"]
        assert call(api, "deliveryCapacity", headers=ui).json()["configured_limit"] == value
    for value in (0, 33, True, "5", 4.5):
        assert call(api, "setDeliveryCapacity", {"concurrent_tasks": value}, ui).status_code == 422
    for arguments in ({"project_id": 1}, {"run_token": "forged"}):
        assert call(api, "deliveryCapacity", arguments, ui).status_code in (403, 422)
    assert (
        call(api, "setDeliveryCapacity", {"concurrent_tasks": 4, "project_id": 1}, ui).status_code
        == 422
    )
    for name in ("deliveryCapacity", "setDeliveryCapacity"):
        arguments = {} if name == "deliveryCapacity" else {"concurrent_tasks": 4}
        assert call(api, name, arguments, {"Authorization": ""}).status_code == 401
        assert call(api, name, {**arguments, "response_mode": "ack"}, ui).status_code == 422
        spec = build_operation_registry().get(f"tracker.{name}")
        with pytest.raises(NotFoundError):
            build_operation_registry().get(f"tracker.{name}", surface="mcp")
        assert spec.surfaces.cli.enabled and spec.surfaces.rest.browser_safe
        assert spec.effective_response_policy.allowed_modes == ("compact", "raw")
    assert call(api, "deliveryCapacity", headers=ui).json()["configured_limit"] == 6


def test_capacity_uses_existing_database_backup_and_restore(api, settings, tmp_path):
    assert call(api, "setDeliveryCapacity", {"concurrent_tasks": 11}).status_code == 200
    private, public = tmp_path / "keys/private.pem", tmp_path / "keys/public.pem"
    backups.generate_recovery_key(settings, private, public)
    backups.update_policy(settings, {"recipient_public_key": public.read_text()})
    (tmp_path / "archives").mkdir()
    result = backups.create_backup(settings, output=tmp_path / "archives/capacity.sbak")
    restored = tmp_path / "restored"
    backups.restore_backup(settings, Path(result["path"]), private, restored)
    with sqlite3.connect(restored / "data/stackos.db") as connection:
        assert connection.execute(
            "SELECT concurrent_tasks FROM task_delivery_capacity"
        ).fetchall() == [(11,)]
    assert backups.backup_status(settings)["enabled"] is False


def test_capacity_survives_daemon_app_restart(settings):
    from fastapi.testclient import TestClient

    from stackos.server import create_app
    from tests.helpers.prepared_app import prepare_app_database

    prepare_app_database(settings)
    for restart in range(2):
        with TestClient(create_app(settings), base_url="http://127.0.0.1:5180") as client:
            client.headers.update(
                {"Authorization": f"Bearer {settings.token_path.read_text().strip()}"}
            )
            if restart == 0:
                assert (
                    call(client, "setDeliveryCapacity", {"concurrent_tasks": 8}).status_code == 200
                )
            result = call(client, "deliveryCapacity")
            assert result.status_code == 200, result.text
            assert result.json()["configured_limit"] == 8
            assert result.json()["running_count"] == 0
