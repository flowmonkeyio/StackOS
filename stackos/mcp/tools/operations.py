"""MCP adapter registration for operation discovery tools."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry
from stackos.operations.adapters.mcp import operation_to_tool_spec
from stackos.operations.discovery import OperationDescribeInput, OperationListInput
from stackos.operations.registry import build_operation_registry
from stackos.operations.schemas import SchemaGetInput


def register(registry: ToolRegistry) -> None:
    operations = build_operation_registry()
    registry.register(operation_to_tool_spec(operations.get("operation.list")))
    registry.register(operation_to_tool_spec(operations.get("operation.describe")))
    registry.register(operation_to_tool_spec(operations.get("schema.get")))


__all__ = ["OperationDescribeInput", "OperationListInput", "SchemaGetInput", "register"]
