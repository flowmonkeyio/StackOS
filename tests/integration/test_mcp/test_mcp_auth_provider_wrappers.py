"""MCP tests for daemon-owned auth-provider wrapper probes."""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from .conftest import MCPClient


def _create_integration_credential(
    mcp: MCPClient,
    *,
    project_id: int,
    kind: str,
    payload: bytes,
    config_json: dict | None = None,
) -> dict:
    text_payload = payload.decode("utf-8")
    if kind == "wordpress":
        parsed = json.loads(text_payload)
        fields = {
            "username": parsed["username"],
            "application_password": parsed["application_password"],
            "wp_url": str((config_json or {})["wp_url"]),
        }
        body = {"auth_method_key": "application_password", "fields": fields}
    elif kind == "ghost":
        fields = {
            "admin_api_key": text_payload,
            "ghost_url": str((config_json or {})["ghost_url"]),
            "api_version": str((config_json or {}).get("api_version") or "v5.0"),
        }
        body = {"auth_method_key": "admin_api_key", "fields": fields}
    else:
        body = {"auth_method_key": "api_key", "fields": {"api_key": text_payload}}
    response = mcp.test_client.post(
        f"/api/v1/auth/accounts/{kind}",
        json={
            **body,
            "display_name": f"{kind} - Default",
            "attach_project_id": project_id,
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return response.json()


def _credential_ref(mcp: MCPClient, *, project_id: int, provider_key: str) -> str:
    status = mcp.call_tool_structured(
        "connection.list",
        {
            "project_id": project_id,
            "provider_key": provider_key,
            "response_mode": "raw",
        },
    )
    return status["accounts"][0]["credential_ref"]


def test_auth_test_dispatches_to_firecrawl(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# ok"}},
    )

    project_id = seeded_project["data"]["id"]
    _create_integration_credential(
        mcp_client,
        project_id=project_id,
        kind="firecrawl",
        payload=b"fc-key",
    )
    credential_ref = _credential_ref(mcp_client, project_id=project_id, provider_key="firecrawl")

    out = mcp_client.call_tool_structured(
        "account.test",
        {"credential_ref": credential_ref, "response_mode": "raw"},
    )

    assert out["data"]["ok"] is True
    assert out["data"]["provider_key"] == "firecrawl"
    assert out["data"]["credential_ref"] == credential_ref
    assert "fc-key" not in json.dumps(out)


@pytest.mark.parametrize("response_mode", ["compact", "raw"])
def test_failed_probe_diagnostics_survive_mcp_and_rest_inventory(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch,
    response_mode: str,
) -> None:
    class Probe:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def test_credentials(self) -> dict:
            return {
                "ok": False,
                "status": "failed",
                "summary": "Provider denied the account probe.",
                "next_action": "Review the key permissions and test again.",
                "retryable": False,
                "metadata": {"provider_status_code": 403, "request_id": "req_fixture"},
            }

    monkeypatch.setattr(
        "stackos.auth_providers.repository.integration_class_for", lambda _key: Probe
    )
    project_id = seeded_project["data"]["id"]
    _create_integration_credential(
        mcp_client, project_id=project_id, kind="firecrawl", payload=b"fc-private-fixture"
    )
    ref = _credential_ref(mcp_client, project_id=project_id, provider_key="firecrawl")
    tested = mcp_client.call_tool_structured(
        "account.test", {"credential_ref": ref, "response_mode": "raw"}
    )["data"]
    assert tested["ok"] is False
    assert tested["retryable"] is False
    for operation in ("account.list", "connection.list"):
        arguments = {"provider_key": "firecrawl", "response_mode": response_mode}
        if operation == "connection.list":
            arguments["project_id"] = project_id
        listed = mcp_client.call_tool_structured(
            operation,
            arguments,
        )
        listed = listed.get("data", listed)
        assert listed["accounts"][0]["last_test"] == tested
        assert listed["accounts"][0]["status"] == "connected"
    for path in (
        "/api/v1/auth/accounts?provider_key=firecrawl",
        f"/api/v1/projects/{project_id}/connections/accounts?provider_key=firecrawl",
    ):
        response = mcp_client.test_client.get(path, headers=mcp_client._headers())
        assert response.status_code == 200
        assert response.json()["accounts"][0]["last_test"] == tested
        assert "fc-private-fixture" not in response.text


@pytest.mark.parametrize("response_mode", [None, "compact", "raw"])
def test_imap_failed_probe_is_actionable_in_compact_account_inventory(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
    response_mode: str | None,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("private IMAP transcript with fixture-password")

    monkeypatch.setattr("stackos.integrations.imap.imaplib.IMAP4_SSL", unavailable)
    project_id = seeded_project["data"]["id"]
    created = mcp_client.test_client.post(
        "/api/v1/auth/accounts/imap",
        json={
            "display_name": "IMAP isolated diagnostic fixture",
            "auth_method_key": "imap-password",
            "attach_project_id": project_id,
            "fields": {
                "host": "imap.example.test",
                "port": 993,
                "tls_mode": "ssl",
                "username": "fixture",
                "password": "fixture-password",
                "default_mailbox": "INBOX",
            },
        },
        headers=mcp_client._headers(),
    )
    created.raise_for_status()
    ref = _credential_ref(mcp_client, project_id=project_id, provider_key="imap")
    arguments = {"credential_ref": ref}
    if response_mode is not None:
        arguments["response_mode"] = response_mode
    tested = mcp_client.call_tool_structured("account.test", arguments)["data"]

    assert tested["ok"] is False
    assert tested["retryable"] is True, tested
    assert tested["metadata"] == {"stage": "connect", "reason_code": "connection_refused"}
    assert "connect" in tested["summary"].lower()
    assert tested["next_action"]
    for operation in ("account.list", "connection.list"):
        arguments = {"provider_key": "imap", "response_mode": "compact"}
        if operation == "connection.list":
            arguments["project_id"] = project_id
        listed = mcp_client.call_tool_structured(operation, arguments)
        account = listed.get("data", listed)["accounts"][0]
        assert account["status"] == "connected"
        assert account["last_test"] == tested
        assert "fixture-password" not in json.dumps(listed)
        assert "private IMAP transcript" not in json.dumps(listed)


def test_auth_test_dispatches_to_wordpress_provider_manifest(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    httpx_mock.add_response(
        method="GET",
        url="https://wp.example/wp-json/wp/v2/users/me?context=edit",
        json={"id": 7, "name": "Editor", "roles": ["editor"]},
    )
    _create_integration_credential(
        mcp_client,
        project_id=project_id,
        kind="wordpress",
        payload=json.dumps({"username": "editor", "application_password": "app pass"}).encode(
            "utf-8"
        ),
        config_json={"wp_url": "https://wp.example"},
    )
    credential_ref = _credential_ref(mcp_client, project_id=project_id, provider_key="wordpress")

    out = mcp_client.call_tool_structured(
        "account.test",
        {"credential_ref": credential_ref, "response_mode": "raw"},
    )

    rendered = json.dumps(out)
    assert out["data"]["ok"] is True
    assert out["data"]["provider_key"] == "wordpress"
    assert out["data"]["metadata"]["user_id"] == 7
    assert "app pass" not in rendered


def test_auth_test_dispatches_to_ghost_provider_manifest(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    httpx_mock.add_response(
        method="GET",
        url="https://ghost.example/ghost/api/admin/users/?limit=1&include=roles",
        json={"users": [{"id": "u1", "name": "Editor", "roles": [{"name": "Editor"}]}]},
    )
    _create_integration_credential(
        mcp_client,
        project_id=project_id,
        kind="ghost",
        payload=b"keyid:00112233445566778899aabbccddeeff",
        config_json={"ghost_url": "https://ghost.example", "api_version": "v5.0"},
    )
    credential_ref = _credential_ref(mcp_client, project_id=project_id, provider_key="ghost")

    out = mcp_client.call_tool_structured(
        "account.test",
        {"credential_ref": credential_ref, "response_mode": "raw"},
    )

    rendered = json.dumps(out)
    assert out["data"]["ok"] is True
    assert out["data"]["provider_key"] == "ghost"
    assert out["data"]["metadata"]["user_id"] == "u1"
    assert "00112233445566778899aabbccddeeff" not in rendered


def test_auth_test_validates_unknown_kind(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    response = mcp_client.test_client.post(
        "/api/v1/auth/accounts/unknown-vendor",
        json={
            "display_name": "Unknown - Default",
            "auth_method_key": "api_key",
            "fields": {"api_key": "x"},
            "attach_project_id": project_id,
        },
        headers=mcp_client._headers(),
    )

    assert response.status_code == 404


def test_local_admin_auth_mutations_are_not_agent_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    for tool_name, arguments in [
        ("account.start", {"provider_key": "firecrawl"}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32007
        assert err["message"] == "ToolNotGrantedError"


def test_removed_integration_mcp_tools_are_not_registered(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    listed = mcp_client.call_tool_structured("integration.list", {"project_id": project_id})
    assert listed["project_id"] == project_id

    for tool_name, arguments in [
        (
            "integration.set",
            {"project_id": project_id, "kind": "firecrawl", "secret_payload": "x"},
        ),
        ("integration.test", {"project_id": project_id, "credential_id": 1}),
        ("integration.remove", {"credential_id": 1}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32601
        assert err["message"] == "MethodNotFoundError"
