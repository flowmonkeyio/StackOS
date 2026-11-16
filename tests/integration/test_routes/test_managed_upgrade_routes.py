"""Real REST/CLI caller and denied project/console authority for upgrade preparation."""

import asyncio
import json

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from stackos.auth import derive_ui_token
from stackos.db.models import ManagedUpgrade
from stackos.mcp.context import MCPContext
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.maintenance import PrepareUpgradeInput, prepare_upgrade
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import NotFoundError


def call(api, name, arguments=None, headers=None):
    return api.post(
        f"/api/v1/operations/maintenance.{name}/call",
        json={"arguments": arguments or {}},
        headers=headers,
    )


def test_admin_prepare_status_and_denied_authority(api, settings, project_id):
    from tests.integration.test_mcp.conftest import MCPClient
    from tests.integration.test_routes.test_operations_routes import _start_mock_run_plan

    payload = {
        "upgrade_id": "upgrade-fixture",
        "source_identity": "source",
        "target_identity": "target",
        "expected_revision": 0,
    }
    assert call(api, "upgradeStatus").json()["status"] == "idle"
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    _plan, token, _run, _step = _start_mock_run_plan(api, project_id)
    mcp = MCPClient(api, settings.token_path.read_text().strip())
    for name, arguments in (("prepareUpgrade", payload), ("upgradeStatus", {})):
        for headers, body in (
            (ui, arguments),
            ({"Authorization": ""}, arguments),
            (None, arguments | {"run_token": token}),
        ):
            denied = call(api, name, body, headers)
            assert denied.status_code in (401, 403), denied.text
        spec = build_operation_registry().get(f"maintenance.{name}")
        assert spec.surfaces.cli.enabled and not spec.surfaces.rest.browser_safe
        with pytest.raises(NotFoundError):
            build_operation_registry().get(f"maintenance.{name}", surface="mcp")
        denied = mcp.call_tool_error(f"maintenance.{name}", arguments | {"project_id": project_id})
        assert denied["code"] == -32601 and denied["message"] == "MethodNotFoundError"
    with Session(api.app.state.engine) as session:
        assert session.exec(select(ManagedUpgrade)).all() == []
        with pytest.raises(ToolNotGrantedError):
            asyncio.run(
                prepare_upgrade(
                    PrepareUpgradeInput(**payload),
                    MCPContext(
                        session=session,
                        project_id=project_id,
                        request_id="foreign-project-agent",
                        extras={"surface": "mcp"},
                    ),
                    None,
                )
            )
    saved = call(api, "prepareUpgrade", payload)
    assert saved.status_code == 200, saved.text
    result = saved.json()["data"]
    assert result["status"] == "quiesced" and result["fenced"]
    assert result["revision"] == 2
    assert (result["source_identity"], result["target_identity"]) == ("source", "target")
    assert call(api, "upgradeStatus").json() == result
    assert call(api, "upgradeStatus", {"response_mode": "raw"}).json() == result
    assert call(api, "prepareUpgrade", payload).json()["data"] == result
    assert call(api, "prepareUpgrade", payload | {"response_mode": "raw"}).json()["data"] == result
    conflict = call(api, "prepareUpgrade", payload | {"target_identity": "other"})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["data"]["code"] == "upgrade_identity_changed"
    assert conflict.json()["data"]["revision"] == result["revision"]
    assert call(api, "upgradeStatus").json() == result
    for invalid in ({"project_id": project_id}, {"response_mode": "ack"}):
        assert call(api, "prepareUpgrade", payload | invalid).status_code == 422


def test_cli_uses_same_preparation_owner(api, tmp_path, monkeypatch):
    import stackos.cli.operation_commands as operation_cli
    from stackos.cli import app

    def request(method, path, *, body=None, **_kwargs):
        response = api.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", request)
    source = tmp_path / "prepare.json"
    source.write_text(
        json.dumps(
            {
                "upgrade_id": "cli-fixture",
                "source_identity": "source",
                "target_identity": "target",
                "expected_revision": 0,
            }
        )
    )
    result = CliRunner().invoke(
        app,
        ["ops", "call", "maintenance.prepareUpgrade", "--input", str(source)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    observed = json.loads(result.stdout)["data"]
    read = CliRunner().invoke(
        app, ["ops", "call", "maintenance.upgradeStatus"], catch_exceptions=False
    )
    assert read.exit_code == 0 and json.loads(read.stdout) == observed


def test_fence_survives_real_app_restart(settings):
    from fastapi.testclient import TestClient

    from stackos.server import create_app
    from tests.helpers.prepared_app import prepare_app_database

    prepare_app_database(settings)
    payload = {
        "upgrade_id": "restart-fixture",
        "source_identity": "source",
        "target_identity": "target",
        "expected_revision": 0,
    }
    for generation in range(2):
        with TestClient(create_app(settings), base_url="http://127.0.0.1:5180") as client:
            client.headers.update(
                {"Authorization": f"Bearer {settings.token_path.read_text().strip()}"}
            )
            if generation == 0:
                saved = call(client, "prepareUpgrade", payload)
                assert saved.status_code == 200, saved.text
                receipt = saved.json()["data"]
            assert call(client, "upgradeStatus").json() == receipt
            assert call(client, "prepareUpgrade", payload).json()["data"] == receipt


def test_persisted_unknown_request_returns_admin_repair_context_without_preparation(
    api, project_id, tmp_path, monkeypatch
):
    from stackos.host_mcp.result import AgentRunResult
    from stackos.repositories.agent_requests import AgentRequestRepository
    from tests.integration.test_repositories.test_request_execution import launch, owner

    def runner(*_args, on_session, **_kwargs):
        on_session("unobserved-terminal-write")
        return AgentRunResult(
            0, thread_id="unobserved-terminal-write", result={"status": "success"}
        )

    def failed_terminal_write(*_args, **_kwargs):
        raise RuntimeError("fixture terminal write failed")

    monkeypatch.setattr(AgentRequestRepository, "_finish_execution", failed_terminal_write)
    with Session(api.app.state.engine) as session:
        repo, request, _claim, execution = owner(session, project_id)
        with pytest.raises(RuntimeError, match="terminal write failed"):
            launch(monkeypatch, tmp_path, execution, runner)
        before = repo.get(project_id=project_id, request_id=request.id).metadata_json
    response = call(
        api,
        "prepareUpgrade",
        {
            "upgrade_id": "unknown-request",
            "source_identity": "source",
            "target_identity": "target",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 409, response.text
    repair = response.json()["data"]
    assert repair["code"] == "native_request_quiescence_unconfirmed"
    assert repair["requests"][0]["request_id"] == request.id
    assert repair["next_operations"] == ["agentRequest.get"]
    assert "snapshot" not in repair["requests"][0]
    with Session(api.app.state.engine) as session:
        assert session.exec(select(ManagedUpgrade)).all() == []
        assert (
            AgentRequestRepository(session)
            .get(project_id=project_id, request_id=request.id)
            .metadata_json
            == before
        )
