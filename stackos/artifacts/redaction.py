"""Redaction helpers for agent-visible artifact metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_KEY_PARTS = (
    "service_account_json",
    "assertion",
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
    r"(?i)(?P<prefix>(?<![\w-])(?P<key_quote>[\"']?)(?P<key>[\w-]*(?:"
    r"access[_-]?token|api[_-]?key|apikey|authorization|client[_-]?secret|"
    r"service[_-]?account[_-]?json|assertion|credential|password|private[_-]?key|"
    r"refresh[_-]?token|secret|token))(?P<key_end_quote>[\"']?)\s*(?P<separator>[:=])\s*"
    r"(?P<value_quote>[\"']?))(?!bearer\b)([^\"'\s,;}&]+)"
)
_AUTH_BEARER_TEXT_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;}&]+")
_BEARER_TEXT_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_TELEGRAM_BOT_URL_RE = re.compile(r"(?i)(/bot)\d{5,}:[A-Za-z0-9_-]+(?=/)")
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----",
    re.DOTALL,
)
_SIGNED_URL_PARAM_RE = re.compile(
    r"(?i)([?&](?:x-amz-[^=&\s\"']+|x-goog-[^=&\s\"']+|x-oss-[^=&\s\"']+|"
    r"signature|sig|expires|expiresat|policy|security-token|token)=)[^&#\s\"']+"
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if (
        normalized in {"credential_ref", "credential_refs", "credential_id", "credential_ids"}
        or normalized.endswith("_credential_ref")
        or normalized.endswith("_credential_refs")
    ):
        # StackOS credential refs are intentionally opaque, non-secret public
        # identities. They are safe selectors; provider credential material is
        # never embedded in them.
        return False
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
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def redact_secret_text(value: str) -> str:
    """Redact secret-like assignments inside vendor-controlled text."""

    def redact_assignment(match: re.Match[str]) -> str:
        # Intuit's exact public revision field is not credential material.
        # Complete-key matching keeps prefixed secret names protected.
        if match["key"] == "SyncToken":
            return match[0]
        if match["separator"] == ":" and not (
            match["key_quote"] or match["key_end_quote"] or match["value_quote"]
        ):
            # Bare inline colons in sentences are ambiguous. Require a line/header
            # or explicit object/log delimiter; equals, quotes and bearer syntax
            # remain credential evidence anywhere, even for one-character values.
            before = match.string[: match.start()].rsplit("\n", 1)[-1].rstrip()
            if before and before[-1] not in "{[(,;]":
                return match[0]
        return f"{match['prefix']}[redacted]"

    redacted = _PEM_PRIVATE_KEY_RE.sub("[redacted private key]", value)
    redacted = _AUTH_BEARER_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    redacted = _SIGNED_URL_PARAM_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    redacted = _SECRET_TEXT_RE.sub(redact_assignment, redacted)
    redacted = _TELEGRAM_BOT_URL_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    return _BEARER_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)


__all__ = ["redact_secret_text", "redact_secrets"]
