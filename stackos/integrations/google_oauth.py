"""Shared Google OAuth helpers for daemon-side provider credentials."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from stackos.artifacts import redact_secrets
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


def parse_google_oauth_payload(payload: bytes, *, provider: str) -> dict[str, Any]:
    """Decode a Google OAuth credential payload without exposing secrets."""
    text = payload.decode("utf-8").strip()
    if not text:
        raise ValidationError(f"{provider} credential is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"access_token": text}
    if not isinstance(parsed, dict):
        raise ValidationError(f"{provider} credential payload must be a JSON object")
    return parsed


async def google_access_token(
    payload: Mapping[str, Any],
    *,
    provider: str,
    http: httpx.AsyncClient | None = None,
) -> str:
    """Return an access token from a direct token or OAuth refresh-token payload."""
    access_token = payload.get("access_token")
    if isinstance(access_token, str) and access_token.strip():
        return access_token.strip()
    raw_value = payload.get("value")
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value.strip()

    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")
    refresh_token = payload.get("refresh_token")
    missing = [
        key
        for key, value in {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValidationError(
            f"{provider} credential missing access_token or refresh-token fields: "
            + ", ".join(missing)
        )

    owns_client = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "client_id": str(client_id),
                "client_secret": str(client_secret),
                "refresh_token": str(refresh_token),
                "grant_type": "refresh_token",
            },
        )
    finally:
        if owns_client:
            await client.aclose()
    if response.status_code >= 400:
        provider_error = _token_provider_error(response)
        if response.status_code == 429:
            raise RateLimitedError(
                f"{provider} token refresh failed with status {response.status_code}",
                data={
                    "vendor": provider,
                    "op": "oauth.token.refresh",
                    "status": response.status_code,
                    "provider_error": provider_error,
                },
            )
        raise IntegrationDownError(
            f"{provider} token refresh failed with status {response.status_code}",
            data={
                "vendor": provider,
                "op": "oauth.token.refresh",
                "status": response.status_code,
                "provider_error": provider_error,
            },
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise ValidationError(f"{provider} token refresh returned non-JSON response") from exc
    refreshed = data.get("access_token")
    if not isinstance(refreshed, str) or not refreshed.strip():
        raise ValidationError(f"{provider} token refresh response missing access_token")
    return refreshed.strip()


def _token_provider_error(response: httpx.Response) -> Any:
    try:
        body: Any = response.json()
    except ValueError:
        body = {"message": response.text[:500]}
    return redact_secrets(body)


async def google_bearer_headers(
    payload: Mapping[str, Any],
    *,
    provider: str,
    http: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    token = await google_access_token(payload, provider=provider, http=http)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


__all__ = [
    "GOOGLE_OAUTH_TOKEN_URL",
    "google_access_token",
    "google_bearer_headers",
    "parse_google_oauth_payload",
]
