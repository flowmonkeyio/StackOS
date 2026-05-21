"""MCP tool registry — every per-domain module exports a ``register`` callable.

Per the M3 layout, tools are grouped by domain (projects, clusters,
articles, …). Each module exports a ``register(registry)`` function
that calls ``registry.register(ToolSpec(...))`` once per tool. The top-
level ``register_all`` here just wires every per-domain registrar so
``content_stack.mcp.server.register_mcp`` can stay agnostic of the
catalog.
"""

from __future__ import annotations

from content_stack.mcp.server import ToolRegistry
from content_stack.mcp.tools import (
    articles,
    artifacts,
    authors,
    clusters,
    cost,
    gsc,
    interlinks,
    meta,
    plugins,
    projects,
    resources,
    runs,
    sitemap,
    vendor_ops,
    workspaces,
)


def register_all(registry: ToolRegistry) -> None:
    """Populate ``registry`` with every M3-shipped tool."""
    projects.register(registry)
    clusters.register(registry)
    articles.register(registry)
    interlinks.register(registry)
    runs.register(registry)
    gsc.register(registry)
    authors.register(registry)
    cost.register(registry)
    meta.register(registry)
    plugins.register(registry)
    resources.register(registry)
    artifacts.register(registry)
    sitemap.register(registry)
    workspaces.register(registry)
    vendor_ops.register(registry)


__all__ = ["register_all"]
