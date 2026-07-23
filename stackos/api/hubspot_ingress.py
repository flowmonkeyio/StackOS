"""Signed HubSpot ingress for CRM events and custom workflow handoffs.

Official docs verified:
- Request validation: https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/request-validation
- Webhook batches: https://developers.hubspot.com/docs/api-reference/latest/webhooks/guide
- Custom workflow actions: https://developers.hubspot.com/docs/api-reference/latest/automation/workflow-actions/custom-action-guide
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlmodel import Session, col, select

from stackos.api.deps import get_session
from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.communications import (
    NormalizedInboundEvent,
    NormalizedResourceWrite,
    communication_record_by_external_id,
    evaluate_inbound_event_allowlist,
    process_inbound_event,
)
from stackos.db.models import Credential, CredentialAccount, IntegrationCredential
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository

router = APIRouter(prefix="/api/v1/ingress/hubspot", tags=["hubspot-ingress"])

_MAX_BODY_BYTES = 1_000_000
_MAX_BATCH_EVENTS = 100
_REPLAY_WINDOW_MS = 5 * 60 * 1_000
_PROFILE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_WORKFLOW_DEFINITION_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_V3_URI_DECODES = {
    "%3A": ":",
    "%2F": "/",
    "%3F": "?",
    "%40": "@",
    "%21": "!",
    "%24": "$",
    "%27": "'",
    "%28": "(",
    "%29": ")",
    "%2A": "*",
    "%2C": ",",
    "%3B": ";",
}
_SUBSCRIPTION_OBJECT_TYPES = {
    "contact": "contact",
    "company": "company",
    "deal": "deal",
}
_SUPPORTED_SUBSCRIPTION_TYPES = {
    f"{object_name}.{event_name}"
    for object_name, event_names in {
        "contact": (
            "creation",
            "deletion",
            "merge",
            "associationChange",
            "restore",
            "privacyDeletion",
            "propertyChange",
        ),
        "company": (
            "creation",
            "deletion",
            "merge",
            "associationChange",
            "restore",
            "propertyChange",
        ),
        "deal": (
            "creation",
            "deletion",
            "merge",
            "associationChange",
            "restore",
            "propertyChange",
        ),
    }.items()
    for event_name in event_names
}
_WORKFLOW_OBJECT_TYPES = {
    "CONTACT": "contact",
    "COMPANY": "company",
    "DEAL": "deal",
}


@dataclass(frozen=True)
class HubSpotIngressProfile:
    profile_key: str
    portal_id: str
    app_id: str
    event_allowlist: tuple[str, ...]
    workflow_action_allowlist: tuple[str, ...]
    credential: Credential = field(repr=False)
    client_secret: str = field(repr=False)


@router.post("/{project_id}/{profile_key}", status_code=status.HTTP_200_OK)
async def ingest_hubspot_payload(
    project_id: int,
    profile_key: str,
    request: Request,
    signature_v3: str | None = Header(default=None, alias="X-HubSpot-Signature-V3"),
    request_timestamp: str | None = Header(
        default=None,
        alias="X-HubSpot-Request-Timestamp",
    ),
    content_length: str | None = Header(default=None, alias="Content-Length"),
    content_type: str | None = Header(default=None, alias="Content-Type"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Verify, normalize, store, and optionally enqueue one HubSpot request.

    Webhook subscription batches and custom workflow-action invocations use
    HubSpot signature v3. The route only writes provider-neutral resources and
    agent requests; it never creates, selects, starts, or executes a run plan.
    """

    _validate_content_length(content_length)
    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="HubSpot payload is too large",
        )
    if content_type is not None and "application/json" not in content_type.lower():
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="HubSpot payload must use application/json",
        )
    profile = _require_hubspot_profile(
        session,
        project_id=project_id,
        profile_key=profile_key,
    )
    canonical_uri = _canonical_public_uri(
        session,
        project_id=project_id,
        profile_key=profile_key,
        request=request,
    )
    _verify_signature(
        profile=profile,
        raw_body=raw_body,
        canonical_uri=canonical_uri,
        signature_v3=signature_v3,
        request_timestamp=request_timestamp,
    )
    payload = _parse_json_payload(raw_body)
    if isinstance(payload, list):
        return _store_subscription_batch(
            session,
            project_id=project_id,
            profile=profile,
            payload=payload,
        )
    if isinstance(payload, dict):
        return _store_workflow_action(
            session,
            project_id=project_id,
            profile=profile,
            payload=payload,
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="HubSpot payload does not match its signature contract",
    )


