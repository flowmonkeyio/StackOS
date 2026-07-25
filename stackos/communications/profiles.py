"""Communication profile helpers shared by providers."""

from __future__ import annotations

from typing import Any, Literal

from sqlmodel import Session, col, select

from stackos.auth_providers import AuthRepository
from stackos.communications.resources import communication_record_by_external_id
from stackos.db.models import Credential, Plugin, ProjectCredential, Resource, ResourceRecord
from stackos.repositories.base import ConflictError, ValidationError

_PROVIDER_OWNED_INGRESS_KEYS = frozenset({"slack-bot", "telegram-bot"})
_DAEMON_OWNED_INGRESS_FIELDS = frozenset(
    {
        "ingress_path",
        "ingress_url",
        "ingress_public_base_url",
        "ingress_driver",
        "ingress_endpoint_ref",
        "manual_ingress_confirmation",
        "webhook_base_url",
        "allowed_webhook_hosts",
        "webhook_policy",
    }
)
CommunicationProfileBindingStatus = Literal["ready", "repair-required", "disabled"]


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
            raise ValidationError(
                f"{provider_key} communication profile facet requires credential_ref",
                data={
                    "project_id": project_id,
                    "profile_ref": profile_ref,
                    "provider_key": provider_key,
                    "field": "credential_ref",
                    "next_action": (
                        "Select one Account attached to this project before saving "
                        "the communication profile."
                    ),
                },
            )
        accounts.require_attached_account(
            project_id=project_id,
            credential_ref=credential_ref,
            provider_key=provider_key,
        )


