"""Slack signed HTTP ingress for communication-triggered requests.

Official docs verified:
- Request signing: https://docs.slack.dev/authentication/verifying-requests-from-slack/
- Events API: https://docs.slack.dev/apis/events-api/
- Interactivity: https://docs.slack.dev/interactivity/handling-user-interaction/
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import Session, select

from stackos.api.deps import get_session
from stackos.artifacts import redact_secret_text
from stackos.communications import (
    CommunicationDecision,
    CommunicationInteractionCheck,
    CommunicationPolicyEvent,
    CommunicationPolicyProfile,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
    candidate_refs,
    communication_record_by_external_id,
    config_policy,
    evaluate_inbound_policy,
    process_inbound_event,
)
from stackos.communications.provider_ids import (
    slack_message_ref as _message_ref,
)
from stackos.communications.provider_ids import (
    slack_outbound_button_external_id as _outbound_button_external_id,
)
from stackos.communications.provider_ids import (
    slack_surface_ref as _surface_ref,
)
from stackos.communications.provider_ids import (
    slack_thread_ref as _thread_ref,
)
from stackos.db.models import IntegrationCredential
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

router = APIRouter(prefix="/api/v1/ingress/slack", tags=["slack-ingress"])

_REPLAY_WINDOW_SECONDS = 60 * 5
_MAX_PREVIEW = 4_000


class SlackIngressOut(BaseModel):
    """Result of storing one Slack event or interaction payload."""

    ok: bool
    profile_key: str
    event_key: str | None = None
    update_type: str | None = None
    policy_status: str
    event_record_id: int | None = None
    message_record_id: int | None = None
    interaction_record_id: int | None = None
    agent_request_id: int | None = None


@dataclass(frozen=True)
class SlackProfile:
    key: str
    auth_profile_key: str
    data: dict[str, Any]


@router.post("/{project_id}/{profile_key}", status_code=status.HTTP_200_OK)
async def ingest_slack_payload(
    project_id: int,
    profile_key: str,
    request: Request,
    slack_signature: str | None = Header(default=None, alias="X-Slack-Signature"),
    slack_timestamp: str | None = Header(default=None, alias="X-Slack-Request-Timestamp"),
    retry_num: str | None = Header(default=None, alias="X-Slack-Retry-Num"),
    retry_reason: str | None = Header(default=None, alias="X-Slack-Retry-Reason"),
    content_type: str | None = Header(default=None, alias="Content-Type"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Store a signed Slack HTTP event/interaction and maybe create agent work.

    This route verifies Slack's HMAC signature with the daemon-held signing
    secret, normalizes a bounded event/interactivity shape into communication
    resources, applies shared communication policy, optionally creates one
    generic agent request, and stops. It never calls Slack, never starts a
    model, and never decides the business workflow.
    """

    profile = _require_slack_profile(session, project_id=project_id, profile_key=profile_key)
    raw_body = await request.body()
    _verify_signature(
        session,
        project_id=project_id,
        profile=profile,
        raw_body=raw_body,
        timestamp=slack_timestamp,
        signature=slack_signature,
    )
    parsed_payload = _parse_payload(raw_body, content_type=content_type)
    if parsed_payload.get("type") == "url_verification":
        challenge = parsed_payload.get("challenge")
        return {"challenge": str(challenge or "")}
    stored = _store_payload(
        session,
        project_id=project_id,
        profile=profile,
        payload=parsed_payload,
        raw_body=raw_body,
        retry_num=retry_num,
        retry_reason=retry_reason,
    )
    return SlackIngressOut(**stored).model_dump(mode="json")


def _verify_signature(
    session: Session,
    *,
    project_id: int,
    profile: SlackProfile,
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
) -> None:
    if not timestamp or not signature:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid Slack signature",
        ) from exc
    if abs(int(time.time()) - ts) > _REPLAY_WINDOW_SECONDS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    signing_secret = _signing_secret(
        session,
        project_id=project_id,
        profile_key=profile.auth_profile_key,
    )
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + raw_body
    digest = hmac.new(signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")


def _signing_secret(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
) -> str:
    credential = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "slack-bot",
            IntegrationCredential.profile_key == profile_key,
        )
    ).first()
    if credential is None or credential.id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    raw = IntegrationCredentialRepository(session).get_decrypted(credential.id)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    secret = str(payload.get("signing_secret") or "")
    if not secret.strip():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    return secret.strip()


