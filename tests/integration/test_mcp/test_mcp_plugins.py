"""MCP tests for StackOS plugin/catalog tools."""

from __future__ import annotations

from .conftest import MCPClient


def test_plugin_catalog_read_tools_are_callable(mcp_client: MCPClient) -> None:
    plugins = mcp_client.call_tool_structured("plugin.list", {})["items"]
    assert [p["slug"] for p in plugins] == [
        "engineering",
        "support",
        "communications",
        "gtm",
        "marketing",
        "branding",
        "media-buying",
        "trackbooth",
        "shopify",
        "publishing",
        "seo",
        "core",
        "utils",
    ]

    catalog = mcp_client.call_tool_structured(
        "catalog.describe",
        {"plugin_slug": "utils", "response_mode": "raw"},
    )
    assert catalog["plugins"][0]["plugin"]["slug"] == "utils"
    assert {r["key"] for r in catalog["plugins"][0]["resources"]} >= {
        "generated-image",
        "web-document",
    }

    capabilities = mcp_client.call_tool_structured("capability.list", {"plugin_slug": "seo"})[
        "items"
    ]
    assert {cap["key"] for cap in capabilities} >= {"seo-content", "seo-research"}

    gtm = mcp_client.call_tool_structured(
        "catalog.describe",
        {"plugin_slug": "gtm", "response_mode": "raw"},
    )
    assert {a["key"] for a in gtm["plugins"][0]["actions"]} >= {
        "hubspot.crm.companies.batch_upsert",
        "outreach.sequence_state.create",
        "custom_gtm.pipeline.fetch",
    }
    gtm_action = next(
        action
        for action in gtm["plugins"][0]["actions"]
        if action["key"] == "hubspot.crm.companies.batch_upsert"
    )
    assert gtm_action["connector_key"] == "hubspot"
    assert gtm_action["availability"]["status"] == "unknown"

    media = mcp_client.call_tool_structured(
        "catalog.describe",
        {"plugin_slug": "media-buying", "response_mode": "raw"},
    )
    assert {a["key"] for a in media["plugins"][0]["actions"]} >= {
        "meta.campaign.create",
        "meta.ad_set.create",
        "google.campaign.create",
        "custom_media.campaign.create",
    }
    media_action = next(
        action
        for action in media["plugins"][0]["actions"]
        if action["key"] == "meta.campaign.create"
    )
    assert media_action["connector_key"] == "meta-ads"
    assert media_action["availability"]["status"] == "unknown"

    provider = mcp_client.call_tool_structured(
        "provider.describe",
        {
            "plugin_slug": "utils",
            "key": "openai-images",
            "response_mode": "raw",
        },
    )
    assert provider["auth_type"] == "api-key"

    publishing = mcp_client.call_tool_structured(
        "catalog.describe",
        {"plugin_slug": "publishing", "response_mode": "raw"},
    )
    assert {a["key"] for a in publishing["plugins"][0]["actions"]} >= {
        "wordpress.post.create",
        "ghost.post.create",
    }

    branding = mcp_client.call_tool_structured(
        "catalog.describe",
        {"plugin_slug": "branding", "response_mode": "raw"},
    )
    assert {r["key"] for r in branding["plugins"][0]["resources"]} >= {
        "evidence-item",
        "content-piece",
        "distribution-record",
    }
    assert {a["key"] for a in branding["plugins"][0]["actions"]} >= {
        "publish.git-blog",
        "evidence.capture",
        "distribution.snapshot",
    }

    engineering = mcp_client.call_tool_structured(
        "catalog.describe",
        {"plugin_slug": "engineering", "response_mode": "raw"},
    )
    assert {r["key"] for r in engineering["plugins"][0]["resources"]} >= {
        "engineering-decision",
        "engineering-evidence",
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