def _validate_content_length(value: str | None) -> None:
    if value is None:
        return
    try:
        length = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HubSpot Content-Length is invalid",
        ) from exc
    if length < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HubSpot Content-Length is invalid",
        )
    if length > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="HubSpot payload is too large",
        )


def _require_hubspot_profile(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
) -> HubSpotIngressProfile:
    if not _PROFILE_KEY_RE.fullmatch(profile_key):
        _invalid_ingress_target()
    credential = session.exec(
        select(Credential).where(
            col(Credential.project_id) == project_id,
            col(Credential.provider_key) == "hubspot",
            col(Credential.profile_key) == profile_key,
            col(Credential.revoked_at).is_(None),
        )
    ).first()
    if credential is None or credential.id is None or credential.integration_credential_id is None:
        _invalid_ingress_target()
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    if integration is None or integration.id is None:
        _invalid_ingress_target()
    config = {**dict(integration.config_json or {}), **dict(credential.config_json or {})}
    if config.get("webhook_enabled") is not True:
        _invalid_ingress_target()
    app_id = _normalized_positive_integer(config.get("app_id"))
    if app_id is None:
        _invalid_ingress_target()
    accounts = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).all()
    portal_ids = {
        str(account.provider_account_id).strip()
        for account in accounts
        if account.provider_account_id is not None and str(account.provider_account_id).strip()
    }
    portal_id = (
        _normalized_positive_integer(next(iter(portal_ids))) if len(portal_ids) == 1 else None
    )
    if portal_id is None:
        _invalid_ingress_target()
    try:
        raw_secret = IntegrationCredentialRepository(session).get_decrypted(integration.id)
        decoded = json.loads(raw_secret.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _invalid_ingress_target()
    if not isinstance(decoded, Mapping):
        _invalid_ingress_target()
    application = decoded.get("_oauth_application_pending")
    secret_source = application if isinstance(application, Mapping) else decoded
    client_secret = secret_source.get("client_secret")
    if not isinstance(client_secret, str) or not client_secret.strip():
        _invalid_ingress_target()
    event_allowlist = _string_set(config.get("webhook_event_allowlist"))
    if any(item not in _SUPPORTED_SUBSCRIPTION_TYPES for item in event_allowlist):
        _invalid_ingress_target()
    workflow_action_allowlist = _string_set(config.get("workflow_action_allowlist"))
    if any(not _WORKFLOW_DEFINITION_RE.fullmatch(item) for item in workflow_action_allowlist):
        _invalid_ingress_target()
    return HubSpotIngressProfile(
        profile_key=profile_key,
        portal_id=portal_id,
        app_id=app_id,
        event_allowlist=tuple(sorted(event_allowlist)),
        workflow_action_allowlist=tuple(sorted(workflow_action_allowlist)),
        credential=credential,
        client_secret=client_secret.strip(),
    )


def _invalid_ingress_target() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="invalid HubSpot ingress target",
    )


