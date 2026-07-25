"""Shared helpers for auth-provider repository modules."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def credential_ref() -> str:
    return f"cred_{secrets.token_urlsafe(18)}"


def telegram_bot_id_from_token(token: str | None) -> str | None:
    if token is None:
        return None
    value = token.strip()
    bot_id, separator, secret = value.partition(":")
    return bot_id if separator and bot_id.isdigit() and secret else None
