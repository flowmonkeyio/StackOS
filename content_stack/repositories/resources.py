"""Generic StackOS resources and artifacts repositories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import Session, select

from content_stack.artifacts import redact_secrets
from content_stack.db.models import Artifact, Plugin, Project, Resource, ResourceRecord
from content_stack.repositories.base import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _normalise_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", data={"limit": limit})
    if limit > MAX_PAGE_LIMIT:
        raise ValidationError(
            f"limit must be <= {MAX_PAGE_LIMIT}",
            data={"limit": limit, "max": MAX_PAGE_LIMIT},
        )
    return limit


def _scalar_count(session: Session, statement: Any) -> int:
    raw = session.exec(statement).one()
    if isinstance(raw, tuple):
        return int(raw[0])
    try:
        return int(raw[0])  # type: ignore[index]
    except (KeyError, TypeError):
        pass
    return int(raw)


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plugin_id: int
    plugin_slug: str
    key: str
    name: str
    description: str
    schema_json: dict[str, Any]
    ui_schema_json: dict[str, Any] | None
    config_json: dict[str, Any] | None


class ResourceRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    resource_id: int
    plugin_slug: str
    resource_key: str
    external_id: str | None
    title: str | None
    data_json: dict[str, Any]
    provenance_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ResourceGetOut(BaseModel):
    resource: ResourceOut | None = None
    record: ResourceRecordOut | None = None


class ResourceQueryOut(BaseModel):
    resources: list[ResourceOut]
    records: list[ResourceRecordOut]
    next_cursor: int | None = None
    total_estimate: int = 0


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    plugin_id: int | None
    plugin_slug: str | None
    resource_record_id: int | None
    kind: str
    uri: str
    name: str | None
    mime_type: str | None
    size_bytes: int | None
    metadata_json: dict[str, Any] | None
    provenance_json: dict[str, Any] | None
    created_at: datetime


class ResourceRepository:
    """Read and write plugin-defined generic resource records."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def list_resources(self, *, plugin_slug: str | None = None) -> list[ResourceOut]:
        self._sync_catalog()
        rows = self._resource_rows(plugin_slug=plugin_slug)
        return [self._resource_out(resource, plugin) for resource, plugin in rows]

    def get_resource(self, *, key: str, plugin_slug: str | None = None) -> ResourceOut:
        self._sync_catalog()
        resource, plugin = self._get_resource_pair(key=key, plugin_slug=plugin_slug)
        return self._resource_out(resource, plugin)

    def get_record(self, record_id: int) -> ResourceRecordOut:
        self._sync_catalog()
        row = self._s.exec(
            select(ResourceRecord, Resource, Plugin)
            .join(Resource, ResourceRecord.resource_id == Resource.id)
            .join(Plugin, Resource.plugin_id == Plugin.id)
            .where(ResourceRecord.id == record_id)
        ).first()
        if row is None:
            raise NotFoundError(f"resource record {record_id} not found")
        record, resource, plugin = row
        return self._record_out(record, resource, plugin)

    def query_records(
        self,
        *,
        project_id: int,
        plugin_slug: str | None = None,
        resource_key: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ResourceRecordOut]:
        self._sync_catalog()
        n = _normalise_limit(limit)
        filters = [ResourceRecord.project_id == project_id]
        if plugin_slug is not None:
            filters.append(Plugin.slug == plugin_slug)
        if resource_key is not None:
            filters.append(Resource.key == resource_key)

        count_stmt = (
            sa_select(func.count())
            .select_from(ResourceRecord)
            .join(Resource, ResourceRecord.resource_id == Resource.id)
            .join(Plugin, Resource.plugin_id == Plugin.id)
            .where(*filters)
        )
        total = _scalar_count(self._s, count_stmt)

        stmt = (
            select(ResourceRecord, Resource, Plugin)
            .join(Resource, ResourceRecord.resource_id == Resource.id)
            .join(Plugin, Resource.plugin_id == Plugin.id)
            .where(*filters)
        )
        if after_id is not None:
            stmt = stmt.where(ResourceRecord.id > after_id)
        rows = list(
            self._s.exec(
                stmt.order_by(ResourceRecord.id.asc()).limit(n + 1)  # type: ignore[union-attr]
            ).all()
        )
        page_rows = rows[:n]
        next_cursor = int(page_rows[-1][0].id) if len(rows) > n and page_rows else None
        return Page(
            items=[
                self._record_out(record, resource, plugin)
                for record, resource, plugin in page_rows
            ],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def query(
        self,
        *,
        project_id: int | None = None,
        plugin_slug: str | None = None,
        resource_key: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ResourceQueryOut:
        resources = self.list_resources(plugin_slug=plugin_slug)
        if resource_key is not None:
            resources = [resource for resource in resources if resource.key == resource_key]
        if project_id is None:
            return ResourceQueryOut(resources=resources, records=[], total_estimate=0)
        records = self.query_records(
            project_id=project_id,
            plugin_slug=plugin_slug,
            resource_key=resource_key,
            limit=limit,
            after_id=after_id,
        )
        return ResourceQueryOut(
            resources=resources,
            records=records.items,
            next_cursor=records.next_cursor,
            total_estimate=records.total_estimate,
        )

    def upsert_record(
        self,
        *,
        project_id: int,
        plugin_slug: str,
        resource_key: str,
        data_json: dict[str, Any],
        record_id: int | None = None,
        external_id: str | None = None,
        title: str | None = None,
        provenance_json: dict[str, Any] | None = None,
    ) -> Envelope[ResourceRecordOut]:
        self._sync_catalog()
        self._require_project(project_id)
        resource, plugin = self._get_resource_pair(key=resource_key, plugin_slug=plugin_slug)
        row = self._find_record(
            project_id=project_id,
            resource_id=int(resource.id),
            record_id=record_id,
            external_id=external_id,
        )
        now = _utcnow()
        clean_data = redact_secrets(data_json)
        clean_provenance = redact_secrets(provenance_json) if provenance_json is not None else None
        if row is None:
            row = ResourceRecord(
                project_id=project_id,
                resource_id=int(resource.id),
                external_id=external_id,
                title=title,
                data_json=clean_data,
                provenance_json=clean_provenance,
            )
        else:
            row.external_id = external_id if external_id is not None else row.external_id
            row.title = title
            row.data_json = clean_data
            row.provenance_json = clean_provenance
            row.updated_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=self._record_out(row, resource, plugin),
            project_id=project_id,
        )

    def _find_record(
        self,
        *,
        project_id: int,
        resource_id: int,
        record_id: int | None,
        external_id: str | None,
    ) -> ResourceRecord | None:
        if record_id is not None:
            row = self._s.get(ResourceRecord, record_id)
            if row is None:
                raise NotFoundError(f"resource record {record_id} not found")
            if row.project_id != project_id or row.resource_id != resource_id:
                raise ConflictError(
                    "resource record does not match requested project/resource",
                    data={
                        "record_id": record_id,
                        "project_id": project_id,
                        "resource_id": resource_id,
                    },
                )
            return row
        if external_id is None:
            return None
        return self._s.exec(
            select(ResourceRecord).where(
                ResourceRecord.project_id == project_id,
                ResourceRecord.resource_id == resource_id,
                ResourceRecord.external_id == external_id,
            )
        ).first()

    def _resource_rows(self, *, plugin_slug: str | None = None) -> list[tuple[Resource, Plugin]]:
        stmt = select(Resource, Plugin).join(Plugin, Resource.plugin_id == Plugin.id)
        if plugin_slug is not None:
            stmt = stmt.where(Plugin.slug == plugin_slug)
        return list(self._s.exec(stmt.order_by(Plugin.slug.asc(), Resource.key.asc())).all())

    def _get_resource_pair(
        self,
        *,
        key: str,
        plugin_slug: str | None = None,
    ) -> tuple[Resource, Plugin]:
        rows = [
            (resource, plugin)
            for resource, plugin in self._resource_rows(plugin_slug=plugin_slug)
            if resource.key == key
        ]
        if not rows:
            raise NotFoundError(f"resource {key!r} not found")
        if len(rows) > 1 and plugin_slug is None:
            raise ConflictError(
                "resource key is ambiguous; pass plugin_slug",
                data={"key": key, "plugin_slugs": sorted(plugin.slug for _, plugin in rows)},
            )
        return rows[0]

    def _resource_out(self, row: Resource, plugin: Plugin) -> ResourceOut:
        assert row.id is not None and row.plugin_id is not None
        return ResourceOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug,
            key=row.key,
            name=row.name,
            description=row.description,
            schema_json=row.schema_json,
            ui_schema_json=row.ui_schema_json,
            config_json=row.config_json,
        )

    def _record_out(
        self,
        row: ResourceRecord,
        resource: Resource,
        plugin: Plugin,
    ) -> ResourceRecordOut:
        assert row.id is not None and row.resource_id is not None
        return ResourceRecordOut(
            id=row.id,
            project_id=row.project_id,
            resource_id=row.resource_id,
            plugin_slug=plugin.slug,
            resource_key=resource.key,
            external_id=row.external_id,
            title=row.title,
            data_json=redact_secrets(row.data_json),
            provenance_json=redact_secrets(row.provenance_json)
            if row.provenance_json is not None
            else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _sync_catalog(self) -> None:
        from content_stack.repositories.plugins import PluginRepository

        PluginRepository(self._s).sync_builtin_plugins()


class ArtifactRepository:
    """Read and write generic artifact references."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        *,
        kind: str,
        uri: str,
        project_id: int | None = None,
        plugin_slug: str | None = None,
        resource_record_id: int | None = None,
        name: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        metadata_json: dict[str, Any] | None = None,
        provenance_json: dict[str, Any] | None = None,
    ) -> Envelope[ArtifactOut]:
        self._sync_catalog()
        if not kind:
            raise ValidationError("kind is required")
        if not uri:
            raise ValidationError("uri is required")
        if project_id is not None:
            self._require_project(project_id)
        plugin = self._plugin_row(plugin_slug) if plugin_slug is not None else None
        record = self._s.get(ResourceRecord, resource_record_id) if resource_record_id else None
        if resource_record_id is not None and record is None:
            raise NotFoundError(f"resource record {resource_record_id} not found")
        if record is not None:
            if project_id is None:
                project_id = record.project_id
            elif record.project_id != project_id:
                raise ConflictError(
                    "artifact project does not match resource record project",
                    data={"project_id": project_id, "resource_record_id": resource_record_id},
                )
        row = Artifact(
            project_id=project_id,
            plugin_id=plugin.id if plugin is not None else None,
            resource_record_id=resource_record_id,
            kind=kind,
            uri=uri,
            name=name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            provenance_json=(
                redact_secrets(provenance_json) if provenance_json is not None else None
            ),
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._artifact_out(row, plugin), project_id=project_id)

    def get(self, artifact_id: int) -> ArtifactOut:
        self._sync_catalog()
        row = self._s.get(Artifact, artifact_id)
        if row is None:
            raise NotFoundError(f"artifact {artifact_id} not found")
        plugin = self._s.get(Plugin, row.plugin_id) if row.plugin_id is not None else None
        return self._artifact_out(row, plugin)

    def query(
        self,
        *,
        project_id: int | None = None,
        plugin_slug: str | None = None,
        resource_record_id: int | None = None,
        kind: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ArtifactOut]:
        self._sync_catalog()
        n = _normalise_limit(limit)
        filters = []
        if project_id is not None:
            filters.append(Artifact.project_id == project_id)
        if resource_record_id is not None:
            filters.append(Artifact.resource_record_id == resource_record_id)
        if kind is not None:
            filters.append(Artifact.kind == kind)
        if plugin_slug is not None:
            filters.append(Plugin.slug == plugin_slug)

        count_stmt = (
            sa_select(func.count())
            .select_from(Artifact)
            .outerjoin(Plugin, Artifact.plugin_id == Plugin.id)
            .where(*filters)
        )
        total = _scalar_count(self._s, count_stmt)

        stmt = select(Artifact, Plugin).outerjoin(Plugin, Artifact.plugin_id == Plugin.id)
        if filters:
            stmt = stmt.where(*filters)
        if after_id is not None:
            stmt = stmt.where(Artifact.id > after_id)
        rows = list(
            self._s.exec(stmt.order_by(Artifact.id.asc()).limit(n + 1)).all()  # type: ignore[union-attr]
        )
        page_rows = rows[:n]
        next_cursor = int(page_rows[-1][0].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._artifact_out(artifact, plugin) for artifact, plugin in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def _artifact_out(self, row: Artifact, plugin: Plugin | None) -> ArtifactOut:
        assert row.id is not None
        return ArtifactOut(
            id=row.id,
            project_id=row.project_id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug if plugin is not None else None,
            resource_record_id=row.resource_record_id,
            kind=row.kind,
            uri=row.uri,
            name=row.name,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            metadata_json=row.metadata_json,
            provenance_json=row.provenance_json,
            created_at=row.created_at,
        )

    def _plugin_row(self, plugin_slug: str) -> Plugin:
        row = self._s.exec(select(Plugin).where(Plugin.slug == plugin_slug)).first()
        if row is None:
            raise NotFoundError(f"plugin {plugin_slug!r} not found")
        return row

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _sync_catalog(self) -> None:
        from content_stack.repositories.plugins import PluginRepository

        PluginRepository(self._s).sync_builtin_plugins()


__all__ = [
    "ArtifactOut",
    "ArtifactRepository",
    "ResourceGetOut",
    "ResourceOut",
    "ResourceQueryOut",
    "ResourceRecordOut",
    "ResourceRepository",
]
