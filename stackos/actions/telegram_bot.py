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
from urllib.parse import quote, urlparse

import httpx
from sqlmodel import col, select

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    credential_config,
    credential_payload,
    credential_value,
    issue,
    result,
    send_json,
    unknown_operation,
)
from stackos.artifacts import redact_secret_text
from stackos.config import Settings
from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository

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
                _required_text(payload, "bot_profile_key", issues)
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
                _required_text(payload, "bot_profile_key", issues)
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
            case "callback.answer":
                _required_text(payload, "bot_profile_key", issues)
                _required_text(payload, "callback_query_id", issues)
                _optional_text(payload, "text", issues, max_chars=_MAX_CALLBACK_TEXT)
                _optional_bool(payload, "show_alert", issues)
                _optional_text(payload, "url", issues)
                _optional_int(payload, "cache_time", issues, minimum=0, maximum=3600)
            case "updates.poll":
                _required_text(payload, "bot_profile_key", issues)
                _optional_text(payload, "cursor_ref", issues)
                _optional_int(payload, "offset", issues, minimum=0, maximum=2_147_483_647)
                _optional_int(payload, "limit", issues, minimum=1, maximum=100)
                _optional_int(payload, "timeout_s", issues, minimum=0, maximum=60)
                _allowed_updates(payload.get("allowed_updates"), issues)
            case "webhook.set":
                _required_text(payload, "bot_profile_key", issues)
                _required_text(payload, "webhook_url", issues, max_chars=2048)
                _allowed_updates(payload.get("allowed_updates"), issues, required=False)
                _optional_bool(payload, "drop_pending_updates", issues)
                _optional_int(payload, "max_connections", issues, minimum=1, maximum=100)
                _optional_text(payload, "ip_address", issues)
            case "webhook.delete":
                _required_text(payload, "bot_profile_key", issues)
                _optional_bool(payload, "drop_pending_updates", issues)
            case "webhook.info":
                _required_text(payload, "bot_profile_key", issues)
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
                _enforce_bot_profile(request)
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
                profile = _enforce_bot_profile(request)
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
                profile = _enforce_bot_profile(request)
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
                _enforce_bot_profile(request)
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
                _enforce_bot_profile(request)
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
    profile_key = str(request.input_json["bot_profile_key"]).strip()
    webhook_url = str(request.input_json["webhook_url"]).strip()
    parsed = urlparse(webhook_url)
    expected_path = f"/api/v1/ingress/telegram/{request.project_id}/{quote(profile_key, safe='')}"
    if parsed.path.rstrip("/") != expected_path:
        raise ValidationError(
            "Telegram webhook_url must target this project bot profile ingress route"
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


async def _send_photo(
    request: ActionConnectorRequest,
    chat_id: Any,
    profile: Mapping[str, Any],
) -> ActionConnectorResult:
    payload = request.input_json
    photo = payload["photo"]
    assert isinstance(photo, dict)
    base_body: dict[str, Any] = {"chat_id": chat_id}
    if "caption" in payload:
        base_body["caption"] = payload["caption"]
    _copy_common_message_fields(request, profile, base_body)
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
        _store_outbound_message(request, profile, body, content_type="photo")
        _store_callback_buttons(request, profile, body)
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
                f"provider action returned status {response.status_code}: {response.text[:500]}"
            )
        )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    _store_outbound_message(request, profile, body, content_type="photo")
    _store_callback_buttons(request, profile, body)
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


