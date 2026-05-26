"""Telegram outbound resource and interaction storage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest
from stackos.communications import telegram_callback_button_external_id
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository

from .policy import _request_profile_key, _split_config_values


def _store_outbound_message(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any],
    provider_body: Any,
    *,
    content_type: str,
) -> None:
    if request.session is None:
        return
    result_body = provider_body.get("result") if isinstance(provider_body, Mapping) else None
    if not isinstance(result_body, Mapping):
        return
    message_ref = _provider_message_ref(result_body)
    if message_ref is None:
        return
    chat = result_body.get("chat")
    chat_id = chat.get("id") if isinstance(chat, Mapping) else None
    message_id = result_body.get("message_id")
    profile_key = _request_profile_key(request)
    profile_ref = str(profile.get("profile_ref") or f"communication-profile:{profile_key}")
    resources = ResourceRepository(request.session)
    if chat_id is not None:
        resources.upsert_record(
            project_id=request.project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id=f"telegram-chat:{profile_key}:{chat_id}",
            title=str(chat.get("title") or chat.get("username") or chat_id)
            if isinstance(chat, Mapping)
            else str(chat_id),
            data_json={
                "provider_key": "telegram-bot",
                "profile_key": profile_key,
                "profile_ref": profile_ref,
                "provider_chat_id": str(chat_id),
                "channel_type": chat.get("type") if isinstance(chat, Mapping) else None,
            },
            provenance_json={"source": "telegram-bot-action"},
        )
    resources.upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=f"telegram-message:{profile_key}:{chat_id}:{message_id}",
        title="Telegram outbound message",
        data_json={
            "provider_key": "telegram-bot",
            "profile_key": profile_key,
            "profile_ref": profile_ref,
            "direction": "outbound",
            "channel_ref": f"telegram-chat:{chat_id}" if chat_id is not None else None,
            "message_ref": message_ref,
            "provider_message_id": str(message_id),
            "content_type": content_type,
            "text_preview": (
                request.input_json.get("text") or request.input_json.get("caption") or ""
            ),
            "attention_status": "sent",
            "source_agent_request_id": request.input_json.get("source_agent_request_id"),
            "action_ref": request.action_ref,
        },
        provenance_json={"source": "telegram-bot-action"},
    )


def _store_callback_buttons(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any],
    provider_body: Any,
) -> None:
    if request.session is None:
        return
    reply_markup = request.input_json.get("reply_markup")
    if not isinstance(reply_markup, Mapping):
        return
    inline_keyboard = reply_markup.get("inline_keyboard")
    if not isinstance(inline_keyboard, list):
        return
    control_metadata = (
        request.input_json.get("control_metadata")
        if isinstance(request.input_json.get("control_metadata"), Mapping)
        else {}
    )
    result_body = provider_body.get("result") if isinstance(provider_body, Mapping) else None
    message_ref = _provider_message_ref(result_body)
    if message_ref is None:
        return
    source_scope = _source_button_scope(request)
    resources = ResourceRepository(request.session)
    for row in inline_keyboard:
        if not isinstance(row, list):
            continue
        for button in row:
            if not isinstance(button, Mapping):
                continue
            callback_data = button.get("callback_data")
            if not isinstance(callback_data, str) or not callback_data:
                continue
            metadata = _button_metadata(control_metadata, callback_data=callback_data)
            allowed_user_refs = _split_config_values(button.get("allowed_user_refs"))
            if not allowed_user_refs:
                allowed_user_refs = source_scope.get("allowed_user_refs", [])
            allowed_chat_refs = _split_config_values(button.get("allowed_chat_refs"))
            if not allowed_chat_refs:
                allowed_chat_refs = source_scope.get("allowed_chat_refs", [])
            profile_key = _request_profile_key(request)
            profile_ref = str(profile.get("profile_ref") or f"communication-profile:{profile_key}")
            resources.upsert_record(
                project_id=request.project_id,
                plugin_slug="communications",
                resource_key="communication-interaction",
                external_id=telegram_callback_button_external_id(
                    profile_key=profile_key,
                    message_ref=message_ref,
                    callback_data=callback_data,
                ),
                title=str(button.get("text") or callback_data),
                data_json={
                    "provider_key": "telegram-bot",
                    "profile_key": profile_key,
                    "profile_ref": profile_ref,
                    "interaction_type": "outbound_inline_button",
                    "callback_data": callback_data,
                    "control_action": metadata.get("action"),
                    "control_payload": metadata.get("payload"),
                    "control_metadata": metadata or None,
                    "message_ref": message_ref,
                    "chat_ref": request.input_json.get("chat_ref"),
                    "allowed_user_refs": allowed_user_refs,
                    "allowed_chat_refs": allowed_chat_refs,
                    "source_agent_request_id": request.input_json.get("source_agent_request_id"),
                    "status": "active",
                },
                provenance_json={"source": "telegram-bot-action"},
            )


def _button_metadata(control_metadata: Any, *, callback_data: str) -> dict[str, Any]:
    if not isinstance(control_metadata, Mapping):
        return {}
    item = control_metadata.get(callback_data)
    return dict(item) if isinstance(item, Mapping) else {}


def _source_button_scope(request: ActionConnectorRequest) -> dict[str, list[str]]:
    if request.session is None:
        return {}
    request_id = request.input_json.get("source_agent_request_id")
    if not isinstance(request_id, int) or isinstance(request_id, bool):
        return {}
    source = AgentRequestRepository(request.session).get(
        project_id=request.project_id,
        request_id=request_id,
    )
    metadata = source.metadata_json or {}
    scope: dict[str, list[str]] = {}
    invoker_ref = metadata.get("invoker_ref")
    if isinstance(invoker_ref, str) and invoker_ref:
        scope["allowed_user_refs"] = [invoker_ref]
    chat_ref = metadata.get("chat_ref")
    if isinstance(chat_ref, str) and chat_ref:
        scope["allowed_chat_refs"] = [chat_ref]
    return scope


def _provider_message_ref(result_body: Any) -> str | None:
    if not isinstance(result_body, Mapping):
        return None
    chat = result_body.get("chat")
    chat_id = chat.get("id") if isinstance(chat, Mapping) else None
    message_id = result_body.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return f"telegram-message:{chat_id}:{message_id}"
