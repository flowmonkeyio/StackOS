"""Generic StackOS resource MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.resources import (
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
    project_id: int | None = None


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
        record = repo.get_record(inp.record_id)
        if inp.project_id is not None and record.project_id != inp.project_id:
            raise NotFoundError(
                f"resource record {inp.record_id} not found in project {inp.project_id}",
                data={"project_id": inp.project_id, "record_id": inp.record_id},
            )
        return ResourceGetOut(record=record)
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
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        ("resource.get", "resource.query", "resource.upsert"),
    )


__all__ = ["register"]
