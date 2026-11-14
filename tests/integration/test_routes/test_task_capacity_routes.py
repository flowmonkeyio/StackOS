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


def test_capacity_contract_and_local_operator_save_readback(api, settings, project_id):
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    default = call(api, "deliveryCapacity", headers=ui)
    assert default.status_code == 200, default.text
    row = default.json()["projects"][0]
    assert row["project_id"] == project_id
    assert (row["configured_limit"], row["running_count"], row["queued_count"]) == (3, 0, 0)
    assert default.json()["supported_min"] == 1
    assert default.json()["supported_max"] == 32
    assert call(api, "deliveryCapacity", {"response_mode": "raw"}, ui).json() == default.json()
    for value in (32, 1, 6):
        update = {"updates": [{"project_id": project_id, "max_concurrent_tasks": value}]}
        saved = call(api, "setDeliveryCapacity", update, ui)
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["projects"][0]["configured_limit"] == value
        raw = call(api, "setDeliveryCapacity", {**update, "response_mode": "raw"}, ui)
        assert raw.status_code == 200 and raw.json()["data"] == saved.json()["data"]
        assert (
            call(api, "deliveryCapacity", headers=ui).json()["projects"][0]["configured_limit"]
            == value
        )
    for value in (0, 33, True, "5", 4.5):
        assert (
            call(
                api,
                "setDeliveryCapacity",
                {"updates": [{"project_id": project_id, "max_concurrent_tasks": value}]},
                ui,
            ).status_code
            == 422
        )
    assert call(
        api, "deliveryCapacity", {"project_id": project_id}, ui
    ).json() == default.json() | {"projects": [row | {"configured_limit": 6}]}
    assert call(api, "deliveryCapacity", {"project_id": 999999}, ui).status_code == 404
    for arguments in ({"project_id": 0}, {"run_token": "forged"}):
        assert call(api, "deliveryCapacity", arguments, ui).status_code in (403, 422)
    assert call(api, "setDeliveryCapacity", {"concurrent_tasks": 4}, ui).status_code == 422
    for name in ("deliveryCapacity", "setDeliveryCapacity"):
        arguments = (
            {}
            if name == "deliveryCapacity"
            else {"updates": [{"project_id": project_id, "max_concurrent_tasks": 4}]}
        )
        assert call(api, name, arguments, {"Authorization": ""}).status_code == 401
        assert call(api, name, {**arguments, "response_mode": "ack"}, ui).status_code == 422
        spec = build_operation_registry().get(f"tracker.{name}")
        with pytest.raises(NotFoundError):
            build_operation_registry().get(f"tracker.{name}", surface="mcp")
        assert spec.surfaces.cli.enabled and spec.surfaces.rest.browser_safe
        assert spec.effective_response_policy.allowed_modes == ("compact", "raw")
    assert call(api, "deliveryCapacity", headers=ui).json()["projects"][0]["configured_limit"] == 6


def test_capacity_keeps_retired_bad_history_out_and_returns_uncertain_owner_context(
    api, settings, project_id, tmp_path
):
    from copy import deepcopy

    from sqlmodel import Session

    from stackos.db.models import AgentRequest
    from tests.integration.test_repositories.test_managed_setup import stored_setup

    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    payload = {
        "schema_version": 999,
        "entry_kind": "managed_ui",
        "status": "failed",
        "managed": {
            "slot_reserved": False,
            "admission_id": "prior-daemon",
            "settings": {"project_dir": str(tmp_path / "owned")},
        },
    }
    with Session(api.app.state.engine) as session:
        request_id = stored_setup(session, project_id, "legacy-setup", payload)
    retired = call(api, "deliveryCapacity", headers=ui)
    assert retired.status_code == 200, retired.text
    assert retired.json()["projects"][0]["running_count"] == 0
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, request_id)
        metadata = deepcopy(row.metadata_json)
        metadata["project_setup"]["managed"].pop("slot_reserved")
        row.metadata_json = {**metadata}
        session.add(row)
        session.commit()
        before = row.metadata_json
    unknown = call(api, "deliveryCapacity", headers=ui)
    assert unknown.status_code == 409, unknown.text
    assert unknown.json()["data"] == {
        "project_id": project_id,
        "request_id": request_id,
        "next_action": "inspect_request",
    }
    with Session(api.app.state.engine) as session:
        assert session.get(AgentRequest, request_id).metadata_json == before


def test_capacity_uses_existing_database_backup_and_restore(api, settings, tmp_path, project_id):
    assert (
        call(
            api,
            "setDeliveryCapacity",
            {"updates": [{"project_id": project_id, "max_concurrent_tasks": 11}]},
        ).status_code
        == 200
    )
    private, public = tmp_path / "keys/private.pem", tmp_path / "keys/public.pem"
    backups.generate_recovery_key(settings, private, public)
    backups.update_policy(settings, {"recipient_public_key": public.read_text()})
    (tmp_path / "archives").mkdir()
    result = backups.create_backup(settings, output=tmp_path / "archives/capacity.sbak")
    restored = tmp_path / "restored"
    backups.restore_backup(settings, Path(result["path"]), private, restored)
    with sqlite3.connect(restored / "data/stackos.db") as connection:
        assert connection.execute(
            "SELECT project_id, concurrent_tasks FROM task_delivery_capacity"
        ).fetchall() == [(project_id, 11)]
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
                created = client.post(
                    "/api/v1/projects",
                    json={"slug": "capacity", "name": "Capacity", "domain": "fixture.test"},
                )
                assert created.status_code in (200, 201), created.text
                project_id = created.json()["data"]["id"]
                assert (
                    call(
                        client,
                        "setDeliveryCapacity",
                        {"updates": [{"project_id": project_id, "max_concurrent_tasks": 8}]},
                    ).status_code
                    == 200
                )
            result = call(client, "deliveryCapacity")
            assert result.status_code == 200, result.text
            assert result.json()["projects"][0]["configured_limit"] == 8
            assert result.json()["projects"][0]["running_count"] == 0


