"""Slack conversation, message, membership, and button storage."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest
from stackos.communications import communication_record_by_external_id
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository

from .constants import _RECOMMENDED_TEXT_CHARS
from .profile import _communication_profile_key, _credential_profile_key
from .refs import (
    _button_specs,
    _channel_display_name,
    _channel_from_body,
    _channel_id,
    _channel_id_from_obj,
    _channel_kind,
    _message_ref,
    _nested,
    _outbound_button_external_id,
    _surface_ref,
    _team_id,
    _thread_ref,
)


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
    control_metadata = (
        request.input_json.get("control_metadata")
        if isinstance(request.input_json.get("control_metadata"), Mapping)
        else {}
    )
    profile_key = _communication_profile_key(request)
    auth_profile_key = _credential_profile_key(request)
    source_scope = _source_button_scope(request, fallback_channel_ref=_surface_ref(channel))
    for button in _button_specs(blocks):
        action_id = str(button.get("action_id") or "")
        value = str(button.get("value") or "")
        metadata = _button_metadata(control_metadata, action_id=action_id, value=value)
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
                "control_action": metadata.get("action"),
                "control_payload": metadata.get("payload"),
                "control_metadata": metadata or None,
                "url_button": bool(button.get("url")),
                "status": "active",
                "source_agent_request_id": request.input_json.get("source_agent_request_id"),
                "allowed_user_refs": source_scope.get("allowed_user_refs", []),
                "allowed_channel_refs": source_scope.get("allowed_channel_refs", []),
            },
            provenance_json={"source": "slack-bot-action"},
        )


def _store_reaction_add(
    request: ActionConnectorRequest,
    sent_payload: Mapping[str, Any],
) -> None:
    if request.session is None:
        return
    channel = str(sent_payload.get("channel") or "")
    timestamp = str(sent_payload.get("timestamp") or "")
    reaction_name = str(sent_payload.get("name") or "")
    if not channel or not timestamp or not reaction_name:
        return
    profile_key = _communication_profile_key(request)
    auth_profile_key = _credential_profile_key(request)
    message_ref = _message_ref(channel, timestamp)
    digest = _reaction_digest(message_ref=message_ref, reaction_name=reaction_name)
    ResourceRepository(request.session).upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
        external_id=f"slack-reaction:{profile_key}:{digest}",
        title=f"Slack reaction {reaction_name}",
        data_json={
            "provider_key": "slack-bot",
            "profile_key": profile_key,
            "auth_profile_key": auth_profile_key,
            "interaction_type": "reaction_added",
            "surface_ref": _surface_ref(channel),
            "channel_ref": _surface_ref(channel),
            "message_ref": message_ref,
            "reaction_name": reaction_name,
            "status": "sent",
            "action_ref": request.action_ref,
        },
        provenance_json={"source": "slack-bot-action"},
    )


def _store_file_upload(
    request: ActionConnectorRequest,
    provider_body: Any,
    sent_payload: Mapping[str, Any],
) -> None:
    if request.session is None:
        return
    channel = str(sent_payload.get("channel_id") or sent_payload.get("channel") or "")
    if not channel:
        return
    profile_key = _communication_profile_key(request)
    auth_profile_key = _credential_profile_key(request)
    team_id = _team_id(request, provider_body)
    thread_ts = str(sent_payload.get("thread_ts") or "") or _first_file_share_ts(
        provider_body,
        channel=channel,
    )
    message_ts = _first_file_share_ts(provider_body, channel=channel)
    files = _uploaded_file_refs(provider_body, sent_payload)
    digest = _file_upload_digest(channel=channel, file_refs=[item["file_ref"] for item in files])
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
    message_ref = _message_ref(channel, message_ts) if message_ts else None
    resources.upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=(
            f"slack-message:{profile_key}:{channel}:{message_ts}"
            if message_ts
            else f"slack-file-upload:{profile_key}:{digest}"
        ),
        title="Slack outbound file upload",
        data_json={
            "provider_key": "slack-bot",
            "profile_key": profile_key,
            "auth_profile_key": auth_profile_key,
            "team_id": team_id,
            "direction": "outbound",
            "surface_ref": _surface_ref(channel),
            "channel_ref": _surface_ref(channel),
            "thread_ref": _thread_ref(channel, thread_ts) if thread_ts else None,
            "message_ref": message_ref,
            "provider_message_ts": message_ts or None,
            "content_type": "file_upload",
            "text_preview": str(sent_payload.get("initial_comment") or "")[
                :_RECOMMENDED_TEXT_CHARS
            ],
            "attachments": files,
            "transport_status": "accepted",
            "attention_status": "sent",
            "source_agent_request_id": request.input_json.get("source_agent_request_id"),
            "action_ref": request.action_ref,
        },
        provenance_json={"source": "slack-bot-action"},
    )


def _mark_message_deleted(
    request: ActionConnectorRequest,
    sent_payload: Mapping[str, Any],
) -> None:
    if request.session is None:
        return
    channel = str(sent_payload.get("channel") or "")
    timestamp = str(sent_payload.get("ts") or "")
    if not channel or not timestamp:
        return
    profile_key = _communication_profile_key(request)
    external_id = f"slack-message:{profile_key}:{channel}:{timestamp}"
    record = communication_record_by_external_id(
        request.session,
        project_id=request.project_id,
        resource_key="communication-message",
        external_id=external_id,
    )
    if record is None:
        return
    data = dict(record.data_json or {})
    data.update(
        {
            "transport_status": "deleted",
            "attention_status": "deleted",
            "deleted_at": datetime.now(tz=UTC).isoformat(),
            "deleted_by_action_ref": request.action_ref,
        }
    )
    record.data_json = data
    request.session.add(record)
    request.session.commit()


def _reaction_digest(*, message_ref: str, reaction_name: str) -> str:
    return hashlib.sha256(f"{message_ref}\0{reaction_name}".encode()).hexdigest()[:24]


def _file_upload_digest(*, channel: str, file_refs: list[str]) -> str:
    return hashlib.sha256(f"{channel}\0{','.join(file_refs)}".encode()).hexdigest()[:24]


def _uploaded_file_refs(
    provider_body: Any, sent_payload: Mapping[str, Any]
) -> list[dict[str, Any]]:
    sent_files = sent_payload.get("files")
    sent_items = sent_files if isinstance(sent_files, list) else []
    sent_by_id = {
        str(item.get("id")): item
        for item in sent_items
        if isinstance(item, Mapping) and item.get("id")
    }
    raw_files = provider_body.get("files") if isinstance(provider_body, Mapping) else None
    provider_files = raw_files if isinstance(raw_files, list) else []
    refs: list[dict[str, Any]] = []
    for item in provider_files or list(sent_by_id.values()):
        if not isinstance(item, Mapping):
            continue
        file_id = str(item.get("id") or "")
        if not file_id:
            continue
        sent = sent_by_id.get(file_id, {})
        refs.append(
            {
                "type": "file",
                "file_ref": f"slack-file:{file_id}",
                "file_id": file_id,
                "filename": item.get("name") or sent.get("filename"),
                "title": item.get("title") or sent.get("title"),
                "mime_type": item.get("mimetype") or sent.get("mime_type"),
                "size_bytes": item.get("size") or sent.get("size_bytes"),
            }
        )
    return refs


def _first_file_share_ts(provider_body: Any, *, channel: str) -> str | None:
    if not isinstance(provider_body, Mapping):
        return None
    raw_files = provider_body.get("files")
    if not isinstance(raw_files, list):
        return None
    for item in raw_files:
        if not isinstance(item, Mapping):
            continue
        shares = item.get("shares")
        if not isinstance(shares, Mapping):
            continue
        for visibility in ("public", "private"):
            by_channel = shares.get(visibility)
            if not isinstance(by_channel, Mapping):
                continue
            entries = by_channel.get(channel)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, Mapping) and entry.get("ts"):
                    return str(entry["ts"])
    return None


def _button_metadata(
    control_metadata: Any,
    *,
    action_id: str,
    value: str,
) -> dict[str, Any]:
    if not isinstance(control_metadata, Mapping):
        return {}
    for key in (value, action_id):
        item = control_metadata.get(key)
        if isinstance(item, Mapping):
            return dict(item)
    return {}


def _source_button_scope(
    request: ActionConnectorRequest,
    *,
    fallback_channel_ref: str,
) -> dict[str, list[str]]:
    scope: dict[str, list[str]] = {"allowed_channel_refs": [fallback_channel_ref]}
    if request.session is None:
        return scope
    request_id = request.input_json.get("source_agent_request_id")
    if not isinstance(request_id, int) or isinstance(request_id, bool):
        return scope
    source = AgentRequestRepository(request.session).get(
        project_id=request.project_id,
        request_id=request_id,
    )
    metadata = source.metadata_json or {}
    invoker_ref = metadata.get("invoker_ref")
    if isinstance(invoker_ref, str) and invoker_ref:
        scope["allowed_user_refs"] = [invoker_ref]
    surface_ref = metadata.get("surface_ref") or metadata.get("channel_ref")
    if isinstance(surface_ref, str) and surface_ref:
        scope["allowed_channel_refs"] = [surface_ref]
    return scope


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
