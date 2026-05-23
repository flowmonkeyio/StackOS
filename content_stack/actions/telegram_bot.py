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

import mimetypes
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.provider_utils import (
    credential_config,
    credential_value,
    issue,
    resolve_ref,
    result,
    send_json,
    unknown_operation,
)
from content_stack.artifacts import redact_secret_text
from content_stack.config import Settings
from content_stack.repositories.base import ValidationError

_BASE_URL = "https://api.telegram.org"
_MAX_MESSAGE_TEXT = 4096
_MAX_CAPTION_TEXT = 1024
_MAX_CALLBACK_TEXT = 200
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_INLINE_ROWS = 20
_MAX_INLINE_BUTTONS_PER_ROW = 8
_ALLOWED_PARSE_MODES = {"Markdown", "MarkdownV2", "HTML"}
_ALLOWED_UPDATES = {
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "message_reaction",
    "message_reaction_count",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "purchased_paid_media",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "chat_boost",
    "removed_chat_boost",
}
_SECRETISH_CALLBACK_RE = re.compile(
    r"(?i)(bearer\s+|sk-[a-z0-9]|api[_-]?key|client[_-]?secret|"
    r"refresh[_-]?token|access[_-]?token|password|secret)"
)


class TelegramBotActionConnector:
    """Decision-free adapter for explicit Telegram Bot API calls."""

    key = "telegram-bot"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "identity.get":
                return []
            case "message.send":
                _required_text(payload, "chat_ref", issues)
                _required_text(payload, "text", issues, max_chars=_MAX_MESSAGE_TEXT)
                _optional_parse_mode(payload, issues)
                _optional_bool(payload, "disable_notification", issues)
                _optional_text(payload, "reply_to_message_ref", issues)
                _optional_text(payload, "thread_ref", issues)
                _optional_text(payload, "direct_messages_topic_ref", issues)
                _reply_markup(payload.get("reply_markup"), issues, "$.reply_markup")
            case "photo.send":
                _required_text(payload, "chat_ref", issues)
                _photo_source(payload.get("photo"), issues)
                _optional_text(payload, "caption", issues, max_chars=_MAX_CAPTION_TEXT)
                _optional_parse_mode(payload, issues)
                _optional_bool(payload, "disable_notification", issues)
                _optional_text(payload, "reply_to_message_ref", issues)
                _optional_text(payload, "thread_ref", issues)
                _optional_text(payload, "direct_messages_topic_ref", issues)
                _reply_markup(payload.get("reply_markup"), issues, "$.reply_markup")
            case "callback.answer":
                _required_text(payload, "callback_query_id", issues)
                _optional_text(payload, "text", issues, max_chars=_MAX_CALLBACK_TEXT)
                _optional_bool(payload, "show_alert", issues)
                _optional_text(payload, "url", issues)
                _optional_int(payload, "cache_time", issues, minimum=0, maximum=3600)
            case "updates.poll":
                _optional_text(payload, "cursor_ref", issues)
                _optional_int(payload, "offset", issues, minimum=0, maximum=2_147_483_647)
                _optional_int(payload, "limit", issues, minimum=1, maximum=100)
                _optional_int(payload, "timeout_s", issues, minimum=0, maximum=60)
                _allowed_updates(payload.get("allowed_updates"), issues)
                _optional_bool(payload, "allow_webhook_polling", issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

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
                chat_id = _chat_id(request)
                _enforce_allowed_ref(
                    request,
                    "allowed_chat_refs",
                    str(payload["chat_ref"]),
                    chat_id,
                )
                body_json = _message_payload(request, chat_id)
                # Telegram sendMessage: https://core.telegram.org/bots/api#sendmessage
                status, body, headers = await send_json(
                    method="POST",
                    url=_method_url(request, "sendMessage"),
                    json_body=body_json,
                )
                return result(
                    provider="telegram-bot",
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=headers,
                    metadata={"telegram_method": "sendMessage"},
                )
            case "photo.send":
                chat_id = _chat_id(request)
                _enforce_allowed_ref(
                    request,
                    "allowed_chat_refs",
                    str(payload["chat_ref"]),
                    chat_id,
                )
                return await _send_photo(request, chat_id)
            case "callback.answer":
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
                _enforce_polling_mode(request)
                _enforce_allowed_updates(request)
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
            case _:
                raise ValidationError(f"unsupported Telegram operation {request.operation!r}")


def _method_url(request: ActionConnectorRequest, method: str) -> str:
    config = credential_config(request)
    base = str(config.get("api_base_url") or _BASE_URL).rstrip("/")
    token = credential_value(request, "bot_token", "token")
    return f"{base}/bot{quote(token, safe=':-_')}/{method}"


def _message_payload(request: ActionConnectorRequest, chat_id: Any) -> dict[str, Any]:
    payload = request.input_json
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": payload["text"],
    }
    _copy_common_message_fields(request, body)
    return body


