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
        operation_category=operation.category_name,
        operation_grant_policy=operation.grant_policy,
        operation_secret_policy=operation.secret_policy,
        operation_purpose=operation.purpose,
    )


def register_mcp_operations(registry: ToolRegistry, operations: OperationRegistry) -> None:
    for operation in operations.by_surface("mcp"):
        registry.register(operation_to_tool_spec(operation))


def register_mcp_operation_names(registry: ToolRegistry, names: tuple[str, ...]) -> None:
    from stackos.operations.registry import build_operation_registry

    operations = build_operation_registry()
    for name in names:
        registry.register(operation_to_tool_spec(operations.get(name, surface="mcp")))


__all__ = ["operation_to_tool_spec", "register_mcp_operation_names", "register_mcp_operations"]
