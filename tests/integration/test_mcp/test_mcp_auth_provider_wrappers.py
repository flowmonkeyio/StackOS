"""MCP tests for daemon-owned auth-provider wrapper probes."""

from __future__ import annotations

import json

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
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/{kind}/credentials",
        json={
            "plaintext_payload": payload.decode("utf-8"),
            "config_json": config_json,
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return response.json()


def _credential_ref(mcp: MCPClient, *, project_id: int, provider_key: str) -> str:
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": provider_key},
    )
    return status["connections"][0]["credential_ref"]


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
        "auth.test",
        {"project_id": project_id, "credential_ref": credential_ref},
    )

    assert out["data"]["ok"] is True
    assert out["data"]["provider_key"] == "firecrawl"
    assert out["data"]["credential_ref"] == credential_ref
    assert "fc-key" not in json.dumps(out)


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
        "auth.test",
        {"project_id": project_id, "credential_ref": credential_ref},
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
        "auth.test",
        {"project_id": project_id, "credential_ref": credential_ref},
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
        f"/api/v1/projects/{project_id}/auth/unknown-vendor/credentials",
        json={"plaintext_payload": "x"},
        headers=mcp_client._headers(),
    )

    assert response.status_code == 404


def test_local_admin_auth_mutations_are_not_agent_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    for tool_name, arguments in [
        ("auth.start", {"project_id": project_id, "provider_key": "firecrawl"}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32007
        assert err["message"] == "ToolNotGrantedError"


def test_removed_integration_mcp_tools_are_not_registered(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    for tool_name, arguments in [
        ("integration.list", {"project_id": project_id}),
        (
            "integration.set",
            {"project_id": project_id, "kind": "firecrawl", "plaintext_payload": "x"},
        ),
        ("integration.test", {"project_id": project_id, "credential_id": 1}),
        ("integration.remove", {"credential_id": 1}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32601
        assert err["message"] == "MethodNotFoundError"
