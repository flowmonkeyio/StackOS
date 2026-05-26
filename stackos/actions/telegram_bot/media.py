"""Telegram media upload and artifact helpers."""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from stackos.actions.connectors import ActionConnectorRequest, ActionConnectorResult
from stackos.actions.provider_utils import result, send_json
from stackos.artifacts import redact_secret_text
from stackos.config import Settings
from stackos.repositories.base import ValidationError

from .constants import _MAX_PHOTO_BYTES
from .payloads import _copy_common_message_fields, _method_url
from .storage import _store_callback_buttons, _store_outbound_message


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
