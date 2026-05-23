"""Query-string filter + sort parsers with allow-lists.

Per PLAN.md L541-L546:

- Any column listed in an enum schema is filterable via ``?col=val``.
- Multiple keys are combined as ``AND``.
- Sort is ``?sort=col`` (ASC) or ``?sort=-col`` (DESC); default per route.

We use *per-route* allow-lists (no global registry) so each list endpoint
declares its filterable columns and sort columns inline. Disallowed values
return HTTP 422 with a typed pointer at the offending key.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from fastapi import HTTPException

# Hard-coded so we don't depend on starlette's deprecated alias.
_HTTP_422_UNPROCESSABLE = 422

# Reserved query keys that are NEVER filters (pagination + meta).
_RESERVED_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "limit",
        "after",
        "sort",
        "active_only",
        "since",
        "until",
        "month",
        "day",
        "cascade",
        "append",
        "stream",
        "hard",
    }
)


def parse_filters(
    params: Mapping[str, str],
    allowed: Iterable[str],
) -> dict[str, str]:
    """Return ``{col: val}`` for every query key in ``allowed``.

    Raises ``HTTPException(422)`` on the first disallowed key to keep the
    error surface predictable. Reserved keys (limit/after/sort/etc.) are
    silently dropped — they're handled by other dependencies.
    """
    allowed_set = frozenset(allowed)
    out: dict[str, str] = {}
    for key, value in params.items():
        if key in _RESERVED_QUERY_KEYS:
            continue
        if key not in allowed_set:
            raise HTTPException(
                status_code=_HTTP_422_UNPROCESSABLE,
                detail={
                    "detail": f"unknown filter {key!r}",
                    "allowed": sorted(allowed_set),
                    "field": key,
                },
            )
        out[key] = value
    return out


def parse_sort(
    s: str | None,
    allowed: Iterable[str],
    *,
    default: str = "-created_at",
) -> tuple[str, bool]:
    """Parse a ``sort`` query value into ``(column, descending)``.

    Returns ``(column_name, True)`` for ``-col`` and ``(column_name, False)``
    for ``col``. Raises 422 on unknown columns.
    """
    raw = s if s is not None else default
    descending = raw.startswith("-")
    column = raw[1:] if descending else raw
    if column not in set(allowed):
        raise HTTPException(
            status_code=_HTTP_422_UNPROCESSABLE,
            detail={
                "detail": f"unknown sort column {column!r}",
                "allowed": sorted(allowed),
                "field": "sort",
            },
        )
    return column, descending


__all__ = ["parse_filters", "parse_sort"]
