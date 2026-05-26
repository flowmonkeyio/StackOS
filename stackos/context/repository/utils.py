"""Shared context repository helpers and field policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.repositories.base import ValidationError

DEFAULT_CONTEXT_LIMIT = 20
MAX_CONTEXT_LIMIT = 50
_FETCH_MULTIPLIER = 5


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _normalise_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_CONTEXT_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", data={"limit": limit})
    if limit > MAX_CONTEXT_LIMIT:
        raise ValidationError(
            f"limit must be <= {MAX_CONTEXT_LIMIT}",
            data={"limit": limit, "max": MAX_CONTEXT_LIMIT},
        )
    return limit


def _scalar_count(session: Session, statement: Any) -> int:
    raw = session.exec(statement).one()
    if isinstance(raw, tuple):
        return int(raw[0])
    try:
        return int(raw[0])  # type: ignore[index]
    except (KeyError, TypeError):
        pass
    return int(raw)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


def _safe_text(value: str | None) -> str | None:
    return redact_secret_text(value) if value is not None else None


def _safe_json(value: Any) -> Any:
    return redact_secrets(value)


def _normalise_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    return sorted({str(tag).strip() for tag in tags if str(tag).strip()})


def _has_all_tags(row_tags: list[str] | None, required: list[str] | None) -> bool:
    if not required:
        return True
    return set(required) <= set(row_tags or [])


_DEFAULT_FIELDS: dict[str, tuple[str, ...]] = {
    "runs": ("kind", "status", "last_step", "metadata_json"),
    "events": ("event_type", "title", "summary", "tags", "metadata_json"),
    "index": ("source_type", "source_id", "title", "summary", "domain", "status", "tags"),
    "snapshots": ("name", "query_json", "selected_sources_json", "summary_json"),
    "learnings": ("statement", "domain", "confidence", "status", "review_state", "tags"),
    "experiments": ("name", "domain", "hypothesis", "status", "metric_targets_json", "variants"),
    "decisions": ("title", "decision", "rationale", "status", "tags"),
    "metrics": ("metric_key", "metric_value", "dimensions_json", "captured_at"),
}

_FIELD_MAP: dict[str, frozenset[str]] = {
    "runs": frozenset(
        {
            "id",
            "kind",
            "status",
            "started_at",
            "ended_at",
            "last_step",
            "last_step_at",
            "metadata_json",
        }
    ),
    "events": frozenset(_DEFAULT_FIELDS["events"]) | {"occurred_at", "source_type", "source_id"},
    "index": frozenset(_DEFAULT_FIELDS["index"]) | {"occurred_at", "metadata_json"},
    "snapshots": frozenset(_DEFAULT_FIELDS["snapshots"]) | {"run_id", "metadata_json"},
    "learnings": frozenset(_DEFAULT_FIELDS["learnings"])
    | {
        "applies_to_json",
        "evidence_json",
        "metadata_json",
        "created_by",
        "source_snapshot_id",
        "supersedes_learning_id",
    },
    "experiments": frozenset(_DEFAULT_FIELDS["experiments"])
    | {
        "key",
        "linked_template_ids_json",
        "linked_run_ids_json",
        "decision_policy_json",
        "metadata_json",
    },
    "decisions": frozenset(_DEFAULT_FIELDS["decisions"])
    | {"experiment_id", "run_id", "evidence_json", "metadata_json", "decided_by"},
    "metrics": frozenset(_DEFAULT_FIELDS["metrics"])
    | {"source_type", "source_id", "metadata_json"},
}
