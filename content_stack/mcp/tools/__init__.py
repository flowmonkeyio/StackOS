"""MCP tool registry for the StackOS daemon catalog."""

from __future__ import annotations

from content_stack.mcp.server import ToolRegistry
from content_stack.mcp.tools import (
    actions,
    agent_requests,
    artifacts,
    auth,
    communications,
    context,
    cost,
    meta,
    plugins,
    projects,
    resources,
    runs,
    sitemap,
    workflows,
    workspaces,
)


def register_all(registry: ToolRegistry) -> None:
    """Populate ``registry`` with the core StackOS tool catalog."""
    agent_requests.register(registry)
    actions.register(registry)
    projects.register(registry)
    auth.register(registry)
    communications.register(registry)
    context.register(registry)
    runs.register(registry)
    cost.register(registry)
    meta.register(registry)
    plugins.register(registry)
    resources.register(registry)
    artifacts.register(registry)
    sitemap.register(registry)
    workflows.register(registry)
    workspaces.register(registry)


__all__ = ["register_all"]
