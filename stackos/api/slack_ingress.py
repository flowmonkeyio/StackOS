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
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import Session, col, select

from stackos.api.deps import get_session
from stackos.artifacts import redact_secret_text
from stackos.db.models import IntegrationCredential, Plugin, Resource, ResourceRecord
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository

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
    resources, optionally creates one generic agent request from static policy,
    and stops. It never calls Slack, never starts a model, and never decides the
    business workflow.
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
    record = _resource_record_by_external_id(
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


def _resource_record_by_external_id(
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
    if not decision["store"]:
        return {
            "ok": True,
            "profile_key": profile.key,
            "event_key": parsed.get("event_key"),
            "update_type": parsed.get("update_type"),
            "policy_status": decision["status"],
        }
    resources = ResourceRepository(session)
    request_repo = AgentRequestRepository(session)
    request_key = _agent_request_key(profile, parsed) if decision["create_request"] else None
    existing_request = (
        request_repo.find_by_key(project_id=project_id, request_key=request_key)
        if request_key is not None
        else None
    )
    create_request = decision["create_request"] and existing_request is None
    policy_status = "request_deduped" if existing_request is not None else decision["status"]

    event = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
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
            "policy_status": policy_status,
            "triggered": create_request,
            "request_key": request_key,
            "deduped_request_id": existing_request.id if existing_request is not None else None,
            "trigger_reason": decision.get("trigger_reason"),
            "matched_command": decision.get("matched_command"),
            "surface_ref": parsed.get("surface_ref"),
            "thread_ref": parsed.get("thread_ref"),
            "message_ref": parsed.get("message_ref"),
            "interaction_ref": parsed.get("interaction_ref"),
            "retry_num": retry_num,
            "retry_reason": retry_reason,
        },
        provenance_json={"source": "slack-ingress"},
    ).data
    message_record_id = _store_message(
        session,
        resources,
        project_id=project_id,
        profile=profile,
        parsed=parsed,
        policy_status=policy_status,
    )
    interaction_record_id = _store_interaction(
        session,
        resources,
        project_id=project_id,
        profile=profile,
        parsed=parsed,
        policy_status=policy_status,
    )
    source_record_id = interaction_record_id or message_record_id or event.id
    source_resource_key = (
        "communication-interaction"
        if interaction_record_id is not None
        else "communication-message"
        if message_record_id is not None
        else "communication-event"
    )
    request_id = None
    if create_request and request_key is not None:
        request = request_repo.create(
            project_id=project_id,
            request_key=request_key,
            title=parsed["request_title"],
            body_preview=parsed["body_preview"],
            source_provider="slack-bot",
            source_kind=parsed["source_kind"],
            source_resource_key=source_resource_key,
            source_resource_record_id=source_record_id,
            source_message_ref=parsed.get("message_ref"),
            metadata_json={
                "profile_key": profile.key,
                "auth_profile_key": profile.auth_profile_key,
                "event_record_id": event.id,
                "interaction_ref": parsed.get("interaction_ref"),
                "invoker_ref": parsed.get("user_ref"),
                "surface_ref": parsed.get("surface_ref"),
                "channel_ref": parsed.get("surface_ref"),
                "thread_ref": parsed.get("thread_ref"),
                "trigger_reason": decision.get("trigger_reason"),
                "matched_command": decision.get("matched_command"),
                "identity": profile.data.get("identity"),
                "agent_guidance": profile.data.get("agent_guidance"),
                "context_policy": profile.data.get("context_policy"),
                "response_policy": profile.data.get("response_policy"),
            },
        ).data
        request_id = request.id
    elif existing_request is not None:
        request_id = existing_request.id
    return {
        "ok": True,
        "profile_key": profile.key,
        "event_key": parsed["event_key"],
        "update_type": parsed["update_type"],
        "policy_status": policy_status,
        "event_record_id": event.id,
        "message_record_id": message_record_id,
        "interaction_record_id": interaction_record_id,
        "agent_request_id": request_id,
    }


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
) -> dict[str, Any]:
    data = profile.data
    if data.get("enabled") is False:
        return {"store": False, "create_request": False, "status": "profile_disabled"}
    if not _surface_allowed(profile, parsed):
        return {"store": False, "create_request": False, "status": "surface_blocked"}
    trigger_match = _trigger_match(profile, parsed)
    if trigger_match is None:
        visibility = _policy(profile, "visibility_policy")
        if visibility.get("store_non_trigger_messages") is not True:
            return {"store": False, "create_request": False, "status": "not_triggered"}
        return {"store": True, "create_request": False, "status": "observed"}
    if not _user_allowed(profile, parsed):
        return {"store": True, "create_request": False, "status": "invoker_blocked"}
    if parsed["update_type"] == "block_actions" and not _known_interaction(
        session,
        project_id,
        profile,
        parsed,
    ):
        return {"store": True, "create_request": False, "status": "interaction_blocked"}
    return {
        "store": True,
        "create_request": True,
        "status": "request_created",
        "trigger_reason": trigger_match["reason"],
        "matched_command": trigger_match.get("command"),
    }


