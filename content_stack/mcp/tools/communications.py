from __future__ import annotations

from content_stack.mcp.server import ToolRegistry
from content_stack.operations.adapters.mcp import register_mcp_operations
from content_stack.operations.registry import OperationRegistry, build_operation_registry


def _communication_operations() -> OperationRegistry:
    operations = OperationRegistry()
    all_operations = build_operation_registry()
    for name in (
        "communicationBotProfile.list",
        "communicationBotProfile.get",
        "communicationBotProfile.upsert",
    ):
        operations.register(all_operations.get(name))
    return operations


def register(registry: ToolRegistry) -> None:
    register_mcp_operations(registry, _communication_operations())


__all__ = ["register"]