def _resource_record_by_external_id(
    session: Any,
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


def _enforce_bot_profile(request: ActionConnectorRequest) -> dict[str, Any]:
    profile_key = request.input_json.get("bot_profile_key")
    if not isinstance(profile_key, str) or not profile_key.strip():
        raise ValidationError("Telegram bot_profile_key is required")
    if request.session is None:
        raise ValidationError("Telegram bot profile enforcement requires a repository session")
    profile_key = profile_key.strip()
    record = _resource_record_by_external_id(
        request.session,
        project_id=request.project_id,
        resource_key="communication-bot-profile",
        external_id=f"telegram-bot-profile:{profile_key}",
    )
    if record is None:
        raise ValidationError("Telegram bot profile was not found")
    data = dict(record.data_json or {})
    if data.get("key") != profile_key or data.get("provider_key", "telegram-bot") != "telegram-bot":
        raise ValidationError("Telegram bot profile was not found")
    _validate_bot_profile(data)
    expected_profile = data.get("auth_profile_key")
    actual_profile = request.credential.integration.profile_key if request.credential else None
    actual_project = request.credential.integration.project_id if request.credential else None
    if actual_project != request.project_id:
        raise ValidationError("Telegram bot profile requires a project-scoped credential")
    if expected_profile != actual_profile:
        raise ValidationError("Telegram bot profile does not match credential profile")
    if data.get("enabled") is False:
        raise ValidationError("Telegram bot profile is disabled")
    return data


def _enforce_profile_chat(
    request: ActionConnectorRequest,
    raw_ref: str,
) -> dict[str, Any]:
    profile = _enforce_bot_profile(request)
    resolved_ref = _resolve_profile_ref(profile, raw_ref, "chats", "chat_refs")
    access = profile.get("access_policy")
    response = profile.get("response_policy")
    access_policy = access if isinstance(access, Mapping) else {}
    response_policy = response if isinstance(response, Mapping) else {}
    denied = _profile_refs(access_policy, "denied_chat_refs", "denied_chat_ids", "denied_chats")
    candidates = _candidate_refs(raw_ref, resolved_ref, "telegram-chat")
    if denied and any(candidate in denied for candidate in candidates):
        raise ValidationError(f"Telegram bot profile does not allow chat {raw_ref!r}")
    _enforce_response_origin(request, profile, candidates)
    if response_policy.get("reply_to_source_message") is True and not request.input_json.get(
        "reply_to_message_ref"
    ):
        raise ValidationError("Telegram bot profile requires reply_to_message_ref for responses")
    allowed = _profile_refs(access_policy, "allowed_chat_refs", "allowed_chat_ids", "allowed_chats")
    if not allowed:
        return profile
    if any(candidate in allowed for candidate in candidates):
        return profile
    raise ValidationError(f"Telegram bot profile does not allow chat {raw_ref!r}")


def _enforce_response_origin(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any],
    chat_candidates: list[str],
) -> None:
    response = profile.get("response_policy")
    response_policy = response if isinstance(response, Mapping) else {}
    if response_policy.get("origin_required") is not True:
        return
    if request.session is None:
        raise ValidationError("Telegram response origin enforcement requires a repository session")
    request_id = request.input_json.get("source_agent_request_id")
    if not isinstance(request_id, int) or isinstance(request_id, bool):
        raise ValidationError("Telegram response requires source_agent_request_id")
    source = AgentRequestRepository(request.session).get(
        project_id=request.project_id,
        request_id=request_id,
    )
    if source.source_provider != "telegram-bot":
        raise ValidationError("Telegram response source must be a Telegram agent request")
    if not source.source_message_ref:
        raise ValidationError("Telegram response source must include a Telegram source message")
    metadata = source.metadata_json or {}
    if metadata.get("bot_profile_key") != request.input_json.get("bot_profile_key"):
        raise ValidationError("Telegram response source does not match bot profile")
    source_chat = metadata.get("chat_ref")
    if not isinstance(source_chat, str) or source_chat not in chat_candidates:
        raise ValidationError("Telegram response chat does not match request origin")
    source_message = source.source_message_ref
    if response_policy.get("reply_to_source_message") is True and (
        source_message is None or request.input_json.get("reply_to_message_ref") != source_message
    ):
        raise ValidationError("Telegram response must reply to the source message")
    source_thread = metadata.get("thread_ref")
    if (
        response_policy.get("same_thread") is True
        and isinstance(source_thread, str)
        and request.input_json.get("thread_ref") != source_thread
    ):
        raise ValidationError("Telegram response thread does not match request origin")


def _validate_bot_profile(profile: Mapping[str, Any]) -> None:
    access = profile.get("access_policy")
    if not isinstance(access, Mapping):
        raise ValidationError("Telegram bot profile requires access_policy")
    for key in ("dm_mode", "group_mode", "user_mode"):
        mode = access.get(key)
        if mode not in {"all", "allowlist", "denylist", "disabled"}:
            raise ValidationError(f"Telegram bot profile access_policy.{key} is required")
        if mode == "allowlist":
            has_chat_allowlist = bool(
                _profile_refs(access, "allowed_chat_refs", "allowed_chat_ids", "allowed_chats")
            )
            has_user_allowlist = bool(
                _profile_refs(
                    access,
                    "allowed_user_refs",
                    "allowed_user_ids",
                    "allowed_usernames",
                    "allowed_users",
                )
            )
            if key == "dm_mode" and not (has_chat_allowlist or has_user_allowlist):
                raise ValidationError(
                    "Telegram bot profile access_policy.dm_mode=allowlist requires "
                    "allowed chats or users"
                )
            if key == "group_mode" and not has_chat_allowlist:
                raise ValidationError(
                    f"Telegram bot profile access_policy.{key}=allowlist requires allowed chats"
                )
            if key == "user_mode" and not has_user_allowlist:
                raise ValidationError(
                    "Telegram bot profile access_policy.user_mode=allowlist requires allowed users"
                )


