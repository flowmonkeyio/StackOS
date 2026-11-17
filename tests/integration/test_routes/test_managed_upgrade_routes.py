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
from tests.integration.test_repositories.test_task_delivery import native_source_fixtures  # noqa: F401


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
    finish_payload = {
        "upgrade_id": payload["upgrade_id"],
        "target_identity": "target",
        "expected_revision": 2,
        "daemon_generation": "target-generation",
        "doctor_exit_code": 0,
        "doctor_evidence_ref": "fixture:doctor:exit-0",
    }
    for name, arguments in (
        ("prepareUpgrade", payload),
        ("upgradeStatus", {}),
        ("finishUpgrade", finish_payload),
    ):
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
            current = call(client, "upgradeStatus").json()
            assert {k: v for k, v in current.items() if k != "runtime"} == {
                k: v for k, v in receipt.items() if k != "runtime"
            }
            assert current["runtime"]["running_identity"] == receipt["runtime"]["running_identity"]
            assert (
                current["runtime"]["daemon_generation"] == receipt["runtime"]["daemon_generation"]
            ) == (generation == 0)
            assert call(client, "prepareUpgrade", payload).json()["data"] == current


def test_target_postflight_finish_and_queue_across_actual_app_lifespans(
    settings,
    project_id,
    tmp_path,
    monkeypatch,
):
    from threading import Event
    from time import monotonic, sleep

    from fastapi.testclient import TestClient

    from stackos.host_mcp.result import AgentRunResult
    from stackos.repositories.tracker import TrackerRepository
    from stackos.server import create_app
    from tests.integration.test_repositories.test_task_delivery import (
        generic_result,
        git,
        prepared_task,
        submit,
        use_codex_native,
    )

    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "commit", "--allow-empty", "-m", "baseline")
    head = git(root, "rev-parse", "HEAD")
    done, launches = Event(), []

    def native(_directory, _instructions, prompt, _schema, *, on_session, **_kwargs):
        packet = json.loads(prompt)
        identity = f"{packet['brief']['ticket']['key']}-{packet['phase']}"
        launches.append(identity)
        on_session(identity)
        if identity == "second-review":
            done.set()
        return AgentRunResult(
            0, thread_id=identity, result=generic_result(outputs={"commit_sha": head})
        )

    use_codex_native(monkeypatch, native)
    payload = {"upgrade_id": "target-fixture", "source_identity": "source", "expected_revision": 0}
    for generation in range(2):
        with TestClient(create_app(settings), base_url="http://127.0.0.1:5180") as client:
            client.headers.update(
                {"Authorization": f"Bearer {settings.token_path.read_text().strip()}"}
            )
            scheduler = client.app.state.scheduler
            scheduler.pause()
            status = call(
                client, "upgradeStatus", {"upgrade_id": "target-fixture"} if generation else {}
            ).json()
            runtime = status["runtime"]
            if generation == 0:
                with Session(client.app.state.engine) as session:
                    prepared_task(session, project_id)
                    submit(session, project_id, root, scheduler)
                payload["target_identity"] = runtime["running_identity"]
                receipt = call(client, "prepareUpgrade", payload)
                assert receipt.status_code == 200, receipt.text
                old_generation = runtime["daemon_generation"]
                continue
            assert not launches and status["status"] == "quiesced" and status["fenced"]
            assert runtime["daemon_generation"] != old_generation
            finish = {
                "upgrade_id": "target-fixture",
                "target_identity": runtime["running_identity"],
                "expected_revision": status["revision"],
                "daemon_generation": runtime["daemon_generation"],
                "doctor_exit_code": 0,
                "doctor_evidence_ref": "fixture:controlled-doctor:exit-0",
            }
            for invalid in (
                {"target_identity": "wrong-target"},
                {"doctor_exit_code": 9},
                {"daemon_generation": old_generation},
            ):
                rejected = call(client, "finishUpgrade", finish | invalid)
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["data"]["code"] == "upgrade_postflight_failed"
                assert (
                    call(client, "upgradeStatus", {"upgrade_id": "target-fixture"}).json() == status
                )
            result = call(client, "finishUpgrade", finish)
            assert result.status_code == 200, result.text
            assert result.json()["data"]["status"] == "continued"
            first = result.json()
            repeated = call(client, "finishUpgrade", finish).json()
            # The shared queue can reserve between responses without advancing
            # the upgrade revision or admitting a second callback.
            for prior, now in zip(
                first["data"]["checkpoints"], repeated["data"]["checkpoints"], strict=True
            ):
                prior_state = prior.pop("continuation_state")
                now_state = now.pop("continuation_state")
                assert (prior_state, now_state) in {
                    ("pending", "pending"),
                    ("pending", "dispatched"),
                    ("dispatched", "dispatched"),
                }
            assert repeated == first
            assert not launches
            scheduler.resume()
            assert done.wait(5)
            deadline = monotonic() + 5
            with Session(client.app.state.engine) as session:
                while True:
                    session.expire_all()
                    task = (
                        TrackerRepository(session)
                        .get(project_id=project_id, task_key="deliver")
                        .tasks[0]
                    )
                    if (
                        task.metadata_json["delivery"]["status"] == "complete"
                        or monotonic() >= deadline
                    ):
                        break
                    sleep(0.02)
                assert task.metadata_json["delivery"]["status"] == "complete"
            assert launches == [
                "first-delivery",
                "first-review",
                "second-delivery",
                "second-review",
            ]


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
