"""Telegram Bot API action input validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest, ActionValidationIssue
from stackos.actions.provider_utils import issue, unknown_operation

from .constants import (
    _ALLOWED_PARSE_MODES,
    _ALLOWED_UPDATES,
    _MAX_CALLBACK_TEXT,
    _MAX_CAPTION_TEXT,
    _MAX_FILE_DOWNLOAD_BYTES,
    _MAX_INLINE_BUTTONS_PER_ROW,
    _MAX_INLINE_ROWS,
    _MAX_MEDIA_GROUP_ITEMS,
    _MAX_MESSAGE_TEXT,
    _SECRETISH_CALLBACK_RE,
)


def validate_telegram_request(request: ActionConnectorRequest) -> list[ActionValidationIssue]:
    payload = request.input_json
    issues: list[ActionValidationIssue] = []
    match request.operation:
        case "identity.get":
            return []
        case "message.send":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "chat_ref", issues)
            _required_text(payload, "text", issues, max_chars=_MAX_MESSAGE_TEXT)
            _optional_parse_mode(payload, issues)
            _optional_bool(payload, "disable_notification", issues)
            _optional_int(
                payload,
                "source_agent_request_id",
                issues,
                minimum=1,
                maximum=2_147_483_647,
            )
            _optional_text(payload, "reply_to_message_ref", issues)
            _optional_text(payload, "thread_ref", issues)
            _optional_text(payload, "direct_messages_topic_ref", issues)
            _reply_markup(payload.get("reply_markup"), issues, "$.reply_markup")
        case "photo.send":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "chat_ref", issues)
            _photo_source(payload.get("photo"), issues)
            _optional_text(payload, "caption", issues, max_chars=_MAX_CAPTION_TEXT)
            _optional_parse_mode(payload, issues)
            _optional_bool(payload, "disable_notification", issues)
            _optional_int(
                payload,
                "source_agent_request_id",
                issues,
                minimum=1,
                maximum=2_147_483_647,
            )
            _optional_text(payload, "reply_to_message_ref", issues)
            _optional_text(payload, "thread_ref", issues)
            _optional_text(payload, "direct_messages_topic_ref", issues)
            _reply_markup(payload.get("reply_markup"), issues, "$.reply_markup")
        case "file.download":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "file_id", issues)
            _optional_text(payload, "filename", issues)
            _optional_text(payload, "mime_type", issues)
            _optional_text(payload, "source_message_ref", issues)
            _optional_int(
                payload,
                "max_bytes",
                issues,
                minimum=1,
                maximum=_MAX_FILE_DOWNLOAD_BYTES,
            )
        case "file.upload":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "chat_ref", issues)
            _file_or_files(payload, issues)
            _optional_text(payload, "caption", issues, max_chars=_MAX_CAPTION_TEXT)
            _optional_parse_mode(payload, issues)
            _optional_bool(payload, "disable_notification", issues)
            _optional_bool(payload, "delete_after_upload", issues)
            _optional_int(
                payload,
                "source_agent_request_id",
                issues,
                minimum=1,
                maximum=2_147_483_647,
            )
            _optional_text(payload, "reply_to_message_ref", issues)
            _optional_text(payload, "thread_ref", issues)
            _optional_text(payload, "direct_messages_topic_ref", issues)
            _reply_markup(payload.get("reply_markup"), issues, "$.reply_markup")
            if isinstance(payload.get("files"), list) and payload.get("reply_markup") is not None:
                issues.append(
                    issue(
                        "$.reply_markup",
                        "reply_markup is not supported for Telegram media groups",
                        "unsupported",
                    )
                )
        case "callback.answer":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "callback_query_id", issues)
            _optional_text(payload, "text", issues, max_chars=_MAX_CALLBACK_TEXT)
            _optional_bool(payload, "show_alert", issues)
            _optional_text(payload, "url", issues)
            _optional_int(payload, "cache_time", issues, minimum=0, maximum=3600)
        case "message.reaction.set":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "message_ref", issues)
            _required_text(payload, "emoji", issues, max_chars=64)
            _optional_bool(payload, "is_big", issues)
        case "message.delete":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "message_ref", issues)
        case "updates.poll":
            _required_text(payload, "profile_key", issues)
            _optional_text(payload, "cursor_ref", issues)
            _optional_int(payload, "offset", issues, minimum=0, maximum=2_147_483_647)
            _optional_int(payload, "limit", issues, minimum=1, maximum=100)
            _optional_int(payload, "timeout_s", issues, minimum=0, maximum=60)
            _allowed_updates(payload.get("allowed_updates"), issues)
        case "webhook.set":
            _required_text(payload, "profile_key", issues)
            _required_text(payload, "webhook_url", issues, max_chars=2048)
            _allowed_updates(payload.get("allowed_updates"), issues, required=False)
            _optional_bool(payload, "drop_pending_updates", issues)
            _optional_int(payload, "max_connections", issues, minimum=1, maximum=100)
            _optional_text(payload, "ip_address", issues)
        case "webhook.delete":
            _required_text(payload, "profile_key", issues)
            _optional_bool(payload, "drop_pending_updates", issues)
        case "webhook.info":
            _required_text(payload, "profile_key", issues)
        case _:
            issues.extend(unknown_operation(request))
    return issues


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


def _file_or_files(payload: Mapping[str, Any], issues: list[ActionValidationIssue]) -> None:
    has_file = isinstance(payload.get("file"), Mapping)
    has_files = isinstance(payload.get("files"), list)
    if has_file == has_files:
        issues.append(issue("$", "exactly one of file or files is required", "one_of"))
        return
    if has_file:
        _upload_file(payload.get("file"), issues, "$.file")
    else:
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            issues.append(issue("$.files", "files must be a non-empty array", "required"))
            return
        if len(files) > _MAX_MEDIA_GROUP_ITEMS:
            issues.append(
                issue(
                    "$.files",
                    f"files must contain at most {_MAX_MEDIA_GROUP_ITEMS} items",
                    "length",
                )
            )
        for index, item in enumerate(files):
            _upload_file(item, issues, f"$.files[{index}]")


def _upload_file(value: Any, issues: list[ActionValidationIssue], path: str) -> None:
    if not isinstance(value, Mapping):
        issues.append(issue(path, "file must be an object", "type_error"))
        return
    keys = [key for key in ("file_id", "url", "artifact_ref") if value.get(key)]
    if len(keys) != 1:
        issues.append(
            issue(
                path,
                "file must include exactly one of file_id, url, artifact_ref",
                "one_of",
            )
        )
        return
    for key in keys:
        if not isinstance(value[key], str) or not value[key].strip():
            issues.append(issue(f"{path}.{key}", f"{key} must be a string", "type_error"))
    if "url" in keys and not str(value["url"]).startswith("https://"):
        issues.append(issue(f"{path}.url", "file.url must be a public HTTPS URL", "format"))
    file_type = value.get("type")
    if file_type is not None and file_type not in {"image", "document", "file"}:
        issues.append(issue(f"{path}.type", "unsupported file type", "enum"))
    for key in ("filename", "title", "mime_type", "caption"):
        raw = value.get(key)
        if raw is not None and not isinstance(raw, str):
            issues.append(issue(f"{path}.{key}", f"{key} must be a string", "type_error"))


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
    unexpected = set(value) - {
        "text",
        "url",
        "callback_data",
        "allowed_user_refs",
        "allowed_chat_refs",
    }
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
    _optional_string_list(value.get("allowed_user_refs"), issues, f"{path}.allowed_user_refs")
    _optional_string_list(value.get("allowed_chat_refs"), issues, f"{path}.allowed_chat_refs")


def _optional_string_list(
    value: Any,
    issues: list[ActionValidationIssue],
    path: str,
) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(issue(path, "value must be an array of strings", "type_error"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(
                issue(f"{path}[{index}]", "value must be a non-empty string", "type_error")
            )


def _allowed_updates(
    value: Any,
    issues: list[ActionValidationIssue],
    *,
    required: bool = True,
) -> None:
    if value is None and not required:
        return
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