def _require_slack_profile(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
) -> SlackProfile:
    record = communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-profile",
        external_id=f"communication-profile:{profile_key}",
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    data = dict(record.data_json or {})
    if data.get("key") != profile_key or data.get("enabled") is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    facets = data.get("provider_facets")
    slack_facet = facets.get("slack-bot") if isinstance(facets, Mapping) else None
    if not isinstance(slack_facet, Mapping):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    auth_profile_key = str(slack_facet.get("auth_profile_key") or "").strip()
    if not auth_profile_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Slack signature")
    return SlackProfile(key=profile_key, auth_profile_key=auth_profile_key, data=data)


def _parse_payload(raw_body: bytes, *, content_type: str | None) -> dict[str, Any]:
    text = raw_body.decode("utf-8", errors="replace")
    if content_type and "application/x-www-form-urlencoded" in content_type:
        form = parse_qs(text, keep_blank_values=True)
        payload_values = form.get("payload")
        if payload_values:
            try:
                parsed = json.loads(payload_values[0])
            except json.JSONDecodeError as exc:
                raise ValidationError("Slack interaction payload must be JSON") from exc
            if not isinstance(parsed, dict):
                raise ValidationError("Slack interaction payload must be an object")
            return parsed
        return {key: values[-1] if values else "" for key, values in form.items()}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError("Slack payload must be JSON or form encoded") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("Slack payload must be an object")
    return parsed


def _store_payload(
    session: Session,
    *,
    project_id: int,
    profile: SlackProfile,
    payload: dict[str, Any],
    raw_body: bytes,
    retry_num: str | None,
    retry_reason: str | None,
) -> dict[str, Any]:
    parsed = _parse_slack_event(profile, payload, raw_body=raw_body)
    decision = _policy_decision(session, project_id, profile, parsed)
    normalized = _normalized_slack_event(
        profile=profile,
        parsed=parsed,
        retry_num=retry_num,
        retry_reason=retry_reason,
        mark_interaction_clicked=decision.create_request,
    )
    result = process_inbound_event(
        session,
        project_id=project_id,
        event=normalized,
        decision=decision,
    )
    return result.to_response()


def _parse_slack_event(
    profile: SlackProfile,
    payload: dict[str, Any],
    *,
    raw_body: bytes,
) -> dict[str, Any]:
    body_hash = hashlib.sha256(raw_body).hexdigest()[:32]
    top_type = str(payload.get("type") or "")
    if top_type == "event_callback":
        event = payload.get("event")
        if not isinstance(event, dict):
            raise ValidationError("Slack event_callback payload requires event")
        event_type = str(event.get("type") or "event")
        channel_id = _safe_text(event.get("channel"))
        message_ts = _safe_text(event.get("ts") or event.get("event_ts"))
        thread_ts = _safe_text(event.get("thread_ts") or message_ts)
        text = _safe_text(event.get("text"))
        user_id = _safe_text(event.get("user") or event.get("bot_id"))
        bot_id = _safe_text(event.get("bot_id"))
        subtype = _safe_text(event.get("subtype"))
        event_key = _safe_text(payload.get("event_id")) or body_hash
        return {
            "payload": payload,
            "event": event,
            "event_key": event_key,
            "team_id": _safe_text(payload.get("team_id") or event.get("team")),
            "update_type": event_type,
            "event_type": event_type,
            "source_kind": (
                "slack-message" if event_type in {"message", "app_mention"} else "slack-event"
            ),
            "request_title": f"Slack {event_type} in {channel_id or 'unknown channel'}",
            "body_preview": text,
            "surface_ref": _surface_ref(channel_id) if channel_id else None,
            "thread_ref": _thread_ref(channel_id, thread_ts) if channel_id and thread_ts else None,
            "message_ref": (
                _message_ref(channel_id, message_ts) if channel_id and message_ts else None
            ),
            "message_ts": message_ts or None,
            "thread_ts": thread_ts or None,
            "channel_id": channel_id or None,
            "channel_type": _safe_text(event.get("channel_type")),
            "user_id": user_id or None,
            "user_ref": f"slack-user:{user_id}" if user_id else None,
            "bot_id": bot_id or None,
            "subtype": subtype or None,
            "is_self_message": _is_self_message_event(
                profile,
                event_type=event_type,
                user_id=user_id,
                bot_id=bot_id,
                subtype=subtype,
            ),
            "text": text,
            "content_type": "text",
        }
    if top_type:
        action = _first_action(payload)
        channel = _mapping_or_empty(payload.get("channel"))
        container = _mapping_or_empty(payload.get("container"))
        message = _mapping_or_empty(payload.get("message"))
        channel_id = _safe_text(channel.get("id") or container.get("channel_id"))
        message_ts = _safe_text(container.get("message_ts") or message.get("ts"))
        thread_ts = _safe_text(message.get("thread_ts") or message_ts)
        user = _mapping_or_empty(payload.get("user"))
        user_id = _safe_text(user.get("id"))
        action_id = _safe_text(action.get("action_id"))
        value = _safe_text(action.get("value"))
        block_id = _safe_text(action.get("block_id"))
        interaction_ref = f"slack-interaction:{body_hash}"
        return {
            "payload": payload,
            "event": {},
            "event_key": body_hash,
            "team_id": _safe_text(_nested(payload, "team.id") or payload.get("team_id")),
            "update_type": top_type,
            "event_type": top_type,
            "source_kind": "slack-interaction",
            "request_title": f"Slack interaction {action_id or top_type}",
            "body_preview": _safe_text(action.get("text") or action_id or value),
            "surface_ref": _surface_ref(channel_id) if channel_id else None,
            "thread_ref": _thread_ref(channel_id, thread_ts) if channel_id and thread_ts else None,
            "message_ref": (
                _message_ref(channel_id, message_ts) if channel_id and message_ts else None
            ),
            "message_ts": message_ts or None,
            "thread_ts": thread_ts or None,
            "channel_id": channel_id or None,
            "channel_type": _safe_text(channel.get("type")),
            "user_id": user_id or None,
            "user_ref": f"slack-user:{user_id}" if user_id else None,
            "interaction_ref": interaction_ref,
            "action_id": action_id or None,
            "button_value": value or None,
            "block_id": block_id or None,
            "text": _safe_text(action.get("text") or action_id or value),
            "content_type": "interaction",
        }
    raise ValidationError("Slack payload type is required")


