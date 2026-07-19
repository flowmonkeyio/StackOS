"""Slack reference and response helper functions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.provider_utils import credential_config
from stackos.communications.provider_ids import (
    slack_message_ref as _message_ref,  # noqa: F401
)
from stackos.communications.provider_ids import (
    slack_outbound_button_external_id as _outbound_button_external_id,  # noqa: F401
)
from stackos.communications.provider_ids import (
    slack_surface_ref as _surface_ref,
)
from stackos.communications.provider_ids import (
    slack_thread_ref as _thread_ref,  # noqa: F401
)
from stackos.repositories.base import ValidationError


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


def _message_parts(request: ActionConnectorRequest, value: Any) -> tuple[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Slack message_ref is required")
    raw = _resolve_ref(request, value.strip(), "message_refs", "refs")
    text = str(raw).strip()
    if text.startswith("slack-message:"):
        parts = text.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            return parts[1], parts[2]
    raise ValidationError("Slack message_ref must be slack-message:<channel>:<ts>")


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


def _team_id(request: ActionConnectorRequest, body: Any | None = None) -> str | None:
    if isinstance(body, Mapping):
        value = body.get("team_id") or _nested(body, "team.id")
        if value is not None and str(value).strip():
            return str(value)
    config = credential_config(request)
    value = config.get("team_id") or config.get("provider_account_id")
    return str(value).strip() if value is not None and str(value).strip() else None


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
