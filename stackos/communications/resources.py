"""Shared communication resource lookup helpers."""

from __future__ import annotations

from sqlmodel import Session, col, select

from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.repositories.resources import PreparedInboundResources, ResourceRepository


def communication_record_by_external_id(
    session: Session,
    *,
    project_id: int,
    resource_key: str,
    external_id: str,
) -> ResourceRecord | None:
    """Resolve one communications resource record by external id."""

    ResourceRepository(session).list_resources(
        plugin_slug="communications",
        project_id=project_id,
    )
    return session.exec(
        select(ResourceRecord)
        .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
        .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
        .where(
            col(ResourceRecord.project_id) == project_id,
            col(ResourceRecord.external_id) == external_id,
            col(Resource.key) == resource_key,
            col(Plugin.slug) == "communications",
        )
    ).first()


def communication_record_by_external_id_in_transaction(
    session: Session,
    prepared: PreparedInboundResources,
    *,
    project_id: int,
    resource_key: str,
    external_id: str,
) -> ResourceRecord | None:
    """Resolve a prepared communications record without catalog synchronization."""
    return ResourceRepository(session).find_record_in_transaction(
        prepared,
        project_id=project_id,
        plugin_slug="communications",
        resource_key=resource_key,
        external_id=external_id,
    )
