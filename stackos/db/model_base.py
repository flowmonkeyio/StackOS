"""Shared SQLModel column/default helpers for StackOS DB model modules."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum


def _enum_column(
    enum_cls: type[enum.Enum],
    *,
    nullable: bool = False,
    name: str | None = None,
) -> Column[Any]:
    """Build a portable TEXT-backed Enum column.

    SQLite has no native ENUM type; using ``native_enum=False`` forces a
    CHECK-constrained TEXT column, which matches PLAN.md L383
    ("string columns, validated by pydantic"). ``values_callable``
    returns the canonical hyphenated value string (PLAN.md spellings
    like ``aborted-publish`` cannot be Python identifiers, so we keep
    the Python member ``ABORTED_PUBLISH`` while persisting the value).
    """
    return Column(
        SAEnum(
            enum_cls,
            native_enum=False,
            length=64,
            values_callable=lambda cls: [m.value for m in cls],
            name=name or f"ck_{enum_cls.__name__.lower()}",
        ),
        nullable=nullable,
    )


def _utcnow() -> datetime:
    """Naive UTC default for ``created_at`` / ``updated_at`` columns.

    SQLite stores datetimes as ISO-8601 text; we keep the value naive but
    explicitly UTC for consistency. Tests rely on a callable default so
    rows inserted in the same transaction do not share a frozen timestamp.
    """
    return datetime.now(tz=UTC).replace(tzinfo=None)


__all__ = ["_enum_column", "_utcnow"]
