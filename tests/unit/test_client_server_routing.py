"""Native client session/target safety; HTTP transport is substituted here."""

import json

import httpx
import pytest
import typer

from stackos.auth import session_cookie_name
from stackos.cli.api_client import _api_request
from stackos.cli.server_session import ServerSession
from stackos.mcp.bridge import AgentBridgeProxy


@pytest.fixture
def selected(settings):
    return settings.model_copy(
        update={
            "process_role": "runtime",
            "server_url": "http://127.0.0.1:18510",
            "environment_id": "native-test",
        }
    )


def save(selected):
    session = ServerSession(selected)
    with httpx.Client() as client:
        client.cookies.set(session_cookie_name(selected), "opaque-cookie")
        session.save(
            client, {"environment_id": "native-test", "user_id": "owner", "csrf_token": "csrf"}
        )
    return session


def test_native_session_is_private_and_wrong_environment_refuses(selected):
    session = save(selected)
    assert session.path.stat().st_mode & 0o777 == 0o600
    assert "opaque-cookie" not in repr(session.load())
    with pytest.raises(ValueError, match="another server or environment"):
        ServerSession(selected.model_copy(update={"environment_id": "other"})).load()
    session.path.chmod(0o644)
    with pytest.raises(ValueError, match="private client session"):
        session.load()
    session.path.unlink()
    session.path.symlink_to(selected.state_dir / "other")
    with pytest.raises(OSError):
        session.load()


def test_cli_data_uses_server_session_without_daemon_token(selected, monkeypatch):
    session = save(selected)
    seen = []

    def respond(request):
        seen.append(request)
        assert request.url.host == "127.0.0.1" and request.url.port == 18510
        assert request.headers["Cookie"].endswith("=opaque-cookie")
        assert "Authorization" not in request.headers
        if request.url.path == "/api/v1/auth/session":
            return httpx.Response(
                200,
                json={
                    "status": "authenticated",
                    "environment_id": "native-test",
                    "user_id": "owner",
                },
            )
        return httpx.Response(200, json={"id": 12})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(respond))
    )
    assert _api_request("GET", "/api/v1/projects/12", settings=selected) == {"id": 12}
    assert len(seen) == 2
    assert not selected.token_path.exists()
    session.path.unlink()
    with pytest.raises(typer.Exit):
        _api_request("GET", "/api/v1/projects/12", settings=selected)
    assert len(seen) == 2  # No local listener or anonymous fallback.


@pytest.mark.parametrize(
    "status,identity",
    [
        (401, {}),
        (200, {"status": "authenticated", "environment_id": "other", "user_id": "owner"}),
        (200, {"status": "recovery_required", "environment_id": "native-test", "user_id": "owner"}),
    ],
)
def test_expired_foreign_and_recovery_sessions_refuse(selected, monkeypatch, status, identity):
    session = save(selected)
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            **kw, transport=httpx.MockTransport(lambda _: httpx.Response(status, json=identity))
        ),
    )
    with pytest.raises(ValueError):
        session.headers()


def test_bound_proxy_keeps_local_scope_and_rechecks_headers():
    checks = []
    proxy = AgentBridgeProxy(
        url="http://127.0.0.1:18510/mcp",
        headers={},
        connected_project_id=12,
        headers_provider=lambda: checks.append(True) or {"Cookie": "session"},
    )
    for project, expected_error in [(12, False), (13, True)]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "workspace.startSession", "arguments": {"project_id": project}},
        }
        response = json.loads(
            proxy.handle(None, payload=payload, line=json.dumps(payload), request_id=1)
        )
        assert response["result"]["isError"] is expected_error
    assert proxy.scoped_project_id == 12
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        )
    ) as client:
        proxy.request_daemon(client, '{"jsonrpc":"2.0","id":1,"method":"ping"}')
    assert checks == [True, True, True]


def test_session_repr_and_malformed_record_do_not_expose_cookie(selected):
    session = save(selected)
    session.path.write_text('{"cookie":"private-secret"}')
    with pytest.raises(ValueError) as error:
        session.load()
    assert "private-secret" not in str(error.value)


def test_download_direct_read_policy_does_not_require_a_run(monkeypatch):
    proxy = AgentBridgeProxy(url="http://127.0.0.1:18510/mcp", headers={}, connected_project_id=12)
    proxy.tool_catalog = {
        "artifact.download": {
            "inputSchema": {"properties": {"project_id": {"type": "integer"}}},
            "_meta": {"operation_name": "artifact.download", "grant_policy": "direct-read"},
        }
    }
    monkeypatch.setattr(proxy, "_validate_project", lambda *_: None)
    calls = []

    def request(_client, body):
        calls.append(json.loads(body))
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "structuredContent": {"download_uri": "/owned/file"},
                    "isError": False,
                },
            }
        )

    monkeypatch.setattr(proxy, "request_daemon", request)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {"tool_name": "artifact.download", "arguments": {"artifact_id": 3}},
        },
    }
    response = json.loads(
        proxy.handle(None, payload=payload, line=json.dumps(payload), request_id=1)
    )
    assert not response["result"]["isError"]
    assert len(calls) == 1 and calls[0]["params"]["name"] == "artifact.download"
