"""Shared inbound communication processing.

Provider ingress adapters verify transport auth and normalize payloads. This
module owns the common storage, request dedupe, and agent-request creation path.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, col, select

from stackos.artifacts import redact_secrets
from stackos.communications.contracts import (
    CommunicationDecision,
    CommunicationProcessingResult,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
)
from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository


def process_inbound_event(
    session: Session,
    *,
    project_id: int,
    event: NormalizedInboundEvent,
    decision: CommunicationDecision,
) -> CommunicationProcessingResult:
    """Store one normalized inbound event and maybe create generic agent work."""

    if not decision.store:
        return CommunicationProcessingResult(
            ok=True,
            provider_key=event.provider_key,
            profile_key=event.profile_key,
            event_key=event.event_key,
            update_type=event.update_type,
            policy_status=decision.status,
            response_json=event.response_json,
        )

    request_repo = AgentRequestRepository(session)
    existing_request = (
        request_repo.find_by_key(project_id=project_id, request_key=event.request_key)
        if event.request_key
        else None
    )
    create_request = decision.create_request and event.request_key is not None
    create_request = create_request and existing_request is None
    policy_status = "request_deduped" if existing_request is not None else decision.status
    policy_fields = _policy_fields(
        decision=decision,
        policy_status=policy_status,
        create_request=create_request,
        request_key=event.request_key,
        deduped_request_id=existing_request.id if existing_request is not None else None,
    )

    resources = ResourceRepository(session)
    surface_record_id = _upsert_optional(
        session,
        resources,
        project_id=project_id,
        write=event.surface,
    )
    _ = surface_record_id
    event_write = _merge_write(event.event, policy_fields)
    assert event_write is not None
    event_record_id = _upsert_required(
        session,
        resources,
        project_id=project_id,
        write=event_write,
        deduped=existing_request is not None,
    )
    message_record_id = _upsert_optional(
        session,
        resources,
        project_id=project_id,
        write=_merge_write(event.message, {"policy_status": policy_status}),
        deduped=existing_request is not None,
    )
    interaction_record_id = _upsert_optional(
        session,
        resources,
        project_id=project_id,
        write=_merge_write(event.interaction, {"status": policy_status}),
        deduped=existing_request is not None,
    )
    source_record_id = interaction_record_id or message_record_id or event_record_id
    source_resource_key = (
        "communication-interaction"
        if interaction_record_id is not None
        else "communication-message"
        if message_record_id is not None
        else "communication-event"
    )

    request_id = existing_request.id if existing_request is not None else None
    if create_request and event.request_key is not None:
        metadata_json = {
            **event.request_metadata_json,
            "event_record_id": event_record_id,
            "trigger_reason": decision.trigger_reason,
            "matched_command": decision.matched_command,
            **decision.metadata,
        }
        request = request_repo.create(
            project_id=project_id,
            request_key=event.request_key,
            title=event.request_title,
            body_preview=event.body_preview,
            source_provider=event.provider_key,
            source_kind=event.source_kind,
            source_resource_key=source_resource_key,
            source_resource_record_id=source_record_id,
            source_message_ref=event.source_message_ref,
            metadata_json=metadata_json,
        ).data
        request_id = request.id

    if create_request or existing_request is not None:
        for patch in event.state_patches:
            _apply_patch(session, project_id=project_id, patch=patch)

    return CommunicationProcessingResult(
        ok=True,
        provider_key=event.provider_key,
        profile_key=event.profile_key,
        event_key=event.event_key,
        update_type=event.update_type,
        policy_status=policy_status,
        event_record_id=event_record_id,
        message_record_id=message_record_id,
        interaction_record_id=interaction_record_id,
        agent_request_id=request_id,
        response_json=event.response_json,
    )


def _policy_fields(
    *,
    decision: CommunicationDecision,
    policy_status: str,
    create_request: bool,
    request_key: str | None,
    deduped_request_id: int | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "policy_status": policy_status,
        "triggered": create_request,
    }
    if request_key is not None:
        fields["request_key"] = request_key
    if deduped_request_id is not None:
        fields["deduped_request_id"] = deduped_request_id
    if decision.trigger_reason is not None:
        fields["trigger_reason"] = decision.trigger_reason
    if decision.matched_command is not None:
        fields["matched_command"] = decision.matched_command
    return fields


def _merge_write(
    write: NormalizedResourceWrite | None,
    values: dict[str, Any],
) -> NormalizedResourceWrite | None:
    if write is None:
        return None
    data_json = {**write.data_json, **values}
    return NormalizedResourceWrite(
        resource_key=write.resource_key,
        external_id=write.external_id,
        title=write.title,
        data_json=data_json,
        provenance_json=write.provenance_json,
        preserve_existing_on_dedupe=write.preserve_existing_on_dedupe,
    )


def _upsert_required(
    session: Session,
    resources: ResourceRepository,
    *,
    project_id: int,
    write: NormalizedResourceWrite,
    deduped: bool = False,
) -> int:
    return (
        _upsert_optional(
            session,
            resources,
            project_id=project_id,
            write=write,
            deduped=deduped,
        )
        or 0
    )


def _upsert_optional(
    session: Session,
    resources: ResourceRepository,
    *,
    project_id: int,
    write: NormalizedResourceWrite | None,
    deduped: bool = False,
) -> int | None:
    if write is None:
        return None
    if deduped and write.preserve_existing_on_dedupe:
        existing = _record_by_external_id(
            session,
            project_id=project_id,
            resource_key=write.resource_key,
            external_id=write.external_id,
        )
        if existing is not None:
            return existing.id
    record = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key=write.resource_key,
        external_id=write.external_id,
        title=write.title,
        data_json=write.data_json,
        provenance_json=write.provenance_json,
    ).data
    return record.id


def _apply_patch(
    session: Session,
    *,
    project_id: int,
    patch: NormalizedResourcePatch,
) -> None:
    record = _record_by_external_id(
        session,
        project_id=project_id,
        resource_key=patch.resource_key,
        external_id=patch.external_id,
    )
    if record is None:
        return
    record.data_json = {
        **dict(record.data_json or {}),
        **redact_secrets(patch.data_json),
    }
    session.add(record)
    session.commit()


def _record_by_external_id(
    session: Session,
    *,
    project_id: int,
    resource_key: str,
    external_id: str,
) -> ResourceRecord | None:
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
