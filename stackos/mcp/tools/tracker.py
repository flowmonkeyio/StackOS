"""MCP adapter registration for StackOS tracker operations."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry
from stackos.operations.adapters.mcp import register_mcp_operations
from stackos.operations.registry import OperationRegistry, build_operation_registry


def _tracker_operations() -> OperationRegistry:
    operations = OperationRegistry()
    all_operations = build_operation_registry()
    for name in (
        "tracker.status",
        "tracker.get",
        "tracker.next",
        "tracker.blockers",
        "tracker.brief",
        "tracker.why",
        "tracker.execute",
        "tracker.verify",
        "tracker.history",
        "tracker.changed",
        "tracker.search",
        "tracker.createTask",
        "tracker.createTicket",
        "tracker.updateTask",
        "tracker.updateTicket",
        "tracker.patch",
        "tracker.pick",
        "tracker.rejectTask",
        "tracker.release",
        "tracker.linkRunPlan",
    ):
        operations.register(all_operations.get(name))
    return operations


def register(registry: ToolRegistry) -> None:
    register_mcp_operations(registry, _tracker_operations())


__all__ = ["register"]
