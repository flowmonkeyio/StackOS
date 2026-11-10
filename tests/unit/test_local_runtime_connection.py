"""Connection boundary through REST and the actual local folder consumer.

Supabase identity transport is substituted here; real Auth/Postgres proof is
run separately against an explicitly selected disposable environment.
"""

from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlmodel import Session, select

from stackos.api.deps import get_settings
from stackos.config import Settings
from stackos.local_runtime.connection import LocalConnectionStore, connect, disconnect

ORIGIN = "http://127.0.0.1:18510"


@pytest.fixture
def online(client, settings: Settings, monkeypatch):
    selected = settings.model_copy(
        update={
            "process_role": "server",
            "environment_id": "connection-test",
            "server_url": ORIGIN,
            "supabase_url": "http://127.0.0.1:18521",
            "supabase_anon_key": SecretStr("synthetic-anon"),
        }
    )
    client.app.state.settings = selected
    client.app.dependency_overrides[get_settings] = lambda: selected
    client.base_url = httpx.URL(ORIGIN)
    client.headers.clear()
    client.headers["Origin"] = ORIGIN
    state = {"user_id": str(uuid4())}
    original = httpx.AsyncClient

    def respond(request):
        if request.url.path.endswith("/user"):
            return httpx.Response(
                200, json={"id": state["user_id"], "email_confirmed_at": "2026-11-10"}
            )
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh"})

    monkeypatch.setattr(
        "stackos.auth.httpx.AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": "password123"}
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    project = client.post(
        "/api/v1/projects", json={"slug": "owned", "name": "Owned", "domain": "example.test"}
    )
    assert project.status_code in {200, 201}, project.text
    return client, selected, state, project.json()["data"]["id"]


def call(client, name, **arguments):
    return client.post(
        f"/api/v1/operations/localRuntime.{name}/call", json={"arguments": arguments}
    )


def test_authenticated_mcp_uses_same_owner_and_refuses_unadapted_tools(online):
    client, _selected, _state, project_id = online
    assert client.get("/api/v1/operations").status_code == 200
    assert client.get("/api/v1/operations/artifact.download").status_code == 200
    client.headers["Accept"] = "application/json, text/event-stream"

    def rpc(method, params):
        response = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        assert response.status_code == 200, response.text
        return response.json()

    initialized = rpc(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "proof", "version": "1"},
        },
    )
    assert "result" in initialized
    own = rpc("tools/call", {"name": "project.get", "arguments": {"id_or_slug": project_id}})
    assert own["result"]["isError"] is False, own
    foreign = rpc(
        "tools/call", {"name": "project.get", "arguments": {"id_or_slug": project_id + 1}}
    )
    assert foreign["result"]["isError"] is True
    unadapted = rpc(
        "tools/call",
        {
            "name": "workspace.startSession",
            "arguments": {"global_session": True, "project_id": project_id},
        },
    )
    assert unadapted["result"]["isError"] is True
    client.cookies.clear()
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


def test_credential_is_scoped_revocable_and_never_replayed(online):
    from stackos.db.models import IdempotencyKey, LocalRuntimeConnection

    client, selected, state, project_id = online
    response = call(client, "authorize", project_id=project_id, response_mode="raw")
    assert response.status_code == 200, response.text
    issued = response.json()
    token = issued["credential"]
    identity = {"project_id": project_id, "connection_id": issued["connection_id"]}
    assert issued["user_id"] == state["user_id"]
    assert issued["environment_id"] == selected.environment_id
    with Session(client.app.state.engine) as session:
        row = session.exec(select(LocalRuntimeConnection)).one()
        assert token not in row.model_dump_json()
        assert session.exec(select(IdempotencyKey)).all() == []
    assert (
        call(client, "authorize", project_id=project_id, idempotency_key="replay").status_code
        == 403
    )
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token}"
    assert call(client, "status", **identity).status_code == 200
    assert call(client, "status", **(identity | {"project_id": project_id + 1})).status_code == 404
    assert client.get("/api/v1/projects").status_code == 403
    assert call(client, "authorize", project_id=project_id).status_code == 403
    assert call(client, "revoke", **identity).status_code == 200
    assert call(client, "status", **identity).status_code == 401


def test_deliberate_local_binding_reuse_and_changed_folder_refusal(online, tmp_path):
    client, selected, _state, project_id = online
    folder = tmp_path / "chosen"
    folder.mkdir()
    store = LocalConnectionStore(tmp_path / "runtime")
    bound = connect(
        client,
        store,
        server_url=ORIGIN,
        environment_id=selected.environment_id,
        project_id=project_id,
        folder=folder,
    )
    assert Path(bound.root) == folder.resolve()
    assert store.path.stat().st_mode & 0o777 == 0o600
    reused = connect(
        client,
        store,
        server_url=ORIGIN,
        environment_id=selected.environment_id,
        project_id=project_id,
        folder=folder,
    )
    assert reused.connection_id == bound.connection_id
    with pytest.raises(ValueError):
        store.require_folder(bound, folder.parent)
    folder.rename(tmp_path / "moved")
    folder.mkdir()
    with pytest.raises(ValueError, match="folder"):
        store.require_folder(bound, folder)
    assert not (store.path.parent / "stackos.db").exists()


