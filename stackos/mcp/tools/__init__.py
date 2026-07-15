"""MCP tool registry for the StackOS daemon catalog."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry
from stackos.mcp.tools import (
    actions,
    agent_presets,
    agent_requests,
    artifacts,
    auth,
    browser,
    communications,
    context,
    cost,
    execution_contexts,
    guides,
    meta,
    operations,
    plugins,
    projects,
    readiness,
    resources,
    runs,
    sitemap,
    tool_profiles,
    tracker,
    workflows,
    workspaces,
)


def register_all(registry: ToolRegistry) -> None:
    """Populate ``registry`` with the core StackOS tool catalog."""
    agent_requests.register(registry)
    actions.register(registry)
    projects.register(registry)
    auth.register(registry)
    browser.register(registry)
    communications.register(registry)
    tool_profiles.register(registry)
    agent_presets.register(registry)
    tracker.register(registry)
    context.register(registry)
    runs.register(registry)
    cost.register(registry)
    execution_contexts.register(registry)
    guides.register(registry)
    meta.register(registry)
    operations.register(registry)
    plugins.register(registry)
    readiness.register(registry)
    resources.register(registry)
    artifacts.register(registry)
    sitemap.register(registry)
    workflows.register(registry)
    workspaces.register(registry)


__all__ = ["register_all"]
