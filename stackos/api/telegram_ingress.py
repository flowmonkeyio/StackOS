"""Telegram webhook/local Bot API ingress for communication-triggered requests."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
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
    communication_profile_record_by_key,
    config_nested,
    config_refs,
    config_string_list,
    evaluate_inbound_policy,
    merged_provider_profile,
    process_inbound_event,
)
from stackos.db.models import IntegrationCredential
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

router = APIRouter(prefix="/api/v1/ingress/telegram", tags=["telegram-ingress"])


class TelegramIngressOut(BaseModel):
    """Result of storing one Telegram update."""

    ok: bool
    update_id: int
    profile_key: str
    profile_ref: str
    policy_status: str
    event_record_id: int | None = None
    message_record_id: int | None = None
    interaction_record_id: int | None = None
    agent_request_id: int | None = None


@dataclass(frozen=True)
class TelegramProfile:
    key: str
    profile_ref: str
    auth_profile_key: str
    data: dict[str, Any]


@router.post(
    "/{project_id}/{profile_key}",
    response_model=TelegramIngressOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_telegram_update(
    project_id: int,
    profile_key: str,
    update: dict[str, Any] = Body(...),
    secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
    session: Session = Depends(get_session),
) -> TelegramIngressOut:
    """Store a Telegram update and maybe create one claimable agent request.

    This endpoint is intentionally static plumbing: it validates Telegram's
    secret-token header, normalizes the event, applies shared communication
    policy, persists Communications resources, and stops. It does not call a
    model, infer intent, approve work, or choose follow-up tools.
    """

    profile = _require_telegram_profile(
        session,
        project_id=project_id,
        profile_key=profile_key,
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
    profile: TelegramProfile,
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


def _require_telegram_profile(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
) -> TelegramProfile:
    record = communication_profile_record_by_key(
        session,
        project_id=project_id,
        key=profile_key,
    )
    if record is not None:
        data = merged_provider_profile(dict(record.data_json or {}), "telegram-bot")
        provider_key = data.get("provider_key")
        auth_profile_key = data.get("auth_profile_key")
        if (
            data.get("key") == profile_key
            and provider_key == "telegram-bot"
            and isinstance(auth_profile_key, str)
        ):
            profile_ref = str(data.get("profile_ref") or f"communication-profile:{profile_key}")
            return TelegramProfile(
                key=profile_key,
                profile_ref=profile_ref,
                auth_profile_key=auth_profile_key,
                data=data,
            )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Telegram secret")


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
    profile: TelegramProfile,
    update: dict[str, Any],
) -> dict[str, Any]:
    update_id = update.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        raise ValidationError("Telegram update_id is required")
    parsed = _parse_update(update)
    decision = _policy_decision(session, project_id, profile, parsed)
    normalized = _normalized_telegram_event(
        profile=profile,
        parsed=parsed,
        update_id=update_id,
        mark_interaction_clicked=decision.create_request,
    )
    result = process_inbound_event(
        session,
        project_id=project_id,
        event=normalized,
        decision=decision,
    )
    return result.to_response()


def _policy_decision(
    session: Session,
    project_id: int,
    profile: TelegramProfile,
    parsed: dict[str, Any],
) -> CommunicationDecision:
    data = profile.data
    _validate_telegram_profile(data)
    allowed_updates = config_string_list(data.get("allowed_updates"))
    if not allowed_updates:
        allowed_updates = config_string_list(
            config_nested(data, "visibility_policy.allowed_updates")
        )
    return evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=CommunicationPolicyProfile(
            provider_key="telegram-bot",
            profile_key=profile.key,
            data=profile.data,
            disabled_status="profile_disabled",
            store_non_trigger_default=True,
            visibility_blocked_status="chat_blocked",
            ingress_mode_key="ingress_mode",
            ingress_required_value="webhook",
            ingress_disabled_status="ingress_disabled",
            allowed_update_types=tuple(allowed_updates),
            update_blocked_status="update_blocked",
        ),
        event=_telegram_policy_event(profile, parsed),
    )


def _telegram_policy_event(
    profile: TelegramProfile,
    parsed: Mapping[str, Any],
) -> CommunicationPolicyEvent:
    bot_username = _bot_username(profile)
    message = parsed.get("message")
    user_candidates = list(
        candidate_refs(parsed.get("user_ref"), parsed.get("user_id"), "telegram-user")
    )
    username = parsed.get("user_username")
    if isinstance(username, str) and username:
        user_candidates.append(f"telegram-username:{username.lstrip('@')}")
    return CommunicationPolicyEvent(
        update_type=str(parsed["update_type"]),
        event_type=str(parsed["update_type"]),
        text=str(parsed.get("body_preview") or ""),
        is_direct=parsed.get("chat_type") == "private",
        visibility_mode_keys=(
            ("dm_mode",) if parsed.get("chat_type") == "private" else ("group_mode",)
        ),
        visibility_allowed_keys=(
            "allowed_surface_refs",
            "allowed_chat_refs",
            "allowed_chat_ids",
            "allowed_chats",
        ),
        visibility_denied_keys=(
            "denied_surface_refs",
            "denied_chat_refs",
            "denied_chat_ids",
            "denied_chats",
        ),
        surface_candidate_refs=candidate_refs(
            parsed.get("chat_ref"),
            parsed.get("chat_id"),
            "telegram-chat",
        ),
        user_candidate_refs=tuple(dict.fromkeys(user_candidates)),
        user_allowed_keys=(
            "allowed_user_refs",
            "allowed_user_ids",
            "allowed_usernames",
            "allowed_users",
        ),
        user_denied_keys=("denied_user_refs", "denied_user_ids", "denied_users"),
        surface_id_prefix="telegram-chat",
        user_id_prefix="telegram-user",
        username_prefix="telegram-username",
        group_trigger_keys=("group_trigger",),
        group_always_reason="group_always",
        command_suffixes=(bot_username,) if bot_username else (),
        mention_literals=(f"@{bot_username}",) if bot_username else (),
        is_reply_to_bot=isinstance(message, Mapping) and _is_reply_to_bot(message, profile),
        interaction=_telegram_interaction_check(profile, parsed),
    )


def _telegram_interaction_check(
    profile: TelegramProfile,
    parsed: Mapping[str, Any],
) -> CommunicationInteractionCheck | None:
    if parsed.get("update_type") != "callback_query":
        return None
    callback = parsed.get("callback_query")
    callback_data = callback.get("data") if isinstance(callback, Mapping) else None
    message_ref = parsed.get("message_ref")
    external_id = None
    if isinstance(callback_data, str) and callback_data and isinstance(message_ref, str):
        external_id = _callback_button_external_id(profile.key, message_ref, callback_data)
    return CommunicationInteractionCheck(
        external_id=external_id,
        trigger_reason="callback",
        blocked_status="callback_blocked",
    )


def _callback_button_external_id(
    profile_key: str,
    message_ref: str,
    callback_data: str,
) -> str:
    return f"telegram-button:{profile_key}:{message_ref}:{callback_data}"


def _validate_telegram_profile(data: Mapping[str, Any]) -> None:
    identity = data.get("identity")
    if not isinstance(identity, Mapping) or not str(identity.get("display_name") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid Telegram communication profile",
        )
    trigger = data.get("trigger_policy")
    commands = trigger.get("commands") if isinstance(trigger, Mapping) else []
    if commands is not None and (
        not isinstance(commands, list) or any(not isinstance(item, Mapping) for item in commands)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid Telegram communication profile",
        )
    access = data.get("access_policy")
    if not isinstance(access, Mapping):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid Telegram communication profile",
        )
    for key in ("dm_mode", "group_mode", "user_mode"):
        mode = access.get(key)
        if mode not in {"all", "allowlist", "denylist", "disabled"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid Telegram communication profile",
            )
        if key == "user_mode" and mode == "allowlist":
            has_user_allowlist = bool(
                config_refs(
                    access,
                    keys=(
                        "allowed_user_refs",
                        "allowed_user_ids",
                        "allowed_usernames",
                        "allowed_users",
                    ),
                    surface_id_prefix="telegram-chat",
                    user_id_prefix="telegram-user",
                    username_prefix="telegram-username",
                )
            )
            if not has_user_allowlist:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="invalid Telegram communication profile",
                )


def _is_reply_to_bot(message: Mapping[str, Any], profile: TelegramProfile) -> bool:
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


def _bot_username(profile: TelegramProfile) -> str | None:
    value = profile.data.get("bot_username")
    if isinstance(value, str) and value.strip():
        return value.strip().lstrip("@")
    return None


def _normalized_telegram_event(
    *,
    profile: TelegramProfile,
    parsed: dict[str, Any],
    update_id: int,
    mark_interaction_clicked: bool,
) -> NormalizedInboundEvent:
    return NormalizedInboundEvent(
        provider_key="telegram-bot",
        profile_key=profile.key,
        event_key=str(update_id),
        update_type=str(parsed["update_type"]),
        source_kind=str(parsed["source_kind"]),
        request_key=f"telegram-update:{profile.key}:{update_id}",
        request_title=str(parsed["request_title"]),
        body_preview=str(parsed["body_preview"] or ""),
        source_message_ref=parsed.get("message_ref"),
        surface=_telegram_surface_write(profile, parsed),
        event=NormalizedResourceWrite(
            resource_key="communication-event",
            external_id=f"telegram-update:{profile.key}:{update_id}",
            title=f"Telegram update {update_id}",
            data_json={
                "provider_key": "telegram-bot",
                "profile_key": profile.key,
                "profile_ref": profile.profile_ref,
                "auth_profile_key": profile.auth_profile_key,
                "update_id": update_id,
                "update_type": parsed["update_type"],
                "message_ref": parsed.get("message_ref"),
                "interaction_ref": parsed.get("interaction_ref"),
            },
            provenance_json={"source": "telegram-ingress"},
            preserve_existing_on_dedupe=True,
        ),
        message=_telegram_message_write(profile, parsed),
        interaction=_telegram_interaction_write(profile, parsed),
        state_patches=(
            [_telegram_click_patch(profile, parsed)]
            if mark_interaction_clicked and parsed["update_type"] == "callback_query"
            else []
        ),
        request_metadata_json={
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "auth_profile_key": profile.auth_profile_key,
            "update_id": update_id,
            "interaction_ref": parsed.get("interaction_ref"),
            "invoker_ref": parsed.get("user_ref"),
            "chat_ref": parsed.get("chat_ref"),
            "thread_ref": parsed.get("thread_ref"),
            "identity": profile.data.get("identity"),
            "agent_guidance": profile.data.get("agent_guidance"),
            "context_policy": profile.data.get("context_policy"),
            "response_policy": profile.data.get("response_policy"),
        },
        response_json={
            "update_id": update_id,
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
        },
    )


def _telegram_message_write(
    profile: TelegramProfile,
    parsed: dict[str, Any],
) -> NormalizedResourceWrite | None:
    if parsed.get("update_type") == "callback_query":
        return None
    message = parsed.get("message")
    if not isinstance(message, dict):
        return None
    raw_chat = message.get("chat")
    chat: Mapping[str, Any] = raw_chat if isinstance(raw_chat, Mapping) else {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return NormalizedResourceWrite(
        resource_key="communication-message",
        external_id=f"telegram-message:{profile.key}:{chat_id}:{message_id}",
        title=parsed["message_title"],
        data_json={
            "provider_key": "telegram-bot",
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "direction": "inbound",
            "channel_ref": f"telegram-chat:{chat_id}",
            "thread_ref": _thread_ref(message),
            "message_ref": parsed["message_ref"],
            "provider_message_id": str(message_id),
            "content_type": parsed["content_type"],
            "text_preview": parsed["body_preview"],
            "transport_status": "received",
            "attention_status": "unread",
            "attachments": _message_attachments(message),
            "from_ref": _user_ref(message.get("from")),
            "from_username": _username(message.get("from")),
            "date": message.get("date"),
        },
        provenance_json={"source": "telegram-ingress"},
        preserve_existing_on_dedupe=True,
    )


def _telegram_interaction_write(
    profile: TelegramProfile,
    parsed: dict[str, Any],
) -> NormalizedResourceWrite | None:
    callback = parsed.get("callback_query")
    if not isinstance(callback, dict):
        return None
    callback_id = callback.get("id")
    if not callback_id:
        return None
    return NormalizedResourceWrite(
        resource_key="communication-interaction",
        external_id=f"telegram-callback:{profile.key}:{callback_id}",
        title=f"Telegram callback {callback_id}",
        data_json={
            "provider_key": "telegram-bot",
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "interaction_ref": parsed["interaction_ref"],
            "interaction_type": "inline_callback",
            "callback_query_id": str(callback_id),
            "callback_data": _safe_text(callback.get("data")),
            "button_key": _safe_text(callback.get("data")),
            "message_ref": parsed.get("message_ref"),
            "from_ref": _user_ref(callback.get("from")),
            "from_username": _username(callback.get("from")),
        },
        provenance_json={"source": "telegram-ingress"},
        preserve_existing_on_dedupe=True,
    )


def _telegram_click_patch(
    profile: TelegramProfile,
    parsed: Mapping[str, Any],
) -> NormalizedResourcePatch:
    callback = parsed.get("callback_query")
    callback_data = callback.get("data") if isinstance(callback, Mapping) else ""
    message_ref = str(parsed.get("message_ref") or "")
    return NormalizedResourcePatch(
        resource_key="communication-interaction",
        external_id=_callback_button_external_id(profile.key, message_ref, str(callback_data)),
        data_json={
            "status": "clicked",
            "last_callback_query_id": callback.get("id") if isinstance(callback, Mapping) else None,
            "last_clicked_by_ref": parsed.get("user_ref"),
            "last_clicked_message_ref": parsed.get("message_ref"),
        },
    )


def _telegram_surface_write(
    profile: TelegramProfile,
    parsed: Mapping[str, Any],
) -> NormalizedResourceWrite | None:
    message = parsed.get("message")
    if not isinstance(message, Mapping):
        return None
    raw_chat = message.get("chat")
    chat: Mapping[str, Any] = raw_chat if isinstance(raw_chat, Mapping) else {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    chat_type = str(chat.get("type") or "unknown") if isinstance(chat, Mapping) else "unknown"
    return NormalizedResourceWrite(
        resource_key="communication-channel",
        external_id=f"telegram-chat:{profile.key}:{chat_id}",
        title=_chat_title(chat),
        data_json={
            "provider_key": "telegram-bot",
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "surface_ref": f"telegram-chat:{chat_id}",
            "channel_ref": f"telegram-chat:{chat_id}",
            "provider_chat_id": str(chat_id),
            "kind": f"telegram-{chat_type}",
            "channel_type": chat_type,
            "display_name": _chat_title(chat),
            "title": _chat_title(chat),
            "safe_external_ref": f"telegram-chat:{chat_id}",
            "send_enabled": True,
            "ingest_enabled": True,
            "capabilities": {"can_read": True, "can_write": True},
        },
        provenance_json={"source": "telegram-ingress"},
    )


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
