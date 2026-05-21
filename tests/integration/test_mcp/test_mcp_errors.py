"""RepositoryError → JSON-RPC error code mapping."""

from __future__ import annotations

from .conftest import MCPClient


def test_not_found_maps_to_minus_32004(mcp_client: MCPClient) -> None:
    """project.get on a missing slug returns -32004."""
    err = mcp_client.call_tool_error("project.get", {"id_or_slug": "ghost"})
    assert err["code"] == -32004
    assert err["message"] == "NotFoundError"


def test_validation_error_maps_to_minus_32602(mcp_client: MCPClient) -> None:
    """project.create with empty slug → -32602."""
    err = mcp_client.call_tool_error(
        "project.create",
        {"slug": "", "name": "X", "domain": "x", "locale": "en-US"},
    )
    assert err["code"] == -32602
    # Either ValidationError or pydantic input validation; both -32602.
