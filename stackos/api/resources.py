"""StackOS generic resource and artifact REST routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from stackos.api.deps import get_session
from stackos.api.envelopes import WriteResponse, write_response
from stackos.api.pagination import (
    PageResponse,
    PaginationParams,
    page_response,
    pagination_params,
)
from stackos.repositories.resources import (
    ArtifactOut,
    ArtifactRepository,
    ResourceOut,
    ResourceRecordOut,
    ResourceRepository,
)

router = APIRouter(prefix="/api/v1", tags=["resources"])


class ResourceRecordUpsertRequest(BaseModel):
    """Local-admin/internal body for generic resource record upserts."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "plugin_slug": "core",
                "resource_key": "learning",
                "external_id": "weekly-1",
                "title": "Winning creative pattern",
                "data_json": {"body": "Short, concrete hooks performed best."},
            }
        }
    )

    plugin_slug: str
    resource_key: str
    data_json: dict[str, Any] = Field(default_factory=dict)
    record_id: int | None = None
    external_id: str | None = None
    title: str | None = None
    provenance_json: dict[str, Any] | None = None


class ArtifactCreateRequest(BaseModel):
    """Local-admin/internal body for creating artifact references."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "plugin_slug": "utils",
                "kind": "image",
                "uri": "/generated-assets/image.png",
                "metadata_json": {"width": 1024, "height": 1024},
            }
        }
    )

    kind: str
    uri: str
    status: Literal["draft", "approved", "superseded", "archived"] = "draft"
    plugin_slug: str | None = None
    resource_record_id: int | None = None
    name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    metadata_json: dict[str, Any] | None = None
    provenance_json: dict[str, Any] | None = None


@router.get("/resources", response_model=list[ResourceOut])
async def list_resources(
    plugin_slug: str | None = Query(default=None),
    project_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ResourceOut]:
    """List installed plugin resource schemas."""
    return ResourceRepository(session).list_resources(
        plugin_slug=plugin_slug,
        project_id=project_id,
    )


@router.get("/resources/{resource_key}", response_model=ResourceOut)
async def get_resource(
    resource_key: str,
    plugin_slug: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> ResourceOut:
    """Describe one resource schema; pass plugin_slug if the key is ambiguous."""
    return ResourceRepository(session).get_resource(key=resource_key, plugin_slug=plugin_slug)


@router.get(
    "/projects/{project_id}/resource-records",
    response_model=PageResponse[ResourceRecordOut],
)
async def query_resource_records(
    project_id: int,
    plugin_slug: str | None = Query(default=None),
    resource_key: str | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ResourceRecordOut]:
    """Query generic resource records for a project."""
    return page_response(
        ResourceRepository(session).query_records(
            project_id=project_id,
            plugin_slug=plugin_slug,
            resource_key=resource_key,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.get("/resource-records/{record_id}", response_model=ResourceRecordOut)
async def get_resource_record(
    record_id: int,
    session: Session = Depends(get_session),
) -> ResourceRecordOut:
    """Fetch one generic resource record."""
    return ResourceRepository(session).get_record(record_id)


@router.post(
    "/projects/{project_id}/resource-records",
    response_model=WriteResponse[ResourceRecordOut],
    status_code=status.HTTP_200_OK,
)
async def upsert_resource_record(
    project_id: int,
    body: ResourceRecordUpsertRequest = Body(...),
    session: Session = Depends(get_session),
) -> WriteResponse[ResourceRecordOut]:
    """Upsert a generic resource record through the local/admin REST surface."""
    return write_response(
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug=body.plugin_slug,
            resource_key=body.resource_key,
            record_id=body.record_id,
            external_id=body.external_id,
            title=body.title,
            data_json=body.data_json,
            provenance_json=body.provenance_json,
        )
    )


@router.get("/projects/{project_id}/artifacts", response_model=PageResponse[ArtifactOut])
async def query_project_artifacts(
    project_id: int,
    plugin_slug: str | None = Query(default=None),
    resource_record_id: int | None = Query(default=None),
    kind: str | None = Query(default=None),
    artifact_status: Literal["draft", "approved", "superseded", "archived"] | None = Query(
        default=None,
        alias="status",
    ),
    include_inactive: bool = Query(default=False),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ArtifactOut]:
    """Query artifact references for a project."""
    return page_response(
        ArtifactRepository(session).query(
            project_id=project_id,
            plugin_slug=plugin_slug,
            resource_record_id=resource_record_id,
            kind=kind,
            status=artifact_status,
            include_inactive=include_inactive,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def get_artifact(
    artifact_id: int,
    session: Session = Depends(get_session),
) -> ArtifactOut:
    """Fetch one artifact reference."""
    return ArtifactRepository(session).get(artifact_id)


@router.post(
    "/projects/{project_id}/artifacts",
    response_model=WriteResponse[ArtifactOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_artifact(
    project_id: int,
    body: ArtifactCreateRequest = Body(...),
    session: Session = Depends(get_session),
) -> WriteResponse[ArtifactOut]:
    """Create an artifact reference through the local/admin REST surface."""
    return write_response(
        ArtifactRepository(session).create(
            project_id=project_id,
            plugin_slug=body.plugin_slug,
            resource_record_id=body.resource_record_id,
            kind=body.kind,
            uri=body.uri,
            status=body.status,
            name=body.name,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            metadata_json=body.metadata_json,
            provenance_json=body.provenance_json,
        )
    )


__all__ = ["router"]
