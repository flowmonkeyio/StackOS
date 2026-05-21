"""MCP tests for StackOS action describe/validate tools."""

from __future__ import annotations

from .conftest import MCPClient


def test_action_describe_and_validate_are_read_only_discovery_tools(
    mcp_client: MCPClient,
) -> None:
    tools = {tool["name"] for tool in mcp_client.list_tools()}

    assert "action.describe" in tools
    assert "action.validate" in tools
    assert "action.execute" not in tools

    described = mcp_client.call_tool_structured(
        "action.describe",
        {"action_ref": "core.catalog.describe"},
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {"action_ref": "core.catalog.describe", "input_json": {}},
    )

    assert described["manifest"]["action_ref"] == "core.catalog.describe"
    assert described["execution_available"] is False
    assert described["agent_execute_available"] is False
    assert validation["valid"] is True
    assert validation["issues"] == []


def test_action_validate_rejects_raw_secret_payloads(mcp_client: MCPClient) -> None:
    err = mcp_client.call_tool_error(
        "action.validate",
        {
            "action_ref": "core.catalog.describe",
            "input_json": {"api_key": "sk-leak"},
        },
    )

    assert err["code"] == -32602
    assert "must not contain secrets" in err["data"]["detail"]
