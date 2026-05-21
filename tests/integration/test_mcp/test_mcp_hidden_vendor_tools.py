"""Hidden vendor tools are not directly agent-callable."""

from __future__ import annotations

from .conftest import MCPClient


def _start_run_for_skill(mcp: MCPClient, project_id: int, skill_name: str) -> str:
    env = mcp.call_tool_structured(
        "run.start",
        {
            "project_id": project_id,
            "kind": "run-plan",
            "skill_name": skill_name,
        },
    )
    return env["data"]["run_token"]


def test_hidden_vendor_tool_requires_grant(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    err = mcp_client.call_tool_error(
        "jina.read",
        {"project_id": project_id, "url": "https://example.com"},
    )

    assert err["code"] == -32007
    assert err["message"] == "ToolNotGrantedError"
    assert err["data"]["tool"] == "jina.read"
    assert err["data"]["skill"] == "__system__"


def test_removed_legacy_skill_names_do_not_grant_vendor_tools(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    token = _start_run_for_skill(mcp_client, project_id, "01-research/serp-analyzer")

    err = mcp_client.call_tool_error(
        "jina.read",
        {
            "project_id": project_id,
            "url": "https://example.com",
            "run_token": token,
        },
    )

    assert err["code"] == -32007
    assert err["data"]["skill"] == "01-research/serp-analyzer"