def _canonical_public_uri(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
    request: Request,
) -> str:
    endpoint = communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="ingress-endpoint",
        external_id="ingress-endpoint:default",
    )
    data = dict(endpoint.data_json or {}) if endpoint is not None else {}
    base_url = data.get("public_base_url")
    if (
        endpoint is None
        or data.get("enabled") is False
        or not isinstance(base_url, str)
        or not base_url.startswith("https://")
    ):
        _invalid_ingress_target()
    expected_path = f"/api/v1/ingress/hubspot/{project_id}/{quote(profile_key, safe='')}"
    raw_path = request.scope.get("raw_path")
    request_path = (
        bytes(raw_path).decode("ascii", errors="strict")
        if isinstance(raw_path, bytes)
        else request.url.path
    )
    if request_path != expected_path:
        _invalid_ingress_target()
    canonical = f"{base_url.rstrip('/')}{expected_path}"
    query_string = request.scope.get("query_string")
    if isinstance(query_string, bytes) and query_string:
        canonical = f"{canonical}?{query_string.decode('latin-1')}"
    return canonical


def _verify_signature(
    *,
    profile: HubSpotIngressProfile,
    raw_body: bytes,
    canonical_uri: str,
    signature_v3: str | None,
    request_timestamp: str | None,
) -> None:
    if signature_v3:
        if not request_timestamp:
            _invalid_signature()
        try:
            timestamp_ms = int(request_timestamp)
        except (TypeError, ValueError):
            _invalid_signature()
        if abs(int(time.time() * 1_000) - timestamp_ms) > _REPLAY_WINDOW_MS:
            _invalid_signature()
        signed_uri = _decode_v3_uri(canonical_uri)
        source = b"POST" + signed_uri.encode("utf-8") + raw_body + request_timestamp.encode("utf-8")
        expected = base64.b64encode(
            hmac.new(
                profile.client_secret.encode("utf-8"),
                source,
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        if not hmac.compare_digest(expected, signature_v3):
            _invalid_signature()
        return
    _invalid_signature()
    raise AssertionError("unreachable")


def _decode_v3_uri(value: str) -> str:
    decoded = value
    for encoded, plain in _V3_URI_DECODES.items():
        decoded = re.sub(encoded, plain, decoded, flags=re.I)
    return decoded


def _invalid_signature() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="invalid HubSpot signature",
    )


def _parse_json_payload(raw_body: bytes) -> list[Any] | dict[str, Any]:
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HubSpot payload must be valid JSON",
        ) from exc
    if not isinstance(parsed, list | dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HubSpot payload must be an object or array",
        )
    return parsed


def _store_subscription_batch(
    session: Session,
    *,
    project_id: int,
    profile: HubSpotIngressProfile,
    payload: list[Any],
) -> dict[str, Any]:
    events = [_validated_subscription_event(item, profile=profile) for item in payload]
    if not events or len(events) > _MAX_BATCH_EVENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HubSpot webhook batch must contain 1 to 100 events",
        )
    outcomes = [
        _process_subscription_event(
            session,
            project_id=project_id,
            profile=profile,
            event=event,
        )
        for event in events
    ]
    statuses = [str(item["policy_status"]) for item in outcomes]
    return {
        "ok": True,
        "profile_key": profile.profile_key,
        "received": len(outcomes),
        "request_created": statuses.count("request_created"),
        "request_deduped": statuses.count("request_deduped"),
        "not_allowlisted": statuses.count("event_not_allowlisted"),
    }