def _policy_decision(
    session: Session,
    project_id: int,
    profile: SlackProfile,
    parsed: dict[str, Any],
) -> CommunicationDecision:
    return evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=CommunicationPolicyProfile(
            provider_key="slack-bot",
            profile_key=profile.key,
            data=profile.data,
            disabled_status="profile_disabled",
            store_non_trigger_default=False,
            visibility_blocked_status="surface_blocked",
        ),
        event=_slack_policy_event(profile, parsed),
    )


def _slack_policy_event(
    profile: SlackProfile,
    parsed: Mapping[str, Any],
) -> CommunicationPolicyEvent:
    bot_user_id = _slack_facet_text(profile, "bot_user_id")
    return CommunicationPolicyEvent(
        update_type=str(parsed["update_type"]),
        event_type=str(parsed.get("event_type") or parsed["update_type"]),
        text=str(parsed.get("text") or ""),
        is_direct=parsed.get("channel_type") == "im",
        visibility_mode_keys=(
            ("dm_mode",) if parsed.get("channel_type") == "im" else ("channel_mode", "group_mode")
        ),
        visibility_allowed_keys=(
            "allowed_surface_refs",
            "allowed_channel_refs",
            "allowed_channel_ids",
            "allowed_channels",
        ),
        visibility_denied_keys=(
            "denied_surface_refs",
            "denied_channel_refs",
            "denied_channel_ids",
            "denied_channels",
        ),
        surface_candidate_refs=candidate_refs(
            parsed.get("surface_ref"),
            parsed.get("channel_id"),
            "slack-channel",
        ),
        user_candidate_refs=candidate_refs(
            parsed.get("user_ref"),
            parsed.get("user_id"),
            "slack-user",
        ),
        user_allowed_keys=("allowed_user_refs", "allowed_user_ids", "allowed_users"),
        user_denied_keys=("denied_user_refs", "denied_user_ids", "denied_users"),
        surface_id_prefix="slack-channel",
        user_id_prefix="slack-user",
        group_trigger_keys=("channel_trigger", "group_trigger"),
        group_always_reason="channel_always",
        mention_literals=(f"<@{bot_user_id}>",) if bot_user_id else (),
        is_self=bool(parsed.get("is_self_message")),
        interaction=_slack_interaction_check(profile, parsed),
    )


def _slack_interaction_check(
    profile: SlackProfile,
    parsed: Mapping[str, Any],
) -> CommunicationInteractionCheck | None:
    if parsed.get("update_type") != "block_actions":
        return None
    trigger = config_policy(profile.data, "trigger_policy")
    message_ref = parsed.get("message_ref")
    action_id = str(parsed.get("action_id") or "")
    external_id = None
    if isinstance(message_ref, str) and action_id:
        external_id = _outbound_button_external_id(
            profile_key=profile.key,
            message_ref=message_ref,
            action_id=action_id,
            value=str(parsed.get("button_value") or ""),
            block_id=str(parsed.get("block_id") or ""),
        )
    return CommunicationInteractionCheck(
        external_id=external_id,
        trigger_reason="interaction",
        blocked_status="interaction_blocked",
        allow_unknown=trigger.get("allow_unknown_interactions") is True,
    )


