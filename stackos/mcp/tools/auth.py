"""MCP adapter registration for reusable Account and project Connection operations."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry


def register(registry: ToolRegistry) -> None:
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        (
            "account.list",
            "account.start",
            "account.test",
            "account.revoke",
            "connection.list",
            "connection.attach",
            "connection.detach",
        ),
    )


__all__ = ["register"]
