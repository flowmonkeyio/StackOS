"""MCP adapter registration for browser automation operations."""

from __future__ import annotations

from stackos.mcp.server import ToolRegistry
from stackos.operations.adapters.mcp import register_mcp_operation_names

_BROWSER_OPERATION_NAMES: tuple[str, ...] = (
    "browser.runtime.status",
    "browser.method.manifest",
    "browser.profile.create",
    "browser.profile.list",
    "browser.session.start",
    "browser.session.stop",
    "browser.session.list",
    "browser.session.status",
    "browser.page.call",
    "browser.context.call",
    "browser.script.run",
    "browser.script.inject",
    "browser.page.snapshot",
    "browser.page.screenshot",
)


def register(registry: ToolRegistry) -> None:
    register_mcp_operation_names(registry, _BROWSER_OPERATION_NAMES)


__all__ = ["register"]