def _normalized_slack_event(
    *,
    profile: SlackProfile,
    parsed: dict[str, Any],
    retry_num: str | None,
    retry_reason: str | None,
    mark_interaction_clicked: bool,
) -> NormalizedInboundEvent:
    return NormalizedInboundEvent(
        provider_key="slack-bot",
        profile_key=profile.key,
        event_key=str(parsed["event_key"]),
        update_type=str(parsed["update_type"]),
        source_kind=str(parsed["source_kind"]),
        request_key=_agent_request_key(profile, parsed),
        request_title=str(parsed["request_title"]),
        body_preview=str(parsed["body_preview"] or ""),
        source_message_ref=parsed.get("message_ref"),
        surface=_slack_surface_write(profile, parsed),
        event=NormalizedResourceWrite(
            resource_key="communication-event",
            external_id=f"slack-event:{profile.key}:{parsed['event_key']}",
            title=f"Slack {parsed['update_type']}",
            data_json={
                "provider_key": "slack-bot",
                "profile_key": profile.key,
                "auth_profile_key": profile.auth_profile_key,
                "event_key": parsed["event_key"],
                "team_id": parsed.get("team_id"),
                "update_type": parsed["update_type"],
                "event_type": parsed.get("event_type"),
                "surface_ref": parsed.get("surface_ref"),
                "thread_ref": parsed.get("thread_ref"),
                "message_ref": parsed.get("message_ref"),
                "interaction_ref": parsed.get("interaction_ref"),
                "retry_num": retry_num,
                "retry_reason": retry_reason,
            },
            provenance_json={"source": "slack-ingress"},
            preserve_existing_on_dedupe=True,
        ),
        message=_slack_message_write(profile, parsed),
        interaction=_slack_interaction_write(profile, parsed),
        state_patches=(
            [_slack_click_patch(profile, parsed)]
            if mark_interaction_clicked and parsed["update_type"] == "block_actions"
            else []
        ),
        request_metadata_json={
            "profile_key": profile.key,
            "auth_profile_key": profile.auth_profile_key,
            "interaction_ref": parsed.get("interaction_ref"),
            "invoker_ref": parsed.get("user_ref"),
            "surface_ref": parsed.get("surface_ref"),
            "channel_ref": parsed.get("surface_ref"),
            "thread_ref": parsed.get("thread_ref"),
            "identity": profile.data.get("identity"),
            "agent_guidance": profile.data.get("agent_guidance"),
            "context_policy": profile.data.get("context_policy"),
            "response_policy": profile.data.get("response_policy"),
        },
        response_json={
            "profile_key": profile.key,
            "event_key": parsed.get("event_key"),
            "update_type": parsed.get("update_type"),
        },
    )


def _slack_message_write(
    profile: SlackProfile,
    parsed: dict[str, Any],
) -> NormalizedResourceWrite | None:
    if parsed["source_kind"] != "slack-message":
        return None
    if parsed.get("is_self_message") is True:
        return None
    channel_id = parsed.get("channel_id")
    message_ts = parsed.get("message_ts")
    if not isinstance(channel_id, str) or not isinstance(message_ts, str):
        return None
    return NormalizedResourceWrite(
        resource_key="communication-message",
        external_id=f"slack-message:{profile.key}:{channel_id}:{message_ts}",
        title=parsed["request_title"],
        data_json={
            "provider_key": "slack-bot",
            "profile_key": profile.key,
            "auth_profile_key": profile.auth_profile_key,
            "team_id": parsed.get("team_id"),
            "direction": "inbound",
            "surface_ref": parsed.get("surface_ref"),
            "channel_ref": parsed.get("surface_ref"),
            "thread_ref": parsed.get("thread_ref"),
            "message_ref": parsed.get("message_ref"),
            "provider_message_ts": message_ts,
            "content_type": parsed.get("content_type"),
            "text_preview": parsed.get("body_preview"),
            "transport_status": "received",
            "attention_status": "unread",
            "from_ref": parsed.get("user_ref"),
        },
        provenance_json={"source": "slack-ingress"},
        preserve_existing_on_dedupe=True,
    )


