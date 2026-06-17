"""Schema catalog operations."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackos.action_output_contract import (
    ACTION_OUTPUT_SCHEMA,
    ACTION_OUTPUT_SCHEMA_CONTENT_TYPE,
    ACTION_OUTPUT_SCHEMA_REF,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import NotFoundError


class SchemaGetInput(MCPInput):
    """Fetch a stable StackOS JSON Schema by ref."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"schema_ref": ACTION_OUTPUT_SCHEMA_REF}},
    )

    schema_ref: str = Field(
        description="Stable schema ref, for example stackos.action-output.v1.",
    )


class SchemaGetOut(BaseModel):
    schema_ref: str
    content_type: str
    schema_data: dict[str, Any]


_SCHEMAS: dict[str, dict[str, Any]] = {
    ACTION_OUTPUT_SCHEMA_REF: ACTION_OUTPUT_SCHEMA,
}


async def schema_get(
    inp: SchemaGetInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> SchemaGetOut:
    schema = _SCHEMAS.get(inp.schema_ref)
    if schema is None:
        raise NotFoundError(
            "schema ref is not registered",
            data={
                "schema_ref": inp.schema_ref,
                "available_schema_refs": sorted(_SCHEMAS),
            },
        )
    return SchemaGetOut(
        schema_ref=inp.schema_ref,
        content_type=ACTION_OUTPUT_SCHEMA_CONTENT_TYPE,
        schema_data=copy.deepcopy(schema),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="schema.get",
            summary="Fetch a StackOS JSON Schema by stable schema ref.",
            input_model=SchemaGetInput,
            output_model=SchemaGetOut,
            handler=schema_get,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(enabled=True, path="/api/v1/operations/schema.get/call"),
                cli=OperationSurface(enabled=True, command="ops call schema.get"),
            ),
            purpose=(
                "Use this when a compact response names a schema_ref and the agent needs the "
                "full JSON Schema contract before reading or validating a saved response file."
            ),
            when_to_use=(
                "A file-backed action output pointer includes schema_ref.",
                "An agent needs the exact envelope contract for a response file.",
            ),
            prerequisites=("Pass the exact schema_ref from the compact response or saved file.",),
            returns=(
                "The requested schema_ref, content type, and JSON Schema in schema_data.",
                "No local filesystem paths or StackOS source paths are exposed.",
            ),
            examples=(
                OperationExample(
                    title="Fetch action output file envelope schema",
                    arguments={"schema_ref": ACTION_OUTPUT_SCHEMA_REF},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
            category="operations",
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("raw",),
                raw_only_reason=(
                    "schema.get is called only when the agent explicitly needs the full schema"
                ),
            ),
        )
    ]


__all__ = ["SchemaGetInput", "SchemaGetOut", "operation_specs", "schema_get"]