def _surface_allowed(profile: SlackProfile, parsed: Mapping[str, Any]) -> bool:
    access = _policy(profile, "access_policy")
    mode_key = "dm_mode" if parsed.get("channel_type") == "im" else "channel_mode"
    mode = access.get(mode_key, access.get("group_mode"))
    if mode == "disabled" or mode is None:
        return False
    denied = _refs(access, "denied_surface_refs", "denied_channel_refs", "denied_channels")
    surface_ref = parsed.get("surface_ref")
    if isinstance(surface_ref, str) and surface_ref in denied:
        return False
    if mode in {"all", "denylist"}:
        return True
    allowed = _refs(access, "allowed_surface_refs", "allowed_channel_refs", "allowed_channels")
    if isinstance(surface_ref, str) and surface_ref in allowed:
        return True
    if parsed.get("channel_type") == "im":
        allowed_users = _refs(access, "allowed_user_refs", "allowed_user_ids", "allowed_users")
        return bool(parsed.get("user_ref") in allowed_users)
    return False


def _user_allowed(profile: SlackProfile, parsed: Mapping[str, Any]) -> bool:
    access = _policy(profile, "access_policy")
    mode = access.get("user_mode")
    if mode == "disabled" or mode is None:
        return False
    denied = _refs(access, "denied_user_refs", "denied_user_ids", "denied_users")
    user_ref = parsed.get("user_ref")
    if isinstance(user_ref, str) and user_ref in denied:
        return False
    if mode in {"all", "denylist"}:
        return True
    allowed = _refs(access, "allowed_user_refs", "allowed_user_ids", "allowed_users")
    return bool(isinstance(user_ref, str) and user_ref in allowed)


def _trigger_match(profile: SlackProfile, parsed: Mapping[str, Any]) -> dict[str, Any] | None:
    if parsed.get("update_type") == "block_actions":
        return {"reason": "interaction"}
    trigger = _policy(profile, "trigger_policy")
    event_type = parsed.get("event_type")
    allowed_events = _string_list(trigger.get("event_types") or trigger.get("allowed_events"))
    if allowed_events and event_type not in allowed_events:
        return None
    text = str(parsed.get("text") or "")
    command = _matched_command(text, trigger)
    if command is not None:
        return {"reason": "command", "command": command}
    if event_type == "app_mention":
        return {"reason": "mention"}
    if parsed.get("channel_type") == "im" and trigger.get("dm_trigger", "always") == "always":
        return {"reason": "dm"}
    channel_trigger = trigger.get(
        "channel_trigger",
        trigger.get("group_trigger", "mention_or_command"),
    )
    if channel_trigger == "always":
        return {"reason": "channel_always"}
    if channel_trigger != "never" and _matches_mention(text, trigger, profile):
        return {"reason": "mention"}
    return None


def _matched_command(text: str, trigger: Mapping[str, Any]) -> dict[str, Any] | None:
    first_token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not first_token:
        return None
    commands = trigger.get("commands")
    if not isinstance(commands, list):
        return None
    for item in commands:
        if not isinstance(item, Mapping) or item.get("enabled") is False:
            continue
        candidates = [str(item.get("command") or ""), *_string_list(item.get("aliases"))]
        for candidate in candidates:
            normalized = candidate if candidate.startswith("/") else f"/{candidate}"
            if first_token == normalized:
                return dict(item)
    return None


def _matches_mention(text: str, trigger: Mapping[str, Any], profile: SlackProfile) -> bool:
    bot_user_id = _slack_facet_text(profile, "bot_user_id")
    if bot_user_id and f"<@{bot_user_id}>" in text:
        return True
    for pattern in _string_list(trigger.get("mention_patterns")):
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _known_interaction(
    session: Session,
    project_id: int,
    profile: SlackProfile,
    parsed: Mapping[str, Any],
) -> bool:
    trigger = _policy(profile, "trigger_policy")
    if trigger.get("allow_unknown_interactions") is True:
        return True
    message_ref = parsed.get("message_ref")
    action_id = str(parsed.get("action_id") or "")
    value = str(parsed.get("button_value") or "")
    block_id = str(parsed.get("block_id") or "")
    if not isinstance(message_ref, str) or not action_id:
        return False
    external_id = _outbound_button_external_id(
        profile_key=profile.key,
        message_ref=message_ref,
        action_id=action_id,
        value=value,
        block_id=block_id,
    )
    record = _resource_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-interaction",
        external_id=external_id,
    )
    if record is None:
        return False
    _mark_button_clicked(session, record, parsed)
    return True


