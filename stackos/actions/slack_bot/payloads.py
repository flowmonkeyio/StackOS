"""Slack Web API request payload builders."""

from __future__ import annotations

from typing import Any

from stackos.actions.connectors import ActionConnectorRequest

from .refs import _channel_id, _message_parts, _thread_ts, _user_id


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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


def _conversation_history_params(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    params: dict[str, Any] = {
        "channel": _channel_id(request, payload.get("channel_ref") or payload.get("surface_ref")),
        "limit": payload.get("limit", 20),
    }
    for key in ("cursor", "latest", "oldest"):
        if payload.get(key):
            params[key] = payload[key]
    if "inclusive" in payload:
        params["inclusive"] = payload["inclusive"]
    return params


def _reaction_add_payload(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    channel, timestamp = _message_parts(request, payload.get("message_ref"))
    if payload.get("channel_ref") or payload.get("surface_ref"):
        channel = _channel_id(request, payload.get("channel_ref") or payload.get("surface_ref"))
    return {
        "channel": channel,
        "timestamp": timestamp,
        "name": payload["name"],
    }


def _message_delete_payload(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    channel, timestamp = _message_parts(request, payload.get("message_ref"))
    if payload.get("channel_ref") or payload.get("surface_ref"):
        channel = _channel_id(request, payload.get("channel_ref") or payload.get("surface_ref"))
    return {
        "channel": channel,
        "ts": timestamp,
    }
