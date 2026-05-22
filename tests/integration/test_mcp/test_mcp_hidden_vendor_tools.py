"""Legacy vendor MCP tools are removed from the daemon catalog."""

from __future__ import annotations

import pytest

from content_stack.mcp.server import ToolRegistry
from content_stack.mcp.tools import register_all

from .conftest import MCPClient

LEGACY_VENDOR_NAMESPACES = {
    "ahrefs",
    "dataforseo",
    "firecrawl",
    "googlePaa",
    "jina",
    "openaiImages",
    "reddit",
}
LEGACY_VENDOR_TOOL_CALLS = [
    (
        "dataforseo.serp",
        {"project_id": 1, "keyword": "best crm software"},
    ),
    (
        "firecrawl.scrape",
        {"project_id": 1, "url": "https://example.com"},
    ),
    (
        "openaiImages.generate",
        {"project_id": 1, "prompt": "hero image"},
    ),
    (
        "jina.read",
        {"project_id": 1, "url": "https://example.com"},
    ),
    (
        "ahrefs.keywordsForSite",
        {"project_id": 1, "target": "example.com"},
    ),
    (
        "googlePaa.extract",
        {"project_id": 1, "query": "content marketing"},
    ),
    (
        "reddit.search",
        {"project_id": 1, "subreddit": "seo", "query": "content"},
    ),
]


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


@pytest.mark.parametrize(("tool_name", "arguments"), LEGACY_VENDOR_TOOL_CALLS)
def test_legacy_vendor_tool_is_unknown(
    mcp_client: MCPClient,
    seeded_project: dict,
    tool_name: str,
    arguments: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    arguments = {**arguments, "project_id": project_id}

    err = mcp_client.call_tool_error(tool_name, arguments)

    assert err["code"] == -32601


def test_daemon_registry_does_not_contain_legacy_vendor_namespaces() -> None:
    registry = ToolRegistry()
    register_all(registry)

    namespaces = {name.split(".", 1)[0] for name in registry._tools}

    assert LEGACY_VENDOR_NAMESPACES.isdisjoint(namespaces)


@pytest.mark.parametrize(("tool_name", "arguments"), LEGACY_VENDOR_TOOL_CALLS)
def test_removed_legacy_skill_names_do_not_restore_vendor_tools(
    mcp_client: MCPClient,
    seeded_project: dict,
    tool_name: str,
    arguments: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    token = _start_run_for_skill(mcp_client, project_id, "01-research/serp-analyzer")
    arguments = {**arguments, "project_id": project_id, "run_token": token}

    err = mcp_client.call_tool_error(tool_name, arguments)

    assert err["code"] == -32601
