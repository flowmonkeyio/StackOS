"""``tools/list`` returns the operation registry's full MCP catalog.

One discovery scenario verifies adapter completeness, critical domains,
JSON-Schema well-formedness, descriptions, and streaming metadata. Legacy
handshake and modern self-contained requests remain separate protocol checks.
"""

from __future__ import annotations

import pytest

from stackos.operations.registry import build_operation_registry

from .conftest import MODERN_PROTOCOL_VERSION, MCPClient

# Keep the domain contract explicit; exact operation names come from their
# canonical owner rather than a second set of stale namespace totals.
EXPECTED_NAMESPACES = {
    "action",
    "actionCall",
    "agentPreset",
    "agentRequest",
    "artifact",
    "account",
    "browser",
    "budget",
    "catalog",
    "capability",
    "communication",
    "communicationContact",
    "communicationContext",
    "communicationMembership",
    "communicationProfile",
    "communicationRoute",
    "communicationSurface",
    "communicationTarget",
    "connection",
    "context",
    "cost",
    "decision",
    "executionContext",
    "experiment",
    "guide",
    "hostMcp",
    "ingressEndpoint",
    "integration",
    "learning",
    "localAgentChat",
    "meta",
    "plugin",
    "operation",
    "project",
    "provider",
    "resource",
    "run",
    "readiness",
    "runPlan",
    "schedule",
    "schema",
    "secret",
    "sitemap",
    "skillPreset",
    "toolProfile",
    "tracker",
    "workflowExtension",
    "workflowTemplate",
    "workspace",
}

CRITICAL_OPERATION_NAMES = {
    "account.session.connect",
    "account.session.disconnect",
    "account.session.status",
    "action.execute",
    "action.run",
    "actionCall.cancel",
    "actionCall.items",
    "actionCall.pause",
    "actionCall.resume",
    "actionCall.retry",
    "browser.session.start",
    "browser.session.stop",
    "communication.send",
    "communication.sendBatch",
    "operation.list",
    "operation.describe",
    "secret.set",
    "workspace.startSession",
    "workspace.resolve",
}

FORBIDDEN_LEGACY_NAMESPACES = {
    "article",
    "asset",
    "author",
    "ahrefs",
    "cluster",
    "compliance",
    "dataforseo",
    "drift",
    "eeat",
    "firecrawl",
    "ghost",
    "googlePaa",
    "gsc",
    "gscOauth",
    "interlink",
    "jina",
    "openaiImages",
    "publish",
    "reddit",
    "redirect",
    "source",
    "target",
    "topic",
    "voice",
    "wordpress",
}


@pytest.fixture(scope="module")
def expected_mcp_names() -> set[str]:
    """MCP has no extra tools outside the canonical operation registry."""
    return {operation.name for operation in build_operation_registry().by_surface("mcp")}


def test_initialize_handshake_succeeds(mcp_client: MCPClient) -> None:
    """The ``initialize`` JSON-RPC method returns the server info."""
    result = mcp_client.initialize()
    assert "result" in result
    server_info = result["result"]["serverInfo"]
    assert server_info["name"] == "stackos"


def test_modern_discovery_succeeds_without_initialize(mcp_client: MCPClient) -> None:
    """Protocol 2026-07-28 uses a self-contained discovery request."""
    envelope = mcp_client.modern_discover()

    assert mcp_client._initialized is False
    result = envelope["result"]
    assert result["supportedVersions"] == [MODERN_PROTOCOL_VERSION]
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["resultType"] == "complete"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "stackos"


def test_modern_tool_list_and_call_are_self_contained(
    mcp_client: MCPClient, expected_mcp_names: set[str]
) -> None:
    """Modern list/call requests work without session or handshake state."""
    tools = mcp_client.modern_list_tools()
    assert {tool["name"] for tool in tools} == expected_mcp_names
    assert len(tools) == len(expected_mcp_names), "Modern catalog contains duplicate tools"

    envelope = mcp_client.modern_call_tool("meta.enums", {"response_mode": "raw"})
    result = envelope["result"]
    assert result["isError"] is False
    assert result["resultType"] == "complete"
    assert "runs_status" in result["structuredContent"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "stackos"


def test_tools_list_catalog_contract(mcp_client: MCPClient, expected_mcp_names: set[str]) -> None:
    """One wire response proves catalog completeness and every tool's contract."""
    tools = mcp_client.list_tools()
    names = {tool["name"] for tool in tools}
    assert names == expected_mcp_names, (
        f"Missing MCP operations: {expected_mcp_names - names}; "
        f"unexpected tools: {names - expected_mcp_names}"
    )
    assert len(tools) == len(names), "Legacy catalog contains duplicate tools"
    assert names >= CRITICAL_OPERATION_NAMES
    namespaces = {name.split(".", 1)[0] for name in names}
    assert namespaces == EXPECTED_NAMESPACES
    assert FORBIDDEN_LEGACY_NAMESPACES.isdisjoint(namespaces)

    for t in tools:
        schema = t["inputSchema"]
        assert isinstance(schema, dict), f"{t['name']!r} input schema is not an object"
        assert "type" in schema or "$ref" in schema or "properties" in schema, (
            f"{t['name']!r} input schema malformed: {schema}"
        )
        schema = t["outputSchema"]
        assert isinstance(schema, dict), f"{t['name']!r} output schema is not an object"
        # Must have at least one of ``type``, ``$ref``, ``properties``,
        # ``items`` (list returns), or ``$defs``.
        keys = set(schema.keys())
        assert keys & {"type", "$ref", "properties", "items", "$defs", "anyOf", "oneOf"}, (
            f"{t['name']!r} output schema malformed: {schema}"
        )
        assert t.get("description"), f"{t['name']!r} has no description"
        assert len(t["description"]) > 5, f"{t['name']!r} description is too short"

    streaming_names = {t["name"] for t in tools if (t.get("_meta") or {}).get("streaming") is True}
    assert not streaming_names, f"Unexpected streaming tools: {streaming_names}"
