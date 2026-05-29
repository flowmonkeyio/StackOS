"""Slack Web API HTTP transport."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.provider_utils import credential_config, credential_value
from stackos.artifacts import redact_secret_text
from stackos.repositories.base import ValidationError

from .constants import _BASE_URL, _SLACK_TOKEN_RE


async def _slack_api(
    request: ActionConnectorRequest,
    method: str,
    api_method: str,
    *,
    json_body: Mapping[str, Any] | None = None,
    form_body: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
) -> tuple[int, Any, httpx.Headers]:
    if json_body is not None and form_body is not None:
        raise ValidationError("Slack API request cannot use both json_body and form_body")
    url = f"{_api_base_url(request)}/{api_method}"
    token = credential_value(request, "bot_token", "access_token", "token")
    headers = {"Authorization": f"Bearer {token}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.request(
            method,
            url,
            headers=headers,
            json=dict(json_body or {}) if json_body is not None else None,
            data=dict(form_body or {}) if form_body is not None else None,
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
        raise ValidationError(_slack_error_message(api_method, body))
    return response.status_code, body, response.headers


async def _slack_upload_bytes(
    request: ActionConnectorRequest,
    *,
    upload_url: str,
    content: bytes,
    mime_type: str,
) -> tuple[int, str, httpx.Headers]:
    headers = {"Content-Type": mime_type or "application/octet-stream"}
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.post(upload_url, headers=headers, content=content)
    if response.status_code >= 400:
        raise ValidationError(
            _redact_slack_text(
                f"Slack upload URL returned status {response.status_code}: {response.text[:500]}"
            )
        )
    return response.status_code, response.text, response.headers


def _api_base_url(request: ActionConnectorRequest) -> str:
    config = credential_config(request)
    return str(config.get("api_base_url") or _BASE_URL).rstrip("/")


def _redact_slack_text(value: str) -> str:
    return _SLACK_TOKEN_RE.sub("[redacted]", redact_secret_text(value))


def _slack_error_message(api_method: str, body: Mapping[str, Any]) -> str:
    error = str(body.get("error") or "unknown_error")
    details: list[str] = []
    for key in ("needed", "provided"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            details.append(f"{key}={value.strip()}")
    metadata = body.get("response_metadata")
    messages = metadata.get("messages") if isinstance(metadata, Mapping) else None
    if isinstance(messages, list):
        details.extend(str(item) for item in messages if isinstance(item, str) and item.strip())
    suffix = f" ({', '.join(details)})" if details else ""
    return _redact_slack_text(f"Slack {api_method} returned error {error}{suffix}")