def normalize_communication_profile_facets(
    provider_facets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Store operator intent while dropping daemon-owned binding and ingress state."""

    normalized: dict[str, dict[str, Any]] = {}
    for provider_key, raw_facet in provider_facets.items():
        facet = dict(raw_facet)
        facet.pop("auth_profile_key", None)
        for field in _DAEMON_OWNED_INGRESS_FIELDS:
            facet.pop(field, None)
        refs = facet.get("refs")
        if isinstance(refs, dict):
            safe_refs = dict(refs)
            safe_refs.pop("ingress_url", None)
            safe_refs.pop("ingress_endpoint_ref", None)
            if safe_refs:
                facet["refs"] = safe_refs
            else:
                facet.pop("refs", None)
        if provider_key in _PROVIDER_OWNED_INGRESS_KEYS:
            ingress_enabled = facet.get("ingress_enabled", True)
            if not isinstance(ingress_enabled, bool):
                raise ValidationError(
                    f"{provider_key} ingress_enabled must be a boolean",
                    data={
                        "provider_key": provider_key,
                        "field": "ingress_enabled",
                        "expected": "boolean",
                        "next_action": (
                            "Use true to receive provider webhooks in this project "
                            "or false for outbound-only Account reuse."
                        ),
                    },
                )
            facet["ingress_enabled"] = ingress_enabled
        normalized[provider_key] = facet
    return normalized


def communication_profile_binding_summary(
    session: Session,
    *,
    project_id: int,
    provider_facets: dict[str, dict[str, Any]],
    enabled: bool,
) -> tuple[CommunicationProfileBindingStatus, list[dict[str, str]]]:
    """Return one fail-closed, safe Account-binding summary for reads and UI."""

    if not enabled:
        return "disabled", []
    issues: list[dict[str, str]] = []
    for provider_key, facet in provider_facets.items():
        use = _communication_profile_account_use(
            session,
            project_id=project_id,
            profile_ref="communication-profile:candidate",
            profile_key="candidate",
            profile_display_name="Communication profile",
            profile_enabled=enabled,
            provider_key=provider_key,
            facet=facet,
        )
        if use["binding_state"] != "ready":
            issues.append(
                {
                    "provider_key": provider_key,
                    "code": str(use["binding_state"]),
                    "message": str(use["repair_message"]),
                }
            )
    return ("repair-required" if issues else "ready"), issues


def communication_profile_account_uses(
    session: Session,
    *,
    project_id: int | None = None,
) -> list[dict[str, Any]]:
    """Project Account usage derived from canonical communication profile facets."""

    stmt = (
        select(ResourceRecord, Resource, Plugin)
        .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
        .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
        .where(
            col(Resource.key) == "communication-profile",
            col(Plugin.slug) == "communications",
        )
    )
    if project_id is not None:
        stmt = stmt.where(col(ResourceRecord.project_id) == project_id)
    uses: list[dict[str, Any]] = []
    for record, _resource, _plugin in session.exec(stmt).all():
        data = dict(record.data_json or {})
        profile_key = str(data.get("key") or "").strip()
        profile_ref = str(data.get("profile_ref") or record.external_id)
        identity = dict(data.get("identity") or {})
        profile_display_name = str(identity.get("display_name") or profile_key or profile_ref)
        profile_enabled = data.get("enabled") is not False
        facets = data.get("provider_facets")
        if not isinstance(facets, dict):
            continue
        for provider_key, facet in facets.items():
            if not isinstance(facet, dict):
                continue
            uses.append(
                _communication_profile_account_use(
                    session,
                    project_id=record.project_id,
                    profile_ref=profile_ref,
                    profile_key=profile_key,
                    profile_display_name=profile_display_name,
                    profile_enabled=profile_enabled,
                    provider_key=str(provider_key),
                    facet=dict(facet),
                )
            )
    return sorted(
        uses,
        key=lambda item: (
            int(item["project_id"]),
            str(item["provider_key"]),
            str(item["profile_key"]),
        ),
    )


def _communication_profile_account_use(
    session: Session,
    *,
    project_id: int,
    profile_ref: str,
    profile_key: str,
    profile_display_name: str,
    profile_enabled: bool,
    provider_key: str,
    facet: dict[str, Any],
) -> dict[str, Any]:
    credential_ref = str(facet.get("credential_ref") or "").strip() or None
    ingress_enabled = (
        provider_ingress_enabled(facet) if provider_key in _PROVIDER_OWNED_INGRESS_KEYS else False
    )
    binding_state = "unconfigured"
    repair_message = "Select an Account attached to this project."
    if credential_ref:
        credential = session.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            binding_state = "missing_account"
            repair_message = "The selected Account no longer exists. Select another Account."
        elif credential.provider_key != provider_key:
            binding_state = "provider_mismatch"
            repair_message = "The selected Account belongs to a different provider."
        elif credential.revoked_at is not None or credential.integration_credential_id is None:
            binding_state = "missing_account"
            repair_message = "The selected Account is revoked. Select another Account."
        elif credential.status != "connected":
            binding_state = "not_connected"
            repair_message = "Reconnect or test the selected Account before using this profile."
        else:
            assert credential.id is not None
            attachment = session.exec(
                select(ProjectCredential).where(
                    col(ProjectCredential.project_id) == project_id,
                    col(ProjectCredential.credential_id) == credential.id,
                )
            ).first()
            if attachment is None:
                binding_state = "unattached"
                repair_message = "Attach the selected Account to this project."
            else:
                binding_state = "ready"
                repair_message = ""
    ingress_url = str(facet.get("ingress_url") or "").strip() or None
    confirmation = facet.get("manual_ingress_confirmation")
    confirmed_url = (
        str(confirmation.get("ingress_url") or "").strip() if isinstance(confirmation, dict) else ""
    )
    attention_required = bool(
        provider_key == "slack-bot"
        and profile_enabled
        and ingress_enabled
        and binding_state == "ready"
        and ingress_url
        and confirmed_url != ingress_url
    )
    return {
        "project_id": project_id,
        "profile_ref": profile_ref,
        "profile_key": profile_key,
        "profile_display_name": profile_display_name,
        "provider_key": provider_key,
        "credential_ref": credential_ref,
        "profile_enabled": profile_enabled,
        "ingress_enabled": ingress_enabled,
        "owns_provider_ingress": bool(
            profile_enabled and ingress_enabled and provider_key in _PROVIDER_OWNED_INGRESS_KEYS
        ),
        "binding_state": binding_state,
        "repair_message": repair_message,
        "attention_required": attention_required,
        "attention_message": (
            "Update the Slack Events API and Interactivity URLs for this profile."
            if attention_required
            else None
        ),
        "ingress_url": ingress_url,
        "manual_confirmed_url": confirmed_url or None,
    }


def provider_ingress_enabled(facet: dict[str, Any]) -> bool:
    """Only an explicit canonical boolean enables provider-owned ingress."""

    return facet.get("ingress_enabled") is True


def validate_communication_profile_ingress_ownership(
    session: Session,
    *,
    project_id: int,
    profile_ref: str,
    provider_facets: dict[str, dict[str, Any]],
    profile_enabled: bool = True,
) -> None:
    """Ensure each inbound-enabled provider Account has only one profile owner."""

    if not profile_enabled:
        return
    for provider_key, facet in provider_facets.items():
        if provider_key not in _PROVIDER_OWNED_INGRESS_KEYS:
            continue
        if not isinstance(facet.get("ingress_enabled"), bool):
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
            raise ValidationError(
                f"{provider_key} inbound profile requires credential_ref",
                data={
                    "provider_key": provider_key,
                    "profile_ref": profile_ref,
                    "field": "credential_ref",
                },
            )
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
        if data.get("enabled") is False:
            continue
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
