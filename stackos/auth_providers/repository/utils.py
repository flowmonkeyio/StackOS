"""Shared helpers for auth-provider repository modules."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def credential_ref() -> str:
    return f"cred_{secrets.token_urlsafe(18)}"
