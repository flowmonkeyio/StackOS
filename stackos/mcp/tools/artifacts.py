"""Generic StackOS artifact MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry, ToolSpec
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import NotFoundError, Page
from stackos.repositories.resources import ArtifactOut, ArtifactRepository


class ArtifactCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "plugin_slug": "utils",
                "kind": "image",
                "uri": "/generated-assets/image.png",
            }
        },
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    resource_record_id: int | None = None
    kind: str
    uri: str
    name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    metadata_json: dict[str, Any] | None = None
    provenance_json: dict[str, Any] | None = None


class ArtifactGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"artifact_id": 1}})

    artifact_id: int
    project_id: int | None = None


class ArtifactQueryInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "kind": "image"}},
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    resource_record_id: int | None = None
    kind: str | None = None
    limit: int | None = None
    after_id: int | None = None


async def _artifact_create(
    inp: ArtifactCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ArtifactOut]:
    env = ArtifactRepository(ctx.session).create(
        project_id=inp.project_id,
        plugin_slug=inp.plugin_slug,
        resource_record_id=inp.resource_record_id,
        kind=inp.kind,
        uri=inp.uri,
        name=inp.name,
        mime_type=inp.mime_type,
        size_bytes=inp.size_bytes,
        metadata_json=inp.metadata_json,
        provenance_json=inp.provenance_json,
    )
    return WriteEnvelope[ArtifactOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _artifact_get(
    inp: ArtifactGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ArtifactOut:
    artifact = ArtifactRepository(ctx.session).get(inp.artifact_id)
    if inp.project_id is not None and artifact.project_id != inp.project_id:
        raise NotFoundError(
            f"artifact {inp.artifact_id} not found in project {inp.project_id}",
            data={"project_id": inp.project_id, "artifact_id": inp.artifact_id},
        )
    return artifact


async def _artifact_query(
    inp: ArtifactQueryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[ArtifactOut]:
    return ArtifactRepository(ctx.session).query(
        project_id=inp.project_id,
        plugin_slug=inp.plugin_slug,
        resource_record_id=inp.resource_record_id,
        kind=inp.kind,
        limit=inp.limit,
        after_id=inp.after_id,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="artifact.create",
            description="Internal/admin create for a generic artifact reference.",
            input_model=ArtifactCreateInput,
            output_model=WriteEnvelope[ArtifactOut],
            handler=_artifact_create,
        )
    )
    registry.register(
        ToolSpec(
            name="artifact.get",
            description="Fetch one generic artifact reference.",
            input_model=ArtifactGetInput,
            output_model=ArtifactOut,
            handler=_artifact_get,
        )
    )
    registry.register(
        ToolSpec(
            name="artifact.query",
            description="Query bounded generic artifact references.",
            input_model=ArtifactQueryInput,
            output_model=Page[ArtifactOut],
            handler=_artifact_query,
        )
    )


__all__ = ["register"]
