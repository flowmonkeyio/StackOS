"""Operation catalog discovery contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationDescribeOut,
    OperationExample,
    OperationListOut,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)

OperationSurfaceName = Literal["mcp", "rest", "cli"]
OperationListMode = Literal["standard", "grouped"]


class OperationListInput(MCPInput):
    """List operation specs, optionally filtered by adapter surface/category/query."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"surface": "mcp", "category": "actions"}},
    )

    surface: OperationSurfaceName | None = Field(
        default=None,
        description="Optional operation surface filter: mcp, rest, or cli.",
    )
    category: str | None = Field(
        default=None,
        description=(
            "Optional category filter such as setup, tracker, workflow, resources, auth, "
            "actions, communications, catalog, operations, or system."
        ),
    )
    query: str | None = Field(
        default=None,
        description=(
            "Optional case-insensitive search over name, summary, category, and grant policy."
        ),
    )
    mode: OperationListMode = Field(
        default="standard",
        description="standard returns summaries plus groups; grouped returns only compact groups.",
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


async def operation_list(
    inp: OperationListInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> OperationListOut:
    from stackos.operations.registry import build_operation_registry

    return build_operation_registry().list_out(
        surface=inp.surface,
        category=inp.category,
        query=inp.query,
        mode=inp.mode,
    )


async def operation_describe(
    inp: OperationDescribeInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> OperationDescribeOut:
    from stackos.operations.registry import build_operation_registry

    return build_operation_registry().get(inp.name, surface=inp.surface).describe_out()


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="operation.list",
            summary=(
                "List StackOS operation specs with filters, grouping, surfaces, grant policy, "
                "and secret policy."
            ),
            input_model=OperationListInput,
            output_model=OperationListOut,
            handler=operation_list,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/operation.list/call",
                ),
                cli=OperationSurface(enabled=True, command="ops list"),
            ),
            purpose=(
                "Use this as the first agent-facing discovery step when a caller needs to "
                "understand which StackOS operations exist on MCP, REST, or CLI."
            ),
            when_to_use=(
                "An agent needs the available StackOS operation inventory before choosing a tool.",
                "A caller needs to filter operations by adapter surface such as mcp, rest, or cli.",
                "A caller wants compact category groups instead of the full operation list.",
            ),
            prerequisites=(
                "Pass surface only when the caller needs operations exposed on one adapter.",
                "Pass category or query to keep the result scoped to the current decision.",
                "Use mode='grouped' for compact category counts and operation names.",
            ),
            returns=(
                "Sorted operation summaries with surface availability, mutating/read-only flags, "
                "grant policy, and secret policy.",
                "Category groups so agents can browse by setup, tracker, workflow, resources, "
                "auth, actions, communications, catalog, operations, or system.",
            ),
            examples=(
                OperationExample(
                    title="List MCP operations",
                    arguments={"surface": "mcp"},
                ),
                OperationExample(
                    title="List action operations only",
                    arguments={"category": "actions"},
                ),
                OperationExample(
                    title="Browse compact operation groups",
                    arguments={"mode": "grouped"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="operation.describe",
            summary=(
                "Describe one StackOS operation with purpose, schemas, prerequisites, returns, "
                "and examples."
            ),
            input_model=OperationDescribeInput,
            output_model=OperationDescribeOut,
            handler=operation_describe,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/operation.describe/call",
                ),
                cli=OperationSurface(enabled=True, command="ops describe"),
            ),
            purpose=(
                "Use this before calling a StackOS operation when the agent needs the exact "
                "input schema, output schema, grant model, examples, or operational guidance."
            ),
            when_to_use=(
                "An agent is about to call an operation and needs the exact payload contract.",
                "A caller is debugging why an operation is unavailable on a specific surface.",
                "A caller wants the self-description for operation.list or operation.describe.",
            ),
            prerequisites=(
                "Pass name as the exact operation name from operation.list.",
                "Pass surface only when the operation must be available on that adapter.",
            ),
            returns=(
                "The operation summary plus purpose, usage guidance, prerequisites, return notes, "
                "JSON input schema, JSON output schema, and examples.",
            ),
            examples=(
                OperationExample(
                    title="Describe communication send",
                    arguments={"name": "communication.send", "surface": "mcp"},
                ),
                OperationExample(
                    title="Describe operation discovery",
                    arguments={"name": "operation.describe", "surface": "mcp"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
    ]


__all__ = [
    "OperationDescribeInput",
    "OperationListInput",
    "operation_describe",
    "operation_list",
    "operation_specs",
]
