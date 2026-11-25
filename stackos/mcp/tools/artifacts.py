"""Generic StackOS artifact MCP tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field

from stackos.artifacts.json_path import select_json_path
from stackos.config import Settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import NotFoundError, Page, ValidationError
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
    status: Literal["draft", "approved", "superseded", "archived"] = "draft"
    name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    metadata_json: dict[str, Any] | None = None
    provenance_json: dict[str, Any] | None = None


class ArtifactUpdateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "artifact_id": 1,
                "project_id": 1,
                "status": "approved",
                "metadata_patch_json": {"review": {"approved_by": "operator"}},
            }
        },
    )

    artifact_id: int
    project_id: int | None = None
    plugin_slug: str | None = None
    resource_record_id: int | None = None
    kind: str | None = None
    uri: str | None = None
    status: Literal["draft", "approved", "superseded", "archived"] | None = None
    name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    superseded_by_artifact_id: int | None = None
    metadata_json: dict[str, Any] | None = None
    metadata_patch_json: dict[str, Any] | None = None
    provenance_json: dict[str, Any] | None = None
    provenance_patch_json: dict[str, Any] | None = None


class ArtifactArchiveInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "artifact_id": 1,
                "project_id": 1,
                "reason": "Accidental scratch draft created during iteration.",
            }
        },
    )

    artifact_id: int
    project_id: int | None = None
    reason: str | None = None
    metadata_patch_json: dict[str, Any] | None = None


class ArtifactSupersedeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "artifact_id": 1,
                "replacement_artifact_id": 2,
                "project_id": 1,
                "reason": "Operator approved the refined packet.",
            }
        },
    )

    artifact_id: int
    replacement_artifact_id: int
    project_id: int | None = None
    reason: str | None = None
    metadata_patch_json: dict[str, Any] | None = None


class ArtifactGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"artifact_id": 1}})

    artifact_id: int
    project_id: int | None = None


class ArtifactReadInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "artifact_id": 1,
                "json_path": "$.data.items[0]",
                "max_bytes": 16000,
            }
        },
    )

    artifact_id: int
    project_id: int | None = None
    json_path: str | None = None
    max_bytes: int = Field(default=16000, ge=1, le=200000)


class ArtifactQueryInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "kind": "image"}},
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    resource_record_id: int | None = None
    kind: str | None = None
    status: Literal["draft", "approved", "superseded", "archived"] | None = None
    include_inactive: bool = False
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
        status=inp.status,
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


async def _artifact_update(
    inp: ArtifactUpdateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ArtifactOut]:
    env = ArtifactRepository(ctx.session).update(
        inp.artifact_id,
        project_id=inp.project_id,
        fields=set(inp.model_fields_set) - {"artifact_id", "project_id"},
        plugin_slug=inp.plugin_slug,
        resource_record_id=inp.resource_record_id,
        kind=inp.kind,
        uri=inp.uri,
        status=inp.status,
        name=inp.name,
        mime_type=inp.mime_type,
        size_bytes=inp.size_bytes,
        superseded_by_artifact_id=inp.superseded_by_artifact_id,
        metadata_json=inp.metadata_json,
        metadata_patch_json=inp.metadata_patch_json,
        provenance_json=inp.provenance_json,
        provenance_patch_json=inp.provenance_patch_json,
    )
    return WriteEnvelope[ArtifactOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _artifact_archive(
    inp: ArtifactArchiveInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ArtifactOut]:
    env = ArtifactRepository(ctx.session).archive(
        inp.artifact_id,
        project_id=inp.project_id,
        reason=inp.reason,
        metadata_patch_json=inp.metadata_patch_json,
    )
    return WriteEnvelope[ArtifactOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _artifact_supersede(
    inp: ArtifactSupersedeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ArtifactOut]:
    env = ArtifactRepository(ctx.session).supersede(
        inp.artifact_id,
        project_id=inp.project_id,
        replacement_artifact_id=inp.replacement_artifact_id,
        reason=inp.reason,
        metadata_patch_json=inp.metadata_patch_json,
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


async def _artifact_read(
    inp: ArtifactReadInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> dict[str, Any]:
    artifact = ArtifactRepository(ctx.session).get(inp.artifact_id)
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    if project_id is not None and artifact.project_id != project_id:
        raise NotFoundError(
            f"artifact {inp.artifact_id} not found in project {project_id}",
            data={"project_id": project_id, "artifact_id": inp.artifact_id},
        )

    base = {
        "artifact_id": inp.artifact_id,
        "artifact": artifact.model_dump(mode="json"),
        "json_path": inp.json_path,
        "max_bytes": inp.max_bytes,
    }
    uri = artifact.uri
    if not uri.startswith("/generated-assets/"):
        return {
            **base,
            "content_available": False,
            "read_instructions": (
                "This artifact is a reference outside StackOS generated assets; "
                "use the artifact URI/provider-specific access path instead."
            ),
        }

    settings = ctx.extras.get("settings")
    asset_root = Path(
        getattr(settings, "generated_assets_dir", Settings().generated_assets_dir)
    ).resolve()
    path = (asset_root / uri.removeprefix("/generated-assets/")).resolve()
    try:
        path.relative_to(asset_root)
    except ValueError as exc:
        raise ValidationError("artifact path escaped generated assets") from exc

    metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
    metadata_path = metadata.get("absolute_path") or metadata.get("path")
    if isinstance(metadata_path, str) and Path(metadata_path).resolve() != path:
        return {
            **base,
            "path": str(path),
            "content_available": False,
            "error": "artifact metadata path does not match generated asset URI",
        }
    if not path.exists() or not path.is_file():
        return {
            **base,
            "path": str(path),
            "content_available": False,
            "error": "artifact file is missing",
        }

    raw = path.read_bytes()
    mime_type = artifact.mime_type or ""
    selected_json_path = inp.json_path or "$"
    if _is_json_artifact(mime_type, path):
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError("artifact file is not valid JSON") from exc
        value = select_json_path(value, selected_json_path)
        content = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    elif _is_text_artifact(mime_type, path):
        if inp.json_path not in (None, "", "$"):
            raise ValidationError("json_path is only supported for JSON artifacts")
        content = raw.decode("utf-8", errors="replace")
    else:
        return {
            **base,
            "path": str(path),
            "content_available": False,
            "content_type": mime_type or None,
            "bytes": len(raw),
            "sha256": metadata.get("sha256"),
            "read_instructions": "This artifact is binary; use artifact metadata and URI.",
        }

    encoded = content.encode("utf-8")
    truncated = len(encoded) > inp.max_bytes
    if truncated:
        content = encoded[: inp.max_bytes].decode("utf-8", errors="ignore")
    return {
        **base,
        "path": str(path),
        "json_path": selected_json_path if _is_json_artifact(mime_type, path) else None,
        "content_available": True,
        "content_type": mime_type or "application/octet-stream",
        "bytes": len(raw),
        "sha256": metadata.get("sha256"),
        "content_truncated": truncated,
        "content": content,
    }


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
        status=inp.status,
        include_inactive=inp.include_inactive,
        limit=inp.limit,
        after_id=inp.after_id,
    )


def register(registry: ToolRegistry) -> None:
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        (
            "artifact.create",
            "artifact.update",
            "artifact.archive",
            "artifact.supersede",
            "artifact.get",
            "artifact.read",
            "artifact.query",
        ),
    )


__all__ = ["register"]


def _is_json_artifact(mime_type: str, path: Path) -> bool:
    return mime_type == "application/json" or path.suffix.lower() == ".json"


def _is_text_artifact(mime_type: str, path: Path) -> bool:
    if mime_type.startswith("text/"):
        return True
    if mime_type in {"application/markdown", "application/xml", "application/x-ndjson"}:
        return True
    return path.suffix.lower() in {".md", ".txt", ".csv", ".xml", ".html", ".log"}
