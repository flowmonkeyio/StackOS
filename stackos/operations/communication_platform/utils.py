"""Shared validation, resource lookup, and response shaping helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from stackos.communications import communication_profile_ref
from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRepository
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

from .constants import _CONTEXT_ALLOWED_FIELDS, _PROFILE_KEY_RE
from .schemas import (
    CommunicationContactOut,
    CommunicationMembershipOut,
    CommunicationProfileOut,
    CommunicationRouteOut,
    CommunicationSurfaceOut,
    CommunicationTargetOut,
)


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _require_project(session: Session, project_id: int) -> None:
    ProjectRepository(session).get(project_id)


def _validate_profile_key(value: str) -> None:
    if not _PROFILE_KEY_RE.fullmatch(value.strip()):
        raise ValidationError("communication keys must be 1-80 chars of letters, numbers, _, or -")


def _validate_provider_key(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", value.strip()):
        raise ValidationError("provider_key must be a stable provider key")


def _validate_ref(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240:
        raise ValidationError(f"{label} must be a non-empty ref up to 240 chars")


def _validate_identity(value: dict[str, Any]) -> None:
    display_name = value.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValidationError("identity.display_name is required")


def _validate_no_setup_secrets(operation: str, fields: dict[str, Any]) -> None:
    paths = find_run_plan_secret_paths(fields)
    if paths:
        raise ValidationError(
            f"{operation} setup fields must not contain secrets; use auth profiles "
            "or opaque credential_ref values: " + ", ".join(paths[:8])
        )


def _communication_profile_ref(key: str) -> str:
    return communication_profile_ref(key)


def _communication_target_ref(key: str) -> str:
    return f"communication-target:{key.strip()}"


def _communication_contact_ref(key: str) -> str:
    return f"communication-contact:{key.strip()}"


def _communication_route_ref(key: str) -> str:
    return f"communication-route:{key.strip()}"


def _surface_external_id(surface_ref: str) -> str:
    return f"communication-surface:{surface_ref.strip()}"


def _membership_ref(surface_ref: str, member_ref: str) -> str:
    return f"communication-membership:{surface_ref.strip()}:{member_ref.strip()}"


def _record_by_resource_external_id(
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


def _resource_records(
    session: Session,
    *,
    project_id: int,
    resource_key: str,
) -> list[ResourceRecord]:
    ResourceRepository(session).list_resources(plugin_slug="communications", project_id=project_id)
    return list(
        session.exec(
            select(ResourceRecord)
            .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
            .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
            .where(
                col(ResourceRecord.project_id) == project_id,
                col(Resource.key) == resource_key,
                col(Plugin.slug) == "communications",
            )
            .order_by(col(ResourceRecord.id).asc())
        ).all()
    )


def _communication_profile_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationProfileOut:
    key = str(data.get("key") or "")
    return CommunicationProfileOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        profile_ref=str(data.get("profile_ref") or _communication_profile_ref(key)),
        key=key,
        enabled=bool(data.get("enabled", True)),
        identity=dict(data.get("identity") or {}),
        agent_guidance=dict(data.get("agent_guidance") or {}),
        provider_facets={
            str(k): dict(v)
            for k, v in dict(data.get("provider_facets") or {}).items()
            if isinstance(v, dict)
        },
        access_policy=dict(data.get("access_policy") or {}),
        visibility_policy=dict(data.get("visibility_policy") or {}),
        trigger_policy=dict(data.get("trigger_policy") or {}),
        context_policy=dict(data.get("context_policy") or {}),
        response_policy=dict(data.get("response_policy") or {}),
        send_policy=dict(data.get("send_policy") or {}),
        handoff_policy=dict(data.get("handoff_policy") or {}),
        approval_policy=dict(data.get("approval_policy") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_surface_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationSurfaceOut:
    surface_ref = str(data.get("surface_ref") or data.get("channel_ref") or "")
    return CommunicationSurfaceOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        surface_ref=surface_ref,
        channel_ref=str(data.get("channel_ref") or surface_ref),
        provider_key=str(data.get("provider_key") or ""),
        kind=str(data.get("kind") or data.get("channel_type") or ""),
        display_name=(
            data.get("display_name") if isinstance(data.get("display_name"), str) else None
        ),
        credential_ref=(
            data.get("credential_ref") if isinstance(data.get("credential_ref"), str) else None
        ),
        safe_external_ref=(
            data.get("safe_external_ref")
            if isinstance(data.get("safe_external_ref"), str)
            else None
        ),
        ingest_enabled=bool(data.get("ingest_enabled", True)),
        send_enabled=bool(data.get("send_enabled", True)),
        capabilities=dict(data.get("capabilities") or {}),
        audience=str(data.get("audience") or "unknown"),
        intent=dict(data.get("intent") or {}),
        agent_guidance=dict(data.get("agent_guidance") or {}),
        data_scope=dict(data.get("data_scope") or {}),
        external_context=dict(data.get("external_context") or {}),
        metadata_json=dict(data.get("metadata_json") or data.get("metadata") or {}),
    )


def _communication_contact_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationContactOut:
    key = str(data.get("key") or "")
    provider_refs: dict[str, list[str]] = {}
    for provider, refs in dict(data.get("provider_refs") or {}).items():
        provider_refs[str(provider)] = _string_list(refs)
    return CommunicationContactOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        contact_ref=str(data.get("contact_ref") or _communication_contact_ref(key)),
        key=key,
        display_name=str(data.get("display_name") or key),
        kind=str(data.get("kind") or "person"),
        status=str(data.get("status") or "unknown"),
        provider_refs=provider_refs,
        safe_external_refs=_string_list(data.get("safe_external_refs")),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_membership_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationMembershipOut:
    surface_ref = str(data.get("surface_ref") or "")
    member_ref = str(data.get("member_ref") or "")
    return CommunicationMembershipOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        membership_ref=str(data.get("membership_ref") or _membership_ref(surface_ref, member_ref)),
        surface_ref=surface_ref,
        member_ref=member_ref,
        provider_key=str(data.get("provider_key") or ""),
        membership_kind=str(data.get("membership_kind") or "profile"),
        status=str(data.get("status") or "unknown"),
        roles=[str(item) for item in data.get("roles") or []],
        permissions=dict(data.get("permissions") or {}),
        scope_status=dict(data.get("scope_status") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_target_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationTargetOut:
    key = str(data.get("key") or "")
    return CommunicationTargetOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        target_ref=str(data.get("target_ref") or _communication_target_ref(key)),
        key=key,
        display_name=(
            data.get("display_name") if isinstance(data.get("display_name"), str) else None
        ),
        provider_key=str(data.get("provider_key") or ""),
        surface_ref=str(data.get("surface_ref") or ""),
        profile_ref=data.get("profile_ref") if isinstance(data.get("profile_ref"), str) else None,
        thread_ref=data.get("thread_ref") if isinstance(data.get("thread_ref"), str) else None,
        enabled=bool(data.get("enabled", True)),
        action_ref=data.get("action_ref") if isinstance(data.get("action_ref"), str) else None,
        action_input_defaults=dict(data.get("action_input_defaults") or {}),
        send_policy=dict(data.get("send_policy") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_route_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationRouteOut:
    key = str(data.get("key") or "")
    return CommunicationRouteOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        route_ref=str(data.get("route_ref") or _communication_route_ref(key)),
        key=key,
        enabled=bool(data.get("enabled", True)),
        source_surface_refs=_string_list(data.get("source_surface_refs")),
        target_refs=_string_list(data.get("target_refs")),
        allowed_profile_refs=_string_list(data.get("allowed_profile_refs")),
        requires_approval=bool(data.get("requires_approval", False)),
        field_policy=dict(data.get("field_policy") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _default_action_ref(provider_key: str) -> str | None:
    match provider_key.strip():
        case "telegram-bot":
            return "communications.telegram-bot.message.send"
        case "slack-bot":
            return "communications.slack-bot.message.send"
        case "smtp":
            return "communications.smtp.email.send"
        case "local-agent-chat":
            return "localAgentChat.createMessage"
        case _:
            return None


def _target_action_defaults(
    session: Session,
    target: CommunicationTargetOut,
) -> dict[str, Any]:
    defaults = dict(target.action_input_defaults or {})
    if target.provider_key == "slack-bot":
        defaults.setdefault("surface_ref", target.surface_ref)
        if target.profile_ref:
            defaults.setdefault("profile_ref", target.profile_ref)
        if target.thread_ref:
            defaults.setdefault("thread_ref", target.thread_ref)
    elif target.provider_key == "telegram-bot":
        defaults.setdefault("chat_ref", target.surface_ref)
        profile_key = _telegram_profile_key(session, target)
        if profile_key:
            defaults.setdefault("profile_key", profile_key)
        if target.thread_ref:
            defaults.setdefault("thread_ref", target.thread_ref)
    return defaults


def _telegram_profile_key(
    session: Session,
    target: CommunicationTargetOut,
) -> str | None:
    explicit = target.action_input_defaults.get("profile_key")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if isinstance(target.profile_ref, str) and target.profile_ref.startswith(
        "communication-profile:"
    ):
        row = _record_by_resource_external_id(
            session,
            project_id=target.project_id,
            resource_key="communication-profile",
            external_id=target.profile_ref,
        )
        if row is not None:
            facets = dict((row.data_json or {}).get("provider_facets") or {})
            if isinstance(facets.get("telegram-bot"), dict):
                return target.profile_ref.split(":", 1)[1].strip() or None
    return None


def _target_policy_allowed(
    policy: dict[str, Any],
    *,
    target_ref: str,
    profile_ref: str | None,
    source_surface_ref: str | None,
    invoker_ref: str | None,
) -> tuple[bool, str | None]:
    mode = str(policy.get("mode") or "explicit-target")
    if mode in {"disabled", "deny"}:
        return False, "send_policy_disabled"
    denied_invokers = set(_string_list(policy.get("denied_invoker_refs")))
    if invoker_ref is not None and invoker_ref in denied_invokers:
        return False, "invoker_denied"
    allowed_profiles = set(_string_list(policy.get("allowed_profile_refs")))
    allowed_sources = set(_string_list(policy.get("allowed_source_surface_refs")))
    allowed_targets = set(_string_list(policy.get("allowed_target_refs")))
    allowed_invokers = set(_string_list(policy.get("allowed_invoker_refs")))
    if mode == "denylist" and not (
        allowed_profiles or allowed_sources or allowed_targets or allowed_invokers
    ):
        if policy.get("requires_approval") is True:
            return False, "approval_required"
        return True, None
    if (
        not allowed_profiles
        and not allowed_sources
        and not allowed_targets
        and not allowed_invokers
    ):
        return False, "send_policy_missing_allowlist"
    if allowed_profiles and profile_ref not in allowed_profiles:
        return False, "profile_not_allowed"
    if allowed_sources and source_surface_ref not in allowed_sources:
        return False, "source_surface_not_allowed"
    if allowed_targets and target_ref not in allowed_targets:
        return False, "target_not_allowed"
    if allowed_invokers and invoker_ref not in allowed_invokers:
        return False, "invoker_not_allowed"
    if policy.get("requires_approval") is True:
        return False, "approval_required"
    return True, None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _select_context_fields(data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: data.get(field) for field in fields if field in _CONTEXT_ALLOWED_FIELDS}