def _store_message(
    session: Session,
    resources: ResourceRepository,
    *,
    project_id: int,
    profile: SlackProfile,
    parsed: dict[str, Any],
    policy_status: str,
) -> int | None:
    if parsed["source_kind"] != "slack-message":
        return None
    channel_id = parsed.get("channel_id")
    message_ts = parsed.get("message_ts")
    if not isinstance(channel_id, str) or not isinstance(message_ts, str):
        return None
    _upsert_channel(resources, project_id, profile=profile, parsed=parsed)
    external_id = f"slack-message:{profile.key}:{channel_id}:{message_ts}"
    existing = _resource_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-message",
        external_id=external_id,
    )
    if existing is not None and policy_status == "request_deduped":
        return existing.id
    record = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=external_id,
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
            "policy_status": policy_status,
            "text_preview": parsed.get("body_preview"),
            "transport_status": "received",
            "attention_status": "unread",
            "from_ref": parsed.get("user_ref"),
        },
        provenance_json={"source": "slack-ingress"},
    ).data
    return record.id


def _agent_request_key(profile: SlackProfile, parsed: Mapping[str, Any]) -> str:
    message_ref = parsed.get("message_ref")
    if parsed.get("source_kind") == "slack-message" and isinstance(message_ref, str):
        return f"slack-message-trigger:{profile.key}:{message_ref}"
    interaction_ref = parsed.get("interaction_ref")
    if isinstance(interaction_ref, str):
        return f"slack-interaction:{profile.key}:{interaction_ref}"
    return f"slack-event:{profile.key}:{parsed['event_key']}"


def _store_interaction(
    session: Session,
    resources: ResourceRepository,
    *,
    project_id: int,
    profile: SlackProfile,
    parsed: dict[str, Any],
    policy_status: str,
) -> int | None:
    if parsed["update_type"] != "block_actions":
        return None
    interaction_ref = parsed.get("interaction_ref")
    if not isinstance(interaction_ref, str):
        return None
    record = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
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
            "status": policy_status,
            "from_ref": parsed.get("user_ref"),
        },
        provenance_json={"source": "slack-ingress"},
    ).data
    # The known-interaction policy path already marks matching outbound state,
    # but keep this idempotent fallback for profiles that allow unknown actions.
    if policy_status == "request_created":
        _ = _known_interaction(session, project_id, profile, parsed)
    return record.id


def _upsert_channel(
    resources: ResourceRepository,
    project_id: int,
    *,
    profile: SlackProfile,
    parsed: Mapping[str, Any],
) -> None:
    channel_id = parsed.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id:
        return
    resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
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


def _mark_button_clicked(
    session: Session,
    record: ResourceRecord,
    parsed: Mapping[str, Any],
) -> None:
    data = dict(record.data_json or {})
    data["status"] = "clicked"
    data["last_clicked_by_ref"] = parsed.get("user_ref")
    data["last_clicked_message_ref"] = parsed.get("message_ref")
    data["last_action_id"] = parsed.get("action_id")
    record.data_json = data
    session.add(record)
    session.commit()


def _outbound_button_external_id(
    *,
    profile_key: str,
    message_ref: str,
    action_id: str,
    value: str,
    block_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{message_ref}\0{block_id}\0{action_id}\0{value}".encode()
    ).hexdigest()[:24]
    return f"slack-button:{profile_key}:{digest}"


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


def _policy(profile: SlackProfile, key: str) -> dict[str, Any]:
    value = profile.data.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _refs(policy: Mapping[str, Any], *keys: str) -> set[str]:
    refs: set[str] = set()
    for key in keys:
        for value in _string_list(policy.get(key)):
            if key.endswith("_ids"):
                prefix = "slack-user" if "user" in key else "slack-channel"
                refs.add(f"{prefix}:{value}")
            else:
                refs.add(value)
    return refs


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _nested(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _surface_ref(channel_id: str) -> str:
    return f"slack-channel:{channel_id}"


def _message_ref(channel_id: str, ts: str) -> str:
    return f"slack-message:{channel_id}:{ts}"


def _thread_ref(channel_id: str, ts: str) -> str:
    return f"slack-thread:{channel_id}:{ts}"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return redact_secret_text(str(value))[:_MAX_PREVIEW]


__all__ = ["router"]
