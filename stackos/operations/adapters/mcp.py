"""MCP adapter for StackOS operations."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry, ToolSpec
from stackos.operations.registry import OperationRegistry
from stackos.operations.spec import OperationSpec


def operation_to_tool_spec(operation: OperationSpec) -> ToolSpec:
    return ToolSpec(
        name=operation.name,
        description=operation.mcp_description,
        input_model=operation.input_model,
        output_model=operation.output_model,
        handler=operation.handler,
        operation_name=operation.name,
    )


def register_mcp_operations(registry: ToolRegistry, operations: OperationRegistry) -> None:
    for operation in operations.by_surface("mcp"):
        registry.register(operation_to_tool_spec(operation))


__all__ = ["operation_to_tool_spec", "register_mcp_operations"]
