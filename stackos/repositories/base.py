"""Shared utilities for the M1.B repository layer.

Lives at the bottom of every repo module's import graph. The four
exported abstractions are:

- ``RepositoryError`` + subclasses — typed, transport-agnostic
  exceptions. Each carries a JSON-RPC ``code`` per PLAN.md L745-L754
  (the M2/M3 transport adapters re-raise these as HTTP / JSON-RPC).
- ``Page[T]`` — pagination envelope (``items``, ``next_cursor``,
  ``total_estimate``) per PLAN.md L536-L538.
- ``Envelope[T]`` — mutating-method return wrapper per PLAN.md L758-L763
  (``data``, ``run_id``, ``project_id``).
- ``validate_transition`` — pure helper that consumes the per-table
  transition maps declared in ``stackos.db.models``.

Design notes:

- We do not import any concrete model here; the helpers stay generic
  and live at the bottom of the import graph so individual repository
  modules can import from this module without circular imports.
- Pagination is cursor-based on ``id ASC`` (stable, monotonic). Tests
  in ``tests/unit/test_base.py`` lock the math.
- Errors are subclasses of ``RepositoryError`` so one call site can
  ``except RepositoryError`` if it wants generic mapping.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.sql import Select
from sqlmodel import Session

# ---------------------------------------------------------------------------
# Exceptions — JSON-RPC code mapping per PLAN.md L745-L754.
# ---------------------------------------------------------------------------


class RepositoryError(Exception):
    """Base of all repository-layer typed errors.

    Concrete subclasses set ``code`` (JSON-RPC numeric) and ``http_status``
    (REST). Carries a ``data`` dict the M2/M3 transport adapters can
    splat into the response envelope's ``data`` field.
    """

    code: int = -32603  # internal
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        detail: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.data: dict[str, Any] = dict(data or {})

    def to_dict(self) -> dict[str, Any]:
        """Serialise for tests + transport adapters."""
        return {
            "code": self.code,
            "detail": self.detail,
            "retryable": self.retryable,
            "data": self.data,
        }


class ValidationError(RepositoryError):
    """Domain validation failure (-32602)."""

    code = -32602
    http_status = 422


class NotFoundError(RepositoryError):
    """Row not found (-32004)."""

    code = -32004
    http_status = 404


class ConflictError(RepositoryError):
    """State-machine violation, etag mismatch, or unique-constraint dupe.

    PLAN.md L750 maps this to JSON-RPC -32008 / HTTP 409. The ``data``
    dict carries the canonical fields the transport layer surfaces:
    ``current_etag``, ``current_status``, ``attempted_status``, etc.
    """

    code = -32008
    http_status = 409


class BudgetExceededError(RepositoryError):
    """Pre-emptive cost cap breach (-32012, PLAN.md L753)."""

    code = -32012
    http_status = 402


class IdempotencyReplayError(RepositoryError):
    """Replay within 24 h dedup window — caller should short-circuit.

    Not a true error in the failure sense; it's a flow-control signal.
    The transport layer maps this to a 200 with the cached ``response_json``
    rather than a 4xx. Carrying the original ``run_id`` in ``data`` lets
    the M2/M3 layer return the original envelope unchanged.
    """

    code = -32008  # mirrors conflict; transports inspect ``data['replay']``
    http_status = 200


# ---------------------------------------------------------------------------
# Pagination + Envelope models.
# ---------------------------------------------------------------------------


class Page[T](BaseModel):
    """Cursor-paginated list response.

    PLAN.md L536-L538: the wire shape is ``{items, next_cursor, total_estimate}``.
    ``total_estimate`` is allowed to be a ceiling (we use ``COUNT(*)`` here;
    when tables grow large enough that becomes painful, caller can switch to
    an approximate count without changing the envelope).
    """

    items: list[T]
    next_cursor: int | None = None
    total_estimate: int = 0


class Envelope[T](BaseModel):
    """Mutating-method return wrapper.

    PLAN.md L758-L763: ``{data, run_id, project_id}``. ``run_id`` is
    optional at the repository layer (None when called outside a run
    context). The MCP layer wires it from run-plan and tool execution.
    """

    data: T
    run_id: int | None = None
    project_id: int | None = None


# ---------------------------------------------------------------------------
# Pagination + transition helpers.
# ---------------------------------------------------------------------------

# Hard ceiling per PLAN.md L535 ("max 200"). Reject larger limits with
# ValidationError so the transport adapters don't have to repeat the check.
MAX_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 50


def _normalise_limit(limit: int | None) -> int:
    """Validate a caller-supplied ``limit``; clamp to the spec range."""
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", data={"limit": limit})
    if limit > MAX_PAGE_LIMIT:
        raise ValidationError(
            f"limit must be <= {MAX_PAGE_LIMIT}",
            data={"limit": limit, "max": MAX_PAGE_LIMIT},
        )
    return limit


def cursor_paginate(
    session: Session,
    statement: Select[Any],
    *,
    id_col: Any,
    limit: int | None,
    after_id: int | None,
    converter: Any,
) -> Page[Any]:
    """Apply ``id_col > after_id ORDER BY id_col ASC LIMIT n`` paging.

    The ``converter`` callable maps a raw ORM row to the public output
    type (typically a ``BaseModel.model_validate``); it gives caller-side
    repos full control over response shape without coupling this helper
    to a specific table.

    ``total_estimate`` runs a separate ``COUNT(*)`` against the same WHERE
    clause minus the cursor predicate so the UI can render "page X of Y"
    without an extra round-trip. For tables that outgrow this we'll switch
    to a per-table approximate count without changing the wire envelope.
    """
    n = _normalise_limit(limit)

    # Total estimate against the unfiltered (by cursor) query.
    count_stmt = statement.with_only_columns(id_col).order_by(None).subquery()
    from sqlalchemy import func  # local import keeps top tidy
    from sqlalchemy import select as sa_select

    # ``session.exec(sa_select(...))`` returns a ``Result`` whose ``one()``
    # yields a SQLAlchemy ``Row`` — index by position. Cast to int because
    # SQLite's COUNT() lands as a Python int already but the typing helper
    # is friendlier when we're explicit.
    count_row = session.exec(sa_select(func.count()).select_from(count_stmt)).one()  # type: ignore[call-overload]
    total_estimate = int(count_row[0])

    paged_stmt = statement.order_by(id_col.asc()).limit(n + 1)
    if after_id is not None:
        paged_stmt = paged_stmt.where(id_col > after_id)

    rows = list(session.exec(paged_stmt))  # type: ignore[call-overload]
    items_rows = rows[:n]
    has_more = len(rows) > n
    items = [converter(r) for r in items_rows]
    next_cursor: int | None = None
    if has_more and items_rows:
        # Read the id off the last *included* row, not the peeked-extra one.
        last = items_rows[-1]
        # ORM row OR Row-tuple: lookup by attribute name preserved on `id_col`.
        next_cursor = int(getattr(last, id_col.key))
    return Page(items=items, next_cursor=next_cursor, total_estimate=total_estimate)


def cursor_paginate_desc(
    session: Session,
    statement: Select[Any],
    *,
    id_col: Any,
    limit: int | None,
    after_id: int | None,
    converter: Any,
) -> Page[Any]:
    """Apply ``id_col < after_id ORDER BY id_col DESC LIMIT n`` paging."""
    n = _normalise_limit(limit)

    count_stmt = statement.with_only_columns(id_col).order_by(None).subquery()
    from sqlalchemy import func  # local import keeps top tidy
    from sqlalchemy import select as sa_select

    count_row = session.exec(sa_select(func.count()).select_from(count_stmt)).one()  # type: ignore[call-overload]
    total_estimate = int(count_row[0])

    paged_stmt = statement.order_by(id_col.desc()).limit(n + 1)
    if after_id is not None:
        paged_stmt = paged_stmt.where(id_col < after_id)

    rows = list(session.exec(paged_stmt))  # type: ignore[call-overload]
    items_rows = rows[:n]
    has_more = len(rows) > n
    items = [converter(r) for r in items_rows]
    next_cursor: int | None = None
    if has_more and items_rows:
        last = items_rows[-1]
        next_cursor = int(getattr(last, id_col.key))
    return Page(items=items, next_cursor=next_cursor, total_estimate=total_estimate)


def validate_transition(
    current: Enum,
    next_status: Enum,
    transitions: dict[Any, frozenset[Any]],
    *,
    label: str = "status",
) -> None:
    """Raise ``ConflictError`` if ``current → next_status`` is illegal.

    The transition maps live in ``stackos.db.models`` (one per
    state-machine: projects, runs, run plans, resources, and action calls). Tests
    defending the maps are in ``tests/unit/test_models.py``; tests for
    *this* helper live in ``tests/unit/test_base.py``.
    """
    legal = transitions.get(current, frozenset())
    if next_status not in legal:
        raise ConflictError(
            f"illegal {label} transition {current.value!r} → {next_status.value!r}",
            data={
                "current": current.value,
                "attempted": next_status.value,
                "allowed": sorted(s.value for s in legal),
                "kind": label,
            },
        )


# ---------------------------------------------------------------------------
# Re-exports.
# ---------------------------------------------------------------------------


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "BudgetExceededError",
    "ConflictError",
    "Envelope",
    "Field",
    "IdempotencyReplayError",
    "NotFoundError",
    "Page",
    "RepositoryError",
    "ValidationError",
    "cursor_paginate",
    "validate_transition",
]
