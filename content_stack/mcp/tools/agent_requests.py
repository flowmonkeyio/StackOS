"""MCP adapter registration for StackOS agent request operations."""

from __future__ import annotations

from content_stack.mcp.server import ToolRegistry
from content_stack.operations.adapters.mcp import register_mcp_operations
from content_stack.operations.registry import OperationRegistry, build_operation_registry


def _agent_request_operations() -> OperationRegistry:
    operations = OperationRegistry()
    all_operations = build_operation_registry()
    for name in (
        "agentRequest.list",
        "agentRequest.get",
        "agentRequest.create",
        "agentRequest.claim",
        "agentRequest.release",
        "agentRequest.linkRunPlan",
        "agentRequest.prepareRunPlan",
        "agentRequest.complete",
        "agentRequest.ignore",
    ):
        operations.register(all_operations.get(name))
    return operations


def register(registry: ToolRegistry) -> None:
    register_mcp_operations(registry, _agent_request_operations())


__all__ = ["register"]
