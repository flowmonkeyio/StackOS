"""Communication profile helpers shared by providers."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, col, select

from stackos.auth_providers import AuthRepository
from stackos.communications.resources import communication_record_by_external_id
from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.repositories.base import ConflictError, ValidationError

_PROVIDER_OWNED_INGRESS_KEYS = frozenset({"slack-bot", "telegram-bot"})


def communication_profile_ref(key: str) -> str:
    return f"communication-profile:{key.strip()}"


def communication_profile_record_by_key(
    session: Session,
    *,
    project_id: int,
    key: str,
) -> ResourceRecord | None:
    return communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-profile",
        external_id=communication_profile_ref(key),
    )


def validate_communication_profile_account_bindings(
    session: Session,
    *,
    project_id: int,
    profile_ref: str,
    provider_facets: dict[str, dict[str, Any]],
) -> None:
    """Validate that every referenced Account is attached and provider-compatible."""

    accounts = AuthRepository(session)
    for provider_key, facet in provider_facets.items():
        credential_ref = str(facet.get("credential_ref") or "").strip()
        if not credential_ref:
            continue
        accounts.require_attached_account(
            project_id=project_id,
            credential_ref=credential_ref,
            provider_key=provider_key,
        )


def provider_ingress_enabled(facet: dict[str, Any]) -> bool:
    """Treat legacy facets as inbound while allowing explicit outbound-only reuse."""

    return facet.get("ingress_enabled") is not False


def validate_communication_profile_ingress_ownership(
    session: Session,
    *,
    project_id: int,
    profile_ref: str,
    provider_facets: dict[str, dict[str, Any]],
) -> None:
    """Ensure each inbound-enabled provider Account has only one profile owner."""

    for provider_key, facet in provider_facets.items():
        if provider_key not in _PROVIDER_OWNED_INGRESS_KEYS:
            continue
        if "ingress_enabled" in facet and not isinstance(facet["ingress_enabled"], bool):
            raise ValidationError(
                f"{provider_key} ingress_enabled must be a boolean",
                data={
                    "provider_key": provider_key,
                    "field": "ingress_enabled",
                    "expected": "boolean",
                    "next_action": (
                        "Use true to receive provider webhooks in this project or false "
                        "for outbound-only Account reuse."
                    ),
                },
            )
        if not provider_ingress_enabled(facet):
            continue
        credential_ref = str(facet.get("credential_ref") or "").strip()
        if not credential_ref:
            continue
        _assert_provider_ingress_account_available(
            session,
            project_id=project_id,
            profile_ref=profile_ref,
            provider_key=provider_key,
            credential_ref=credential_ref,
        )


def _assert_provider_ingress_account_available(
    session: Session,
    *,
    project_id: int,
    profile_ref: str,
    provider_key: str,
    credential_ref: str,
) -> None:
    rows = session.exec(
        select(ResourceRecord, Resource, Plugin)
        .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
        .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
        .where(
            col(Resource.key) == "communication-profile",
            col(Plugin.slug) == "communications",
        )
    ).all()
    for record, _resource, _plugin in rows:
        if record.project_id == project_id and record.external_id == profile_ref:
            continue
        data = dict(record.data_json or {})
        facets = data.get("provider_facets")
        facet = facets.get(provider_key) if isinstance(facets, dict) else None
        if not isinstance(facet, dict):
            continue
        if not provider_ingress_enabled(facet):
            continue
        existing_ref = str(facet.get("credential_ref") or "").strip()
        if existing_ref != credential_ref:
            continue
        raise ConflictError(
            f"{provider_key} Account already owns ingress through another communication profile",
            data={
                "provider_key": provider_key,
                "credential_ref": credential_ref,
                "owner_project_id": record.project_id,
                "owner_profile_ref": record.external_id,
                "next_action": (
                    "Turn off inbound webhook ownership on this profile to reuse the "
                    "Account for outbound messages, or release the existing inbound "
                    "profile before configuring provider ingress here."
                ),
            },
        )


def provider_facet(profile: dict[str, Any], provider_key: str) -> dict[str, Any]:
    facets = profile.get("provider_facets")
    facet = facets.get(provider_key) if isinstance(facets, dict) else None
    return dict(facet) if isinstance(facet, dict) else {}


def merged_provider_profile(profile: dict[str, Any], provider_key: str) -> dict[str, Any]:
    """Return generic profile data with provider facet fields promoted."""

    facet = provider_facet(profile, provider_key)
    key = str(profile.get("key") or "").strip()
    merged = {
        **profile,
        **facet,
        "key": key,
        "profile_ref": str(profile.get("profile_ref") or communication_profile_ref(key)),
        "provider_key": provider_key,
        "provider_facets": dict(profile.get("provider_facets") or {}),
        "credential_ref": (
            str(facet.get("credential_ref")).strip()
            if facet.get("credential_ref") is not None
            else None
        ),
        "identity": dict(profile.get("identity") or {}),
        "agent_guidance": dict(profile.get("agent_guidance") or {}),
        "access_policy": dict(profile.get("access_policy") or {}),
        "visibility_policy": dict(profile.get("visibility_policy") or {}),
        "trigger_policy": dict(profile.get("trigger_policy") or {}),
        "context_policy": dict(profile.get("context_policy") or {}),
        "response_policy": dict(profile.get("response_policy") or {}),
        "refs": dict(facet.get("refs") or {}),
        "allowed_webhook_hosts": list(facet.get("allowed_webhook_hosts") or []),
    }
    if "bot_username" in facet:
        merged["bot_username"] = facet.get("bot_username")
    if "ingress_mode" in facet:
        merged["ingress_mode"] = facet.get("ingress_mode")
    if "allowed_updates" in facet:
        merged["allowed_updates"] = facet.get("allowed_updates")
    if "webhook_base_url" in facet:
        merged["webhook_base_url"] = facet.get("webhook_base_url")
    for key_name in ("reply_to_message_refs", "thread_refs", "direct_messages_topic_refs"):
        merged[key_name] = dict(facet.get(key_name) or {})
    return merged
