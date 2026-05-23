"""MCP tool definitions + Streamable HTTP transport mount.

Top-level export is ``register_mcp`` — the single entry point
``stackos.server.create_app`` calls to wire the MCP transport
onto the FastAPI app.
"""

from __future__ import annotations

from stackos.mcp.server import register_mcp

__all__ = ["register_mcp"]
