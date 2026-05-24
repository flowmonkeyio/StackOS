"""Slack Web API action connector.

Official docs verified:
- auth.test: https://docs.slack.dev/reference/methods/auth.test/
- chat.postMessage: https://docs.slack.dev/reference/methods/chat.postMessage/
- conversations.open: https://docs.slack.dev/reference/methods/conversations.open/
- conversations.info: https://docs.slack.dev/reference/methods/conversations.info/
- conversations.list: https://docs.slack.dev/reference/methods/conversations.list/
- conversations.members: https://docs.slack.dev/reference/methods/conversations.members/
- Block Kit buttons: https://docs.slack.dev/reference/block-kit/block-elements/button-element/
- Actions block: https://docs.slack.dev/reference/block-kit/blocks/actions-block/
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

import httpx
from sqlmodel import Session, col, select

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    credential_config,
    credential_value,
    issue,
    unknown_operation,
)
from stackos.artifacts import redact_secret_text
from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository

_BASE_URL = "https://slack.com/api"
_MAX_TEXT_CHARS = 40_000
_RECOMMENDED_TEXT_CHARS = 4_000
_MAX_BLOCKS = 50
_MAX_ACTION_BLOCK_ELEMENTS = 25
_MAX_BUTTON_TEXT_CHARS = 75
_MAX_BUTTON_VALUE_CHARS = 2_000
_MAX_BUTTON_ACTION_ID_CHARS = 255
_MAX_BUTTON_URL_CHARS = 3_000
_MAX_CONVERSATION_LIMIT = 1_000
_MAX_OPEN_USERS = 8
_CONVERSATION_TYPES = {"public_channel", "private_channel", "mpim", "im"}
_SECRETISH_BUTTON_RE = re.compile(
    r"(?i)(bearer\s+|xox[baprs]-|sk-[a-z0-9]|api[_-]?key|client[_-]?secret|"
    r"refresh[_-]?token|access[_-]?token|password|secret)"
)
_SLACK_TOKEN_RE = re.compile(r"(?i)xox[baprs]-[A-Za-z0-9-]+")


class SlackBotActionConnector:
    """Decision-free adapter for explicit Slack Web API calls."""

    key = "slack-bot"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "identity.get":
                return []
            case "message.send":
                _required_any(payload, ("channel_ref", "surface_ref"), issues)
                _optional_text(payload, "profile_ref", issues)
                _optional_text(payload, "channel_ref", issues)
                _optional_text(payload, "surface_ref", issues)
                _optional_text(payload, "text", issues, max_chars=_MAX_TEXT_CHARS)
                _optional_text(payload, "thread_ref", issues)
                _optional_bool(payload, "reply_broadcast", issues)
                _optional_bool(payload, "unfurl_links", issues)
                _optional_bool(payload, "unfurl_media", issues)
                _optional_int(payload, "source_agent_request_id", issues, minimum=1)
                _blocks(payload.get("blocks"), issues)
                if not _has_text(payload.get("text")) and not isinstance(
                    payload.get("blocks"), list
                ):
                    issues.append(issue("$", "text or blocks is required", "required"))
            case "conversation.open":
                if "users" not in payload and "channel_ref" not in payload:
                    issues.append(issue("$", "users or channel_ref is required", "required"))
                _optional_text(payload, "profile_ref", issues)
                _user_list(payload.get("users"), issues)
                _optional_text(payload, "channel_ref", issues)
                _optional_bool(payload, "return_im", issues)
            case "conversation.info":
                _required_any(payload, ("channel_ref", "surface_ref"), issues)
                _optional_text(payload, "profile_ref", issues)
                _optional_text(payload, "channel_ref", issues)
                _optional_text(payload, "surface_ref", issues)
                _optional_bool(payload, "include_num_members", issues)
            case "conversation.list":
                _optional_text(payload, "profile_ref", issues)
                _optional_text(payload, "cursor", issues)
                _optional_text(payload, "team_id", issues)
                _optional_bool(payload, "exclude_archived", issues)
                _optional_int(
                    payload,
                    "limit",
                    issues,
                    minimum=1,
                    maximum=_MAX_CONVERSATION_LIMIT,
                )
                _conversation_types(payload.get("types"), issues)
            case "conversation.members":
                _required_any(payload, ("channel_ref", "surface_ref"), issues)
                _optional_text(payload, "profile_ref", issues)
                _optional_text(payload, "channel_ref", issues)
                _optional_text(payload, "surface_ref", issues)
                _optional_text(payload, "cursor", issues)
                _optional_int(
                    payload,
                    "limit",
                    issues,
                    minimum=1,
                    maximum=_MAX_CONVERSATION_LIMIT,
                )
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        match request.operation:
            case "identity.get":
                # Slack auth.test:
                # https://docs.slack.dev/reference/methods/auth.test/
                status, body, headers = await _slack_api(request, "POST", "auth.test")
                return _identity_result(request, status, body, headers)
            case "message.send":
                _communication_profile_key(request)
                body_json = _message_payload(request)
                # Slack chat.postMessage:
                # https://docs.slack.dev/reference/methods/chat.postMessage/
                status, body, headers = await _slack_api(
                    request,
                    "POST",
                    "chat.postMessage",
                    json_body=body_json,
                )
                _store_outbound_message(request, body, body_json)
                return _message_result(request, status, body, headers, body_json)
            case "conversation.open":
                _communication_profile_key(request)
                # Slack conversations.open:
                # https://docs.slack.dev/reference/methods/conversations.open/
                status, body, headers = await _slack_api(
                    request,
                    "POST",
                    "conversations.open",
                    json_body=_conversation_open_payload(request),
                )
                _store_conversation_from_body(request, body)
                return _conversation_open_result(request, status, body, headers)
            case "conversation.info":
                _communication_profile_key(request)
                # Slack conversations.info:
                # https://docs.slack.dev/reference/methods/conversations.info/
                status, body, headers = await _slack_api(
                    request,
                    "GET",
                    "conversations.info",
                    params=_conversation_info_params(request),
                )
                _store_conversation_from_body(request, body)
                return _conversation_info_result(request, status, body, headers)
            case "conversation.list":
                _communication_profile_key(request)
                # Slack conversations.list:
                # https://docs.slack.dev/reference/methods/conversations.list/
                status, body, headers = await _slack_api(
                    request,
                    "GET",
                    "conversations.list",
                    params=_conversation_list_params(request),
                )
                _store_conversation_list(request, body)
                return _conversation_list_result(request, status, body, headers)
            case "conversation.members":
                _communication_profile_key(request)
                # Slack conversations.members:
                # https://docs.slack.dev/reference/methods/conversations.members/
                status, body, headers = await _slack_api(
                    request,
                    "GET",
                    "conversations.members",
                    params=_conversation_members_params(request),
                )
                _store_memberships_from_body(request, body)
                return _conversation_members_result(request, status, body, headers)
            case _:
                raise ValidationError(f"unsupported Slack operation {request.operation!r}")


async def _slack_api(
    request: ActionConnectorRequest,
    method: str,
    api_method: str,
    *,
    json_body: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
) -> tuple[int, Any, httpx.Headers]:
    url = f"{_api_base_url(request)}/{api_method}"
    token = credential_value(request, "bot_token", "access_token", "token")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.request(
            method,
            url,
            headers=headers,
            json=dict(json_body or {}) if json_body is not None else None,
            params=dict(params or {}),
        )
    if response.status_code >= 400:
        raise ValidationError(
            _redact_slack_text(
                f"Slack {api_method} returned status {response.status_code}: {response.text[:500]}"
            )
        )
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    if isinstance(body, Mapping) and body.get("ok") is False:
        error = _redact_slack_text(str(body.get("error") or "unknown_error"))
        raise ValidationError(f"Slack {api_method} returned error {error}")
    return response.status_code, body, response.headers


def _api_base_url(request: ActionConnectorRequest) -> str:
    config = credential_config(request)
    return str(config.get("api_base_url") or _BASE_URL).rstrip("/")


def _message_payload(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    channel_ref = payload.get("channel_ref") or payload.get("surface_ref")
    body: dict[str, Any] = {"channel": _channel_id(request, channel_ref)}
    if _has_text(payload.get("text")):
        body["text"] = str(payload["text"])
    if isinstance(payload.get("blocks"), list):
        body["blocks"] = payload["blocks"]
    thread_ts = _thread_ts(request, payload.get("thread_ref"))
    if thread_ts is not None:
        body["thread_ts"] = thread_ts
    for key in ("reply_broadcast", "unfurl_links", "unfurl_media"):
        if key in payload:
            body[key] = payload[key]
    return body


def _conversation_open_payload(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    body: dict[str, Any] = {}
    if "channel_ref" in payload:
        body["channel"] = _channel_id(request, payload.get("channel_ref"))
    if "users" in payload:
        users = [_user_id(request, item) for item in payload.get("users") or []]
        body["users"] = ",".join(users)
    if "return_im" in payload:
        body["return_im"] = payload["return_im"]
    return body


def _conversation_info_params(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    channel_ref = payload.get("channel_ref") or payload.get("surface_ref")
    body = {"channel": _channel_id(request, channel_ref)}
    if "include_num_members" in payload:
        body["include_num_members"] = payload["include_num_members"]
    return body


def _conversation_list_params(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    params: dict[str, Any] = {
        "limit": payload.get("limit", 100),
        "exclude_archived": payload.get("exclude_archived", True),
    }
    if payload.get("cursor"):
        params["cursor"] = payload["cursor"]
    if payload.get("team_id"):
        params["team_id"] = payload["team_id"]
    types = payload.get("types")
    if isinstance(types, list) and types:
        params["types"] = ",".join(str(item) for item in types)
    return params


def _conversation_members_params(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    params: dict[str, Any] = {
        "channel": _channel_id(request, payload.get("channel_ref") or payload.get("surface_ref")),
        "limit": payload.get("limit", 100),
    }
    if payload.get("cursor"):
        params["cursor"] = payload["cursor"]
    return params


def _channel_id(request: ActionConnectorRequest, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Slack channel_ref is required")
    raw = _resolve_ref(request, value.strip(), "channel_refs", "surface_refs", "refs")
    text = str(raw).strip()
    for prefix in ("slack-channel:", "slack-dm:", "slack-mpim:"):
        if text.startswith(prefix):
            return text.removeprefix(prefix)
    if text.startswith("slack-thread:"):
        parts = text.split(":", 2)
        if len(parts) == 3:
            return parts[1]
    return text


def _thread_ts(request: ActionConnectorRequest, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Slack thread_ref must be a string")
    raw = _resolve_ref(request, value.strip(), "thread_refs", "refs")
    text = str(raw).strip()
    if text.startswith("slack-thread:"):
        parts = text.split(":", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
        raise ValidationError("Slack thread_ref must be slack-thread:<channel>:<ts>")
    return text


def _user_id(request: ActionConnectorRequest, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Slack users must be non-empty strings")
    raw = _resolve_ref(request, value.strip(), "user_refs", "refs")
    text = str(raw).strip()
    return text.removeprefix("slack-user:")


def _resolve_ref(request: ActionConnectorRequest, value: str, *map_keys: str) -> Any:
    config = credential_config(request)
    for map_key in map_keys:
        mapping = config.get(map_key)
        if isinstance(mapping, Mapping) and value in mapping:
            return mapping[value]
    return value


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


def _store_outbound_message(
    request: ActionConnectorRequest,
    provider_body: Any,
    sent_payload: Mapping[str, Any],
) -> None:
    if request.session is None or not isinstance(provider_body, Mapping):
        return
    channel = str(provider_body.get("channel") or sent_payload.get("channel") or "")
    ts = str(provider_body.get("ts") or _nested(provider_body, "message.ts") or "")
    if not channel or not ts:
        return
    profile_key = _communication_profile_key(request)
    auth_profile_key = _credential_profile_key(request)
    team_id = _team_id(request, provider_body)
    text = str(sent_payload.get("text") or _nested(provider_body, "message.text") or "")
    thread_ts = str(sent_payload.get("thread_ts") or ts)
    resources = ResourceRepository(request.session)
    _upsert_channel(
        resources,
        request.project_id,
        profile_key=profile_key,
        auth_profile_key=auth_profile_key,
        team_id=team_id,
        channel_obj={"id": channel, "name": channel},
        source="slack-bot-action",
    )
    message_ref = _message_ref(channel, ts)
    resources.upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=f"slack-message:{profile_key}:{channel}:{ts}",
        title="Slack outbound message",
        data_json={
            "provider_key": "slack-bot",
            "profile_key": profile_key,
            "auth_profile_key": auth_profile_key,
            "team_id": team_id,
            "direction": "outbound",
            "surface_ref": _surface_ref(channel),
            "channel_ref": _surface_ref(channel),
            "thread_ref": _thread_ref(channel, thread_ts),
            "message_ref": message_ref,
            "provider_message_ts": ts,
            "content_type": "blocks" if isinstance(sent_payload.get("blocks"), list) else "text",
            "text_preview": text[:_RECOMMENDED_TEXT_CHARS],
            "transport_status": "accepted",
            "attention_status": "sent",
            "source_agent_request_id": request.input_json.get("source_agent_request_id"),
            "action_ref": request.action_ref,
        },
        provenance_json={"source": "slack-bot-action"},
    )
    _store_outbound_buttons(request, resources, message_ref=message_ref, channel=channel)


def _store_outbound_buttons(
    request: ActionConnectorRequest,
    resources: ResourceRepository,
    *,
    message_ref: str,
    channel: str,
) -> None:
    blocks = request.input_json.get("blocks")
    if not isinstance(blocks, list):
        return
    profile_key = _communication_profile_key(request)
    auth_profile_key = _credential_profile_key(request)
    for button in _button_specs(blocks):
        action_id = str(button.get("action_id") or "")
        value = str(button.get("value") or "")
        interaction_external_id = _outbound_button_external_id(
            profile_key=profile_key,
            message_ref=message_ref,
            action_id=action_id,
            value=value,
            block_id=str(button.get("block_id") or ""),
        )
        resources.upsert_record(
            project_id=request.project_id,
            plugin_slug="communications",
            resource_key="communication-interaction",
            external_id=interaction_external_id,
            title=str(button.get("text") or action_id or "Slack button"),
            data_json={
                "provider_key": "slack-bot",
                "profile_key": profile_key,
                "auth_profile_key": auth_profile_key,
                "interaction_type": "outbound_block_button",
                "surface_ref": _surface_ref(channel),
                "channel_ref": _surface_ref(channel),
                "message_ref": message_ref,
                "block_id": button.get("block_id"),
                "action_id": action_id,
                "button_value": value or None,
                "url_button": bool(button.get("url")),
                "status": "active",
                "source_agent_request_id": request.input_json.get("source_agent_request_id"),
            },
            provenance_json={"source": "slack-bot-action"},
        )


def _store_conversation_from_body(request: ActionConnectorRequest, provider_body: Any) -> None:
    if request.session is None:
        return
    channel = _channel_from_body(provider_body)
    if not channel:
        return
    _upsert_channel(
        ResourceRepository(request.session),
        request.project_id,
        profile_key=_communication_profile_key(request),
        auth_profile_key=_credential_profile_key(request),
        team_id=_team_id(request, provider_body),
        channel_obj=channel,
        source="slack-bot-action",
    )


def _store_conversation_list(request: ActionConnectorRequest, provider_body: Any) -> None:
    if request.session is None or not isinstance(provider_body, Mapping):
        return
    channels = provider_body.get("channels")
    if not isinstance(channels, list):
        return
    resources = ResourceRepository(request.session)
    profile_key = _communication_profile_key(request)
    auth_profile_key = _credential_profile_key(request)
    for channel in channels:
        if isinstance(channel, Mapping):
            _upsert_channel(
                resources,
                request.project_id,
                profile_key=profile_key,
                auth_profile_key=auth_profile_key,
                team_id=_team_id(request, provider_body),
                channel_obj=channel,
                source="slack-bot-action",
            )


def _store_memberships_from_body(request: ActionConnectorRequest, provider_body: Any) -> None:
    if request.session is None or not isinstance(provider_body, Mapping):
        return
    members = provider_body.get("members")
    if not isinstance(members, list):
        return
    channel_ref = request.input_json.get("channel_ref") or request.input_json.get("surface_ref")
    channel = _channel_id(request, channel_ref)
    resources = ResourceRepository(request.session)
    profile_key = _communication_profile_key(request)
    auth_profile_key = _credential_profile_key(request)
    for member in members:
        if not isinstance(member, str) or not member:
            continue
        resources.upsert_record(
            project_id=request.project_id,
            plugin_slug="communications",
            resource_key="communication-membership",
            external_id=f"slack-membership:{profile_key}:{channel}:{member}",
            title=f"{member} in {channel}",
            data_json={
                "provider_key": "slack-bot",
                "profile_key": profile_key,
                "auth_profile_key": auth_profile_key,
                "surface_ref": _surface_ref(channel),
                "member_ref": f"slack-user:{member}",
                "membership_kind": "user",
                "status": "joined",
                "roles": [],
                "permissions": {},
                "scope_status": {},
            },
            provenance_json={"source": "slack-bot-action"},
        )


def _upsert_channel(
    resources: ResourceRepository,
    project_id: int,
    *,
    profile_key: str,
    auth_profile_key: str,
    team_id: str | None,
    channel_obj: Mapping[str, Any],
    source: str,
) -> None:
    channel_id = _channel_id_from_obj(channel_obj)
    if not channel_id:
        return
    display = _channel_display_name(channel_obj)
    resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-channel",
        external_id=f"slack-channel:{profile_key}:{channel_id}",
        title=display,
        data_json={
            "provider_key": "slack-bot",
            "profile_key": profile_key,
            "auth_profile_key": auth_profile_key,
            "team_id": team_id,
            "surface_ref": _surface_ref(channel_id),
            "channel_ref": _surface_ref(channel_id),
            "provider_channel_id": channel_id,
            "kind": _channel_kind(channel_obj),
            "display_name": display,
            "safe_external_ref": f"slack-team:{team_id}:channel:{channel_id}"
            if team_id
            else f"slack-channel:{channel_id}",
            "send_enabled": True,
            "ingest_enabled": True,
            "capabilities": {
                "can_read": True,
                "can_write": bool(channel_obj.get("is_member", True)),
                "can_thread": True,
            },
            "metadata_json": {
                "is_private": channel_obj.get("is_private"),
                "is_archived": channel_obj.get("is_archived"),
                "is_member": channel_obj.get("is_member"),
                "user": channel_obj.get("user"),
            },
        },
        provenance_json={"source": source},
    )


def _channel_from_body(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        return {}
    raw = body.get("channel")
    return raw if isinstance(raw, Mapping) else {}


def _channel_id_from_obj(channel: Mapping[str, Any]) -> str:
    raw = channel.get("id")
    return str(raw).strip() if raw is not None and str(raw).strip() else ""


def _safe_channel(channel: Mapping[str, Any]) -> dict[str, Any]:
    channel_id = _channel_id_from_obj(channel)
    return {
        "channel_ref": _surface_ref(channel_id) if channel_id else None,
        "id": channel_id or None,
        "name": channel.get("name"),
        "kind": _channel_kind(channel),
        "is_member": channel.get("is_member"),
        "is_archived": channel.get("is_archived"),
    }


def _channel_display_name(channel: Mapping[str, Any]) -> str:
    for key in ("name", "user", "id"):
        value = channel.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return "Slack conversation"


def _channel_kind(channel: Mapping[str, Any]) -> str:
    if channel.get("is_im") is True:
        return "slack-dm"
    if channel.get("is_mpim") is True:
        return "slack-mpim"
    if channel.get("is_group") is True or channel.get("is_private") is True:
        return "slack-private-channel"
    return "slack-channel"


def _credential_profile_key(request: ActionConnectorRequest) -> str:
    if request.credential is not None:
        return request.credential.integration.profile_key
    return str(credential_config(request).get("profile_key") or "default")


def _communication_profile_key(request: ActionConnectorRequest) -> str:
    """Resolve the communication profile that owns outbound interaction state."""

    raw = request.input_json.get("profile_ref") or request.input_json.get("profile_key")
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        profile_key = text.removeprefix("communication-profile:")
        _communication_profile_data(request, profile_key=profile_key)
        return profile_key
    profile_key = _credential_profile_key(request)
    if request.operation in {"message.send", "conversation.open"}:
        _communication_profile_data(request, profile_key=profile_key)
    return profile_key


def _communication_profile_data(
    request: ActionConnectorRequest,
    *,
    profile_key: str,
) -> dict[str, Any]:
    session = request.session
    if not isinstance(session, Session):
        raise ValidationError("Slack profile_ref requires a database session")
    record = _resource_record_by_external_id(
        session,
        project_id=request.project_id,
        resource_key="communication-profile",
        external_id=f"communication-profile:{profile_key}",
    )
    if record is None:
        raise ValidationError(
            "Slack communication profile not found",
            data={"profile_ref": f"communication-profile:{profile_key}"},
        )
    data = dict(record.data_json or {})
    if data.get("key") != profile_key or data.get("enabled") is False:
        raise ValidationError(
            "Slack communication profile is disabled or malformed",
            data={"profile_ref": f"communication-profile:{profile_key}"},
        )
    facets = data.get("provider_facets")
    slack_facet = facets.get("slack-bot") if isinstance(facets, Mapping) else None
    if not isinstance(slack_facet, Mapping):
        raise ValidationError(
            "Slack communication profile missing slack-bot provider facet",
            data={"profile_ref": f"communication-profile:{profile_key}"},
        )
    auth_profile_key = str(slack_facet.get("auth_profile_key") or "").strip()
    credential_profile_key = _credential_profile_key(request)
    if auth_profile_key != credential_profile_key:
        raise ValidationError(
            "Slack communication profile auth_profile_key does not match credential profile",
            data={
                "profile_ref": f"communication-profile:{profile_key}",
                "auth_profile_key": auth_profile_key,
                "credential_profile_key": credential_profile_key,
            },
        )
    return data


def _resource_record_by_external_id(
    session: Session,
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


def _team_id(request: ActionConnectorRequest, body: Any | None = None) -> str | None:
    if isinstance(body, Mapping):
        value = body.get("team_id") or _nested(body, "team.id")
        if value is not None and str(value).strip():
            return str(value)
    config = credential_config(request)
    value = config.get("team_id") or config.get("provider_account_id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _surface_ref(channel_id: str) -> str:
    return f"slack-channel:{channel_id}"


def _message_ref(channel: str, ts: str) -> str:
    return f"slack-message:{channel}:{ts}"


def _thread_ref(channel: str, ts: str) -> str:
    return f"slack-thread:{channel}:{ts}"


def _outbound_button_external_id(
    *,
    profile_key: str,
    message_ref: str,
    action_id: str,
    value: str,
    block_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{message_ref}\0{block_id}\0{action_id}\0{value}".encode()
    ).hexdigest()[:24]
    return f"slack-button:{profile_key}:{digest}"


def _button_specs(blocks: list[Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") != "actions":
            continue
        block_id = str(block.get("block_id") or "")
        elements = block.get("elements")
        if not isinstance(elements, list):
            continue
        for element in elements:
            if not isinstance(element, Mapping) or element.get("type") != "button":
                continue
            text_obj = element.get("text")
            text = text_obj.get("text") if isinstance(text_obj, Mapping) else None
            specs.append(
                {
                    "block_id": block_id,
                    "action_id": element.get("action_id"),
                    "value": element.get("value"),
                    "url": element.get("url"),
                    "text": text,
                }
            )
    return specs


def _next_cursor(body: Any) -> str | None:
    value = _nested(body, "response_metadata.next_cursor")
    return str(value) if value is not None and str(value).strip() else None


def _nested(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _required_any(
    payload: Mapping[str, Any],
    keys: tuple[str, ...],
    issues: list[ActionValidationIssue],
) -> None:
    if any(_has_text(payload.get(key)) for key in keys):
        return
    issues.append(issue("$", f"one of {', '.join(keys)} is required", "required"))


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
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} must be a non-empty string", "type_error"))
        return
    if max_chars is not None and len(value) > max_chars:
        issues.append(issue(f"$.{key}", f"{key} must be at most {max_chars} characters", "length"))


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
    maximum: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        issues.append(issue(f"$.{key}", f"{key} must be an integer >= {minimum}", "range"))
        return
    if maximum is not None and value > maximum:
        issues.append(issue(f"$.{key}", f"{key} must be <= {maximum}", "range"))


def _user_list(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value:
        issues.append(issue("$.users", "users must be a non-empty array", "type_error"))
        return
    if len(value) > _MAX_OPEN_USERS:
        issues.append(issue("$.users", f"users must contain at most {_MAX_OPEN_USERS} items"))
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(issue(f"$.users[{index}]", "user ref must be a string", "type_error"))


def _conversation_types(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value:
        issues.append(issue("$.types", "types must be a non-empty array", "type_error"))
        return
    for index, item in enumerate(value):
        if item not in _CONVERSATION_TYPES:
            issues.append(issue(f"$.types[{index}]", "unsupported conversation type", "enum"))


def _blocks(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(issue("$.blocks", "blocks must be an array", "type_error"))
        return
    if len(value) > _MAX_BLOCKS:
        issues.append(issue("$.blocks", f"blocks must contain at most {_MAX_BLOCKS} items"))
    for block_index, block in enumerate(value):
        if not isinstance(block, Mapping):
            issues.append(issue(f"$.blocks[{block_index}]", "block must be an object"))
            continue
        if block.get("type") != "actions":
            continue
        elements = block.get("elements")
        if not isinstance(elements, list):
            issues.append(
                issue(
                    f"$.blocks[{block_index}].elements",
                    "actions block elements must be an array",
                    "type_error",
                )
            )
            continue
        if len(elements) > _MAX_ACTION_BLOCK_ELEMENTS:
            issues.append(
                issue(
                    f"$.blocks[{block_index}].elements",
                    f"actions block may contain at most {_MAX_ACTION_BLOCK_ELEMENTS} elements",
                    "length",
                )
            )
        for element_index, element in enumerate(elements):
            _button_element(
                element,
                issues,
                f"$.blocks[{block_index}].elements[{element_index}]",
            )


def _button_element(value: Any, issues: list[ActionValidationIssue], path: str) -> None:
    if not isinstance(value, Mapping) or value.get("type") != "button":
        return
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        issues.append(issue(f"{path}.action_id", "button action_id is required", "required"))
    elif len(action_id) > _MAX_BUTTON_ACTION_ID_CHARS:
        issues.append(
            issue(
                f"{path}.action_id",
                f"button action_id must be at most {_MAX_BUTTON_ACTION_ID_CHARS} characters",
                "length",
            )
        )
    text = value.get("text")
    if not isinstance(text, Mapping) or not _has_text(text.get("text")):
        issues.append(issue(f"{path}.text", "button text object is required", "required"))
    elif len(str(text.get("text"))) > _MAX_BUTTON_TEXT_CHARS:
        issues.append(
            issue(
                f"{path}.text.text",
                f"button text must be at most {_MAX_BUTTON_TEXT_CHARS} characters",
                "length",
            )
        )
    button_value = value.get("value")
    if button_value is not None:
        if not isinstance(button_value, str):
            issues.append(issue(f"{path}.value", "button value must be a string", "type_error"))
        else:
            if len(button_value) > _MAX_BUTTON_VALUE_CHARS:
                issues.append(
                    issue(
                        f"{path}.value",
                        f"button value must be at most {_MAX_BUTTON_VALUE_CHARS} characters",
                        "length",
                    )
                )
            if _SECRETISH_BUTTON_RE.search(button_value):
                issues.append(
                    issue(
                        f"{path}.value",
                        "button value must be an opaque non-secret routing token",
                        "secret_like",
                    )
                )
    url = value.get("url")
    if url is not None:
        if not isinstance(url, str):
            issues.append(issue(f"{path}.url", "button url must be a string", "type_error"))
        elif len(url) > _MAX_BUTTON_URL_CHARS:
            issues.append(
                issue(
                    f"{path}.url",
                    f"button url must be at most {_MAX_BUTTON_URL_CHARS} characters",
                    "length",
                )
            )


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _redact_slack_text(value: str) -> str:
    return _SLACK_TOKEN_RE.sub("[redacted]", redact_secret_text(value))


__all__ = ["SlackBotActionConnector"]
