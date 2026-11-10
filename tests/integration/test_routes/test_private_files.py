"""Private file admission and exact local materialization through real adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from sqlmodel import Session

from stackos.auth import UserPrincipal
from stackos.db.models import ActionCall, ActionCallStatus, Artifact, Project
from stackos.repositories.base import NotFoundError
from stackos.repositories.resources import ArtifactRepository
from tests.unit.test_server_auth import online as online_fixture
from tests.unit.test_server_auth import sign_in

online = online_fixture


def test_artifact_repository_refuses_foreign_owner(api):
    with Session(api.app.state.engine) as db_session:
        _repository_denials(db_session)


def _repository_denials(db_session):
    own = Project(
        slug="files-own",
        name="Own",
        domain="own.test",
        locale="en",
        owner_user_id="u1",
        environment_id="files",
    )
    foreign = Project(
        slug="files-foreign",
        name="Foreign",
        domain="foreign.test",
        locale="en",
        owner_user_id="u2",
        environment_id="files",
    )
    db_session.add_all([own, foreign])
    db_session.flush()
    row = Artifact(project_id=foreign.id, kind="file", uri="/generated-assets/foreign.bin")
    db_session.add(row)
    db_session.commit()
    db_session.info["user_principal"] = UserPrincipal("u1", "files")
    with pytest.raises(NotFoundError):
        ArtifactRepository(db_session).get(row.id)
    page = ArtifactRepository(db_session).query()
    assert page.total_estimate == 0 and not page.items


def _seed(client, settings, state, relative=None):
    return _seed_engine(client.app.state.engine, settings, state, relative)


def _seed_engine(engine, settings, state, relative=None):
    with Session(engine) as session:
        project = Project(
            slug="files-owner-" + uuid4().hex,
            name="Files",
            domain="files.test",
            locale="en",
            owner_user_id=state["user_id"],
            environment_id=settings.environment_id,
        )
        session.add(project)
        session.flush()
        path = settings.generated_assets_dir / (relative or f"projects/{project.id}/result.bin")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = bytes(range(256)) * 11
        path.write_bytes(data)
        row = Artifact(
            project_id=project.id,
            kind="file",
            uri="/generated-assets/" + str(path.relative_to(settings.generated_assets_dir)),
            mime_type="application/octet-stream",
        )
        session.add(row)
        session.commit()
        return project.id, row.id, path, data


def test_private_file_download_and_foreign_denial(online, tmp_path):
    client, settings, state = online
    project_id, artifact_id, server_path, data = _seed(client, settings, state)
    login = sign_in(client)
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    response = client.post(
        "/api/v1/operations/artifact.download/call",
        json={"arguments": {"project_id": project_id, "artifact_id": artifact_id}},
    )
    assert response.status_code == 200, response.text
    pointer = response.json()
    assert str(server_path) not in response.text
    assert pointer["sha256"] == hashlib.sha256(data).hexdigest()
    downloaded = client.get(pointer["download_uri"])
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == data
    assert downloaded.headers["cache-control"] == "no-store"
    from stackos.mcp.bridge.downloads import materialize_download

    local = tmp_path / "downloaded.bin"
    result = materialize_download(
        client,
        server_url=settings.server_url,
        headers={},
        tool_name="artifact.download",
        arguments={"project_id": project_id, "artifact_id": artifact_id},
        descriptor=pointer,
        destination_path=str(local),
    )
    assert result["local_path"] == str(local) and local.read_bytes() == data
    state["user_id"] = str(uuid4())
    sign_in(client)
    assert client.get(pointer["download_uri"]).status_code == 404
    assert client.post(
        "/api/v1/operations/artifact.download/call",
        json={"arguments": {"project_id": project_id, "artifact_id": artifact_id}},
    ).status_code in {403, 404}


@pytest.mark.parametrize(
    "suffix",
    [
        "../outside.bin",
        "%2e%2e/outside.bin",
        "x/../../outside.bin",
        "x\\outside.bin",
        "imap-transfers/raw.bin",
    ],
)
def test_traversal_and_staging_denied_through_both_readers(online, suffix):
    client, settings, state = online
    project_id, artifact_id, path, _data = _seed(client, settings, state)
    with Session(client.app.state.engine) as session:
        row = session.get(Artifact, artifact_id)
        row.uri = f"/generated-assets/projects/{project_id}/{suffix}"
        session.add(row)
        session.commit()
    login = sign_in(client)
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    for name in ("artifact.read", "artifact.download"):
        response = client.post(
            f"/api/v1/operations/{name}/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "artifact_id": artifact_id,
                    "response_mode": "raw",
                }
            },
        )
        assert response.status_code == 422, response.text
    assert path.exists()


def test_symlink_and_forged_action_output_are_denied(online):
    client, settings, state = online
    project_id, artifact_id, path, _data = _seed(client, settings, state)
    path.unlink()
    private = settings.generated_assets_dir / "imap-transfers" / "private.bin"
    private.parent.mkdir()
    private.write_bytes(b"private raw staging")
    path.symlink_to(private)
    with Session(client.app.state.engine) as session:
        call = ActionCall(
            project_id=project_id,
            action_key="fixture",
            plugin_slug="fixture",
            operation="fixture",
            status=ActionCallStatus.SUCCESS,
            response_json={
                "output_mode": "file",
                "file": {"uri": "/generated-assets/imap-transfers/private.bin"},
            },
        )
        session.add(call)
        session.commit()
        call_id = call.id
    login = sign_in(client)
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    response = client.post(
        "/api/v1/operations/artifact.download/call",
        json={"arguments": {"project_id": project_id, "artifact_id": artifact_id}},
    )
    assert response.status_code == 422, response.text
    response = client.post(
        "/api/v1/operations/actionCall.download/call",
        json={"arguments": {"project_id": project_id, "action_call_id": call_id}},
    )
    assert response.status_code == 422, response.text


def test_forged_metadata_path_is_not_read(online):
    client, settings, state = online
    project_id, artifact_id, path, _data = _seed(client, settings, state)
    path.write_text("readable output")
    with Session(client.app.state.engine) as session:
        row = session.get(Artifact, artifact_id)
        row.mime_type = "text/plain"
        row.metadata_json = {"absolute_path": str(path.parent / "different-file")}
        session.add(row)
        session.commit()
    login = sign_in(client)
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    response = client.post(
        "/api/v1/operations/artifact.read/call",
        json={
            "arguments": {
                "project_id": project_id,
                "artifact_id": artifact_id,
                "response_mode": "raw",
            }
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["content_available"] is False
    assert "content" not in response.json() and str(path) not in response.text
    response = client.post(
        "/api/v1/operations/artifact.download/call",
        json={"arguments": {"project_id": project_id, "artifact_id": artifact_id}},
    )
    assert response.status_code == 422, response.text


def test_real_supabase_file_download(monkeypatch, tmp_path):
    """Real Auth and Postgres; only explicitly selected disposable fixture state."""
    env_file = os.environ.get("STACKOS_TEST_POSTGRES_ENV_FILE")
    if not env_file:
        pytest.skip("requires explicit disposable STACKOS_TEST_POSTGRES_ENV_FILE")
    import httpx

    from stackos.config import get_settings
    from stackos.db.connection import make_settings_engine
    from stackos.mcp.bridge.downloads import materialize_download

    monkeypatch.setenv("STACKOS_ENV_FILE", env_file)
    selected = get_settings()
    assert selected.process_role == "server"
    assert selected.supabase_url.startswith("http://127.0.0.1:")
    users = [f"{uuid4().hex}@example.test" for _ in range(2)]
    password, ids = uuid4().hex, []
    key = selected.supabase_service_role_key.get_secret_value()
    headers = {"apikey": key, "Authorization": "Bearer " + key}
    server_log = (tmp_path / "server.log").open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "stackos.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(selected.port),
        ],
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    engine = make_settings_engine(selected)
    try:
        with httpx.Client(base_url=selected.supabase_url + "/auth/v1", headers=headers) as auth:
            for email in users:
                response = auth.post(
                    "/admin/users",
                    json={"email": email, "password": password, "email_confirm": True},
                )
                assert response.status_code == 200
                ids.append(response.json()["id"])
        with httpx.Client(base_url=selected.server_url, timeout=15) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    if client.get("/api/v1/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.1)
            client.headers["Origin"] = selected.server_url

            def login(email):
                response = client.post(
                    "/api/v1/auth/login", json={"email": email, "password": password}
                )
                assert response.status_code == 200, response.text
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

            login(users[0])
            project_id, artifact_id, _path, data = _seed_engine(
                engine, selected, {"user_id": ids[0]}
            )
            arguments = {"project_id": project_id, "artifact_id": artifact_id}
            response = client.post(
                "/api/v1/operations/artifact.download/call", json={"arguments": arguments}
            )
            assert response.status_code == 200, response.text
            descriptor = response.json()
            destination = tmp_path / "real-local-result.bin"
            materialize_download(
                client,
                server_url=selected.server_url,
                headers={},
                tool_name="artifact.download",
                arguments=arguments,
                descriptor=descriptor,
                destination_path=str(destination),
            )
            assert destination.read_bytes() == data
            assert hashlib.sha256(destination.read_bytes()).hexdigest() == descriptor["sha256"]
            login(users[1])
            assert client.get(descriptor["download_uri"]).status_code == 404
            assert (
                client.post(
                    "/api/v1/operations/artifact.download/call", json={"arguments": arguments}
                ).status_code
                == 404
            )
            client.cookies.clear()
            assert client.get(descriptor["download_uri"]).status_code == 401
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        server_log.close()
        engine.dispose()
        with httpx.Client(base_url=selected.supabase_url + "/auth/v1", headers=headers) as auth:
            for user_id in ids:
                assert auth.delete("/admin/users/" + user_id).status_code == 200


@pytest.mark.parametrize(
    "relative",
    [
        "imap-transfers/private.bin",
        "stripe-invoice-transfers/private.bin",
        "other-project/private.bin",
    ],
)
def test_forged_artifact_cannot_publish_private_file(online, relative):
    client, settings, state = online
    project_id, artifact_id, _path, _data = _seed(client, settings, state, relative)
    login = sign_in(client)
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    for name in ("artifact.read", "artifact.download"):
        response = client.post(
            f"/api/v1/operations/{name}/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "artifact_id": artifact_id,
                    "response_mode": "raw",
                }
            },
        )
        assert response.status_code in {403, 404, 422}, response.text


def test_bridge_download_returns_real_local_path(api, project_id, tmp_path):
    from stackos.mcp.bridge.proxy import AgentBridgeProxy

    settings = api.app.state.settings
    path = settings.generated_assets_dir / "local-result.bin"
    data = b"\x00\xffexact-file\n"
    path.write_bytes(data)
    with Session(api.app.state.engine) as session:
        row = Artifact(project_id=project_id, kind="file", uri="/generated-assets/local-result.bin")
        session.add(row)
        session.commit()
        artifact_id = row.id
    destination = tmp_path / "client" / "download.bin"
    destination.parent.mkdir()
    proxy = AgentBridgeProxy(
        url=str(api.base_url) + "/mcp",
        headers={
            **dict(api.headers),
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    proxy.request_daemon(
        api,
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "private-files-test", "version": "1"},
                },
            }
        ),
    )
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "artifact.download",
                "arguments": {
                    "project_id": project_id,
                    "artifact_id": artifact_id,
                    "destination_path": str(destination),
                },
            },
        }
    )
    # Exercise the public scoped toolbox path as well as daemon file retrieval.
    startup = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "workspace.startSession",
            "arguments": {"global_session": True, "project_id": project_id},
        },
    }
    assert not json.loads(
        proxy.handle(api, payload=startup, line=json.dumps(startup), request_id=2)
    )["result"].get("isError")
    request = json.loads(body)
    request["params"] = {
        "name": "toolbox.call",
        "arguments": {
            "tool_name": "artifact.download",
            "arguments": {
                "artifact_id": artifact_id,
                "project_id": project_id,
                "destination_path": str(destination),
            },
        },
    }
    denied = json.loads(proxy.handle(api, payload=request, line=json.dumps(request), request_id=1))[
        "result"
    ]
    assert denied["isError"] is True
    assert not destination.exists()
    # Named disposable source-test state; never workflow-796 or installed state.
    from stackos.repositories.run_plans import RunPlanRepository

    with Session(api.app.state.engine) as session:
        repository = RunPlanRepository(session)
        plan = repository.create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "file-download-proof",
                "title": "Disposable file download proof",
                "grants": {"step_tools": {"download": ["artifact.download"]}},
                "steps": [{"id": "download", "title": "Download"}],
            },
        ).data
        started = repository.start(plan.id, project_id=project_id).data
        repository.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="download")
        request["params"]["arguments"]["run_id"] = started.run_id
    result = json.loads(proxy.handle(api, payload=request, line=json.dumps(request), request_id=1))[
        "result"
    ]
    assert not result.get("isError"), result
    pointer = result["structuredContent"]
    assert pointer["local_path"] == str(destination)
    assert destination.read_bytes() == data
    assert pointer["sha256"] == hashlib.sha256(data).hexdigest()
    assert str(path) not in json.dumps(result)
    # An explicit retry must preserve an existing local file.
    destination.write_bytes(b"keep me")
    result = json.loads(proxy.request_daemon(api, body))["result"]
    assert result["isError"] is True
    assert destination.read_bytes() == b"keep me"


def test_server_action_producer_and_download(online, tmp_path):
    from stackos.actions import ActionConnectorRegistry, ActionRepository
    from stackos.mcp.bridge.downloads import materialize_download
    from stackos.repositories.base import ValidationError
    from tests.integration.test_repositories.test_actions import (
        _NoAuthConnector,
        _seed_action,
        _seed_noauth_action,
    )

    client, settings, state = online
    project_id, _artifact_id, _path, _data = _seed(client, settings, state)
    with Session(client.app.state.engine) as session:
        _seed_action(session)
        _seed_noauth_action(session)
        session.info["user_principal"] = UserPrincipal(state["user_id"], settings.environment_id)
        fake = _NoAuthConnector()
        connectors = ActionConnectorRegistry()
        connectors.register(fake)
        repository = ActionRepository(
            session, connectors=connectors, asset_dir=settings.generated_assets_dir
        )
        result = asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="test-actions.noauth.run",
                input_json={},
                output_policy_json={"mode": "always_file"},
            )
        ).data
        pointer = result.output_json["file"]
        assert "path" not in pointer and "absolute_path" not in pointer
        assert pointer["uri"].startswith(f"/generated-assets/action-outputs/project-{project_id}/")
        with pytest.raises(ValidationError, match="local-only"):
            asyncio.run(
                repository.execute(
                    project_id=project_id,
                    action_ref="test-actions.noauth.run",
                    input_json={},
                    output_policy_json={
                        "mode": "always_file",
                        "path": str(tmp_path / "wrong-server-directory"),
                    },
                )
            )
        assert fake.calls == 1
    login = sign_in(client)
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    arguments = pointer["download_arguments"]
    response = client.post(
        "/api/v1/operations/actionCall.download/call", json={"arguments": arguments}
    )
    assert response.status_code == 200, response.text
    destination = tmp_path / "action-output.json"
    materialize_download(
        client,
        server_url=settings.server_url,
        headers={},
        tool_name="actionCall.download",
        arguments=arguments,
        descriptor=response.json(),
        destination_path=str(destination),
    )
    saved = json.loads(destination.read_bytes())
    assert saved["response"]["output_json"] == {"ok": True}
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == pointer["sha256"]
