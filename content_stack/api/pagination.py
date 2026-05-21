"""Pagination wire shape — REST counterpart to ``repositories.base.Page[T]``.

Per PLAN.md L529-L539: cursor pagination on ``id ASC``. Query params
``?limit=N&after=<id>`` (default 50, max 200). The response envelope is
``{items, next_cursor, total_estimate}``.

We mirror the repository's ``Page[T]`` rather than reuse it directly so
the OpenAPI generator emits a per-resource ``PageResponse[Resource]``
(``ProjectsPage``, ``RunsPage``, …) the TypeScript codegen can map
to discriminated types. Conversion is one-to-one in ``page_response``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from content_stack.repositories.base import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page


class PaginationParams(BaseModel):
    """Cursor-pagination query params per PLAN.md L529-L539."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"limit": 50, "after": 0},
        }
    )

    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    after: int | None = Field(default=None, ge=0)


def pagination_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> PaginationParams:
    """FastAPI dependency: parse ``?limit=&after=`` into ``PaginationParams``."""
    return PaginationParams(limit=limit, after=after)


class PageResponse[T](BaseModel):
    """Response envelope for cursor-paginated lists.

    Wire shape per PLAN.md L536-L538. ``next_cursor`` is ``null`` on the
    last page; ``total_estimate`` is a ``COUNT(*)`` against the unfiltered
    (by cursor) WHERE clause so the UI can render "page X of Y" without
    a second round-trip.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "next_cursor": None,
                "total_estimate": 0,
            }
        }
    )

    items: list[T]
    next_cursor: int | None = None
    total_estimate: int = 0


def page_response[T](page: Page[T]) -> PageResponse[T]:
    """Convert a repository ``Page[T]`` into a wire ``PageResponse[T]``.

    Kept as a function (not an ``__init__`` shortcut) so OpenAPI emits the
    proper generic without ambiguity, and so future call sites can splice
    in extra fields (e.g. server-side facet aggregates) without touching
    the wire envelope contract.
    """
    return PageResponse[T](
        items=list(page.items),
        next_cursor=page.next_cursor,
        total_estimate=page.total_estimate,
    )


__all__ = [
    "PageResponse",
    "PaginationParams",
    "page_response",
    "pagination_params",
]
