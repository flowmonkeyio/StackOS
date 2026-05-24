"""Telegram webhook/local Bot API ingress for communication-triggered requests."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, col, select

from stackos.api.deps import get_session
from stackos.artifacts import redact_secret_text
from stackos.db.models import IntegrationCredential, Plugin, Resource, ResourceRecord
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository

router = APIRouter(prefix="/api/v1/ingress/telegram", tags=["telegram-ingress"])


class TelegramIngressOut(BaseModel):
    """Result of storing one Telegram update."""

    ok: bool
    update_id: int
    bot_profile_key: str
    policy_status: str
    event_record_id: int | None = None
    message_record_id: int | None = None
    interaction_record_id: int | None = None
    agent_request_id: int | None = None


@dataclass(frozen=True)
class TelegramBotProfile:
    key: str
    auth_profile_key: str
    data: dict[str, Any]


@router.post(
    "/{project_id}/{bot_profile_key}",
    response_model=TelegramIngressOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_telegram_update(
    project_id: int,
    bot_profile_key: str,
    update: dict[str, Any] = Body(...),
    secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
    session: Session = Depends(get_session),
) -> TelegramIngressOut:
    """Store a Telegram update and create one generic claimable agent request.

    This endpoint is intentionally static plumbing: it validates Telegram's
    secret-token header, normalizes the event into Communications resources,
    creates an `agent_requests` row, and stops. It does not call a model, infer
    intent, approve work, or choose follow-up tools.
    """

    profile = _require_bot_profile(
        session,
        project_id=project_id,
        bot_profile_key=bot_profile_key,
    )
    _verify_secret(session, project_id=project_id, profile=profile, header=secret_token)
    stored = _store_update(
        session,
        project_id=project_id,
        profile=profile,
        update=update,
    )
    return TelegramIngressOut(**stored)


def _verify_secret(
    session: Session,
    *,
    project_id: int,
    profile: TelegramBotProfile,
    header: str | None,
) -> None:
    if not header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Telegram secret")
    credential = _integration_credential(
        session,
        project_id=project_id,
        profile_key=profile.auth_profile_key,
    )
    if credential is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Telegram secret")
    assert credential.id is not None
    raw = IntegrationCredentialRepository(session).get_decrypted(credential.id)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    expected = str(payload.get("webhook_secret_token") or "")
    if not expected or not hmac.compare_digest(header, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Telegram secret")


def _require_bot_profile(
    session: Session,
    *,
    project_id: int,
    bot_profile_key: str,
) -> TelegramBotProfile:
    record = _resource_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-bot-profile",
        external_id=f"telegram-bot-profile:{bot_profile_key}",
    )
    if record is not None:
        data = dict(record.data_json or {})
        provider_key = data.get("provider_key", "telegram-bot")
        auth_profile_key = data.get("auth_profile_key")
        if (
            data.get("key") == bot_profile_key
            and provider_key == "telegram-bot"
            and isinstance(auth_profile_key, str)
        ):
            return TelegramBotProfile(
                key=bot_profile_key,
                auth_profile_key=auth_profile_key,
                data=data,
            )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Telegram secret")


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


def _integration_credential(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
) -> IntegrationCredential | None:
    row = session.exec(
        select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.kind == "telegram-bot",
            IntegrationCredential.profile_key == profile_key,
        )
    ).first()
    return row


def _store_update(
    session: Session,
    *,
    project_id: int,
    profile: TelegramBotProfile,
    update: dict[str, Any],
) -> dict[str, Any]:
    update_id = update.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        raise ValidationError("Telegram update_id is required")
    parsed = _parse_update(update)
    decision = _policy_decision(session, project_id, profile, parsed)
    if not decision["store"]:
        return {
            "ok": True,
            "update_id": update_id,
            "bot_profile_key": profile.key,
            "policy_status": decision["status"],
        }
    resources = ResourceRepository(session)
    event = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-event",
        external_id=f"telegram-update:{profile.key}:{update_id}",
        title=f"Telegram update {update_id}",
        data_json={
            "provider_key": "telegram-bot",
            "bot_profile_key": profile.key,
            "auth_profile_key": profile.auth_profile_key,
            "update_id": update_id,
            "update_type": parsed["update_type"],
            "policy_status": decision["status"],
            "triggered": decision["create_request"],
            "trigger_reason": decision.get("trigger_reason"),
            "matched_command": decision.get("matched_command"),
            "message_ref": parsed.get("message_ref"),
            "interaction_ref": parsed.get("interaction_ref"),
        },
        provenance_json={"source": "telegram-ingress"},
    ).data
    message_record_id = _store_message(
        resources,
        project_id=project_id,
        profile=profile,
        parsed=parsed,
        policy_status=decision["status"],
    )
    interaction_record_id = _store_interaction(
        resources,
        project_id=project_id,
        profile=profile,
        parsed=parsed,
        policy_status=decision["status"],
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
    if decision["create_request"]:
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key=f"telegram-update:{profile.key}:{update_id}",
                title=parsed["request_title"],
                body_preview=parsed["body_preview"],
                source_provider="telegram-bot",
                source_kind=parsed["source_kind"],
                source_resource_key=source_resource_key,
                source_resource_record_id=source_record_id,
                source_message_ref=parsed.get("message_ref"),
                metadata_json={
                    "bot_profile_key": profile.key,
                    "auth_profile_key": profile.auth_profile_key,
                    "update_id": update_id,
                    "event_record_id": event.id,
                    "interaction_ref": parsed.get("interaction_ref"),
                    "invoker_ref": parsed.get("user_ref"),
                    "chat_ref": parsed.get("chat_ref"),
                    "thread_ref": parsed.get("thread_ref"),
                    "trigger_reason": decision.get("trigger_reason"),
                    "matched_command": decision.get("matched_command"),
                    "identity": profile.data.get("identity"),
                    "agent_guidance": profile.data.get("agent_guidance"),
                    "context_policy": profile.data.get("context_policy"),
                    "response_policy": profile.data.get("response_policy"),
                },
            )
            .data
        )
        request_id = request.id
    return {
        "ok": True,
        "update_id": update_id,
        "bot_profile_key": profile.key,
        "policy_status": decision["status"],
        "event_record_id": event.id,
        "message_record_id": message_record_id,
        "interaction_record_id": interaction_record_id,
        "agent_request_id": request_id,
    }


def _policy_decision(
    session: Session,
    project_id: int,
    profile: TelegramBotProfile,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    data = profile.data
    if data.get("enabled") is False:
        return {"store": False, "create_request": False, "status": "bot_profile_disabled"}
    _validate_bot_profile(data)
    ingress_mode = data.get("ingress_mode", "webhook")
    if ingress_mode != "webhook":
        return {"store": False, "create_request": False, "status": "ingress_disabled"}
    allowed_updates = _split_config_values(data.get("allowed_updates"))
    if not allowed_updates:
        allowed_updates = _split_config_values(_nested(data, "visibility_policy.allowed_updates"))
    if allowed_updates and parsed["update_type"] not in allowed_updates:
        return {"store": False, "create_request": False, "status": "update_blocked"}

    if not _chat_allowed(profile, parsed):
        return {"store": False, "create_request": False, "status": "chat_blocked"}

    trigger_match = _trigger_match(profile, parsed)
    if trigger_match is None:
        visibility = _policy(profile, "visibility_policy")
        if visibility.get("store_non_trigger_messages") is False:
            return {"store": False, "create_request": False, "status": "not_triggered"}
        return {"store": True, "create_request": False, "status": "observed"}
    user_match = _user_match_type(profile, parsed)
    if user_match is None:
        return {"store": True, "create_request": False, "status": "invoker_blocked"}
    if parsed["update_type"] == "callback_query":
        callback_match = _callback_match_type(session, project_id, profile, parsed)
        if callback_match is None:
            return {"store": True, "create_request": False, "status": "callback_blocked"}
    return {
        "store": True,
        "create_request": True,
        "status": "request_created",
        "trigger_reason": trigger_match["reason"],
        "matched_command": trigger_match.get("command"),
        "identity_confidence": user_match,
    }


def _chat_allowed(profile: TelegramBotProfile, parsed: dict[str, Any]) -> bool:
    access = _policy(profile, "access_policy")
    mode_key = "dm_mode" if parsed.get("chat_type") == "private" else "group_mode"
    mode = access.get(mode_key)
    if mode == "disabled":
        return False
    denied = _refs(access, "denied_chat_refs", "denied_chat_ids", "denied_chats")
    candidates = _candidate_refs(parsed.get("chat_ref"), parsed.get("chat_id"), "telegram-chat")
    if denied and any(candidate in denied for candidate in candidates):
        return False
    if mode in {"all", "denylist"}:
        return True
    allowed = _refs(access, "allowed_chat_refs", "allowed_chat_ids", "allowed_chats")
    if allowed and any(candidate in allowed for candidate in candidates):
        return True
    if parsed.get("chat_type") == "private":
        allowed_users = _refs(
            access,
            "allowed_user_refs",
            "allowed_user_ids",
            "allowed_usernames",
            "allowed_users",
        )
        user_candidates = _candidate_refs(
            parsed.get("user_ref"),
            parsed.get("user_id"),
            "telegram-user",
        )
        username = parsed.get("user_username")
        if isinstance(username, str) and username:
            user_candidates.append(f"telegram-username:{username.lstrip('@')}")
        return bool(
            allowed_users and any(candidate in allowed_users for candidate in user_candidates)
        )
    return False


def _user_allowed(profile: TelegramBotProfile, parsed: dict[str, Any]) -> bool:
    return _user_match_type(profile, parsed) is not None


def _user_match_type(profile: TelegramBotProfile, parsed: dict[str, Any]) -> str | None:
    access = _policy(profile, "access_policy")
    mode = access.get("user_mode")
    if mode == "disabled":
        return None
    denied = _refs(access, "denied_user_refs", "denied_user_ids", "denied_users")
    candidates = _candidate_refs(parsed.get("user_ref"), parsed.get("user_id"), "telegram-user")
    username = parsed.get("user_username")
    if isinstance(username, str) and username:
        candidates.append(f"telegram-username:{username.lstrip('@')}")
    if denied and any(candidate in denied for candidate in candidates):
        return None
    if mode in {"all", "denylist"}:
        return "unrestricted"
    allowed = _refs(
        access,
        "allowed_user_refs",
        "allowed_user_ids",
        "allowed_usernames",
        "allowed_users",
    )
    if not allowed:
        return None
    for candidate in candidates:
        if candidate in allowed:
            return "username" if candidate.startswith("telegram-username:") else "id"
    return None


def _callback_match_type(
    session: Session,
    project_id: int,
    profile: TelegramBotProfile,
    parsed: dict[str, Any],
) -> str | None:
    callback = parsed.get("callback_query")
    if not isinstance(callback, Mapping):
        return None
    callback_data = callback.get("data")
    if not isinstance(callback_data, str) or not callback_data:
        return None
    message_ref = parsed.get("message_ref")
    if not isinstance(message_ref, str) or not message_ref:
        return None
    target = _callback_button_external_id(profile.key, message_ref, callback_data)
    record = _resource_record_by_external_id(
        session,
        project_id=project_id,
        resource_key="communication-interaction",
        external_id=target,
    )
    if record is None:
        return None
    data = record.data_json or {}
    allowed_users = set(_split_config_values(data.get("allowed_user_refs")))
    if allowed_users and parsed.get("user_ref") not in allowed_users:
        return None
    allowed_chats = set(_split_config_values(data.get("allowed_chat_refs")))
    if allowed_chats and parsed.get("chat_ref") not in allowed_chats:
        return None
    _mark_button_clicked(session, record, parsed)
    return "button"


def _callback_button_external_id(
    bot_profile_key: str,
    message_ref: str,
    callback_data: str,
) -> str:
    return f"telegram-button:{bot_profile_key}:{message_ref}:{callback_data}"


def _mark_button_clicked(
    session: Session,
    record: ResourceRecord,
    parsed: dict[str, Any],
) -> None:
    data = dict(record.data_json or {})
    data["status"] = "clicked"
    data["last_callback_query_id"] = parsed.get("callback_query", {}).get("id")
    data["last_clicked_by_ref"] = parsed.get("user_ref")
    data["last_clicked_message_ref"] = parsed.get("message_ref")
    record.data_json = data
    session.add(record)
    session.commit()


def _validate_bot_profile(data: Mapping[str, Any]) -> None:
    identity = data.get("identity")
    if not isinstance(identity, Mapping) or not str(identity.get("display_name") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid Telegram bot profile",
        )
    trigger = data.get("trigger_policy")
    commands = trigger.get("commands") if isinstance(trigger, Mapping) else []
    if commands is not None and (
        not isinstance(commands, list) or any(not isinstance(item, Mapping) for item in commands)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid Telegram bot profile",
        )
    access = data.get("access_policy")
    if not isinstance(access, Mapping):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid Telegram bot profile",
        )
    for key in ("dm_mode", "group_mode", "user_mode"):
        mode = access.get(key)
        if mode not in {"all", "allowlist", "denylist", "disabled"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid Telegram bot profile",
            )
        if mode == "allowlist":
            has_chat_allowlist = bool(
                _refs(access, "allowed_chat_refs", "allowed_chat_ids", "allowed_chats")
            )
            has_user_allowlist = bool(
                _refs(
                    access,
                    "allowed_user_refs",
                    "allowed_user_ids",
                    "allowed_usernames",
                    "allowed_users",
                )
            )
            if key == "dm_mode" and not (has_chat_allowlist or has_user_allowlist):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="invalid Telegram bot profile",
                )
            if key == "group_mode" and not has_chat_allowlist:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="invalid Telegram bot profile",
                )
            if key == "user_mode" and not has_user_allowlist:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="invalid Telegram bot profile",
                )


def _trigger_match(profile: TelegramBotProfile, parsed: dict[str, Any]) -> dict[str, Any] | None:
    if parsed["update_type"] == "callback_query":
        return {"reason": "callback"}
    message = parsed.get("message")
    if not isinstance(message, dict):
        return None
    trigger = _policy(profile, "trigger_policy")
    text = parsed.get("body_preview") or ""
    chat_type = parsed.get("chat_type")
    group_trigger = trigger.get("group_trigger", "mention_or_command")
    if chat_type != "private" and group_trigger == "never":
        return None
    command = _matched_command(text, trigger, profile)
    if command is not None:
        return {"reason": "command", "command": command}
    if chat_type == "private" and trigger.get("dm_trigger", "always") == "always":
        return {"reason": "dm"}
    if group_trigger == "always":
        return {"reason": "group_always"}
    if _matches_mention(text, trigger, profile):
        return {"reason": "mention"}
    if trigger.get("reply_to_bot_triggers") is True and _is_reply_to_bot(message, profile):
        return {"reason": "reply_to_bot"}
    return None


def _matched_command(
    text: str,
    trigger: Mapping[str, Any],
    profile: TelegramBotProfile,
) -> dict[str, Any] | None:
    commands = _command_specs(trigger.get("commands"))
    if not commands:
        return None
    bot_username = _bot_username(profile)
    first_token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    for command in commands:
        if command.get("enabled") is False:
            continue
        candidates = [str(command.get("command") or ""), *command.get("aliases", [])]
        for candidate in candidates:
            normalized = candidate if candidate.startswith("/") else f"/{candidate}"
            if first_token == normalized:
                return command
            if bot_username and first_token == f"{normalized}@{bot_username}":
                return command
    return None


def _command_specs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    specs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        spec = dict(item)
        spec["command"] = command if command.startswith("/") else f"/{command}"
        spec["aliases"] = _split_config_values(spec.get("aliases"))
        specs.append(spec)
    return specs


def _matches_mention(text: str, trigger: Mapping[str, Any], profile: TelegramBotProfile) -> bool:
    bot_username = _bot_username(profile)
    if bot_username and f"@{bot_username}".lower() in text.lower():
        return True
    for pattern in _split_config_values(trigger.get("mention_patterns")):
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _is_reply_to_bot(message: Mapping[str, Any], profile: TelegramBotProfile) -> bool:
    reply = message.get("reply_to_message")
    if not isinstance(reply, dict):
        return False
    sender = reply.get("from")
    if not isinstance(sender, dict) or sender.get("is_bot") is not True:
        return False
    bot_username = _bot_username(profile)
    if not bot_username:
        return True
    return str(sender.get("username") or "").lower() == bot_username.lower()


def _bot_username(profile: TelegramBotProfile) -> str | None:
    value = profile.data.get("bot_username")
    if isinstance(value, str) and value.strip():
        return value.strip().lstrip("@")
    return None


def _policy(profile: TelegramBotProfile, key: str) -> dict[str, Any]:
    value = profile.data.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _nested(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _refs(policy: Mapping[str, Any], *keys: str) -> set[str]:
    refs: set[str] = set()
    for key in keys:
        for value in _split_config_values(policy.get(key)):
            if key.endswith("_ids"):
                prefix = "telegram-user" if "user" in key else "telegram-chat"
                refs.add(f"{prefix}:{value}")
            elif key.endswith("_usernames"):
                refs.add(f"telegram-username:{value.lstrip('@')}")
            else:
                refs.add(value)
    return refs


def _candidate_refs(raw_ref: Any, raw_id: Any, prefix: str) -> list[str]:
    refs: list[str] = []
    if isinstance(raw_ref, str) and raw_ref:
        refs.append(raw_ref)
    if raw_id is not None:
        refs.append(f"{prefix}:{raw_id}")
        refs.append(str(raw_id))
    return refs


def _split_config_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _store_message(
    resources: ResourceRepository,
    *,
    project_id: int,
    profile: TelegramBotProfile,
    parsed: dict[str, Any],
    policy_status: str,
) -> int | None:
    if parsed.get("update_type") == "callback_query":
        return None
    message = parsed.get("message")
    if not isinstance(message, dict):
        return None
    raw_chat = message.get("chat")
    chat: Mapping[str, Any] = raw_chat if isinstance(raw_chat, Mapping) else {}
    chat_id = chat.get("id")
    if chat_id is not None:
        resources.upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id=f"telegram-chat:{profile.key}:{chat_id}",
            title=_chat_title(chat),
            data_json={
                "provider_key": "telegram-bot",
                "bot_profile_key": profile.key,
                "provider_chat_id": str(chat_id),
                "channel_type": chat.get("type"),
                "title": _chat_title(chat),
            },
            provenance_json={"source": "telegram-ingress"},
        )
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return None
    record = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=f"telegram-message:{profile.key}:{chat_id}:{message_id}",
        title=parsed["message_title"],
        data_json={
            "provider_key": "telegram-bot",
            "bot_profile_key": profile.key,
            "direction": "inbound",
            "channel_ref": f"telegram-chat:{chat_id}",
            "thread_ref": _thread_ref(message),
            "message_ref": parsed["message_ref"],
            "provider_message_id": str(message_id),
            "content_type": parsed["content_type"],
            "policy_status": policy_status,
            "text_preview": parsed["body_preview"],
            "attention_status": "unread",
            "attachments": _message_attachments(message),
            "from_ref": _user_ref(message.get("from")),
            "from_username": _username(message.get("from")),
            "date": message.get("date"),
        },
        provenance_json={"source": "telegram-ingress"},
    ).data
    return record.id


def _store_interaction(
    resources: ResourceRepository,
    *,
    project_id: int,
    profile: TelegramBotProfile,
    parsed: dict[str, Any],
    policy_status: str,
) -> int | None:
    callback = parsed.get("callback_query")
    if not isinstance(callback, dict):
        return None
    callback_id = callback.get("id")
    if not callback_id:
        return None
    record = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
        external_id=f"telegram-callback:{profile.key}:{callback_id}",
        title=f"Telegram callback {callback_id}",
        data_json={
            "provider_key": "telegram-bot",
            "bot_profile_key": profile.key,
            "interaction_ref": parsed["interaction_ref"],
            "interaction_type": "inline_callback",
            "callback_query_id": str(callback_id),
            "callback_data": _safe_text(callback.get("data")),
            "button_key": _safe_text(callback.get("data")),
            "message_ref": parsed.get("message_ref"),
            "status": policy_status,
            "from_ref": _user_ref(callback.get("from")),
            "from_username": _username(callback.get("from")),
        },
        provenance_json={"source": "telegram-ingress"},
    ).data
    return record.id


def _parse_update(update: dict[str, Any]) -> dict[str, Any]:
    callback = update.get("callback_query")
    message = _message_from_update(update)
    if isinstance(callback, dict):
        message = callback.get("message") if isinstance(callback.get("message"), dict) else message
        body_preview = _safe_text(callback.get("data"))[:500]
        return {
            "update_type": "callback_query",
            "source_kind": "telegram_callback",
            "callback_query": callback,
            "message": message,
            "message_ref": _message_ref(message),
            "chat_ref": _chat_ref(message),
            "thread_ref": _thread_ref(message) if isinstance(message, dict) else None,
            "chat_id": _chat_id(message),
            "chat_type": _chat_type(message),
            "user_ref": _user_ref(callback.get("from")),
            "user_id": _user_id(callback.get("from")),
            "user_username": _username(callback.get("from")),
            "interaction_ref": f"telegram-callback:{callback.get('id')}",
            "request_title": "Telegram button click",
            "message_title": "Telegram callback source message",
            "body_preview": body_preview,
            "content_type": "callback",
        }
    if isinstance(message, dict):
        body_preview = _safe_text(_message_text(message))[:500]
        return {
            "update_type": _message_update_type(update),
            "source_kind": "telegram_message",
            "message": message,
            "message_ref": _message_ref(message),
            "chat_ref": _chat_ref(message),
            "thread_ref": _thread_ref(message),
            "chat_id": _chat_id(message),
            "chat_type": _chat_type(message),
            "user_ref": _user_ref(message.get("from")),
            "user_id": _user_id(message.get("from")),
            "user_username": _username(message.get("from")),
            "request_title": "Telegram message",
            "message_title": "Telegram inbound message",
            "body_preview": body_preview,
            "content_type": "text" if body_preview else "message",
        }
    return {
        "update_type": "unknown",
        "source_kind": "telegram_event",
        "request_title": "Telegram event",
        "body_preview": "",
        "content_type": "event",
    }


def _message_from_update(update: Mapping[str, Any]) -> dict[str, Any] | None:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        value = update.get(key)
        if isinstance(value, dict):
            return value
    return None


def _message_update_type(update: Mapping[str, Any]) -> str:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        if isinstance(update.get(key), dict):
            return key
    return "message"


def _message_ref(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return f"telegram-message:{chat_id}:{message_id}"


def _chat_ref(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if chat_id is None:
        return None
    return f"telegram-chat:{chat_id}"


def _chat_id(message: Any) -> Any:
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    return chat.get("id") if isinstance(chat, dict) else None


def _chat_type(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    value = chat.get("type") if isinstance(chat, dict) else None
    return str(value) if value is not None else None


def _thread_ref(message: Mapping[str, Any]) -> str | None:
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    thread_id = message.get("message_thread_id")
    if chat_id is None:
        return None
    return f"telegram-thread:{chat_id}:{thread_id or 'default'}"


def _message_text(message: Mapping[str, Any]) -> str:
    for key in ("text", "caption"):
        value = message.get(key)
        if isinstance(value, str):
            return value
    return ""


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return redact_secret_text(value)


def _message_attachments(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        attachments.append({"type": "photo", "count": len(photo)})
    for key in ("document", "video", "audio", "voice"):
        if isinstance(message.get(key), dict):
            attachments.append({"type": key})
    return attachments


def _chat_title(chat: Mapping[str, Any]) -> str:
    for key in ("title", "username", "first_name"):
        value = chat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    chat_id = chat.get("id")
    return f"Telegram chat {chat_id}" if chat_id is not None else "Telegram chat"


def _user_ref(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("id") is not None:
        return f"telegram-user:{value['id']}"
    return None


def _user_id(value: Any) -> Any:
    return value.get("id") if isinstance(value, dict) else None


def _username(value: Any) -> str | None:
    username = value.get("username") if isinstance(value, dict) else None
    return str(username).lstrip("@") if username else None


__all__ = ["router"]
