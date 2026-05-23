"""Telegram webhook/relay ingress for communication-triggered agent requests."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from content_stack.api.deps import get_session
from content_stack.artifacts import redact_secret_text
from content_stack.db.models import IntegrationCredential
from content_stack.repositories.agent_requests import AgentRequestRepository
from content_stack.repositories.base import ValidationError
from content_stack.repositories.projects import IntegrationCredentialRepository
from content_stack.repositories.resources import ResourceRepository

router = APIRouter(prefix="/api/v1/ingress/telegram", tags=["telegram-ingress"])


class TelegramIngressOut(BaseModel):
    """Result of storing one Telegram update."""

    ok: bool
    update_id: int
    event_record_id: int
    message_record_id: int | None = None
    interaction_record_id: int | None = None
    agent_request_id: int


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
    """Store a Telegram update and create one generic claimable agent request.

    This endpoint is intentionally static plumbing: it validates Telegram's
    secret-token header, normalizes the event into Communications resources,
    creates an `agent_requests` row, and stops. It does not call a model, infer
    intent, approve work, or choose follow-up tools.
    """

    _verify_secret(session, project_id=project_id, profile_key=profile_key, header=secret_token)
    stored = _store_update(
        session,
        project_id=project_id,
        profile_key=profile_key,
        update=update,
    )
    return TelegramIngressOut(**stored)


def _verify_secret(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
    header: str | None,
) -> None:
    if not header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid Telegram secret")
    credential = _integration_credential(session, project_id=project_id, profile_key=profile_key)
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


def _profile_config(
    session: Session,
    *,
    project_id: int,
    profile_key: str,
) -> dict[str, Any]:
    credential = _integration_credential(session, project_id=project_id, profile_key=profile_key)
    if credential is None:
        return {}
    return dict(credential.config_json or {})


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
    profile_key: str,
    update: dict[str, Any],
) -> dict[str, Any]:
    update_id = update.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        raise ValidationError("Telegram update_id is required")
    parsed = _parse_update(update)
    config = _profile_config(session, project_id=project_id, profile_key=profile_key)
    _enforce_ingress_allowlist(config, parsed)
    resources = ResourceRepository(session)
    event = resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-event",
        external_id=f"telegram-update:{update_id}",
        title=f"Telegram update {update_id}",
        data_json={
            "provider_key": "telegram-bot",
            "profile_key": profile_key,
            "update_id": update_id,
            "update_type": parsed["update_type"],
            "message_ref": parsed.get("message_ref"),
            "interaction_ref": parsed.get("interaction_ref"),
        },
        provenance_json={"source": "telegram-ingress"},
    ).data
    message_record_id = _store_message(
        resources,
        project_id=project_id,
        parsed=parsed,
    )
    interaction_record_id = _store_interaction(
        resources,
        project_id=project_id,
        parsed=parsed,
    )
    source_record_id = interaction_record_id or message_record_id or event.id
    source_resource_key = (
        "communication-interaction"
        if interaction_record_id is not None
        else "communication-message"
        if message_record_id is not None
        else "communication-event"
    )
    request = AgentRequestRepository(session).create(
        project_id=project_id,
        request_key=f"telegram-update:{update_id}",
        title=parsed["request_title"],
        body_preview=parsed["body_preview"],
        source_provider="telegram-bot",
        source_kind=parsed["source_kind"],
        source_resource_key=source_resource_key,
        source_resource_record_id=source_record_id,
        source_message_ref=parsed.get("message_ref"),
        metadata_json={
            "profile_key": profile_key,
            "update_id": update_id,
            "event_record_id": event.id,
            "interaction_ref": parsed.get("interaction_ref"),
        },
    ).data
    return {
        "ok": True,
        "update_id": update_id,
        "event_record_id": event.id,
        "message_record_id": message_record_id,
        "interaction_record_id": interaction_record_id,
        "agent_request_id": request.id,
    }


def _enforce_ingress_allowlist(config: Mapping[str, Any], parsed: dict[str, Any]) -> None:
    if config.get("ingestion_mode") != "webhook":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram ingress disabled",
        )
    allowed_updates = _split_config_values(config.get("allowed_updates"))
    if allowed_updates and parsed["update_type"] not in allowed_updates:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Telegram update blocked")
    allowed_chat_refs = _split_config_values(config.get("allowed_chat_refs"))
    chat_ref = parsed.get("chat_ref")
    if allowed_chat_refs and (not chat_ref or chat_ref not in allowed_chat_refs):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Telegram chat blocked")
    allowed_user_refs = _split_config_values(config.get("allowed_user_refs"))
    user_ref = parsed.get("user_ref")
    if allowed_user_refs and (not user_ref or user_ref not in allowed_user_refs):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Telegram user blocked")


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
    parsed: dict[str, Any],
) -> int | None:
    message = parsed.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if chat_id is not None:
        resources.upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id=f"telegram-chat:{chat_id}",
            title=_chat_title(chat),
            data_json={
                "provider_key": "telegram-bot",
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
        external_id=f"telegram-message:{chat_id}:{message_id}",
        title=parsed["message_title"],
        data_json={
            "provider_key": "telegram-bot",
            "direction": "inbound",
            "channel_ref": f"telegram-chat:{chat_id}",
            "thread_ref": _thread_ref(message),
            "message_ref": parsed["message_ref"],
            "provider_message_id": str(message_id),
            "content_type": parsed["content_type"],
            "text_preview": parsed["body_preview"],
            "attention_status": "unread",
            "attachments": _message_attachments(message),
            "from_ref": _user_ref(message.get("from")),
            "date": message.get("date"),
        },
        provenance_json={"source": "telegram-ingress"},
    ).data
    return record.id


def _store_interaction(
    resources: ResourceRepository,
    *,
    project_id: int,
    parsed: dict[str, Any],
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
        external_id=f"telegram-callback:{callback_id}",
        title=f"Telegram callback {callback_id}",
        data_json={
            "provider_key": "telegram-bot",
            "interaction_ref": parsed["interaction_ref"],
            "interaction_type": "inline_callback",
            "callback_query_id": str(callback_id),
            "callback_data": _safe_text(callback.get("data")),
            "button_key": _safe_text(callback.get("data")),
            "message_ref": parsed.get("message_ref"),
            "status": "new",
            "from_ref": _user_ref(callback.get("from")),
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
            "user_ref": _user_ref(callback.get("from")),
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
            "user_ref": _user_ref(message.get("from")),
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


__all__ = ["router"]
