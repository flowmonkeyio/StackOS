"""Communication profile helpers shared by providers."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from stackos.communications.resources import communication_record_by_external_id
from stackos.db.models import ResourceRecord


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
        "auth_profile_key": str(facet.get("auth_profile_key") or "default"),
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
