"""Slack file upload helpers."""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from stackos.actions.connectors import ActionConnectorRequest, ActionConnectorResult
from stackos.config import Settings
from stackos.repositories.base import ValidationError

from .http import _slack_api, _slack_upload_bytes
from .refs import _channel_id, _message_ref, _nested, _surface_ref, _thread_ref, _thread_ts
from .results import _metadata
from .storage import _store_file_upload


@dataclass(frozen=True)
class _SlackUploadFile:
    artifact_ref: str
    path: Path
    filename: str
    title: str
    mime_type: str
    size_bytes: int


async def _upload_files(request: ActionConnectorRequest) -> ActionConnectorResult:
    payload = request.input_json
    channel = _channel_id(request, payload.get("channel_ref") or payload.get("surface_ref"))
    resolved_thread_ts = _thread_ts(request, payload.get("thread_ref"))
    files = _file_items(request)
    uploaded: list[dict[str, Any]] = []
    last_upload_headers: httpx.Headers | None = None

    for item in files:
        _status, body, _headers = await _slack_api(
            request,
            "POST",
            "files.getUploadURLExternal",
            form_body={"filename": item.filename, "length": str(item.size_bytes)},
        )
        data = body if isinstance(body, Mapping) else {}
        upload_url = data.get("upload_url")
        file_id = data.get("file_id")
        if not isinstance(upload_url, str) or not upload_url.strip():
            raise ValidationError("Slack files.getUploadURLExternal did not return upload_url")
        if not isinstance(file_id, str) or not file_id.strip():
            raise ValidationError("Slack files.getUploadURLExternal did not return file_id")
        content = item.path.read_bytes()
        _status, _body, last_upload_headers = await _slack_upload_bytes(
            request,
            upload_url=upload_url,
            content=content,
            mime_type=item.mime_type,
        )
        uploaded.append(
            {
                "id": file_id,
                "title": item.title,
                "filename": item.filename,
                "artifact_ref": item.artifact_ref,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "upload_status_code": _status,
            }
        )
    complete_payload: dict[str, Any] = {
        "files": [{"id": item["id"], "title": item["title"]} for item in uploaded],
        "channel_id": channel,
    }
    if _has_text(payload.get("initial_comment")):
        complete_payload["initial_comment"] = str(payload["initial_comment"])
    if resolved_thread_ts is not None:
        complete_payload["thread_ts"] = resolved_thread_ts
    status, body, headers = await _slack_api(
        request,
        "POST",
        "files.completeUploadExternal",
        json_body=complete_payload,
    )
    sent_payload = {
        **complete_payload,
        "channel": channel,
        "files": uploaded,
    }
    _store_file_upload(request, body, sent_payload)
    deleted_artifact_refs = (
        _delete_uploaded_artifacts(request, files)
        if payload.get("delete_after_upload") is True
        else []
    )
    return _file_upload_result(
        request,
        status=status,
        body=body,
        headers=headers,
        sent_payload=sent_payload,
        deleted_artifact_refs=deleted_artifact_refs,
        upload_headers=last_upload_headers,
    )