def _callback_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"callback_query_id": payload["callback_query_id"]}
    for key in ("text", "show_alert", "url", "cache_time"):
        if key in payload:
            body[key] = payload[key]
    return body


def _updates_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "limit": payload.get("limit", 100),
        "timeout": payload.get("timeout_s", 0),
        "allowed_updates": payload.get("allowed_updates", []),
    }
    if "offset" in payload:
        body["offset"] = payload["offset"]
    return body


def _copy_common_message_fields(request: ActionConnectorRequest, body: dict[str, Any]) -> None:
    payload = request.input_json
    parse_mode = payload.get("parse_mode") or credential_config(request).get("default_parse_mode")
    if isinstance(parse_mode, str) and parse_mode != "plain":
        body["parse_mode"] = parse_mode
    if "disable_notification" in payload:
        body["disable_notification"] = payload["disable_notification"]
    _copy_resolved(payload, body, request, "reply_to_message_ref", "reply_to_message_id")
    _copy_resolved(payload, body, request, "thread_ref", "message_thread_id")
    _copy_resolved(
        payload,
        body,
        request,
        "direct_messages_topic_ref",
        "direct_messages_topic_id",
    )
    if "reply_markup" in payload:
        body["reply_markup"] = payload["reply_markup"]


def _copy_resolved(
    payload: Mapping[str, Any],
    body: dict[str, Any],
    request: ActionConnectorRequest,
    input_key: str,
    output_key: str,
) -> None:
    value = payload.get(input_key)
    if value is not None:
        body[output_key] = resolve_ref(request, value, input_key, f"{input_key}s")


def _chat_id(request: ActionConnectorRequest) -> Any:
    return resolve_ref(request, request.input_json["chat_ref"], "chats", "chat_refs")


async def _send_photo(request: ActionConnectorRequest, chat_id: Any) -> ActionConnectorResult:
    payload = request.input_json
    photo = payload["photo"]
    assert isinstance(photo, dict)
    base_body: dict[str, Any] = {"chat_id": chat_id}
    if "caption" in payload:
        base_body["caption"] = payload["caption"]
    _copy_common_message_fields(request, base_body)
    url = _method_url(request, "sendPhoto")
    if "artifact_ref" not in photo:
        body_json = dict(base_body)
        body_json["photo"] = photo.get("file_id") or photo.get("url")
        # Telegram sendPhoto: https://core.telegram.org/bots/api#sendphoto
        status, body, headers = await send_json(
            method="POST",
            url=url,
            json_body=body_json,
            timeout_s=60.0,
        )
        return result(
            provider="telegram-bot",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=headers,
            metadata={"telegram_method": "sendPhoto", "upload_mode": "remote"},
        )

    path = _artifact_path(request, str(photo["artifact_ref"]))
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.stat().st_size > _MAX_PHOTO_BYTES:
        raise ValidationError("Telegram photo artifact must be at most 10 MB")
    # Telegram multipart upload for sendPhoto:
    # https://core.telegram.org/bots/api#sending-files
    async with httpx.AsyncClient(timeout=60.0) as http:
        with path.open("rb") as file_obj:
            response = await http.post(
                url,
                data={key: _form_value(value) for key, value in base_body.items()},
                files={"photo": (path.name, file_obj, mime_type)},
            )
    if response.status_code >= 400:
        raise ValidationError(
            redact_secret_text(
                f"provider action returned status {response.status_code}: "
                f"{response.text[:500]}"
            )
        )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return result(
        provider="telegram-bot",
        operation=request.operation,
        status_code=response.status_code,
        body=body,
        headers=response.headers,
        metadata={"telegram_method": "sendPhoto", "upload_mode": "multipart"},
    )


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        import json

        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _artifact_path(request: ActionConnectorRequest, artifact_ref: str) -> Path:
    if artifact_ref.startswith("/generated-assets/"):
        relative = artifact_ref.removeprefix("/generated-assets/")
    elif artifact_ref.startswith("generated-assets/"):
        relative = artifact_ref.removeprefix("generated-assets/")
    else:
        raise ValidationError(
            "photo.artifact_ref must be a generated asset URI such as "
            "/generated-assets/openai-images/image.webp"
        )
    root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ValidationError("photo.artifact_ref must stay inside generated assets")
    if not path.is_file():
        raise ValidationError("photo.artifact_ref does not point to an existing file")
    return path


