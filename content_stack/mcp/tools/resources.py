"""Generic StackOS resource MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from content_stack.mcp.context import MCPContext
from content_stack.mcp.contract import MCPInput, WriteEnvelope
from content_stack.mcp.server import ToolRegistry, ToolSpec
from content_stack.mcp.streaming import ProgressEmitter
from content_stack.repositories.base import ValidationError
from content_stack.repositories.resources import (
    ResourceGetOut,
    ResourceQueryOut,
    ResourceRecordOut,
    ResourceRepository,
)


class ResourceGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"record_id": 1}},
    )

    record_id: int | None = None
    resource_key: str | None = None
    plugin_slug: str | None = None


class ResourceQueryInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "plugin_slug": "core"}},
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    resource_key: str | None = None
    limit: int | None = None
    after_id: int | None = None


class ResourceUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "plugin_slug": "core",
                "resource_key": "learning",
                "external_id": "run-7-learning",
                "data_json": {"body": "Variant A won on CTR."},
            }
        },
    )

    project_id: int
    plugin_slug: str
    resource_key: str
    data_json: dict[str, Any]
    record_id: int | None = None
    external_id: str | None = None
    title: str | None = None
    provenance_json: dict[str, Any] | None = None


async def _resource_get(
    inp: ResourceGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ResourceGetOut:
    repo = ResourceRepository(ctx.session)
    if inp.record_id is not None:
        return ResourceGetOut(record=repo.get_record(inp.record_id))
    if inp.resource_key is not None:
        return ResourceGetOut(
            resource=repo.get_resource(key=inp.resource_key, plugin_slug=inp.plugin_slug)
        )
    raise ValidationError("record_id or resource_key is required")


async def _resource_query(
    inp: ResourceQueryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ResourceQueryOut:
    return ResourceRepository(ctx.session).query(
        project_id=inp.project_id,
        plugin_slug=inp.plugin_slug,
        resource_key=inp.resource_key,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _resource_upsert(
    inp: ResourceUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ResourceRecordOut]:
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug=inp.plugin_slug,
        resource_key=inp.resource_key,
        record_id=inp.record_id,
        external_id=inp.external_id,
        title=inp.title,
        data_json=inp.data_json,
        provenance_json=inp.provenance_json,
    )
    return WriteEnvelope[ResourceRecordOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="resource.get",
            description="Fetch a generic resource schema or resource record.",
            input_model=ResourceGetInput,
            output_model=ResourceGetOut,
            handler=_resource_get,
        )
    )
    registry.register(
        ToolSpec(
            name="resource.query",
            description="Query generic resource schemas and bounded project records.",
            input_model=ResourceQueryInput,
            output_model=ResourceQueryOut,
            handler=_resource_query,
        )
    )
    registry.register(
        ToolSpec(
            name="resource.upsert",
            description="Internal/admin upsert for a generic project resource record.",
            input_model=ResourceUpsertInput,
            output_model=WriteEnvelope[ResourceRecordOut],
            handler=_resource_upsert,
        )
    )


__all__ = ["register"]
