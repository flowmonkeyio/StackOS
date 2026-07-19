"""Pure redaction and receipt summaries for browser operations."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

_SAFE_INPUT_FIELD_NAMES = frozenset(
    {
        "button",
        "click_count",
        "delay_ms",
        "full_page",
        "key",
        "selector",
        "state",
        "timeout_ms",
        "wait_until",
    }
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"data", "blob", "javascript"}:
        return f"{parsed.scheme}:<redacted>"
    if parsed.scheme in {"about", "file"}:
        return f"{parsed.scheme}:<redacted>" if parsed.scheme == "file" else value
    if parsed.scheme and parsed.netloc:
        redacted = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            redacted += "?<redacted>"
        if parsed.fragment:
            redacted += "#<redacted>"
        return redacted
    return value[:240]


def target_origin(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _value_summary(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, str):
        return {"type": "str", "length": len(value), "sha256": _digest(value)}
    if isinstance(value, bool):
        return {"type": "bool"}
    if isinstance(value, int | float):
        return {"type": type(value).__name__}
    if isinstance(value, list | tuple):
        return {"type": "array", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "key_count": len(value)}
    return {"type": type(value).__name__, "repr": repr(value)[:120]}


def result_summary(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "method": result.method,
        "status": result.status,
        "page_ref": result.page_ref,
    }
    if result.url is not None:
        out["url"] = redact_url(result.url)
    if result.title is not None:
        out["title_summary"] = _value_summary(result.title)
    if result.value is not None:
        out["value_summary"] = _value_summary(result.value)
    if result.page_refs is not None:
        out["page_refs"] = result.page_refs
    return out


def failure_summary(
    *,
    method: str,
    page_ref: str | None,
    url: str | None,
    exc: Exception,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "method": method,
        "status": "failed",
        "error_type": type(exc).__name__,
    }
    if page_ref is not None:
        out["page_ref"] = page_ref
    if url is not None:
        out["url"] = redact_url(url)
    return out


def error_summary(exc: Exception) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: message_sha256={_digest(message)} length={len(message)}"


def call_summary(
    method: str,
    arguments: dict[str, Any],
    args: list[Any] | None,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"method": method}
    if args is not None:
        summary["arg_count"] = len(args)
    if kwargs is not None:
        summary["kwarg_keys"] = sorted(str(key) for key in kwargs)
    for key, value in arguments.items():
        if key in {"script", "handler", "value", "text"} and isinstance(value, str):
            summary[f"{key}_length"] = len(value)
            summary[f"{key}_sha256"] = _digest(value)
        elif key == "url" and isinstance(value, str):
            summary[key] = redact_url(value)
        elif key == "arg":
            summary["arg_summary"] = _value_summary(value)
        elif key in _SAFE_INPUT_FIELD_NAMES:
            summary[key] = value
        else:
            summary[f"{key}_summary"] = _value_summary(value)
    return summary


__all__ = [
    "call_summary",
    "error_summary",
    "failure_summary",
    "redact_url",
    "result_summary",
    "target_origin",
]
