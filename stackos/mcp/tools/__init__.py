"""MCP tool registry for the StackOS daemon catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stackos.mcp.tool_registry import ToolRegistry
    from stackos.operations.registry import OperationRegistry


def register_all(
    registry: ToolRegistry, *, operation_registry: OperationRegistry | None = None
) -> None:
    """Expose the canonical MCP surface from one operation registry.

    App composition supplies the same registry used by its dispatcher. Direct
    catalog consumers can omit it to build one independent registry here.
    """
    from stackos.operations.adapters.mcp import register_mcp_operations

    if operation_registry is None:
        from stackos.operations.registry import build_operation_registry

        operation_registry = build_operation_registry()
    register_mcp_operations(registry, operation_registry)


__all__ = ["register_all"]