def _validated_subscription_event(
    value: Any,
    *,
    profile: HubSpotIngressProfile,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid_payload()
    raw_subscription_type = value.get("subscriptionType")
    raw_event_type = value.get("eventType")
    if raw_subscription_type and raw_event_type and raw_subscription_type != raw_event_type:
        _invalid_payload()
    subscription_type = _required_text(
        raw_subscription_type or raw_event_type,
        max_length=100,
    )
    if subscription_type not in _SUPPORTED_SUBSCRIPTION_TYPES:
        _invalid_payload()
    object_prefix = subscription_type.split(".", maxsplit=1)[0]
    object_type = _SUBSCRIPTION_OBJECT_TYPES[object_prefix]
    portal_id = _required_positive_integer(value.get("portalId"))
    app_id = _required_positive_integer(value.get("appId"))
    if portal_id != profile.portal_id or app_id != profile.app_id:
        _invalid_ingress_target()
    event_id = _required_identifier(value.get("eventId"))
    subscription_id = _required_identifier(value.get("subscriptionId"))
    object_id = _required_identifier(value.get("objectId"))
    occurred_at = _required_nonnegative_integer(value.get("occurredAt"))
    attempt_number = _required_nonnegative_integer(value.get("attemptNumber"))
    property_name = None
    if subscription_type.endswith(".propertyChange"):
        property_name = _required_text(value.get("propertyName"), max_length=200)
    return {
        "subscription_type": subscription_type,
        "object_type": object_type,
        "event_id": event_id,
        "subscription_id": subscription_id,
        "object_id": object_id,
        "occurred_at": occurred_at,
        "attempt_number": attempt_number,
        "property_name": property_name,
        "change_source": _optional_text(value.get("changeSource"), max_length=200),
    }


def _process_subscription_event(
    session: Session,
    *,
    project_id: int,
    profile: HubSpotIngressProfile,
    event: dict[str, Any],
) -> dict[str, Any]:
    identity = "\0".join(
        str(event[key])
        for key in (
            "subscription_type",
            "event_id",
            "subscription_id",
            "object_id",
            "occurred_at",
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    refs = ProviderObjectReferenceRepository(session)
    account_ref = refs.upsert(
        credential=profile.credential,
        object_type="account",
        provider_object_id=profile.portal_id,
        display_name="HubSpot account",
    )
    app_ref = refs.upsert(
        credential=profile.credential,
        object_type="app",
        provider_object_id=profile.app_id,
        display_name="HubSpot app",
    )
    event_ref = refs.upsert(
        credential=profile.credential,
        object_type="webhook-event",
        provider_object_id=identity,
        display_name=event["subscription_type"],
    )
    subscription_ref = refs.upsert(
        credential=profile.credential,
        object_type="webhook-subscription",
        provider_object_id=event["subscription_id"],
        display_name=event["subscription_type"],
    )
    object_ref = refs.upsert(
        credential=profile.credential,
        object_type=event["object_type"],
        provider_object_id=event["object_id"],
    )
    property_ref = None
    if event["property_name"] is not None:
        property_ref = refs.upsert(
            credential=profile.credential,
            object_type=f"{event['object_type']}-property",
            provider_object_id=event["property_name"],
        )
    decision = evaluate_inbound_event_allowlist(
        enabled=True,
        event_key=event["subscription_type"],
        allowed_event_keys=profile.event_allowlist,
        trigger_reason="hubspot_event_allowlist",
    )
    normalized = NormalizedInboundEvent(
        provider_key="hubspot",
        profile_key=profile.profile_key,
        event_key=digest,
        update_type=event["subscription_type"],
        source_kind="hubspot-webhook",
        request_key=f"hubspot-webhook:{profile.profile_key}:{digest}",
        request_title=f"HubSpot {event['subscription_type']}",
        body_preview=f"HubSpot {event['subscription_type']} event",
        event=NormalizedResourceWrite(
            resource_key="communication-event",
            external_id=f"hubspot-webhook:{profile.profile_key}:{digest}",
            title=f"HubSpot {event['subscription_type']}",
            data_json={
                "provider_key": "hubspot",
                "profile_key": profile.profile_key,
                "event_ref": event_ref,
                "account_ref": account_ref,
                "app_ref": app_ref,
                "subscription_ref": subscription_ref,
                "subscription_type": event["subscription_type"],
                "object_ref": object_ref,
                "object_type": event["object_type"],
                "property_ref": property_ref,
                "property_value_omitted": event["property_name"] is not None,
                "change_source": event["change_source"],
                "occurred_at": event["occurred_at"],
                "attempt_number": event["attempt_number"],
            },
            provenance_json={"source": "hubspot-ingress"},
            preserve_existing_on_dedupe=True,
        ),
        request_metadata_json={
            "profile_key": profile.profile_key,
            "event_ref": event_ref,
            "account_ref": account_ref,
            "app_ref": app_ref,
            "subscription_ref": subscription_ref,
            "subscription_type": event["subscription_type"],
            "object_ref": object_ref,
            "object_type": event["object_type"],
            "property_ref": property_ref,
            "occurred_at": event["occurred_at"],
            "agent_guidance": (
                "Inspect the stored HubSpot event and choose an explicit workflow or action. "
                "Ingress never starts a run plan."
            ),
        },
    )
    result = process_inbound_event(
        session,
        project_id=project_id,
        event=normalized,
        decision=decision,
    )
    return {"policy_status": result.policy_status}


def _store_workflow_action(
    session: Session,
    *,
    project_id: int,
    profile: HubSpotIngressProfile,
    payload: dict[str, Any],
) -> dict[str, Any]:
    validated = _validated_workflow_action(payload, profile=profile)
    callback_digest = hashlib.sha256(validated["callback_id"].encode("utf-8")).hexdigest()
    refs = ProviderObjectReferenceRepository(session)
    account_ref = refs.upsert(
        credential=profile.credential,
        object_type="account",
        provider_object_id=profile.portal_id,
        display_name="HubSpot account",
    )
    definition_ref = refs.upsert(
        credential=profile.credential,
        object_type="workflow-action-definition",
        provider_object_id=validated["definition_id"],
    )
    execution_ref = refs.upsert(
        credential=profile.credential,
        object_type="workflow-action-execution",
        provider_object_id=validated["callback_id"],
    )
    object_ref = refs.upsert(
        credential=profile.credential,
        object_type=validated["object_type"],
        provider_object_id=validated["object_id"],
    )
    workflow_ref = None
    if validated["workflow_id"] is not None:
        workflow_ref = refs.upsert(
            credential=profile.credential,
            object_type="workflow",
            provider_object_id=validated["workflow_id"],
        )
    decision = evaluate_inbound_event_allowlist(
        enabled=True,
        event_key=validated["definition_id"],
        allowed_event_keys=profile.workflow_action_allowlist,
        trigger_reason="hubspot_workflow_action_allowlist",
    )
    normalized = NormalizedInboundEvent(
        provider_key="hubspot",
        profile_key=profile.profile_key,
        event_key=callback_digest,
        update_type="workflow_action.invocation",
        source_kind="hubspot-workflow-action",
        request_key=f"hubspot-workflow-action:{profile.profile_key}:{callback_digest}",
        request_title="HubSpot custom workflow action",
        body_preview="HubSpot custom workflow action invocation",
        event=NormalizedResourceWrite(
            resource_key="communication-event",
            external_id=f"hubspot-workflow-action:{profile.profile_key}:{callback_digest}",
            title="HubSpot custom workflow action",
            data_json={
                "provider_key": "hubspot",
                "profile_key": profile.profile_key,
                "account_ref": account_ref,
                "definition_ref": definition_ref,
                "execution_ref": execution_ref,
                "workflow_ref": workflow_ref,
                "object_ref": object_ref,
                "object_type": validated["object_type"],
                "input_fields": validated["input_fields"],
                "object_property_names": validated["object_property_names"],
                "sensitive_external_data": True,
            },
            provenance_json={"source": "hubspot-ingress"},
            preserve_existing_on_dedupe=True,
        ),
        request_metadata_json={
            "profile_key": profile.profile_key,
            "account_ref": account_ref,
            "definition_ref": definition_ref,
            "execution_ref": execution_ref,
            "workflow_ref": workflow_ref,
            "object_ref": object_ref,
            "object_type": validated["object_type"],
            "input_fields": validated["input_fields"],
            "sensitive_external_data": True,
            "agent_guidance": (
                "Treat this as an agent-request handoff. Select any workflow explicitly; "
                "the HubSpot callback did not create or start one."
            ),
        },
    )
    result = process_inbound_event(
        session,
        project_id=project_id,
        event=normalized,
        decision=decision,
    )
    execution_state = (
        "SUCCESS"
        if result.policy_status in {"request_created", "request_deduped"}
        else "FAIL_CONTINUE"
    )
    return {"outputFields": {"hs_execution_state": execution_state}}


def _validated_workflow_action(
    payload: Mapping[str, Any],
    *,
    profile: HubSpotIngressProfile,
) -> dict[str, Any]:
    callback_id = _required_text(payload.get("callbackId"), max_length=300)
    origin = _required_mapping(payload.get("origin"))
    context = _required_mapping(payload.get("context"))
    enrolled_object = _required_mapping(payload.get("object"))
    input_fields = _required_mapping(payload.get("inputFields"))
    portal_id = _required_positive_integer(origin.get("portalId"))
    if portal_id != profile.portal_id:
        _invalid_ingress_target()
    definition_id = _required_positive_integer(origin.get("actionDefinitionId"))
    if not _WORKFLOW_DEFINITION_RE.fullmatch(definition_id):
        _invalid_payload()
    if context.get("source") != "WORKFLOWS":
        _invalid_payload()
    object_type_name = _required_text(enrolled_object.get("objectType"), max_length=50).upper()
    object_type = _WORKFLOW_OBJECT_TYPES.get(object_type_name)
    if object_type is None:
        _invalid_payload()
    object_id = _required_identifier(enrolled_object.get("objectId"))
    raw_properties = enrolled_object.get("properties")
    property_names = (
        sorted(str(key)[:200] for key in raw_properties)
        if isinstance(raw_properties, Mapping)
        else []
    )
    workflow_id = _normalized_positive_integer(context.get("workflowId"))
    return {
        "callback_id": callback_id,
        "definition_id": definition_id,
        "workflow_id": workflow_id,
        "object_type": object_type,
        "object_id": object_id,
        "object_property_names": property_names[:100],
        "input_fields": redact_secrets(_bounded_external_json(dict(input_fields))),
    }


def _bounded_external_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:50]:
            key = redact_secret_text(str(raw_key))[:100]
            result[key] = _bounded_external_json(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_bounded_external_json(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return redact_secret_text(value)[:2_000]
    if value is None or isinstance(value, int | float | bool):
        return value
    return redact_secret_text(str(value))[:2_000]


def _required_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid_payload()
    return value


def _required_text(value: Any, *, max_length: int) -> str:
    if not isinstance(value, str):
        _invalid_payload()
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        _invalid_payload()
    return normalized


def _optional_text(value: Any, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, max_length=max_length)


def _required_identifier(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int):
        _invalid_payload()
    normalized = str(value).strip()
    if not normalized or len(normalized) > 300:
        _invalid_payload()
    return normalized


def _normalized_integer(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.strip().isdigit():
        return str(int(value.strip()))
    return None


def _normalized_positive_integer(value: Any) -> str | None:
    normalized = _normalized_integer(value)
    return normalized if normalized is not None and int(normalized) > 0 else None


def _required_positive_integer(value: Any) -> str:
    normalized = _normalized_positive_integer(value)
    if normalized is None:
        _invalid_payload()
    return normalized


def _required_integer(value: Any) -> str:
    normalized = _normalized_integer(value)
    if normalized is None:
        _invalid_payload()
    return normalized


def _required_nonnegative_integer(value: Any) -> int:
    normalized = _required_integer(value)
    return int(normalized)


def _string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    _invalid_ingress_target()
    return set()


def _invalid_payload() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="invalid HubSpot payload",
    )


__all__ = ["router"]
