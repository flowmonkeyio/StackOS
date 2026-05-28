"""``meta.*`` — server / enum lookup tools.

Mirrors ``GET /api/v1/meta/enums`` (per audit M-16) so MCP clients can
resolve every enum value + state-machine transition in one call. The
output shape is identical to the REST router so a client that already
parses one wire shape doesn't need a second handler.
"""

from __future__ import annotations

from pydantic import ConfigDict

from stackos.api.meta import EnumLookupResponse, get_meta_enums
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter

# ---------------------------------------------------------------------------
# Inputs.
# ---------------------------------------------------------------------------


class MetaEnumsInput(MCPInput):
    """No parameters; returns every enum + transition map."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {}},
    )


# ---------------------------------------------------------------------------
# Handler.
# ---------------------------------------------------------------------------


async def _meta_enums(
    _input: MetaEnumsInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> EnumLookupResponse:
    """Return every enum value + state-machine transitions used by the daemon."""
    return await get_meta_enums()


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------


def register(registry: ToolRegistry) -> None:
    """Register every ``meta.*`` tool."""
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(registry, ("meta.enums",))


__all__ = ["register"]
