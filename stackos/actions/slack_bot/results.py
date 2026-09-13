"""Slack action result shaping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from stackos.actions.connectors import ActionConnectorRequest, ActionConnectorResult
from stackos.actions.provider_utils import credential_payload
from stackos.artifacts import redact_secrets
from stackos.secret_refs import redact_secret_values

from .http import _redact_slack_text
from .refs import (
    _channel_from_body,
    _channel_id_from_obj,
    _message_ref,
    _nested,
    _next_cursor,
    _safe_channel,
    _surface_ref,
    _thread_ref,
)


def _identity_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
) -> ActionConnectorResult:
    data = body if isinstance(body, Mapping) else {}
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "ok",
            "team_id": data.get("team_id"),
            "team": data.get("team"),
            "user_id": data.get("user_id"),
            "user": data.get("user"),
            "bot_id": data.get("bot_id"),
            "url": data.get("url"),
        },
        metadata_json=_metadata("auth.test", request.operation, status, body, headers),
    )


def _message_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
    sent_payload: Mapping[str, Any],
) -> ActionConnectorResult:
    data = body if isinstance(body, Mapping) else {}
    channel = str(data.get("channel") or sent_payload.get("channel") or "")
    ts = str(data.get("ts") or _nested(data, "message.ts") or "")
    thread_ts = str(sent_payload.get("thread_ts") or ts) if ts else None
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "sent",
            "channel_ref": _surface_ref(channel) if channel else None,
            "thread_ref": _thread_ref(channel, thread_ts) if channel and thread_ts else None,
            "message_ref": _message_ref(channel, ts) if channel and ts else None,
            "provider_message_ts": ts or None,
        },
        metadata_json=_metadata("chat.postMessage", request.operation, status, body, headers),
    )


def _reaction_add_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
    sent_payload: Mapping[str, Any],
) -> ActionConnectorResult:
    channel = str(sent_payload.get("channel") or "")
    timestamp = str(sent_payload.get("timestamp") or "")
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "reacted",
            "channel_ref": _surface_ref(channel) if channel else None,
            "message_ref": _message_ref(channel, timestamp) if channel and timestamp else None,
            "reaction_name": sent_payload.get("name"),
        },
        metadata_json=_metadata("reactions.add", request.operation, status, body, headers),
    )


def _message_delete_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
    sent_payload: Mapping[str, Any],
) -> ActionConnectorResult:
    data = body if isinstance(body, Mapping) else {}
    channel = str(data.get("channel") or sent_payload.get("channel") or "")
    timestamp = str(data.get("ts") or sent_payload.get("ts") or "")
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "deleted",
            "channel_ref": _surface_ref(channel) if channel else None,
            "message_ref": _message_ref(channel, timestamp) if channel and timestamp else None,
            "provider_message_ts": timestamp or None,
        },
        metadata_json=_metadata("chat.delete", request.operation, status, body, headers),
    )


def _conversation_open_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
) -> ActionConnectorResult:
    channel = _channel_from_body(body)
    channel_id = _channel_id_from_obj(channel)
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "ok",
            "channel_ref": _surface_ref(channel_id) if channel_id else None,
            "channel": _safe_channel(channel),
        },
        metadata_json=_metadata("conversations.open", request.operation, status, body, headers),
    )


def _conversation_info_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
) -> ActionConnectorResult:
    channel = _channel_from_body(body)
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "ok",
            "channel": _safe_channel(channel),
        },
        metadata_json=_metadata("conversations.info", request.operation, status, body, headers),
    )


def _conversation_list_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
) -> ActionConnectorResult:
    data = body if isinstance(body, Mapping) else {}
    raw_channels = data.get("channels")
    channels: list[Any] = raw_channels if isinstance(raw_channels, list) else []
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "ok",
            "channel_refs": [
                _surface_ref(str(item.get("id")))
                for item in channels
                if isinstance(item, Mapping) and item.get("id")
            ],
            "count": len(channels),
            "next_cursor": _next_cursor(body),
        },
        metadata_json=_metadata("conversations.list", request.operation, status, body, headers),
    )


def _conversation_members_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
) -> ActionConnectorResult:
    data = body if isinstance(body, Mapping) else {}
    raw_members = data.get("members")
    members: list[Any] = raw_members if isinstance(raw_members, list) else []
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "ok",
            "member_refs": [f"slack-user:{member}" for member in members],
            "count": len(members),
            "next_cursor": _next_cursor(body),
        },
        metadata_json=_metadata("conversations.members", request.operation, status, body, headers),
    )


def _conversation_history_result(
    request: ActionConnectorRequest,
    status: int,
    body: Any,
    headers: httpx.Headers,
) -> ActionConnectorResult:
    auth = credential_payload(request) if request.credential is not None else {}
    secret_values = tuple(
        value
        for key in ("bot_token", "access_token", "token", "signing_secret", "value")
        if isinstance((value := auth.get(key)), str) and value
    )
    body = redact_secret_values(body, secret_values)
    data = body if isinstance(body, Mapping) else {}
    channel_ref = request.input_json.get("channel_ref") or request.input_json.get("surface_ref")
    channel = str(channel_ref or "").removeprefix("slack-channel:")
    raw_messages = data.get("messages")
    provider_messages = raw_messages if isinstance(raw_messages, list) else []
    include_content = request.input_json.get("include_content") is True
    messages = [
        _safe_history_message(item, channel=channel, include_content=include_content)
        for item in provider_messages
        if isinstance(item, Mapping)
    ]
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "ok",
            "channel_ref": _surface_ref(channel) if channel else None,
            "messages": messages,
            "message_refs": [
                item["message_ref"]
                for item in messages
                if isinstance(item.get("message_ref"), str) and item["message_ref"]
            ],
            "count": len(messages),
            "content_included": include_content,
            "has_more": bool(data.get("has_more")),
            "next_cursor": _next_cursor(body),
            **({"is_limited": data["is_limited"]} if "is_limited" in data else {}),
        },
        metadata_json=_metadata("conversations.history", request.operation, status, body, headers),
    )


def _safe_history_message(
    item: Mapping[str, Any], *, channel: str, include_content: bool = False
) -> dict[str, Any]:
    ts = str(item.get("ts") or "")
    thread_ts = str(item.get("thread_ts") or ts or "")
    raw_files = item.get("files")
    files = raw_files if isinstance(raw_files, list) else []
    file_refs = [
        f"slack-file:{file_item['id']}"
        for file_item in files
        if isinstance(file_item, Mapping) and file_item.get("id")
    ]
    text = str(item.get("text") or "")
    result = {
        "message_ref": _message_ref(channel, ts) if channel and ts else None,
        "thread_ref": _thread_ref(channel, thread_ts) if channel and thread_ts else None,
        "provider_message_ts": ts or None,
        "user_ref": f"slack-user:{item['user']}" if item.get("user") else None,
        "bot_id": item.get("bot_id"),
        "subtype": item.get("subtype"),
        "text_preview": _redact_slack_text(text[:500]),
        "text_preview_truncated": len(text) > 500,
        "file_refs": file_refs,
    }
    if "reply_count" in item:
        result["reply_count"] = item["reply_count"]
    if include_content:
        for field in ("text", "blocks", "attachments"):
            if field in item:
                result[field] = _safe_history_content(item[field])
        if "files" in item:
            result["files"] = [
                _safe_history_file(file) for file in files if isinstance(file, Mapping)
            ]
    return result


def _safe_history_file(file: Mapping[str, Any]) -> dict[str, Any]:
    """Return file descriptors, never private download URLs or inline file bytes."""
    result = {
        key: file[key]
        for key in (
            "name",
            "title",
            "mimetype",
            "filetype",
            "pretty_type",
            "size",
            "created",
            "timestamp",
            "mode",
            "is_external",
            "external_type",
            "file_access",
        )
        if key in file
    }
    if file.get("id"):
        result["file_ref"] = f"slack-file:{file['id']}"
    if file.get("user"):
        result["user_ref"] = f"slack-user:{file['user']}"
    return _safe_history_content(result)


def _safe_history_content(value: Any) -> Any:
    """Preserve rich business content while excluding private file transport paths.

    Slack image blocks can embed a slack_file id or private URL. A ref is useful
    for later explicit file work; returning the URL is not a file download.
    https://docs.slack.dev/reference/block-kit/composition-objects/slack-file-object/
    """
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"url_private", "url_private_download", "upload_url"}:
                continue
            if key == "slack_file" and isinstance(item, Mapping):
                result[key] = {"file_ref": f"slack-file:{item['id']}"} if item.get("id") else {}
                if "url" in item:
                    result[key]["url_omitted"] = True
            elif key == "files" and isinstance(item, list):
                result[key] = [
                    _safe_history_file(file) for file in item if isinstance(file, Mapping)
                ]
            else:
                result[key] = _safe_history_content(item)
        return redact_secrets(result)
    if isinstance(value, list):
        return [_safe_history_content(item) for item in value]
    if isinstance(value, str):
        return _redact_slack_text(value)
    return value


def _metadata(
    slack_method: str,
    operation: str,
    status: int,
    body: Any,
    headers: httpx.Headers,
) -> dict[str, Any]:
    data = body if isinstance(body, Mapping) else {}
    meta = {
        "vendor": "slack-bot",
        "operation": operation,
        "slack_method": slack_method,
        "status_code": status,
    }
    retry_after = headers.get("retry-after")
    if retry_after:
        meta["retry_after"] = retry_after
    next_cursor = _next_cursor(data)
    if next_cursor:
        meta["next_cursor"] = next_cursor
    if data.get("warning"):
        meta["warning"] = data.get("warning")
    if isinstance(data.get("response_metadata"), Mapping) and data["response_metadata"].get(
        "warnings"
    ):
        meta["warnings"] = data["response_metadata"].get("warnings")
    return meta
