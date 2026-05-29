"""Telegram Bot API action connector.

Official docs verified:
- Bot API overview: https://core.telegram.org/bots/api
- getMe: https://core.telegram.org/bots/api#getme
- getFile: https://core.telegram.org/bots/api#getfile
- sendMessage: https://core.telegram.org/bots/api#sendmessage
- sendPhoto: https://core.telegram.org/bots/api#sendphoto
- sendDocument: https://core.telegram.org/bots/api#senddocument
- sendMediaGroup: https://core.telegram.org/bots/api#sendmediagroup
- answerCallbackQuery: https://core.telegram.org/bots/api#answercallbackquery
- getUpdates: https://core.telegram.org/bots/api#getupdates
- Inline keyboards: https://core.telegram.org/bots/api#inlinekeyboardmarkup
"""

from __future__ import annotations

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import result, send_json
from stackos.repositories.base import ValidationError

from .media import _download_file, _send_file, _send_photo
from .payloads import (
    _callback_payload,
    _chat_id,
    _delete_payload,
    _message_payload,
    _method_url,
    _reaction_payload,
    _updates_payload,
    _webhook_delete_payload,
    _webhook_set_payload,
)
from .policy import (
    _enforce_allowed_updates,
    _enforce_profile_chat,
    _enforce_telegram_profile,
)
from .refs import _message_ref_parts, _resolve_message_ref
from .results import _telegram_delete_result, _telegram_reaction_result, _telegram_result
from .storage import (
    _mark_message_deleted,
    _store_callback_buttons,
    _store_outbound_message,
    _store_reaction,
)
from .validation import validate_telegram_request


class TelegramBotActionConnector:
    """Decision-free adapter for explicit Telegram Bot API calls."""

    key = "telegram-bot"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        return validate_telegram_request(request)

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        match request.operation:
            case "identity.get":
                # Telegram getMe: https://core.telegram.org/bots/api#getme
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "getMe"),
                )
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "getMe"},
                )
            case "message.send":
                profile = _enforce_profile_chat(
                    request,
                    str(payload["chat_ref"]),
                )
                chat_id = _chat_id(request, profile)
                body_json = _message_payload(request, chat_id, profile)
                # Telegram sendMessage: https://core.telegram.org/bots/api#sendmessage
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "sendMessage"),
                    json_body=body_json,
                )
                _store_outbound_message(request, profile, body, content_type="text")
                _store_callback_buttons(request, profile, body)
                return _telegram_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=headers,
                    telegram_method="sendMessage",
                )
            case "photo.send":
                profile = _enforce_profile_chat(
                    request,
                    str(payload["chat_ref"]),
                )
                chat_id = _chat_id(request, profile)
                return await _send_photo(request, chat_id, profile)
            case "file.download":
                _enforce_telegram_profile(request)
                # Telegram getFile and file download:
                # https://core.telegram.org/bots/api#getfile
                return await _download_file(request)
            case "file.upload":
                profile = _enforce_profile_chat(
                    request,
                    str(payload["chat_ref"]),
                )
                chat_id = _chat_id(request, profile)
                return await _send_file(request, chat_id, profile)
            case "callback.answer":
                _enforce_telegram_profile(request)
                # Telegram answerCallbackQuery:
                # https://core.telegram.org/bots/api#answercallbackquery
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "answerCallbackQuery"),
                    json_body=_callback_payload(payload),
                )
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "answerCallbackQuery"},
                )
            case "message.reaction.set":
                profile = _enforce_telegram_profile(request)
                raw_message_ref = _resolve_message_ref(
                    profile, request.input_json.get("message_ref")
                )
                chat_id, _message_id = _message_ref_parts(raw_message_ref)
                _enforce_profile_chat(request, f"telegram-chat:{chat_id}")
                # Telegram setMessageReaction:
                # https://core.telegram.org/bots/api#setmessagereaction
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "setMessageReaction"),
                    json_body=_reaction_payload(request, profile),
                )
                _store_reaction(request, profile)
                return _telegram_reaction_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=headers,
                )
            case "message.delete":
                profile = _enforce_telegram_profile(request)
                raw_message_ref = _resolve_message_ref(
                    profile, request.input_json.get("message_ref")
                )
                chat_id, _message_id = _message_ref_parts(raw_message_ref)
                _enforce_profile_chat(request, f"telegram-chat:{chat_id}")
                # Telegram deleteMessage:
                # https://core.telegram.org/bots/api#deletemessage
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "deleteMessage"),
                    json_body=_delete_payload(request, profile),
                )
                _mark_message_deleted(request, profile)
                return _telegram_delete_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=headers,
                )
            case "updates.poll":
                profile = _enforce_telegram_profile(request)
                _enforce_allowed_updates(request, profile)
                # Telegram getUpdates: https://core.telegram.org/bots/api#getupdates
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "getUpdates"),
                    json_body=_updates_payload(payload),
                    timeout_s=max(5.0, float(payload.get("timeout_s", 0)) + 5.0),
                )
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "getUpdates"},
                )
            case "webhook.set":
                profile = _enforce_telegram_profile(request)
                body_json = _webhook_set_payload(request, profile)
                # Telegram setWebhook:
                # https://core.telegram.org/bots/api#setwebhook
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "setWebhook"),
                    json_body=body_json,
                )
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "setWebhook"},
                )
            case "webhook.delete":
                _enforce_telegram_profile(request)
                # Telegram deleteWebhook:
                # https://core.telegram.org/bots/api#deletewebhook
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "deleteWebhook"),
                    json_body=_webhook_delete_payload(payload),
                )
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "deleteWebhook"},
                )
            case "webhook.info":
                _enforce_telegram_profile(request)
                # Telegram getWebhookInfo:
                # https://core.telegram.org/bots/api#getwebhookinfo
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "getWebhookInfo"),
                )
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "getWebhookInfo"},
                )
            case _:
                raise ValidationError(f"unsupported Telegram operation {request.operation!r}")
