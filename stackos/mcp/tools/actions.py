"""MCP adapter registration for StackOS action operations."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry
from stackos.operations.adapters.mcp import register_mcp_operations
from stackos.operations.registry import OperationRegistry, build_operation_registry


def _action_operations() -> OperationRegistry:
    operations = OperationRegistry()
    all_operations = build_operation_registry()
    for name in ("action.describe", "action.validate", "action.execute", "action.run"):
        operations.register(all_operations.get(name))
    return operations


def register(registry: ToolRegistry) -> None:
    register_mcp_operations(registry, _action_operations())


__all__ = ["register"]
