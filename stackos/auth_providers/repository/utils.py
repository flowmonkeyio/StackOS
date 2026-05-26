"""Shared helpers for auth-provider repository modules."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

_PROFILE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,119}$")
_TELEGRAM_BOT_TOKEN_RE = re.compile(r"^(?P<bot_id>\d+):\S+$")
_PROJECT_SCOPED_PROVIDER_KEYS = frozenset({"telegram-bot", "slack-bot"})


def utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def credential_ref() -> str:
    return f"cred_{secrets.token_urlsafe(18)}"


def telegram_bot_id_from_token(token: str | None) -> str | None:
    if token is None:
        return None
    match = _TELEGRAM_BOT_TOKEN_RE.match(token.strip())
    return match.group("bot_id") if match else None


def is_valid_profile_key(value: str) -> bool:
    return _PROFILE_KEY_RE.match(value) is not None
