"""Telegram media upload and artifact helpers."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from stackos.actions.connectors import ActionConnectorRequest, ActionConnectorResult
from stackos.actions.provider_utils import credential_config, credential_value, send_json
from stackos.artifacts import redact_secret_text
from stackos.config import Settings
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository

from .constants import (
    _BASE_URL,
    _MAX_DOCUMENT_BYTES,
    _MAX_FILE_DOWNLOAD_BYTES,
    _MAX_MEDIA_GROUP_ITEMS,
    _MAX_PHOTO_BYTES,
)
from .payloads import _copy_common_message_fields, _method_url
from .refs import _provider_chat_ref, _provider_message_ref
from .results import _telegram_result
from .storage import _store_callback_buttons, _store_outbound_message

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


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
        return _telegram_result(
            request,
            status_code=status,
            body=body,
            headers=headers,
            telegram_method="sendPhoto",
            metadata={"upload_mode": "remote"},
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
    return _telegram_result(
        request,
        status_code=response.status_code,
        body=body,
        headers=response.headers,
        telegram_method="sendPhoto",
        metadata={"upload_mode": "multipart"},
    )


async def _download_file(request: ActionConnectorRequest) -> ActionConnectorResult:
    payload = request.input_json
    file_id = str(payload["file_id"])
    status, body, headers = await send_json(
        method="POST",
        url=_method_url(request, "getFile"),
        json_body={"file_id": file_id},
        timeout_s=60.0,
    )
    data = body.get("result") if isinstance(body, Mapping) else None
    if not isinstance(data, Mapping):
        raise ValidationError("Telegram getFile did not return file metadata")
    file_path = data.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValidationError("Telegram getFile did not return file_path")
    size_from_provider = data.get("file_size")
    max_bytes = int(payload.get("max_bytes") or _MAX_FILE_DOWNLOAD_BYTES)
    if isinstance(size_from_provider, int) and size_from_provider > max_bytes:
        raise ValidationError(f"Telegram file exceeds max_bytes ({max_bytes})")
    download_url = _file_url(request, file_path)
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.get(download_url)
    if response.status_code >= 400:
        raise ValidationError(
            redact_secret_text(
                f"Telegram file download returned status {response.status_code}: "
                f"{response.text[:500]}"
            )
        )
    content = response.content
    if len(content) > max_bytes:
        raise ValidationError(f"Telegram file exceeds max_bytes ({max_bytes})")
    digest = hashlib.sha256(content).hexdigest()
    filename = _download_filename(
        payload.get("filename"),
        file_path=file_path,
        mime_type=payload.get("mime_type"),
        digest=digest,
    )
    relative = Path("communication-media") / "telegram" / digest[:16] / filename
    root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ValidationError("download path must stay inside generated assets")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    artifact_ref = f"/generated-assets/{relative.as_posix()}"
    artifact_id: int | None = None
    if request.session is not None:
        artifact = (
            ArtifactRepository(request.session)
            .create(
                project_id=request.project_id,
                plugin_slug="communications",
                kind="communication-media",
                uri=artifact_ref,
                name=filename,
                mime_type=str(payload.get("mime_type") or mimetypes.guess_type(filename)[0] or ""),
                size_bytes=len(content),
                metadata_json={
                    "provider_key": "telegram-bot",
                    "operation": request.operation,
                    "source_file_id": file_id,
                    "source_file_unique_id": data.get("file_unique_id"),
                    "source_message_ref": payload.get("source_message_ref"),
                },
                provenance_json={"source": "telegram-bot-action"},
            )
            .data
        )
        artifact_id = artifact.id
    return ActionConnectorResult(
        output_json={
            "provider": "telegram-bot",
            "operation": request.operation,
            "status": "downloaded",
            "artifact_ref": artifact_ref,
            "artifact_id": artifact_id,
            "filename": filename,
            "mime_type": payload.get("mime_type") or mimetypes.guess_type(filename)[0],
            "size_bytes": len(content),
            "source_file_id": file_id,
            "source_file_unique_id": data.get("file_unique_id"),
            "source_message_ref": payload.get("source_message_ref"),
            "delete_after_upload_recommended": True,
        },
        metadata_json={
            "vendor": "telegram-bot",
            "operation": request.operation,
            "telegram_method": "getFile",
            "status_code": status,
            "download_status_code": response.status_code,
            "content_length": len(content),
            "telegram_file_size": size_from_provider,
            "request_id": headers.get("x-request-id"),
        },
    )


async def _send_file(
    request: ActionConnectorRequest,
    chat_id: Any,
    profile: Mapping[str, Any],
) -> ActionConnectorResult:
    files = _upload_file_items(request)
    if len(files) == 1:
        return await _send_single_file(request, chat_id, profile, files[0])
    return await _send_media_group(request, chat_id, profile, files)


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
            "artifact_ref must be a generated asset URI such as "
            "/generated-assets/communication-media/image.webp"
        )
    root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ValidationError("artifact_ref must stay inside generated assets")
    if not path.is_file():
        raise ValidationError("artifact_ref does not point to an existing file")
    return path


async def _send_single_file(
    request: ActionConnectorRequest,
    chat_id: Any,
    profile: Mapping[str, Any],
    item: dict[str, Any],
) -> ActionConnectorResult:
    media_kind = "photo" if item["type"] == "image" else "document"
    method = "sendPhoto" if media_kind == "photo" else "sendDocument"
    base_body: dict[str, Any] = {"chat_id": chat_id}
    caption = request.input_json.get("caption") or item.get("caption")
    if isinstance(caption, str) and caption.strip():
        base_body["caption"] = caption
    _copy_common_message_fields(request, profile, base_body)
    source = item["source"]
    url = _method_url(request, method)
    if source["kind"] != "artifact_ref":
        body_json = dict(base_body)
        body_json[media_kind] = source["value"]
        status, body, headers = await send_json(
            method="POST",
            url=url,
            json_body=body_json,
            timeout_s=60.0,
        )
        _store_outbound_message(request, profile, body, content_type=media_kind)
        _store_callback_buttons(request, profile, body)
        result = _telegram_result(
            request,
            status_code=status,
            body=body,
            headers=headers,
            telegram_method=method,
            metadata={"upload_mode": "remote"},
        )
        return _with_file_output(result, body, request, deleted_artifact_refs=[])

    path = source["path"]
    if media_kind == "photo" and path.stat().st_size > _MAX_PHOTO_BYTES:
        raise ValidationError("Telegram photo artifact must be at most 10 MB")
    if media_kind == "document" and path.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise ValidationError("Telegram document artifact must be at most 50 MB")
    async with httpx.AsyncClient(timeout=60.0) as http:
        with path.open("rb") as file_obj:
            response = await http.post(
                url,
                data={key: _form_value(value) for key, value in base_body.items()},
                files={media_kind: (item["filename"], file_obj, item["mime_type"])},
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
    _store_outbound_message(request, profile, body, content_type=media_kind)
    _store_callback_buttons(request, profile, body)
    deleted = (
        _delete_uploaded_artifacts(request, [item])
        if request.input_json.get("delete_after_upload") is True
        else []
    )
    result = _telegram_result(
        request,
        status_code=response.status_code,
        body=body,
        headers=response.headers,
        telegram_method=method,
        metadata={"upload_mode": "multipart"},
    )
    return _with_file_output(result, body, request, deleted_artifact_refs=deleted)


async def _send_media_group(
    request: ActionConnectorRequest,
    chat_id: Any,
    profile: Mapping[str, Any],
    items: list[dict[str, Any]],
) -> ActionConnectorResult:
    if len(items) > _MAX_MEDIA_GROUP_ITEMS:
        raise ValidationError(
            f"Telegram media groups support at most {_MAX_MEDIA_GROUP_ITEMS} files"
        )
    media_type = "photo" if all(item["type"] == "image" for item in items) else "document"
    body_fields: dict[str, Any] = {"chat_id": chat_id}
    _copy_common_message_fields(request, profile, body_fields)
    body_fields.pop("reply_markup", None)
    parse_mode = body_fields.pop("parse_mode", None)
    media: list[dict[str, Any]] = []
    local_files: dict[str, tuple[str, Any, str]] = {}
    opened_files: list[Any] = []
    try:
        for index, item in enumerate(items):
            source = item["source"]
            media_ref = source["value"]
            if source["kind"] == "artifact_ref":
                if source["path"].stat().st_size > _MAX_DOCUMENT_BYTES:
                    raise ValidationError("Telegram media group artifact must be at most 50 MB")
                field = f"file{index}"
                media_ref = f"attach://{field}"
                file_obj = source["path"].open("rb")
                opened_files.append(file_obj)
                local_files[field] = (item["filename"], file_obj, item["mime_type"])
            media_item: dict[str, Any] = {"type": media_type, "media": media_ref}
            caption = request.input_json.get("caption") or item.get("caption")
            if index == 0 and isinstance(caption, str) and caption.strip():
                media_item["caption"] = caption
                if parse_mode:
                    media_item["parse_mode"] = parse_mode
            media.append(media_item)
        body_fields["media"] = media
        async with httpx.AsyncClient(timeout=60.0) as http:
            response = await http.post(
                _method_url(request, "sendMediaGroup"),
                data={key: _form_value(value) for key, value in body_fields.items()},
                files=local_files or None,
            )
    finally:
        for file_obj in opened_files:
            file_obj.close()
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
    results = body.get("result") if isinstance(body, Mapping) else None
    if isinstance(results, list):
        for message in results:
            _store_outbound_message(
                request,
                profile,
                {"ok": True, "result": message},
                content_type="media_group",
            )
    deleted = (
        _delete_uploaded_artifacts(request, items)
        if request.input_json.get("delete_after_upload") is True
        else []
    )
    return _media_group_result(
        request,
        status_code=response.status_code,
        body=body,
        headers=response.headers,
        deleted_artifact_refs=deleted,
    )


def _upload_file_items(request: ActionConnectorRequest) -> list[dict[str, Any]]:
    payload = request.input_json
    raw_items = []
    if isinstance(payload.get("file"), Mapping):
        raw = dict(payload["file"])
        for key in ("filename", "title", "mime_type", "caption", "type"):
            if key in payload and key not in raw:
                raw[key] = payload[key]
        raw_items = [raw]
    elif isinstance(payload.get("files"), list):
        raw_items = [dict(item) for item in payload["files"] if isinstance(item, Mapping)]
    items = [_upload_file_item(request, item) for item in raw_items]
    if not items:
        raise ValidationError("Telegram file.upload requires at least one file")
    return items


def _upload_file_item(request: ActionConnectorRequest, raw: Mapping[str, Any]) -> dict[str, Any]:
    source_keys = [key for key in ("artifact_ref", "file_id", "url") if raw.get(key)]
    if len(source_keys) != 1:
        raise ValidationError("file must include exactly one of artifact_ref, file_id, url")
    source_key = source_keys[0]
    filename = _safe_filename(raw.get("filename") or raw.get("title") or "telegram-upload.bin")
    mime_type = str(
        raw.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )
    source: dict[str, Any] = {"kind": source_key, "value": raw[source_key]}
    if source_key == "artifact_ref":
        path = _artifact_path(request, str(raw[source_key]))
        filename = _safe_filename(raw.get("filename") or path.name)
        source["path"] = path
        source["value"] = str(raw[source_key])
        mime_type = str(raw.get("mime_type") or mimetypes.guess_type(filename)[0] or mime_type)
    return {
        "type": str(raw.get("type") or "file"),
        "filename": filename,
        "title": str(raw.get("title") or filename),
        "mime_type": mime_type,
        "caption": raw.get("caption"),
        "source": source,
    }


def _download_filename(
    value: Any,
    *,
    file_path: str,
    mime_type: Any,
    digest: str,
) -> str:
    raw_name = Path(str(value or Path(file_path).name or "telegram-file")).name
    stem = _SAFE_FILENAME_RE.sub("-", Path(raw_name).stem).strip(".-_") or "telegram-file"
    suffix = Path(raw_name).suffix
    if not suffix:
        suffix = mimetypes.guess_extension(str(mime_type or "")) or ""
    return f"{stem}-{digest[:12]}{suffix}"


def _safe_filename(value: Any) -> str:
    raw_name = Path(str(value or "")).name
    stem = _SAFE_FILENAME_RE.sub("-", Path(raw_name).stem).strip(".-_") or "telegram-upload"
    suffix = Path(raw_name).suffix
    return f"{stem}{suffix}" if suffix else f"{stem}.bin"


def _file_url(request: ActionConnectorRequest, file_path: str) -> str:
    config = credential_config(request)
    base = str(config.get("file_base_url") or config.get("api_base_url") or _BASE_URL).rstrip("/")
    token = credential_value(request, "bot_token", "token")
    return f"{base}/file/bot{quote(token, safe=':-_')}/{quote(file_path, safe='/')}"


def _delete_uploaded_artifacts(
    request: ActionConnectorRequest, items: list[dict[str, Any]]
) -> list[str]:
    root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    deleted: list[str] = []
    seen: set[Path] = set()
    for item in items:
        source = item.get("source")
        if not isinstance(source, Mapping) or source.get("kind") != "artifact_ref":
            continue
        path = source.get("path")
        if not isinstance(path, Path):
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if root != resolved and root not in resolved.parents:
            continue
        try:
            resolved.unlink()
        except FileNotFoundError:
            continue
        deleted.append(str(source.get("value") or ""))
    return deleted


def _with_file_output(
    result: ActionConnectorResult,
    body: Any,
    request: ActionConnectorRequest,
    *,
    deleted_artifact_refs: list[str],
) -> ActionConnectorResult:
    output = dict(result.output_json)
    file_refs = _telegram_file_refs(body)
    output.update(
        {
            "file_ref": file_refs[0] if len(file_refs) == 1 else None,
            "file_refs": file_refs,
            "attachment_refs": file_refs,
            "deleted_artifact_refs": deleted_artifact_refs,
            "local_artifact_deleted": bool(deleted_artifact_refs),
        }
    )
    thread_ref = request.input_json.get("thread_ref")
    if isinstance(thread_ref, str) and thread_ref:
        output["thread_ref"] = thread_ref
    return ActionConnectorResult(
        output_json=output,
        metadata_json=result.metadata_json,
        cost_cents=result.cost_cents,
    )


def _media_group_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str] | None,
    deleted_artifact_refs: list[str],
) -> ActionConnectorResult:
    results = body.get("result") if isinstance(body, Mapping) else None
    messages = results if isinstance(results, list) else []
    message_refs = [
        ref for message in messages if (ref := _provider_message_ref(message)) is not None
    ]
    chat_ref = None
    for message in messages:
        chat_ref = _provider_chat_ref(message)
        if chat_ref is not None:
            break
    file_refs = _telegram_file_refs(body)
    output = {
        "provider": "telegram-bot",
        "operation": request.operation,
        "status": "sent",
        "status_code": status_code,
        "body": body,
        "chat_ref": chat_ref,
        "channel_ref": chat_ref,
        "message_ref": message_refs[0] if message_refs else None,
        "message_refs": message_refs,
        "file_ref": file_refs[0] if len(file_refs) == 1 else None,
        "file_refs": file_refs,
        "attachment_refs": file_refs,
        "deleted_artifact_refs": deleted_artifact_refs,
        "local_artifact_deleted": bool(deleted_artifact_refs),
    }
    thread_ref = request.input_json.get("thread_ref")
    if isinstance(thread_ref, str) and thread_ref:
        output["thread_ref"] = thread_ref
    return ActionConnectorResult(
        output_json=output,
        metadata_json={
            "vendor": "telegram-bot",
            "operation": request.operation,
            "status_code": status_code,
            "telegram_method": "sendMediaGroup",
            "media_group_count": len(messages),
            "request_id": headers.get("x-request-id") if headers else None,
        },
    )


def _telegram_file_refs(body: Any) -> list[str]:
    result = body.get("result") if isinstance(body, Mapping) else None
    messages = result if isinstance(result, list) else [result]
    refs: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        for key in ("document", "video", "audio", "voice"):
            media = message.get(key)
            if isinstance(media, Mapping) and media.get("file_id"):
                refs.append(f"telegram-file:{media['file_id']}")
        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            candidates = [
                item for item in photo if isinstance(item, Mapping) and item.get("file_id")
            ]
            if candidates:
                refs.append(f"telegram-file:{candidates[-1]['file_id']}")
    return refs
