"""``tools/list`` returns the full catalog with stable shape and counts.

Per the M3 deliverables, the registered tool list is the canonical
contract; this test pins both the namespace counts and the JSON-Schema
well-formedness so a future refactor can't silently drop a tool or
break a tool's input/output schema.
"""

from __future__ import annotations

from .conftest import MCPClient

# Per-namespace expected counts — derived from the registrations in
# ``content_stack.mcp.tools``. Update intentionally when adding tools.
EXPECTED_NAMESPACE_COUNTS = {
    "ahrefs": 2,
    "article": 18,
    "artifact": 3,
    "asset": 4,
    "author": 5,
    "budget": 4,
    "catalog": 2,
    "capability": 2,
    "cluster": 3,
    "compliance": 4,
    "cost": 2,
    "dataforseo": 5,
    "drift": 4,
    "eeat": 8,
    "firecrawl": 4,
    "googlePaa": 1,
    "gsc": 9,
    "gscOauth": 2,
    "integration": 5,
    "interlink": 6,
    "jina": 1,
    "meta": 1,
    "openaiImages": 1,
    "plugin": 3,
    "procedure": 9,
    "project": 8,
    "provider": 2,
    "publish": 4,
    "redirect": 3,
    "reddit": 2,
    "resource": 3,
    "run": 14,
    "schedule": 4,
    "schema": 4,
    "sitemap": 1,
    "source": 3,
    "target": 5,
    "topic": 8,
    "voice": 4,
    "workspace": 5,
}

# Total = sum of values; locked here to flag accidental drops.
EXPECTED_TOTAL = sum(EXPECTED_NAMESPACE_COUNTS.values())
# 163 after adding agent-owned schedule.remove parity.


def test_initialize_handshake_succeeds(mcp_client: MCPClient) -> None:
    """The ``initialize`` JSON-RPC method returns the server info."""
    result = mcp_client.initialize()
    assert "result" in result
    server_info = result["result"]["serverInfo"]
    assert server_info["name"] == "content-stack"


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


def test_streaming_tools_declare_streaming_meta(mcp_client: MCPClient) -> None:
    """The four streaming tools per audit M-21 declare ``streaming=true``."""
    tools = mcp_client.list_tools()
    streaming_names = {t["name"] for t in tools if (t.get("_meta") or {}).get("streaming") is True}
    expected_streaming = {
        "topic.bulkCreate",
        "gsc.bulkIngest",
        "interlink.suggest",
        "procedure.run",
    }
    assert expected_streaming <= streaming_names
