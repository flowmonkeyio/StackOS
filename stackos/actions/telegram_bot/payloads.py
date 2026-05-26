"""Telegram Bot API request payload builders."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlparse

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.provider_utils import credential_config, credential_payload, credential_value
from stackos.repositories.base import ValidationError

from .constants import _BASE_URL
from .policy import _request_profile_key, _resolve_profile_ref, _split_config_values


def _method_url(request: ActionConnectorRequest, method: str) -> str:
    config = credential_config(request)
    base = str(config.get("api_base_url") or _BASE_URL).rstrip("/")
    token = credential_value(request, "bot_token", "token")
    return f"{base}/bot{quote(token, safe=':-_')}/{method}"


def _message_payload(
    request: ActionConnectorRequest,
    chat_id: Any,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    payload = request.input_json
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": payload["text"],
    }
    _copy_common_message_fields(request, profile, body)
    return body


def _callback_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"callback_query_id": payload["callback_query_id"]}
    for key in ("text", "show_alert", "url", "cache_time"):
        if key in payload:
            body[key] = payload[key]
    return body


def _webhook_set_payload(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    payload = request.input_json
    _enforce_webhook_url(request, profile)
    body: dict[str, Any] = {"url": payload["webhook_url"]}
    secret_token = credential_payload(request).get("webhook_secret_token")
    if isinstance(secret_token, str) and secret_token.strip():
        body["secret_token"] = secret_token.strip()
    if "allowed_updates" in payload:
        body["allowed_updates"] = payload["allowed_updates"]
    else:
        profile_updates = _split_config_values(profile.get("allowed_updates"))
        if profile_updates:
            body["allowed_updates"] = profile_updates
    for key in ("drop_pending_updates", "max_connections", "ip_address"):
        if key in payload:
            body[key] = payload[key]
    return body


def _enforce_webhook_url(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any],
) -> None:
    profile_key = _request_profile_key(request)
    webhook_url = str(request.input_json["webhook_url"]).strip()
    parsed = urlparse(webhook_url)
    expected_path = f"/api/v1/ingress/telegram/{request.project_id}/{quote(profile_key, safe='')}"
    if parsed.path.rstrip("/") != expected_path:
        raise ValidationError(
            "Telegram webhook_url must target this project communication profile ingress route"
        )
    host = (parsed.hostname or "").lower()
    if not host or parsed.scheme not in {"http", "https"}:
        raise ValidationError("Telegram webhook_url must include an http or https host")
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if host in local_hosts:
        return
    if parsed.scheme != "https":
        raise ValidationError("Telegram public webhook_url must use https")
    allowed_hosts = set(_split_config_values(profile.get("allowed_webhook_hosts")))
    webhook_policy = profile.get("webhook_policy")
    if isinstance(webhook_policy, Mapping):
        allowed_hosts.update(_split_config_values(webhook_policy.get("allowed_hosts")))
        allowed_hosts.update(_split_config_values(webhook_policy.get("allowed_webhook_hosts")))
    base_url = profile.get("webhook_base_url")
    if isinstance(base_url, str) and base_url.strip():
        base_host = urlparse(base_url).hostname
        if base_host:
            allowed_hosts.add(base_host.lower())
    if host not in allowed_hosts:
        raise ValidationError("Telegram webhook_url must target a configured StackOS webhook host")


def _webhook_delete_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if "drop_pending_updates" in payload:
        body["drop_pending_updates"] = payload["drop_pending_updates"]
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


def _copy_common_message_fields(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any],
    body: dict[str, Any],
) -> None:
    payload = request.input_json
    parse_mode = payload.get("parse_mode") or profile.get("default_parse_mode")
    if isinstance(parse_mode, str) and parse_mode != "plain":
        body["parse_mode"] = parse_mode
    if "disable_notification" in payload:
        body["disable_notification"] = payload["disable_notification"]
    _copy_resolved(payload, body, profile, "reply_to_message_ref", "reply_to_message_id")
    _copy_resolved(payload, body, profile, "thread_ref", "message_thread_id")
    _copy_resolved(
        payload,
        body,
        profile,
        "direct_messages_topic_ref",
        "direct_messages_topic_id",
    )
    if "reply_markup" in payload:
        body["reply_markup"] = _telegram_reply_markup(payload["reply_markup"])


def _copy_resolved(
    payload: Mapping[str, Any],
    body: dict[str, Any],
    profile: Mapping[str, Any],
    input_key: str,
    output_key: str,
) -> None:
    value = payload.get(input_key)
    if value is not None:
        resolved = _resolve_profile_ref(profile, value, input_key, f"{input_key}s")
        if resolved is not None:
            body[output_key] = resolved


def _chat_id(request: ActionConnectorRequest, profile: Mapping[str, Any]) -> Any:
    return _resolve_profile_ref(profile, request.input_json["chat_ref"], "chats", "chat_refs")


def _telegram_reply_markup(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    inline_keyboard = value.get("inline_keyboard")
    if not isinstance(inline_keyboard, list):
        return value
    rows: list[list[dict[str, Any]]] = []
    for row in inline_keyboard:
        if not isinstance(row, list):
            continue
        clean_row: list[dict[str, Any]] = []
        for button in row:
            if not isinstance(button, Mapping):
                continue
            clean_button = {"text": button.get("text")}
            if button.get("url") is not None:
                clean_button["url"] = button.get("url")
            if button.get("callback_data") is not None:
                clean_button["callback_data"] = button.get("callback_data")
            clean_row.append(clean_button)
        rows.append(clean_row)
    return {"inline_keyboard": rows}
