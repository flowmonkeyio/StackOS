"""MCP tests for StackOS plugin/catalog tools."""

from __future__ import annotations

from .conftest import MCPClient


def test_plugin_catalog_read_tools_are_callable(mcp_client: MCPClient) -> None:
    plugins = mcp_client.call_tool_structured("plugin.list", {})["items"]
    assert [p["slug"] for p in plugins] == ["core", "publishing", "seo", "utils"]

    catalog = mcp_client.call_tool_structured("catalog.describe", {"plugin_slug": "utils"})
    assert catalog["plugins"][0]["plugin"]["slug"] == "utils"
    assert {r["key"] for r in catalog["plugins"][0]["resources"]} >= {
        "generated-image",
        "web-document",
    }

    capabilities = mcp_client.call_tool_structured("capability.list", {"plugin_slug": "seo"})[
        "items"
    ]
    assert {cap["key"] for cap in capabilities} >= {"seo-content", "seo-research"}

    provider = mcp_client.call_tool_structured(
        "provider.describe",
        {"plugin_slug": "utils", "key": "openai-images"},
    )
    assert provider["auth_type"] == "api-key"

    publishing = mcp_client.call_tool_structured(
        "catalog.describe",
        {"plugin_slug": "publishing"},
    )
    assert {a["key"] for a in publishing["plugins"][0]["actions"]} >= {
        "wordpress.post.create",
        "ghost.post.create",
    }


def test_plugin_enable_is_registered_but_not_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]

    err = mcp_client.call_tool_error(
        "plugin.enable",
        {"project_id": pid, "plugin_slug": "utils"},
    )

    assert err["code"] == -32007
    assert err["data"]["tool"] == "plugin.enable"
