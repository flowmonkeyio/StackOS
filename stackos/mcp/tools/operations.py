"""MCP operation-registry discovery tools."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.server import ToolRegistry, ToolSpec
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.registry import build_operation_registry
from stackos.operations.spec import OperationDescribeOut, OperationListOut

OperationSurfaceName = Literal["mcp", "rest", "cli"]


class OperationListInput(MCPInput):
    """List operation specs, optionally filtered by adapter surface."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"surface": "mcp"}},
    )

    surface: OperationSurfaceName | None = Field(
        default=None,
        description="Optional operation surface filter: mcp, rest, or cli.",
    )


class OperationDescribeInput(MCPInput):
    """Describe one operation spec with schemas and agent guidance."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"name": "communication.send", "surface": "mcp"}},
    )

    name: str = Field(description="Exact operation name, for example action.execute.")
    surface: OperationSurfaceName | None = Field(
        default=None,
        description=(
            "Optional surface requirement. The call fails if the operation is not exposed there."
        ),
    )


async def _operation_list(
    inp: OperationListInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> OperationListOut:
    return build_operation_registry().list_out(surface=inp.surface)


async def _operation_describe(
    inp: OperationDescribeInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> OperationDescribeOut:
    return build_operation_registry().get(inp.name, surface=inp.surface).describe_out()


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="operation.list",
            description=(
                "List StackOS operation specs with surfaces, grant policy, and secret policy."
            ),
            input_model=OperationListInput,
            output_model=OperationListOut,
            handler=_operation_list,
        )
    )
    registry.register(
        ToolSpec(
            name="operation.describe",
            description=(
                "Describe one StackOS operation with purpose, schemas, "
                "prerequisites, returns, and examples."
            ),
            input_model=OperationDescribeInput,
            output_model=OperationDescribeOut,
            handler=_operation_describe,
        )
    )


__all__ = ["OperationDescribeInput", "OperationListInput", "register"]
