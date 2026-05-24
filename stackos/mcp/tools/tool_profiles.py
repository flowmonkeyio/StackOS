from __future__ import annotations

from stackos.mcp.server import ToolRegistry
from stackos.operations.adapters.mcp import register_mcp_operations
from stackos.operations.registry import OperationRegistry, build_operation_registry


def _tool_profile_operations() -> OperationRegistry:
    operations = OperationRegistry()
    all_operations = build_operation_registry()
    operations.register(all_operations.get("toolProfile.resolve"))
    return operations


def register(registry: ToolRegistry) -> None:
    register_mcp_operations(registry, _tool_profile_operations())


__all__ = ["register"]
