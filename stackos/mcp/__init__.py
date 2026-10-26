"""MCP tool definitions + Streamable HTTP transport mount.

Top-level export is ``register_mcp`` — the single entry point
``stackos.server.create_app`` calls to wire the MCP transport
onto the FastAPI app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stackos.mcp.server import register_mcp as register_mcp

__all__ = ["register_mcp"]


def __getattr__(name: str) -> Any:
    if name != "register_mcp":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # The standalone bridge imports this package too. Load the daemon server
    # only when the application actually requests its transport registration.
    from stackos.mcp.server import register_mcp

    globals()[name] = register_mcp
    return register_mcp
