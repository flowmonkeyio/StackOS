"""MCP adapter registration for agent and skill preset operations."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry
from stackos.operations.adapters.mcp import register_mcp_operations
from stackos.operations.registry import OperationRegistry, build_operation_registry


def _agent_preset_operations() -> OperationRegistry:
    operations = OperationRegistry()
    all_operations = build_operation_registry()
    for name in (
        "agentPreset.list",
        "agentPreset.describe",
        "agentPreset.resolveForWorkflow",
        "skillPreset.list",
        "skillPreset.describe",
        "skillPreset.resolveForWorkflow",
    ):
        operations.register(all_operations.get(name))
    return operations


def register(registry: ToolRegistry) -> None:
    register_mcp_operations(registry, _agent_preset_operations())


__all__ = ["register"]