def _file_items(request: ActionConnectorRequest) -> list[_SlackUploadFile]:
    payload = request.input_json
    if isinstance(payload.get("file"), Mapping):
        raw_items = [dict(payload["file"])]
        for key in ("filename", "title", "mime_type"):
            if key in payload and key not in raw_items[0]:
                raw_items[0][key] = payload[key]
    else:
        raw_items = [dict(item) for item in payload.get("files") or [] if isinstance(item, Mapping)]
    items: list[_SlackUploadFile] = []
    for raw in raw_items:
        artifact_ref = str(raw.get("artifact_ref") or "").strip()
        path = _artifact_path(request, artifact_ref)
        filename = _safe_filename(raw.get("filename") or path.name)
        title = str(raw.get("title") or raw.get("caption") or filename).strip() or filename
        mime_type = str(
            raw.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        items.append(
            _SlackUploadFile(
                artifact_ref=artifact_ref,
                path=path,
                filename=filename,
                title=title,
                mime_type=mime_type,
                size_bytes=path.stat().st_size,
            )
        )
    if not items:
        raise ValidationError("Slack file.upload requires at least one file artifact")
    return items


def _artifact_path(request: ActionConnectorRequest, artifact_ref: str) -> Path:
    if artifact_ref.startswith("/generated-assets/"):
        relative = artifact_ref.removeprefix("/generated-assets/")
    elif artifact_ref.startswith("generated-assets/"):
        relative = artifact_ref.removeprefix("generated-assets/")
    else:
        raise ValidationError(
            "file.artifact_ref must be a generated asset URI such as "
            "/generated-assets/communication-media/image.png"
        )
    root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ValidationError("file.artifact_ref must stay inside generated assets")
    if not path.is_file():
        raise ValidationError("file.artifact_ref does not point to an existing file")
    return path


def _safe_filename(value: Any) -> str:
    raw = Path(str(value or "")).name.strip()
    return raw or "stackos-upload.bin"


def _delete_uploaded_artifacts(
    request: ActionConnectorRequest,
    files: list[_SlackUploadFile],
) -> list[str]:
    root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    deleted: list[str] = []
    seen: set[Path] = set()
    for item in files:
        path = item.path.resolve()
        if path in seen:
            continue
        seen.add(path)
        if root != path and root not in path.parents:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted.append(item.artifact_ref)
    return deleted


def _file_upload_result(
    request: ActionConnectorRequest,
    *,
    status: int,
    body: Any,
    headers: httpx.Headers,
    sent_payload: Mapping[str, Any],
    deleted_artifact_refs: list[str],
    upload_headers: httpx.Headers | None,
) -> ActionConnectorResult:
    channel = str(sent_payload.get("channel") or sent_payload.get("channel_id") or "")
    thread_ts = str(sent_payload.get("thread_ts") or "") or _first_file_share_ts(
        body,
        channel=channel,
    )
    message_ts = _first_file_share_ts(body, channel=channel)
    file_refs = _file_refs(body, sent_payload)
    metadata = _metadata("files.completeUploadExternal", request.operation, status, body, headers)
    if upload_headers is not None and upload_headers.get("x-request-id"):
        metadata["upload_request_id"] = upload_headers["x-request-id"]
    return ActionConnectorResult(
        output_json={
            "provider": "slack-bot",
            "operation": request.operation,
            "status": "sent",
            "channel_ref": _surface_ref(channel) if channel else None,
            "surface_ref": _surface_ref(channel) if channel else None,
            "thread_ref": _thread_ref(channel, thread_ts) if channel and thread_ts else None,
            "message_ref": _message_ref(channel, message_ts) if channel and message_ts else None,
            "file_ref": file_refs[0] if len(file_refs) == 1 else None,
            "file_refs": file_refs,
            "attachment_refs": file_refs,
            "deleted_artifact_refs": deleted_artifact_refs,
            "local_artifact_deleted": bool(deleted_artifact_refs),
        },
        metadata_json=metadata,
    )


def _file_refs(provider_body: Any, sent_payload: Mapping[str, Any]) -> list[str]:
    raw_files = provider_body.get("files") if isinstance(provider_body, Mapping) else None
    provider_files = raw_files if isinstance(raw_files, list) else []
    ids: list[str] = []
    for item in provider_files:
        if isinstance(item, Mapping) and item.get("id"):
            ids.append(f"slack-file:{item['id']}")
    if ids:
        return ids
    sent_files = sent_payload.get("files")
    if not isinstance(sent_files, list):
        return []
    return [
        f"slack-file:{item['id']}"
        for item in sent_files
        if isinstance(item, Mapping) and item.get("id")
    ]


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
    return str(_nested(provider_body, "file.shares.public.0.ts") or "") or None


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