def _resolve_profile_ref(profile: Mapping[str, Any], value: Any, *map_keys: str) -> Any:
    if not isinstance(value, str) or not value:
        return value
    for map_key in map_keys:
        mapping = profile.get(map_key)
        if isinstance(mapping, Mapping):
            mapped = mapping.get(value)
            if mapped is not None:
                return mapped
    refs = profile.get("refs")
    if isinstance(refs, Mapping):
        mapped = refs.get(value)
        if mapped is not None:
            return mapped
    provider_id = _provider_id_from_ref(value)
    if provider_id is not None:
        return provider_id
    return value


def _provider_id_from_ref(value: str) -> int | None:
    if value.startswith("telegram-chat:"):
        return _int_text(value.removeprefix("telegram-chat:"))
    if value.startswith("telegram-message:"):
        parts = value.split(":")
        if len(parts) == 3:
            return _int_text(parts[2])
    if value.startswith("telegram-thread:"):
        parts = value.split(":")
        if len(parts) == 3 and parts[2] != "default":
            return _int_text(parts[2])
    return None


def _int_text(value: str) -> int | None:
    stripped = value.strip()
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    return None


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
    bot_profile_key = str(request.input_json["bot_profile_key"])
    resources = ResourceRepository(request.session)
    if chat_id is not None:
        resources.upsert_record(
            project_id=request.project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id=f"telegram-chat:{bot_profile_key}:{chat_id}",
            title=str(chat.get("title") or chat.get("username") or chat_id)
            if isinstance(chat, Mapping)
            else str(chat_id),
            data_json={
                "provider_key": "telegram-bot",
                "bot_profile_key": bot_profile_key,
                "provider_chat_id": str(chat_id),
                "channel_type": chat.get("type") if isinstance(chat, Mapping) else None,
            },
            provenance_json={"source": "telegram-bot-action"},
        )
    resources.upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=f"telegram-message:{bot_profile_key}:{chat_id}:{message_id}",
        title="Telegram outbound message",
        data_json={
            "provider_key": "telegram-bot",
            "bot_profile_key": bot_profile_key,
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
            allowed_user_refs = _split_config_values(button.get("allowed_user_refs"))
            if not allowed_user_refs:
                allowed_user_refs = source_scope.get("allowed_user_refs", [])
            allowed_chat_refs = _split_config_values(button.get("allowed_chat_refs"))
            if not allowed_chat_refs:
                allowed_chat_refs = source_scope.get("allowed_chat_refs", [])
            resources.upsert_record(
                project_id=request.project_id,
                plugin_slug="communications",
                resource_key="communication-interaction",
                external_id=_callback_button_external_id(
                    str(request.input_json["bot_profile_key"]),
                    message_ref,
                    callback_data,
                ),
                title=str(button.get("text") or callback_data),
                data_json={
                    "provider_key": "telegram-bot",
                    "bot_profile_key": request.input_json["bot_profile_key"],
                    "interaction_type": "outbound_inline_button",
                    "callback_data": callback_data,
                    "message_ref": message_ref,
                    "chat_ref": request.input_json.get("chat_ref"),
                    "allowed_user_refs": allowed_user_refs,
                    "allowed_chat_refs": allowed_chat_refs,
                    "source_agent_request_id": request.input_json.get("source_agent_request_id"),
                    "status": "active",
                },
                provenance_json={"source": "telegram-bot-action"},
            )


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


def _callback_button_external_id(
    bot_profile_key: str,
    message_ref: str,
    callback_data: str,
) -> str:
    return f"telegram-button:{bot_profile_key}:{message_ref}:{callback_data}"


def _provider_message_ref(result_body: Any) -> str | None:
    if not isinstance(result_body, Mapping):
        return None
    chat = result_body.get("chat")
    chat_id = chat.get("id") if isinstance(chat, Mapping) else None
    message_id = result_body.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return f"telegram-message:{chat_id}:{message_id}"


def _profile_refs(policy: Mapping[str, Any], *keys: str) -> set[str]:
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


def _candidate_refs(raw_ref: Any, resolved_ref: Any, prefix: str) -> list[str]:
    refs: list[str] = []
    if isinstance(raw_ref, str) and raw_ref:
        refs.append(raw_ref)
    if resolved_ref is not None:
        refs.append(str(resolved_ref))
        refs.append(f"{prefix}:{resolved_ref}")
    return refs


def _enforce_allowed_updates(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any] | None = None,
) -> None:
    configured = _split_config_values((profile or {}).get("allowed_updates"))
    visibility = (profile or {}).get("visibility_policy")
    if not configured and isinstance(visibility, Mapping):
        configured = _split_config_values(visibility.get("allowed_updates"))
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


__all__ = ["TelegramBotActionConnector"]
