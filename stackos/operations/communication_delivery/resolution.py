"""Routing, request-origin, target, and actor resolution for delivery."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from stackos.auth_providers.repository import AuthRepository
from stackos.communications import (
    communication_profile_ref,
    communication_record_by_external_id,
    merged_provider_profile,
)
from stackos.db.models import ResourceRecord
from stackos.operations.communication_platform import (
    CommunicationTargetOut,
    _communication_target_out,
    _default_action_ref,
    _string_list,
)
from stackos.repositories.agent_requests import AgentRequestOut, AgentRequestRepository

from .errors import _reject
from .schemas import CommunicationContextInput


def _source_context(
    session: Session,
    *,
    project_id: int,
    context: CommunicationContextInput,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_request_id": context.source_request_id,
        "source_surface_ref": context.source_surface_ref,
        "invoker_ref": context.invoker_ref,
    }
    if context.source_request_id is None:
        return data
    request = AgentRequestRepository(session).get(
        project_id=project_id,
        request_id=context.source_request_id,
    )
    data.update(_request_origin(session, project_id=project_id, request=request))
    if context.source_surface_ref:
        data["source_surface_ref"] = context.source_surface_ref
    if context.invoker_ref:
        data["invoker_ref"] = context.invoker_ref
    return data


def _request_origin(
    session: Session,
    *,
    project_id: int,
    request: AgentRequestOut,
) -> dict[str, Any]:
    metadata = dict(request.metadata_json or {})
    message_data: dict[str, Any] = {}
    if request.source_resource_record_id is not None:
        record = session.get(ResourceRecord, request.source_resource_record_id)
        if record is not None and record.project_id == project_id:
            message_data = dict(record.data_json or {})
    provider_key = (
        request.source_provider
        or message_data.get("provider_key")
        or metadata.get("provider_key")
        or ""
    )
    surface_ref = (
        message_data.get("surface_ref")
        or message_data.get("channel_ref")
        or message_data.get("chat_ref")
        or metadata.get("surface_ref")
        or metadata.get("channel_ref")
        or metadata.get("chat_ref")
    )
    return {
        "source_request_id": request.id,
        "provider_key": provider_key,
        "profile_ref": metadata.get("profile_ref") or message_data.get("profile_ref"),
        "profile_key": metadata.get("profile_key") or message_data.get("profile_key"),
        "source_surface_ref": surface_ref,
        "thread_ref": message_data.get("thread_ref") or metadata.get("thread_ref"),
        "message_ref": request.source_message_ref or message_data.get("message_ref"),
        "invoker_ref": metadata.get("invoker_ref") or message_data.get("sender_ref"),
    }


def _require_target(session: Session, *, project_id: int, to: str) -> CommunicationTargetOut:
    key = to.strip()
    external_id = key if key.startswith("communication-target:") else f"communication-target:{key}"
    record = communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-target",
        external_id=external_id,
    )
    if record is None:
        _reject(
            code="COMM_TARGET_NOT_FOUND",
            category="routing",
            message=f"Communication target {to!r} was not found.",
            resolved={"operation": "communication.send", "to": to},
            failed_paths=[{"path": "/to", "requested": to}],
            repair_options=[
                {
                    "id": "list_targets",
                    "description": "Call communicationTarget.list and choose a configured target.",
                },
                {
                    "id": "create_target",
                    "description": "Create a communicationTarget before sending.",
                },
            ],
        )
    assert record is not None
    return _communication_target_out(record.id, record.project_id, record.data_json or {})


def _origin_target(
    source: dict[str, Any],
    *,
    project_id: int,
    provider_key: str,
) -> CommunicationTargetOut:
    surface_ref = str(source.get("source_surface_ref") or "")
    return CommunicationTargetOut(
        record_id=0,
        project_id=project_id,
        target_ref=f"request-origin:{source.get('source_request_id')}",
        key=f"request-origin-{source.get('source_request_id')}",
        provider_key=provider_key,
        surface_ref=surface_ref,
        profile_ref=(
            str(source.get("profile_ref")) if isinstance(source.get("profile_ref"), str) else None
        ),
        thread_ref=str(source.get("thread_ref"))
        if isinstance(source.get("thread_ref"), str)
        else None,
        enabled=True,
        action_ref=_default_action_ref(provider_key),
        action_input_defaults={},
        send_policy={"mode": "explicit-target", "allowed_profile_refs": []},
        metadata_json={},
    )


def _resolve_actor(
    session: Session,
    *,
    project_id: int,
    provider_key: str,
    target: CommunicationTargetOut,
    requested_from: str | None,
    source_actor_ref: Any = None,
) -> dict[str, Any]:
    actor_ref = _choose_actor_ref(
        target=target,
        requested_from=requested_from,
        source_actor_ref=source_actor_ref,
    )
    if actor_ref is None:
        candidates = _candidate_actor_refs(target)
        _reject(
            code="COMM_AMBIGUOUS_ACTOR" if candidates else "COMM_ACTOR_REQUIRED",
            category="routing",
            message=(
                "Multiple communication profiles could send this message; pass from."
                if candidates
                else "No communication profile could be resolved for this message; pass from."
            ),
            resolved={
                "provider": provider_key,
                "target_ref": target.target_ref,
                "candidate_actor_refs": candidates,
            },
            failed_paths=[{"path": "/from", "requested": "communication-profile"}],
            repair_options=[
                {
                    "id": "pass_from",
                    "description": "Retry with from set to a communication profile key or ref.",
                }
            ],
        )
    assert actor_ref is not None
    actor_ref = _normalize_profile_ref(actor_ref)
    record = communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-profile",
        external_id=actor_ref,
    )
    if record is None:
        _reject(
            code="COMM_ACTOR_NOT_FOUND",
            category="routing",
            message=f"Communication profile {actor_ref!r} was not found.",
            resolved={"provider": provider_key, "actor_ref": actor_ref},
            failed_paths=[{"path": "/from", "requested": actor_ref}],
            repair_options=[
                {
                    "id": "list_profiles",
                    "description": "Call communicationProfile.list and choose an existing profile.",
                }
            ],
        )
    assert record is not None
    profile = dict(record.data_json or {})
    provider_profile = merged_provider_profile(profile, provider_key)
    if provider_key not in dict(profile.get("provider_facets") or {}):
        _reject(
            code="COMM_ACTOR_PROVIDER_UNSUPPORTED",
            category="routing",
            message=f"Profile {actor_ref} has no {provider_key} provider facet.",
            resolved={"provider": provider_key, "actor_ref": actor_ref},
            failed_paths=[{"path": "/from", "requested": actor_ref}],
            repair_options=[
                {
                    "id": "choose_profile_with_provider",
                    "description": f"Retry with a profile that has a {provider_key} facet.",
                }
            ],
        )
    requested_credential_ref = str(provider_profile.get("credential_ref") or "").strip()
    credential_ref = _attached_credential_ref(
        session,
        project_id=project_id,
        provider_key=provider_key,
        credential_ref=requested_credential_ref,
    )
    if credential_ref is None:
        _reject(
            code="COMM_CREDENTIAL_REQUIRED",
            category="setup",
            message=(f"No connected {provider_key} Account is attached for profile {actor_ref!r}."),
            resolved={
                "provider": provider_key,
                "actor_ref": actor_ref,
                "credential_ref": provider_profile.get("credential_ref"),
            },
            failed_paths=[{"path": "/from", "requested": actor_ref}],
            repair_options=[
                {
                    "id": "connect_provider",
                    "description": (
                        "Connect or repair the provider credential in StackOS connections."
                    ),
                }
            ],
        )
    return {
        "profile_ref": actor_ref,
        "profile_key": actor_ref.split(":", 1)[1],
        "credential_ref": credential_ref,
        "profile": profile,
        "provider_profile": provider_profile,
    }


def _choose_actor_ref(
    *,
    target: CommunicationTargetOut,
    requested_from: str | None,
    source_actor_ref: Any = None,
) -> str | None:
    if requested_from and requested_from.strip():
        return requested_from.strip()
    if target.profile_ref:
        return target.profile_ref
    policy = dict(target.send_policy or {})
    for key in ("default_actor_ref", "default_profile_ref"):
        value = policy.get(key) or target.metadata_json.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    candidates = _candidate_actor_refs(target)
    if len(candidates) == 1:
        return candidates[0]
    if isinstance(source_actor_ref, str) and source_actor_ref.strip():
        source_ref = _normalize_profile_ref(source_actor_ref)
        if not candidates or source_ref in candidates:
            return source_ref
    return None


def _candidate_actor_refs(target: CommunicationTargetOut) -> list[str]:
    policy = dict(target.send_policy or {})
    refs = _string_list(policy.get("allowed_profile_refs"))
    return [_normalize_profile_ref(item) for item in refs]


def _normalize_profile_ref(value: str) -> str:
    raw = value.strip()
    if raw.startswith("communication-profile:"):
        return raw
    return communication_profile_ref(raw)


def _attached_credential_ref(
    session: Session,
    *,
    project_id: int,
    provider_key: str,
    credential_ref: str,
) -> str | None:
    if not credential_ref:
        return None
    status = AuthRepository(session).status(
        project_id=project_id,
        provider_key=provider_key,
    )
    for account in status.accounts:
        if (
            account.credential_ref == credential_ref
            and account.status == "connected"
            and not account.setup_required
        ):
            return credential_ref
    return None


def _surface_data(session: Session, *, project_id: int, surface_ref: str) -> dict[str, Any]:
    if not surface_ref:
        return {}
    record = communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-channel",
        external_id=f"communication-surface:{surface_ref}",
    )
    return dict(record.data_json or {}) if record is not None else {}