def test_foreign_owner_environment_expiry_and_sibling_refuse(online):
    from stackos.db.models import LocalRuntimeConnection, Project

    client, selected, state, project_id = online
    first = call(client, "authorize", project_id=project_id).json()
    second = call(client, "authorize", project_id=project_id).json()
    identity = {"project_id": project_id, "connection_id": first["connection_id"]}
    assert (
        call(client, "authorize", project_id=project_id, response_mode="compact").status_code == 422
    )
    client.headers["Authorization"] = f"Bearer {first['credential']}"
    assert call(client, "list", project_id=project_id).status_code == 403
    assert (
        call(
            client, "revoke", project_id=project_id, connection_id=second["connection_id"]
        ).status_code
        == 404
    )
    client.app.state.settings = selected.model_copy(update={"environment_id": "other"})
    assert call(client, "status", **identity).status_code == 401
    client.app.state.settings = selected
    with Session(client.app.state.engine) as session:
        row = session.get(LocalRuntimeConnection, first["connection_id"])
        row.expires_at = 1
        session.add(row)
        session.commit()
    assert call(client, "status", **identity).status_code == 401
    client.headers.pop("Authorization")
    owner = state["user_id"]
    state["user_id"] = str(uuid4())
    login = client.post(
        "/api/v1/auth/login", json={"email": "foreign@example.test", "password": "password123"}
    )
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    for name, arguments in (
        ("authorize", {"project_id": project_id}),
        ("list", {"project_id": project_id}),
        ("status", identity),
        ("revoke", identity),
    ):
        response = call(client, name, **arguments)
        assert response.status_code == 404, response.text
        assert owner not in response.text and first["credential"] not in response.text
    with Session(client.app.state.engine) as session:
        assert len(session.exec(select(LocalRuntimeConnection)).all()) == 2
        row = session.get(Project, project_id)
        row.owner_user_id = state["user_id"]
        session.add(row)
        session.commit()
    client.headers["Authorization"] = f"Bearer {second['credential']}"
    assert (
        call(
            client, "status", project_id=project_id, connection_id=second["connection_id"]
        ).status_code
        == 401
    )


def test_local_binding_refuses_implicit_rebind_and_revocation(online, tmp_path):
    from stackos.db.models import LocalRuntimeConnection

    client, selected, _state, project_id = online
    folder = tmp_path / "selected"
    folder.mkdir()
    store = LocalConnectionStore(tmp_path / "runtime")
    arguments = {
        "server_url": ORIGIN,
        "environment_id": selected.environment_id,
        "project_id": project_id,
        "folder": folder,
    }
    for invalid in (tmp_path / "missing", Path("/")):
        with pytest.raises(ValueError):
            connect(client, store, **(arguments | {"folder": invalid}))
    with Session(client.app.state.engine) as session:
        assert session.exec(select(LocalRuntimeConnection)).all() == []
    bound = connect(client, store, **arguments)
    for changes in (
        {"environment_id": "other"},
        {"project_id": project_id + 1},
        {"folder": tmp_path},
        {"server_url": "https://other.example"},
    ):
        with pytest.raises(ValueError):
            connect(client, store, **(arguments | changes))
    assert store.load() == bound
    call(client, "revoke", project_id=project_id, connection_id=bound.connection_id)
    with pytest.raises(ValueError, match="refused"):
        connect(client, store, **arguments)
    disconnect(client, store)
    assert store.load() is None
    rebound = connect(client, store, **arguments)
    assert rebound.connection_id != bound.connection_id
    with Session(client.app.state.engine) as session:
        assert len(session.exec(select(LocalRuntimeConnection)).all()) == 2


def test_private_local_config_and_symlink_checks(tmp_path):
    store = LocalConnectionStore(tmp_path / "private")
    assert store.load() is None
    store.path.symlink_to(tmp_path / "unrelated")
    with pytest.raises(OSError):
        store.load()
    store.path.unlink()
    store.path.write_text("{}")
    store.path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        store.load()


def test_connection_setup_retains_origin_csrf_and_adapter_boundary(online):
    from stackos.operations.registry import build_operation_registry

    client, _selected, _state, project_id = online
    csrf = client.headers.pop("X-CSRF-Token")
    assert call(client, "authorize", project_id=project_id).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    client.headers["Origin"] = "http://127.0.0.1:19999"
    assert call(client, "authorize", project_id=project_id).status_code == 403
    registry = build_operation_registry()
    for name in ("authorize", "list", "status", "revoke"):
        spec = registry.get(f"localRuntime.{name}")
        assert spec.surfaces.rest.enabled and spec.surfaces.rest.browser_safe
        assert not spec.surfaces.mcp.enabled and not spec.surfaces.cli.enabled
        assert spec.idempotency_policy == "handler"
        assert spec.effective_response_policy.allowed_modes == ("raw",)
        spec.input_model.model_validate(spec.examples[0].arguments)
