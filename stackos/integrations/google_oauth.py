"""Google bearer helpers for access tokens already resolved by the daemon."""

from __future__ import annotations

import json
from typing import Any

from stackos.repositories.base import ValidationError


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


def google_access_token(payload: dict[str, Any], *, provider: str) -> str:
    """Require an access token produced or renewed by ``AuthRepository``."""
    access_token = payload.get("access_token")
    if isinstance(access_token, str) and access_token.strip():
        return access_token.strip()
    raw_value = payload.get("value")
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value.strip()

    raise ValidationError(
        f"{provider} credential missing access_token; reconnect or renew the credential"
    )


def google_bearer_headers(payload: dict[str, Any], *, provider: str) -> dict[str, str]:
    token = google_access_token(payload, provider=provider)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


__all__ = [
    "google_access_token",
    "google_bearer_headers",
    "parse_google_oauth_payload",
]
