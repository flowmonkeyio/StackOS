"""Shared constants and helpers for tracker repository modules."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from stackos.artifacts import redact_secret_text
from stackos.db.models import TrackerItemStatus

DEFAULT_TRACKER_KEY = "default"
DEFAULT_LANES: tuple[tuple[str, str], ...] = (
    ("planning", "Planning"),
    ("implementation", "Implementation"),
    ("verification", "Verification"),
    ("done", "Done"),
)
DEFAULT_PRIORITIES: tuple[tuple[str, str, int], ...] = (
    ("p0", "P0", 0),
    ("p1", "P1", 10),
    ("p2", "P2", 20),
    ("p3", "P3", 30),
)
TERMINAL_TICKET_STATUSES = {
    TrackerItemStatus.COMPLETE,
    TrackerItemStatus.DEFERRED,
}
DEPENDENCY_PATCH_FIELDS = {
    "dependency_keys",
    "add_dependency_keys",
    "remove_dependency_keys",
}


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _clean_text(value: str | None) -> str:
    return redact_secret_text(str(value or "")).strip()


def _slug(value: str, *, fallback: str = "item", max_length: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length].strip("-") or fallback


def _status_value(value: TrackerItemStatus | str) -> TrackerItemStatus:
    if isinstance(value, TrackerItemStatus):
        return value
    return TrackerItemStatus(value)


def _required_id(value: int | None, entity: str) -> int:
    if value is None:
        raise RuntimeError(f"{entity} id is required after the row has been flushed")
    return value


__all__ = [
    "DEFAULT_LANES",
    "DEFAULT_PRIORITIES",
    "DEFAULT_TRACKER_KEY",
    "DEPENDENCY_PATCH_FIELDS",
    "TERMINAL_TICKET_STATUSES",
    "_clean_text",
    "_jsonable",
    "_required_id",
    "_slug",
    "_status_value",
    "_utcnow",
]