def _agent_request_key(profile: SlackProfile, parsed: Mapping[str, Any]) -> str:
    message_ref = parsed.get("message_ref")
    if parsed.get("source_kind") == "slack-message" and isinstance(message_ref, str):
        return f"slack-message-trigger:{profile.key}:{message_ref}"
    interaction_ref = parsed.get("interaction_ref")
    if isinstance(interaction_ref, str):
        return f"slack-interaction:{profile.key}:{interaction_ref}"
    return f"slack-event:{profile.key}:{parsed['event_key']}"


def _slack_interaction_write(
    profile: SlackProfile,
    parsed: dict[str, Any],
) -> NormalizedResourceWrite | None:
    if parsed["update_type"] != "block_actions":
        return None
    interaction_ref = parsed.get("interaction_ref")
    if not isinstance(interaction_ref, str):
        return None
    return NormalizedResourceWrite(
        resource_key="communication-interaction",
        external_id=f"{profile.key}:{interaction_ref}",
        title=parsed["request_title"],
        data_json={
            "provider_key": "slack-bot",
            "profile_key": profile.key,
            "auth_profile_key": profile.auth_profile_key,
            "interaction_ref": interaction_ref,
            "interaction_type": "block_actions",
            "surface_ref": parsed.get("surface_ref"),
            "channel_ref": parsed.get("surface_ref"),
            "thread_ref": parsed.get("thread_ref"),
            "message_ref": parsed.get("message_ref"),
            "action_id": parsed.get("action_id"),
            "button_value": parsed.get("button_value"),
            "from_ref": parsed.get("user_ref"),
        },
        provenance_json={"source": "slack-ingress"},
        preserve_existing_on_dedupe=True,
    )


def _slack_surface_write(
    profile: SlackProfile,
    parsed: Mapping[str, Any],
) -> NormalizedResourceWrite | None:
    channel_id = parsed.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id:
        return None
    return NormalizedResourceWrite(
        resource_key="communication-channel",
        external_id=f"slack-channel:{profile.key}:{channel_id}",
        title=channel_id,
        data_json={
            "provider_key": "slack-bot",
            "profile_key": profile.key,
            "auth_profile_key": profile.auth_profile_key,
            "team_id": parsed.get("team_id"),
            "surface_ref": _surface_ref(channel_id),
            "channel_ref": _surface_ref(channel_id),
            "provider_channel_id": channel_id,
            "kind": "slack-dm" if parsed.get("channel_type") == "im" else "slack-channel",
            "display_name": channel_id,
            "safe_external_ref": f"slack-channel:{channel_id}",
            "send_enabled": True,
            "ingest_enabled": True,
            "capabilities": {"can_read": True, "can_write": True, "can_thread": True},
        },
        provenance_json={"source": "slack-ingress"},
    )


def _slack_click_patch(
    profile: SlackProfile,
    parsed: Mapping[str, Any],
) -> NormalizedResourcePatch:
    message_ref = str(parsed.get("message_ref") or "")
    action_id = str(parsed.get("action_id") or "")
    value = str(parsed.get("button_value") or "")
    block_id = str(parsed.get("block_id") or "")
    return NormalizedResourcePatch(
        resource_key="communication-interaction",
        external_id=_outbound_button_external_id(
            profile_key=profile.key,
            message_ref=message_ref,
            action_id=action_id,
            value=value,
            block_id=block_id,
        ),
        data_json={
            "status": "clicked",
            "last_clicked_by_ref": parsed.get("user_ref"),
            "last_clicked_message_ref": parsed.get("message_ref"),
            "last_action_id": parsed.get("action_id"),
        },
    )


def _first_action(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    actions = payload.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, Mapping):
                return action
    return {}


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _slack_facet_text(profile: SlackProfile, key: str) -> str | None:
    facets = profile.data.get("provider_facets")
    slack_facet = facets.get("slack-bot") if isinstance(facets, Mapping) else None
    if not isinstance(slack_facet, Mapping):
        return None
    value = slack_facet.get(key)
    return str(value).strip() if value is not None and str(value).strip() else None


def _is_self_message_event(
    profile: SlackProfile,
    *,
    event_type: str,
    user_id: str,
    bot_id: str,
    subtype: str,
) -> bool:
    if event_type != "message":
        return False
    profile_bot_user_id = _slack_facet_text(profile, "bot_user_id")
    if profile_bot_user_id and user_id == profile_bot_user_id:
        return True
    profile_bot_id = _slack_facet_text(profile, "bot_id")
    if profile_bot_id and bot_id == profile_bot_id:
        return True
    return subtype == "bot_message" and bool(profile_bot_user_id or profile_bot_id)


def _nested(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return redact_secret_text(str(value))[:_MAX_PREVIEW]


__all__ = ["router"]
