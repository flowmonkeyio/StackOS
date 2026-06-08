"""``tools/list`` returns the full catalog with stable shape and counts.

Per the M3 deliverables, the registered tool list is the canonical
contract; this test pins both the namespace counts and the JSON-Schema
well-formedness so a future refactor can't silently drop a tool or
break a tool's input/output schema.
"""

from __future__ import annotations

from .conftest import MCPClient

# Per-namespace expected counts — derived from the clean StackOS core catalog.
# Update intentionally when adding generic StackOS MCP tools.
EXPECTED_NAMESPACE_COUNTS = {
    "action": 5,
    "agentPreset": 3,
    "agentRequest": 9,
    "artifact": 3,
    "auth": 4,
    "budget": 4,
    "catalog": 2,
    "capability": 2,
    "communication": 2,
    "communicationContact": 2,
    "communicationContext": 1,
    "communicationMembership": 2,
    "communicationProfile": 3,
    "communicationRoute": 2,
    "communicationSurface": 2,
    "communicationTarget": 3,
    "context": 3,
    "cost": 2,
    "decision": 2,
    "executionContext": 10,
    "experiment": 4,
    "ingressEndpoint": 5,
    "integration": 1,
    "learning": 3,
    "localAgentChat": 1,
    "meta": 1,
    "plugin": 3,
    "operation": 2,
    "project": 5,
    "provider": 2,
    "resource": 3,
    "run": 12,
    "readiness": 1,
    "runPlan": 12,
    "schedule": 4,
    "sitemap": 1,
    "skillPreset": 3,
    "toolProfile": 1,
    "tracker": 21,
    "workflowExtension": 5,
    "workflowTemplate": 5,
    "workspace": 6,
}

# Total = sum of values; locked here to flag accidental drops.
EXPECTED_TOTAL = sum(EXPECTED_NAMESPACE_COUNTS.values())
# Total follows EXPECTED_NAMESPACE_COUNTS; update intentionally when adding tools.
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
    "schema",
    "source",
    "target",
    "topic",
    "voice",
    "wordpress",
}


def test_initialize_handshake_succeeds(mcp_client: MCPClient) -> None:
    """The ``initialize`` JSON-RPC method returns the server info."""
    result = mcp_client.initialize()
    assert "result" in result
    server_info = result["result"]["serverInfo"]
    assert server_info["name"] == "stackos"


def test_tools_list_returns_full_catalog(mcp_client: MCPClient) -> None:
    """The ``tools/list`` method returns every registered tool."""
    tools = mcp_client.list_tools()
    assert len(tools) == EXPECTED_TOTAL


def test_tools_list_namespace_counts_are_stable(mcp_client: MCPClient) -> None:
    """Per-namespace tool counts match the locked expectations."""
    tools = mcp_client.list_tools()
    counts: dict[str, int] = {}
    for t in tools:
        ns = t["name"].split(".", 1)[0]
        counts[ns] = counts.get(ns, 0) + 1
    assert counts == EXPECTED_NAMESPACE_COUNTS
    assert FORBIDDEN_LEGACY_NAMESPACES.isdisjoint(counts)


def test_every_tool_has_well_formed_input_schema(mcp_client: MCPClient) -> None:
    """Every tool's ``inputSchema`` is a valid JSON Schema object."""
    tools = mcp_client.list_tools()
    for t in tools:
        schema = t["inputSchema"]
        assert isinstance(schema, dict)
        assert "type" in schema or "$ref" in schema or "properties" in schema, (
            f"{t['name']!r} input schema malformed: {schema}"
        )


def test_every_tool_has_well_formed_output_schema(mcp_client: MCPClient) -> None:
    """Every tool's ``outputSchema`` is a valid JSON Schema object."""
    tools = mcp_client.list_tools()
    for t in tools:
        schema = t["outputSchema"]
        assert isinstance(schema, dict)
        # Must have at least one of ``type``, ``$ref``, ``properties``,
        # ``items`` (list returns), or ``$defs``.
        keys = set(schema.keys())
        assert keys & {"type", "$ref", "properties", "items", "$defs", "anyOf", "oneOf"}, (
            f"{t['name']!r} output schema malformed: {schema}"
        )


def test_every_tool_has_description(mcp_client: MCPClient) -> None:
    """Every tool's description is non-empty (becomes MCP description)."""
    tools = mcp_client.list_tools()
    for t in tools:
        assert t.get("description"), f"{t['name']!r} has no description"
        assert len(t["description"]) > 5, f"{t['name']!r} description is too short"


def test_streaming_meta_matches_registered_core_tools(mcp_client: MCPClient) -> None:
    """No removed SEO/content streaming tool remains advertised."""
    tools = mcp_client.list_tools()
    streaming_names = {t["name"] for t in tools if (t.get("_meta") or {}).get("streaming") is True}
    assert not streaming_names
