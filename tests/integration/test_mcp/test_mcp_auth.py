"""MCP tests for the generic StackOS auth-provider boundary."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from stackos.auth import derive_ui_token
from tests.integration.test_repositories.test_telegram_native_authorization import _Runtime

from .conftest import MCPClient
from .test_mcp_bridge_agent_path import (
    _initialize,
    _scoped_bridge,
    _send,
    _structured,
    _toolbox_call,
)


def _create_firecrawl_credential(mcp: MCPClient, project_id: int) -> dict:
    response = mcp.test_client.post(
        "/api/v1/auth/accounts/firecrawl",
        json={
            "auth_method_key": "api_key",
            "display_name": "Primary Firecrawl",
            "fields": {"api_key": "fc-secret"},
            "attach_project_id": project_id,
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return response.json()["data"]


def test_auth_status_and_test_return_sanitized_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    _create_firecrawl_credential(mcp_client, project_id)

    status = mcp_client.call_tool_structured(
        "connection.list",
        {
            "project_id": project_id,
            "provider_key": "firecrawl",
            "response_mode": "raw",
        },
    )

    credential_ref = status["accounts"][0]["credential_ref"]
    assert credential_ref.startswith("cred_")
    rendered_status = json.dumps(status)
    assert "fc-secret" not in rendered_status
    assert "encrypted_payload" not in rendered_status
    assert "secret_payload" not in rendered_status

    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# ok"}},
    )
    tested = mcp_client.call_tool_structured(
        "account.test",
        {
            "credential_ref": credential_ref,
            "response_mode": "raw",
        },
    )

    assert tested["data"]["ok"] is True
    assert tested["data"]["provider_key"] == "firecrawl"
    assert tested["data"]["credential_ref"] == credential_ref
    assert "fc-secret" not in json.dumps(tested)


def test_local_admin_auth_mutations_are_not_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    for tool_name, arguments in [
        ("account.start", {"provider_key": "firecrawl"}),
        ("account.revoke", {"credential_ref": "cred_missing"}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32007
        assert err["message"] == "ToolNotGrantedError"


def test_attached_telegram_session_status_is_exposed_through_mcp_and_rest(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    fake_runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateReady"},
            {"@type": "authorizationStateReady"},
        ]
    )
    mcp_client.test_client.app.state.operation_services["telegram_runtime"] = fake_runtime
    created = mcp_client.test_client.post(
        "/api/v1/auth/accounts/telegram",
        json={
            "auth_method_key": "tdlib-bot-token",
            "display_name": "Session surface bot",
            "fields": {
                "api_id": 12345,
                "api_hash": "application-hash",
                "bot_token": "123456:secret-bot-token",
                "proxy_enabled": False,
            },
            "attach_project_id": project_id,
        },
        headers=mcp_client._headers(),
    )
    created.raise_for_status()
    credential_ref = created.json()["data"]["credential_ref"]
    assert created.json()["data"]["status"] == "disconnected"
    assert created.json()["data"]["setup_required"] is False
    assert fake_runtime.closed == [(credential_ref, 1)]
    path = f"/api/v1/projects/{project_id}/connections/accounts/{credential_ref}/session"
    rest = mcp_client.test_client.get(path, headers=mcp_client._headers())
    rest.raise_for_status()
    assert rest.json()["desired_connected"] is False
    assert rest.json()["connected"] is False
    assert rest.json()["project_ids"] == [project_id]
    ui_headers = {"Authorization": f"Bearer {derive_ui_token(mcp_client.auth_token)}"}
    ui_status = mcp_client.test_client.get(path, headers=ui_headers)
    ui_status.raise_for_status()
    assert ui_status.json() == rest.json()
    ui_connect = mcp_client.test_client.post(f"{path}/connect", headers=ui_headers)
    ui_connect.raise_for_status()
    assert ui_connect.json()["data"]["desired_connected"] is True
    assert ui_connect.json()["data"]["connected"] is True
    ui_disconnect = mcp_client.test_client.post(f"{path}/disconnect", headers=ui_headers)
    ui_disconnect.raise_for_status()
    assert ui_disconnect.json()["data"]["status"] == "disconnected"

    tools = {tool["name"] for tool in mcp_client.list_tools()}
    assert {
        "account.session.status",
        "account.session.connect",
        "account.session.disconnect",
    } <= tools
    mcp_status = mcp_client.call_tool_structured(
        "account.session.status",
        {
            "project_id": project_id,
            "credential_ref": credential_ref,
            "response_mode": "raw",
        },
    )
    assert mcp_status == rest.json()
    assert "secret-bot-token" not in json.dumps(mcp_status)

    mcp_client.test_client.app.state.operation_services["telegram_runtime"] = _Runtime(
        states=[{"@type": "authorizationStateReady"}]
    )
    mcp_connect = mcp_client.call_tool_structured(
        "account.session.connect",
        {
            "project_id": project_id,
            "credential_ref": credential_ref,
            "response_mode": "raw",
        },
    )
    assert mcp_connect["data"]["connected"] is True
    assert mcp_connect["data"]["desired_connected"] is True
    mcp_disconnect = mcp_client.call_tool_structured(
        "account.session.disconnect",
        {
            "project_id": project_id,
            "credential_ref": credential_ref,
            "response_mode": "raw",
        },
    )
    assert mcp_disconnect["data"]["connected"] is False
    assert mcp_disconnect["data"]["desired_connected"] is False
    assert "secret-bot-token" not in json.dumps([mcp_connect, mcp_disconnect])


def test_telegram_account_test_requires_project_attachment_for_mcp(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateReady"},
            {"@type": "authorizationStateReady"},
        ]
    )
    mcp_client.test_client.app.state.operation_services["telegram_runtime"] = runtime
    created = mcp_client.test_client.post(
        "/api/v1/auth/accounts/telegram",
        json={
            "auth_method_key": "tdlib-user-session",
            "display_name": "Attached Telegram user",
            "fields": {"api_id": 12345, "api_hash": "application-hash"},
            "attach_project_id": project_id,
        },
        headers=mcp_client._headers(),
    )
    created.raise_for_status()
    credential_ref = created.json()["data"]["credential_ref"]
    signed_in = mcp_client.test_client.post(
        "/api/v1/auth/accounts/telegram/start",
        json={"credential_ref": credential_ref, "authorization_mode": "phone"},
        headers=mcp_client._headers(),
    )
    signed_in.raise_for_status()
    assert signed_in.json()["data"]["status"] == "disconnected"

    tested = mcp_client.call_tool_structured(
        "account.test",
        {"project_id": project_id, "credential_ref": credential_ref, "response_mode": "raw"},
    )
    assert tested["data"]["ok"] is True
    assert tested["project_id"] == project_id
    assert runtime.native_requests == [{"@type": "getMe"}, {"@type": "getMe"}]

    detached = mcp_client.test_client.post(
        "/api/v1/auth/accounts/telegram",
        json={
            "auth_method_key": "tdlib-user-session",
            "display_name": "Detached Telegram user",
            "fields": {"api_id": 12345, "api_hash": "application-hash"},
        },
        headers=mcp_client._headers(),
    )
    detached.raise_for_status()
    detached_ref = detached.json()["data"]["credential_ref"]
    denied = mcp_client.call_tool_error(
        "account.test", {"project_id": project_id, "credential_ref": detached_ref}
    )
    assert "not attached" in json.dumps(denied)
    assert len(runtime.configurations) == 2


def test_workspace_bridge_injects_project_scope_for_telegram_account_test(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    root = "/tmp/telegram-account-test-bridge-project"
    fingerprint = "path:telegram-account-test-bridge"
    bound = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": fingerprint,
            "last_known_root": root,
        },
    )
    assert bound["project_id"] == project_id

    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateReady"},
            {"@type": "authorizationStateReady"},
        ]
    )
    mcp_client.test_client.app.state.operation_services["telegram_runtime"] = runtime
    created = mcp_client.test_client.post(
        "/api/v1/auth/accounts/telegram",
        json={
            "auth_method_key": "tdlib-user-session",
            "display_name": "Bridge-scoped Telegram user",
            "fields": {"api_id": 12345, "api_hash": "application-hash"},
            "attach_project_id": project_id,
        },
        headers=mcp_client._headers(),
    )
    created.raise_for_status()
    credential_ref = created.json()["data"]["credential_ref"]
    signed_in = mcp_client.test_client.post(
        "/api/v1/auth/accounts/telegram/start",
        json={"credential_ref": credential_ref, "authorization_mode": "phone"},
        headers=mcp_client._headers(),
    )
    signed_in.raise_for_status()

    proxy, client = _scoped_bridge(mcp_client, cwd=root, repo_fingerprint=fingerprint)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="telegram-tools")
    assert proxy.scoped_project_id == project_id
    tested_envelope = _toolbox_call(
        proxy,
        client,
        "account.test",
        {"credential_ref": credential_ref, "response_mode": "raw"},
        request_id="telegram-test",
    )
    assert tested_envelope["result"]["isError"] is False
    tested = _structured(tested_envelope)
    assert tested["data"]["ok"] is True
    assert tested["project_id"] == project_id
    assert runtime.native_requests == [{"@type": "getMe"}, {"@type": "getMe"}]
    assert "application-hash" not in json.dumps(tested)

    cross_project = _toolbox_call(
        proxy,
        client,
        "account.test",
        {"project_id": project_id + 1, "credential_ref": credential_ref},
        request_id="telegram-cross-project-test",
    )
    assert "Bridge refused cross-project agent call" in json.dumps(cross_project)
    assert len(runtime.native_requests) == 2


def test_removed_integration_secret_mcp_tools_are_not_registered(
    mcp_client: MCPClient,
) -> None:
    for tool_name, arguments in [
        ("integration.set", {"project_id": 1, "kind": "firecrawl", "secret_payload": "x"}),
        ("integration.remove", {"credential_id": 1}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32601
        assert err["message"] == "MethodNotFoundError"
