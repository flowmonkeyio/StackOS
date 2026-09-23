"""Publish final TDLib receipts to the canonical communication resources."""

from typing import Any

from stackos.actions.connectors import ActionConnectorRequest
from stackos.artifacts import redact_secrets
from stackos.communications import telegram_callback_button_external_id
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository


def store_sent_messages(request: ActionConnectorRequest, messages: list[dict[str, Any]]) -> None:
    assert request.session is not None
    profile_ref = "communication-profile:" + request.input_json["profile_ref"].removeprefix(
        "communication-profile:"
    )
    key = profile_ref.removeprefix("communication-profile:")
    source_id = request.input_json.get("source_agent_request_id")
    source = (
        AgentRequestRepository(request.session).get(
            project_id=request.project_id, request_id=source_id
        )
        if source_id is not None
        else None
    )
    source_metadata = dict(source.metadata_json or {}) if source else {}
    invoker = source_metadata.get("invoker_ref")
    resources = ResourceRepository(request.session)
    for message in messages:
        chat_id, message_id = message["chat_id"], message["id"]
        message_ref = f"telegram-message:{chat_id}:{message_id}"
        surface_ref = f"telegram-chat:{chat_id}"
        content = message.get("content") or {}
        text = (content.get("text") or content.get("caption") or {}).get("text", "")
        resources.upsert_record(
            project_id=request.project_id,
            plugin_slug="communications",
            resource_key="communication-message",
            external_id=f"telegram-message:{key}:{chat_id}:{message_id}",
            title=str(text)[:120] or message_ref,
            data_json={
                "provider_key": "telegram",
                "profile_key": key,
                "profile_ref": profile_ref,
                "credential_ref": request.credential.credential_ref if request.credential else None,
                "surface_ref": surface_ref,
                "channel_ref": surface_ref,
                "message_ref": message_ref,
                "provider_message_id": message_id,
                "direction": "outbound",
                "transport_status": "sent",
                "text_preview": str(text)[:4000],
                "source_agent_request_id": source_id,
                "action_ref": request.action_ref,
                "action_call_id": request.action_call_id,
            },
            provenance_json={"source": "telegram-tdlib-action"},
        )
        metadata = request.input_json.get("control_metadata") or {}
        for row in request.input_json.get("buttons") or []:
            for button in row:
                token = button.get("callback_data")
                if not token:
                    continue
                control = redact_secrets(metadata.get(token) or {})
                resources.upsert_record(
                    project_id=request.project_id,
                    plugin_slug="communications",
                    resource_key="communication-interaction",
                    external_id=telegram_callback_button_external_id(
                        profile_key=key,
                        message_ref=message_ref,
                        callback_data=token,
                    ),
                    title=button["text"],
                    data_json={
                        "provider_key": "telegram",
                        "profile_ref": profile_ref,
                        "profile_key": key,
                        "interaction_type": "outbound_inline_button",
                        "message_ref": message_ref,
                        "surface_ref": surface_ref,
                        "chat_ref": surface_ref,
                        "callback_data": token,
                        "control_action": control.get("action"),
                        "control_payload": control.get("payload"),
                        "control_metadata": control,
                        "allowed_user_refs": [invoker] if invoker else [],
                        "allowed_chat_refs": [surface_ref],
                        "source_agent_request_id": source_id,
                        "status": "active",
                    },
                    provenance_json={"source": "telegram-tdlib-action"},
                )
