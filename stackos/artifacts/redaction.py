"""Redaction helpers for agent-visible artifact metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)([\"']?(?:access[_-]?token|api[_-]?key|apikey|authorization|client[_-]?secret|"
    r"credential|password|private[_-]?key|refresh[_-]?token|secret|token)[\"']?\s*[:=]\s*"
    r"[\"']?)(?!bearer\b)([^\"'\s,;}&]+)"
)
_AUTH_BEARER_TEXT_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;}&]+")
_BEARER_TEXT_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_TELEGRAM_BOT_URL_RE = re.compile(r"(?i)(/bot)\d{5,}:[A-Za-z0-9_-]+(?=/)")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def redact_secrets(value: Any) -> Any:
    """Return a deep copy with secret-like object keys redacted."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            redacted[key] = "[redacted]" if _is_sensitive_key(key) else redact_secrets(raw_value)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_secrets(item) for item in value]
    return value


def redact_secret_text(value: str) -> str:
    """Redact secret-like assignments inside vendor-controlled text."""
    redacted = _AUTH_BEARER_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", value)
    redacted = _SECRET_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    redacted = _TELEGRAM_BOT_URL_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    return _BEARER_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)


__all__ = ["redact_secret_text", "redact_secrets"]