def _enforce_allowed_ref(
    request: ActionConnectorRequest,
    config_key: str,
    raw_ref: str,
    resolved_ref: Any,
) -> None:
    allowed = _split_config_values(credential_config(request).get(config_key))
    if not allowed:
        return
    if raw_ref not in allowed and str(resolved_ref) not in allowed:
        raise ValidationError(f"Telegram {config_key} does not allow {raw_ref!r}")


def _enforce_polling_mode(request: ActionConnectorRequest) -> None:
    payload = request.input_json
    config = credential_config(request)
    if (
        config.get("ingestion_mode") == "webhook"
        and payload.get("allow_webhook_polling") is not True
    ):
        raise ValidationError(
            "Telegram credential is configured for webhook ingestion, not polling"
        )


def _enforce_allowed_updates(request: ActionConnectorRequest) -> None:
    configured = _split_config_values(credential_config(request).get("allowed_updates"))
    if not configured:
        return
    requested = request.input_json.get("allowed_updates")
    if not isinstance(requested, list):
        return
    extra = sorted({str(item) for item in requested} - set(configured))
    if extra:
        raise ValidationError(
            "Telegram requested updates are outside the credential allowlist",
            data={"updates": extra},
        )


def _split_config_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _required_text(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    max_chars: int | None = None,
) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if max_chars is not None and len(value) > max_chars:
        issues.append(issue(f"$.{key}", f"{key} must be at most {max_chars} chars", "length"))


def _optional_text(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    max_chars: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, str):
        issues.append(issue(f"$.{key}", f"{key} must be a string", "type_error"))
        return
    if max_chars is not None and len(value) > max_chars:
        issues.append(issue(f"$.{key}", f"{key} must be at most {max_chars} chars", "length"))


