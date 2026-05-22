"""MCP tests for the generic StackOS auth-provider boundary."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from .conftest import MCPClient


def _create_firecrawl_credential(mcp: MCPClient, project_id: int) -> dict:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={
            "auth_method_key": "api_key",
            "label": "Primary Firecrawl",
            "fields": {"api_key": "fc-secret"},
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
        "auth.status",
        {"project_id": project_id, "provider_key": "firecrawl"},
    )

    credential_ref = status["connections"][0]["credential_ref"]
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
        "auth.test",
        {"project_id": project_id, "credential_ref": credential_ref},
    )

    assert tested["data"]["ok"] is True
    assert tested["data"]["provider_key"] == "firecrawl"
    assert tested["data"]["credential_ref"] == credential_ref
    assert "fc-secret" not in json.dumps(tested)


def test_local_admin_auth_mutations_are_not_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    for tool_name, arguments in [
        ("auth.start", {"project_id": project_id, "provider_key": "firecrawl"}),
        ("auth.revoke", {"project_id": project_id, "credential_ref": "cred_missing"}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32007
        assert err["message"] == "ToolNotGrantedError"


def test_removed_integration_secret_mcp_tools_are_not_registered(
    mcp_client: MCPClient,
) -> None:
    for tool_name, arguments in [
        ("integration.list", {"project_id": 1}),
        ("integration.set", {"project_id": 1, "kind": "firecrawl", "secret_payload": "x"}),
        ("integration.remove", {"credential_id": 1}),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32601
        assert err["message"] == "MethodNotFoundError"
