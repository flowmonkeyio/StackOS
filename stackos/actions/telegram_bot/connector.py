"""Telegram Bot API action connector.

Official docs verified:
- Bot API overview: https://core.telegram.org/bots/api
- getMe: https://core.telegram.org/bots/api#getme
- sendMessage: https://core.telegram.org/bots/api#sendmessage
- sendPhoto: https://core.telegram.org/bots/api#sendphoto
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

from .media import _send_photo
from .payloads import (
    _callback_payload,
    _chat_id,
    _message_payload,
    _method_url,
    _updates_payload,
    _webhook_delete_payload,
    _webhook_set_payload,
)
from .policy import _enforce_allowed_updates, _enforce_profile_chat, _enforce_telegram_profile
from .storage import _store_callback_buttons, _store_outbound_message
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
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "sendMessage"},
                )
            case "photo.send":
                profile = _enforce_profile_chat(
                    request,
                    str(payload["chat_ref"]),
                )
                chat_id = _chat_id(request, profile)
                return await _send_photo(request, chat_id, profile)
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