def test_batch_save_atomic_commit_before_drain_and_noop(api, project_id, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlmodel import Session

    from stackos.operations.tracker import capacity
    from stackos.repositories.projects import ProjectRepository
    from stackos.task_capacity import configured_limit

    with Session(api.app.state.engine) as session:
        other = (
            ProjectRepository(session)
            .create(slug="other", name="Other", domain="other.test")
            .data.id
        )
    drains, writes = [], []

    def drain(**_kwargs):
        # A new DB session at the actual drain boundary must see both commits.
        with Session(api.app.state.engine) as session:
            drains.append((configured_limit(session, project_id), configured_limit(session, other)))

    def observe(_conn, _cursor, statement, parameters, _context, _many):
        if statement.startswith("INSERT INTO task_delivery_capacity"):
            writes.append(parameters)

    monkeypatch.setattr(capacity, "drain_delivery", drain)
    updates = {
        "updates": [
            {"project_id": project_id, "max_concurrent_tasks": 6},
            {"project_id": other, "max_concurrent_tasks": 2},
        ]
    }
    event.listen(api.app.state.engine, "before_cursor_execute", observe)
    try:
        saved = call(api, "setDeliveryCapacity", updates)
        assert saved.status_code == 200, saved.text
        assert drains == [(6, 2)] and len(writes) == 2
        writes.clear()
        assert call(api, "setDeliveryCapacity", updates).status_code == 200
        assert drains == [(6, 2)] and writes == []
        updates["updates"][0]["max_concurrent_tasks"] = 7
        assert call(api, "setDeliveryCapacity", updates).status_code == 200
        assert drains[-1] == (7, 2) and len(writes) == 1
    finally:
        event.remove(api.app.state.engine, "before_cursor_execute", observe)

    # Reject unknown/duplicate projects without partial persistence or drain.
    for invalid in (
        {"updates": [updates["updates"][0], {"project_id": 999999, "max_concurrent_tasks": 8}]},
        {"updates": [updates["updates"][0]] * 2},
        {"updates": []},
        {"updates": [updates["updates"][0]] * 101},
    ):
        assert call(api, "setDeliveryCapacity", invalid).status_code in (404, 422)
    drain_count = len(drains)
    commit = Session.commit

    def fail_commit(session):
        if session.get_bind() is api.app.state.engine:
            raise OperationalError("fixture", {}, Exception("disk unavailable"))
        return commit(session)

    monkeypatch.setattr(Session, "commit", fail_commit)
    updates["updates"][0]["max_concurrent_tasks"] = 12
    updates["updates"][1]["max_concurrent_tasks"] = 9
    failed = call(api, "setDeliveryCapacity", updates)
    assert failed.status_code == 422, failed.text
    assert failed.json()["data"] == {"code": "delivery-capacity-save", "retryable": True}
    assert len(drains) == drain_count
    with Session(api.app.state.engine) as session:
        assert (configured_limit(session, project_id), configured_limit(session, other)) == (7, 2)


def test_capacity_cli_rest_roundtrip_and_workflow_authority_denied(
    api, project_id, tmp_path, monkeypatch
):
    import json

    from typer.testing import CliRunner

    import stackos.cli.operation_commands as operation_cli
    from stackos.cli import app
    from tests.integration.test_routes.test_operations_routes import _start_mock_run_plan

    def request(method, path, *, body=None, **_kwargs):
        response = api.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", request)
    source = tmp_path / "capacity.json"
    updates = {"updates": [{"project_id": project_id, "max_concurrent_tasks": 9}]}
    source.write_text(json.dumps(updates))
    written = CliRunner().invoke(
        app,
        ["ops", "call", "tracker.setDeliveryCapacity", "--input", str(source)],
        catch_exceptions=False,
    )
    assert written.exit_code == 0, written.stdout
    assert json.loads(written.stdout)["data"]["projects"][0]["configured_limit"] == 9
    read = CliRunner().invoke(
        app,
        ["ops", "call", "tracker.deliveryCapacity", "--project", str(project_id)],
        catch_exceptions=False,
    )
    assert read.exit_code == 0, read.stdout
    assert json.loads(read.stdout)["projects"] == json.loads(written.stdout)["data"]["projects"]

    # A genuine active fixture workflow token adds no local-admin authority.
    _plan, token, _run, _step = _start_mock_run_plan(api, project_id)
    before = call(api, "deliveryCapacity").json()
    for name, arguments in (
        ("deliveryCapacity", {}),
        (
            "setDeliveryCapacity",
            {"updates": [{"project_id": project_id, "max_concurrent_tasks": 1}]},
        ),
    ):
        denied = call(api, name, {**arguments, "run_token": token})
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]
    assert call(api, "deliveryCapacity").json() == before