def _optional_bool(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        issues.append(issue(f"$.{key}", f"{key} must be a boolean", "type_error"))


def _optional_int(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: int,
    maximum: int,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        issues.append(
            issue(f"$.{key}", f"{key} must be an integer between {minimum} and {maximum}", "range")
        )


def _optional_parse_mode(payload: Mapping[str, Any], issues: list[ActionValidationIssue]) -> None:
    value = payload.get("parse_mode")
    if value is None:
        return
    if value not in _ALLOWED_PARSE_MODES:
        issues.append(
            issue(
                "$.parse_mode",
                "parse_mode must be Markdown, MarkdownV2, or HTML",
                "enum_mismatch",
            )
        )


def _photo_source(value: Any, issues: list[ActionValidationIssue]) -> None:
    if not isinstance(value, dict):
        issues.append(issue("$.photo", "photo is required", "required"))
        return
    keys = [key for key in ("file_id", "url", "artifact_ref") if value.get(key)]
    if len(keys) != 1:
        issues.append(
            issue(
                "$.photo",
                "photo must include exactly one of file_id, url, artifact_ref",
                "one_of",
            )
        )
        return
    for key in keys:
        if not isinstance(value[key], str) or not value[key].strip():
            issues.append(issue(f"$.photo.{key}", f"{key} must be a string", "type_error"))
    if "url" in keys and not str(value["url"]).startswith("https://"):
        issues.append(issue("$.photo.url", "photo.url must be a public HTTPS URL", "format"))


def _reply_markup(
    value: Any,
    issues: list[ActionValidationIssue],
    path: str,
) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(issue(path, "reply_markup must be an object", "type_error"))
        return
    unexpected = set(value) - {"inline_keyboard"}
    for key in sorted(unexpected):
        issues.append(
            issue(
                f"{path}.{key}",
                "reply_markup property is not supported",
                "additional_property",
            )
        )
    inline_keyboard = value.get("inline_keyboard")
    if inline_keyboard is None:
        return
    if not isinstance(inline_keyboard, list):
        issues.append(
            issue(f"{path}.inline_keyboard", "inline_keyboard must be an array", "type_error")
        )
        return
    if len(inline_keyboard) > _MAX_INLINE_ROWS:
        issues.append(
            issue(f"{path}.inline_keyboard", "inline_keyboard has too many rows", "length")
        )
    for row_index, row in enumerate(inline_keyboard):
        row_path = f"{path}.inline_keyboard[{row_index}]"
        if not isinstance(row, list):
            issues.append(issue(row_path, "inline_keyboard row must be an array", "type_error"))
            continue
        if len(row) > _MAX_INLINE_BUTTONS_PER_ROW:
            issues.append(issue(row_path, "inline_keyboard row has too many buttons", "length"))
        for button_index, button in enumerate(row):
            _inline_button(button, issues, f"{row_path}[{button_index}]")


def _inline_button(value: Any, issues: list[ActionValidationIssue], path: str) -> None:
    if not isinstance(value, dict):
        issues.append(issue(path, "inline keyboard button must be an object", "type_error"))
        return
    unexpected = set(value) - {"text", "url", "callback_data"}
    for key in sorted(unexpected):
        issues.append(
            issue(f"{path}.{key}", "button property is not supported", "additional_property")
        )
    _required_text(value, "text", issues)
    has_url = isinstance(value.get("url"), str) and bool(str(value.get("url")).strip())
    has_callback = isinstance(value.get("callback_data"), str) and bool(
        str(value.get("callback_data")).strip()
    )
    if has_url == has_callback:
        issues.append(
            issue(path, "button must include exactly one of url or callback_data", "one_of")
        )
    if "url" in value and not isinstance(value.get("url"), str):
        issues.append(issue(f"{path}.url", "url must be a string", "type_error"))
    callback_data = value.get("callback_data")
    if callback_data is not None:
        if not isinstance(callback_data, str):
            issues.append(
                issue(f"{path}.callback_data", "callback_data must be a string", "type_error")
            )
            return
        encoded_len = len(callback_data.encode("utf-8"))
        if encoded_len < 1 or encoded_len > 64:
            issues.append(
                issue(
                    f"{path}.callback_data",
                    "callback_data must be 1-64 bytes",
                    "length",
                )
            )
        if _SECRETISH_CALLBACK_RE.search(callback_data):
            issues.append(
                issue(
                    f"{path}.callback_data",
                    "callback_data must not contain secrets or credential-like text",
                    "secret_like",
                )
            )


def _allowed_updates(value: Any, issues: list[ActionValidationIssue]) -> None:
    if not isinstance(value, list) or not value:
        issues.append(
            issue("$.allowed_updates", "allowed_updates must be a non-empty array", "required")
        )
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in _ALLOWED_UPDATES:
            issues.append(
                issue(
                    f"$.allowed_updates[{index}]",
                    "allowed_updates contains an unsupported Telegram update type",
                    "enum_mismatch",
                )
            )


__all__ = ["TelegramBotActionConnector"]
