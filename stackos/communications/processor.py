"""Shared inbound communication processing.

Provider ingress adapters verify transport auth and normalize payloads. This
module owns the common storage, request dedupe, and agent-request creation path.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, TypeGuard

from sqlmodel import Session

from stackos.artifacts import redact_secrets
from stackos.communications.contracts import (
    CommunicationDecision,
    CommunicationProcessingResult,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
)
from stackos.communications.resources import communication_record_by_external_id
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ConflictError, NotFoundError
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
    if event.provider_key == "slack-bot":
        occurrence_check = event.request_metadata_json.get("source_occurrence_check")
        if existing_request is not None and occurrence_check in {"compare", "unverifiable"}:
            status, mismatch_fields, stored_occurrence = _classify_slack_occurrence(
                session,
                project_id=project_id,
                request=existing_request,
                incoming_metadata=event.request_metadata_json,
                incoming_source_message_ref=event.source_message_ref,
            )
            if occurrence_check == "unverifiable" and status != "source_occurrence_conflict":
                status = "source_occurrence_unverifiable"
            if status != "request_deduped":
                diagnostic_id = _persist_occurrence_diagnostic(
                    session,
                    project_id=project_id,
                    profile_key=event.profile_key,
                    request_id=existing_request.id,
                    event_key=event.event_key,
                    request_key=event.request_key or "",
                    status=status,
                    incoming_occurrence=event.request_metadata_json.get("source_occurrence_v1"),
                    stored_occurrence=stored_occurrence,
                    mismatch_fields=mismatch_fields,
                )
                return CommunicationProcessingResult(
                    ok=True,
                    provider_key=event.provider_key,
                    profile_key=event.profile_key,
                    event_key=event.event_key,
                    update_type=event.update_type,
                    policy_status=status,
                    event_record_id=diagnostic_id,
                    message_record_id=None,
                    interaction_record_id=None,
                    agent_request_id=existing_request.id,
                    response_json=event.response_json,
                )
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
    for related_resource in event.related_resources:
        _upsert_optional(
            session,
            resources,
            project_id=project_id,
            write=related_resource,
            deduped=existing_request is not None,
        )
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
            **{
                key: value
                for key, value in event.request_metadata_json.items()
                if not (event.provider_key == "slack-bot" and key == "source_occurrence_check")
            },
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


_SOURCE_OCCURRENCE_FIELDS = (
    "schema_version",
    "provider_key",
    "source_kind",
    "profile_key",
    "team_id",
    "channel_id",
    "surface_ref",
    "message_ts",
    "message_ref",
    "thread_ref",
    "invoker_ref",
    "content_type",
    "normalized_text_sha256",
)
_SOURCE_OCCURRENCE_REQUIRED_TEXT = frozenset(
    {
        "provider_key",
        "source_kind",
        "profile_key",
        "channel_id",
        "surface_ref",
        "message_ts",
        "message_ref",
        "content_type",
        "normalized_text_sha256",
    }
)
_OCCURRENCE_DIAGNOSTIC_FIELDS = (
    "source_provider",
    "source_kind",
    "source_message_ref",
    "source_resource_key",
    "source_resource_record_id",
    "resource_record.project_id",
    "resource.plugin_slug",
    "resource.key",
    "resource_record.external_id",
    *(f"source_occurrence_v1.{field}" for field in _SOURCE_OCCURRENCE_FIELDS),
)
_OCCURRENCE_DIAGNOSTIC_FIELD_SET = frozenset(_OCCURRENCE_DIAGNOSTIC_FIELDS)
_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _classify_slack_occurrence(
    session: Session,
    *,
    project_id: int,
    request: Any,
    incoming_metadata: dict[str, Any],
    incoming_source_message_ref: str | None,
) -> tuple[str, list[str], dict[str, Any] | None]:
    """Classify one Slack same-key retry before any related resource writes."""

    mismatches: set[str] = set()
    request_metadata = request.metadata_json if isinstance(request.metadata_json, dict) else {}
    incoming = incoming_metadata.get("source_occurrence_v1")
    incoming_occurrence = incoming if _valid_source_occurrence(incoming) else None
    incoming_valid = incoming_occurrence is not None

    def mismatch(field: str) -> None:
        if field in _OCCURRENCE_DIAGNOSTIC_FIELD_SET:
            mismatches.add(field)

    if request.source_provider != "slack-bot":
        mismatch("source_provider")
    if request.source_kind != "slack-message":
        mismatch("source_kind")
    if request.source_resource_key != "communication-message":
        mismatch("source_resource_key")
    if type(request.source_resource_record_id) is not int or request.source_resource_record_id <= 0:
        mismatch("source_resource_record_id")
    if (
        not isinstance(request.source_message_ref, str)
        or not request.source_message_ref
        or (
            incoming_occurrence is not None
            and request.source_message_ref != incoming_occurrence["message_ref"]
        )
        or (
            incoming_source_message_ref is not None
            and request.source_message_ref != incoming_source_message_ref
        )
    ):
        mismatch("source_message_ref")

    record = None
    record_id = request.source_resource_record_id
    if type(record_id) is int and record_id > 0:
        try:
            record = ResourceRepository(session).get_record(record_id)
        except NotFoundError:
            mismatch("source_resource_record_id")

    stored_data = (
        record.data_json if record is not None and isinstance(record.data_json, dict) else {}
    )
    if record is not None:
        if type(record.project_id) is not int or record.project_id != project_id:
            mismatch("resource_record.project_id")
        if record.plugin_slug != "communications":
            mismatch("resource.plugin_slug")
        if record.resource_key != "communication-message":
            mismatch("resource.key")
        if not _slack_message_external_id_matches(record, stored_data, incoming):
            mismatch("resource_record.external_id")
        if stored_data.get("provider_key") != "slack-bot":
            mismatch("source_occurrence_v1.provider_key")
        if stored_data.get("content_type") != "text":
            mismatch("source_occurrence_v1.content_type")

    stored_request_occurrence = request_metadata.get("source_occurrence_v1")
    stored_message_occurrence = stored_data.get("source_occurrence_v1")
    request_occurrence = (
        stored_request_occurrence if _valid_source_occurrence(stored_request_occurrence) else None
    )
    message_occurrence = (
        stored_message_occurrence if _valid_source_occurrence(stored_message_occurrence) else None
    )
    legacy = (
        "source_occurrence_v1" not in request_metadata and "source_occurrence_v1" not in stored_data
    )
    request_binding = {
        "profile_key": request_metadata.get("profile_key"),
        "surface_ref": request_metadata.get("surface_ref"),
        "thread_ref": request_metadata.get("thread_ref"),
        "invoker_ref": request_metadata.get("invoker_ref"),
    }
    resource_binding = {
        "provider_key": stored_data.get("provider_key"),
        "profile_key": stored_data.get("profile_key"),
        "team_id": stored_data.get("team_id"),
        "channel_id": _channel_id_from_surface(stored_data.get("surface_ref")),
        "surface_ref": stored_data.get("surface_ref"),
        "message_ts": stored_data.get("provider_message_ts"),
        "message_ref": stored_data.get("message_ref"),
        "thread_ref": stored_data.get("thread_ref"),
        "invoker_ref": stored_data.get("from_ref"),
        "content_type": stored_data.get("content_type"),
    }
    required_request_keys = ("profile_key", "surface_ref", "thread_ref", "invoker_ref")
    for field in required_request_keys:
        if field not in request_metadata:
            mismatch(f"source_occurrence_v1.{field}")
    required_resource_keys = (
        "provider_key",
        "profile_key",
        "team_id",
        "surface_ref",
        "channel_ref",
        "thread_ref",
        "message_ref",
        "provider_message_ts",
        "content_type",
        "from_ref",
    )
    for key in required_resource_keys:
        if key not in stored_data:
            target_field = {
                "provider_key": "provider_key",
                "profile_key": "profile_key",
                "team_id": "team_id",
                "surface_ref": "surface_ref",
                "channel_ref": "surface_ref",
                "thread_ref": "thread_ref",
                "message_ref": "message_ref",
                "provider_message_ts": "message_ts",
                "content_type": "content_type",
                "from_ref": "invoker_ref",
            }[key]
            mismatch(f"source_occurrence_v1.{target_field}")
    if request_binding["profile_key"] != stored_data.get("profile_key"):
        mismatch("source_occurrence_v1.profile_key")
    if request_binding["surface_ref"] != stored_data.get("surface_ref"):
        mismatch("source_occurrence_v1.surface_ref")
    if request_binding["thread_ref"] != stored_data.get("thread_ref"):
        mismatch("source_occurrence_v1.thread_ref")
    if request_binding["invoker_ref"] != stored_data.get("from_ref"):
        mismatch("source_occurrence_v1.invoker_ref")
    if request.source_message_ref != stored_data.get("message_ref"):
        mismatch("source_message_ref")
    if request_metadata.get("channel_ref") != stored_data.get("channel_ref"):
        mismatch("source_occurrence_v1.surface_ref")
    if stored_data.get("channel_ref") != stored_data.get("surface_ref"):
        mismatch("source_occurrence_v1.surface_ref")

    for stored_occurrence in (request_occurrence, message_occurrence):
        if stored_occurrence is None:
            continue
        for field, actual in resource_binding.items():
            if actual != stored_occurrence[field]:
                mismatch(f"source_occurrence_v1.{field}")
        for field, actual in request_binding.items():
            if actual != stored_occurrence[field]:
                mismatch(f"source_occurrence_v1.{field}")
        if request.source_message_ref != stored_occurrence["message_ref"]:
            mismatch("source_message_ref")

    if incoming_occurrence is not None:
        for field, actual in resource_binding.items():
            if actual != incoming_occurrence[field]:
                mismatch(f"source_occurrence_v1.{field}")
        for field, actual in request_binding.items():
            if actual != incoming_occurrence[field]:
                mismatch(f"source_occurrence_v1.{field}")
        if incoming_source_message_ref != incoming_occurrence["message_ref"]:
            mismatch("source_message_ref")

    if legacy:
        for field, actual in resource_binding.items():
            if field in _SOURCE_OCCURRENCE_FIELDS and not _valid_occurrence_field(field, actual):
                mismatch(f"source_occurrence_v1.{field}")
        status = "source_occurrence_conflict" if mismatches else "source_occurrence_unverifiable"
        return status, _ordered_occurrence_mismatches(mismatches), None

    if request_occurrence is None or message_occurrence is None:
        mismatch("source_occurrence_v1.schema_version")
    if request_occurrence is not None and message_occurrence is not None:
        for field in _SOURCE_OCCURRENCE_FIELDS:
            if request_occurrence[field] != message_occurrence[field]:
                mismatch(f"source_occurrence_v1.{field}")
            if (
                incoming_occurrence is not None
                and incoming_occurrence[field] != request_occurrence[field]
            ):
                mismatch(f"source_occurrence_v1.{field}")

    status = "source_occurrence_conflict" if mismatches else "request_deduped"
    if incoming_occurrence is not None and request_occurrence is not None:
        stored_occurrence = request_occurrence
    else:
        stored_occurrence = None
    if not incoming_valid and not mismatches:
        status = "source_occurrence_unverifiable"
    return status, _ordered_occurrence_mismatches(mismatches), stored_occurrence


def _valid_occurrence_field(field: str, value: Any) -> bool:
    if field == "schema_version":
        return type(value) is int and value == 1
    if field in {"team_id", "thread_ref", "invoker_ref"}:
        return value is None or isinstance(value, str)
    if field == "normalized_text_sha256":
        return isinstance(value, str) and _LOWER_SHA256.fullmatch(value) is not None
    if field in _SOURCE_OCCURRENCE_REQUIRED_TEXT:
        return isinstance(value, str) and bool(value)
    return False


def _valid_source_occurrence(value: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and set(value) == set(_SOURCE_OCCURRENCE_FIELDS)
        and all(_valid_occurrence_field(field, value[field]) for field in _SOURCE_OCCURRENCE_FIELDS)
        and value["provider_key"] == "slack-bot"
        and value["source_kind"] == "slack-message"
        and value["content_type"] == "text"
    )


def _channel_id_from_surface(surface_ref: Any) -> str | None:
    prefix = "slack-channel:"
    if isinstance(surface_ref, str) and surface_ref.startswith(prefix):
        return surface_ref[len(prefix) :] or None
    return None


def _slack_message_external_id_matches(record: Any, data: dict[str, Any], occurrence: Any) -> bool:
    profile_key = data.get("profile_key")
    channel_id = occurrence.get("channel_id") if _valid_source_occurrence(occurrence) else None
    if channel_id is None:
        channel_id = _channel_id_from_surface(data.get("surface_ref"))
    message_ts = data.get("provider_message_ts")
    if not all(isinstance(value, str) and value for value in (profile_key, channel_id, message_ts)):
        return False
    return record.external_id == f"slack-message:{profile_key}:{channel_id}:{message_ts}"


def _ordered_occurrence_mismatches(values: set[str]) -> list[str]:
    return [field for field in _OCCURRENCE_DIAGNOSTIC_FIELDS if field in values]


def _occurrence_digest(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _persist_occurrence_diagnostic(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
    request_id: int,
    event_key: str,
    request_key: str,
    status: str,
    incoming_occurrence: Any,
    stored_occurrence: Any,
    mismatch_fields: list[str],
) -> int:
    safe_status = (
        status
        if status in {"source_occurrence_conflict", "source_occurrence_unverifiable"}
        else "source_occurrence_conflict"
    )
    diagnostic_digest = hashlib.sha256(
        json.dumps(
            [project_id, request_id, event_key, safe_status],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    external_id = f"slack-occurrence-diagnostic:{profile_key}:{diagnostic_digest}"
    data_json = {
        "schema_version": 1,
        "diagnostic_type": safe_status,
        "provider_key": "slack-bot",
        "profile_key": profile_key,
        "agent_request_id": request_id,
        "event_key_sha256": hashlib.sha256(event_key.encode("utf-8")).hexdigest(),
        "request_key_sha256": hashlib.sha256(request_key.encode("utf-8")).hexdigest(),
        "incoming_occurrence_sha256": _occurrence_digest(incoming_occurrence),
        "stored_occurrence_sha256": _occurrence_digest(stored_occurrence),
        "mismatch_fields": _ordered_occurrence_mismatches(set(mismatch_fields)),
    }
    title = "Slack source occurrence diagnostic"
    provenance = {"source": "slack-ingress"}
    existing = communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-event",
        external_id=external_id,
    )
    if existing is not None:
        existing_data = existing.data_json
        if not _strict_occurrence_diagnostic_projection(existing_data):
            raise ConflictError("Slack occurrence diagnostic id contains a different projection")
        if (
            existing.title != title
            or existing_data != data_json
            or existing.provenance_json != provenance
        ):
            raise ConflictError("Slack occurrence diagnostic id contains a different projection")
        if type(existing.id) is not int:
            raise ConflictError("Slack occurrence diagnostic id is unavailable")
        return existing.id
    saved = (
        ResourceRepository(session)
        .upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-event",
            external_id=external_id,
            title=title,
            data_json=data_json,
            provenance_json=provenance,
        )
        .data
    )
    return saved.id


def _strict_occurrence_diagnostic_projection(value: Any) -> bool:
    """Reject malformed stored JSON before Python's bool/int equality can match it."""

    expected_keys = {
        "schema_version",
        "diagnostic_type",
        "provider_key",
        "profile_key",
        "agent_request_id",
        "event_key_sha256",
        "request_key_sha256",
        "incoming_occurrence_sha256",
        "stored_occurrence_sha256",
        "mismatch_fields",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return False
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return False
    if type(value.get("agent_request_id")) is not int or value["agent_request_id"] <= 0:
        return False
    if any(
        not isinstance(value.get(field), str)
        for field in (
            "diagnostic_type",
            "provider_key",
            "profile_key",
            "event_key_sha256",
            "request_key_sha256",
        )
    ):
        return False
    if value["provider_key"] != "slack-bot":
        return False
    for field in ("event_key_sha256", "request_key_sha256"):
        digest = value[field]
        if _LOWER_SHA256.fullmatch(digest) is None:
            return False
    for field in ("incoming_occurrence_sha256", "stored_occurrence_sha256"):
        digest = value[field]
        if digest is not None and (
            not isinstance(digest, str) or _LOWER_SHA256.fullmatch(digest) is None
        ):
            return False
    mismatch_fields = value.get("mismatch_fields")
    return isinstance(mismatch_fields, list) and all(
        type(field) is str for field in mismatch_fields
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
        existing = communication_record_by_external_id(
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
    record = communication_record_by_external_id(
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
