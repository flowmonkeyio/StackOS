"""StackOS: local project, plugin, run-plan, and tool state for agents.

Public surface intentionally thin — the daemon is consumed via REST (`/api/v1`),
MCP (`/mcp`), or the bundled Vue UI (`/`), not via direct Python imports.
"""

from __future__ import annotations

__version__ = "2.1.8"
__milestone__ = "M10"

__all__ = ["__milestone__", "__version__"]
