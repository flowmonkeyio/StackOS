"""FastAPI dependencies — session, settings, pagination, etag.

Centralised so every router pulls its plumbing from the same place. The
session is yielded per-request from the engine on ``app.state``; the
engine itself is built once during the lifespan startup hook and shared
across all requests (SQLAlchemy owns its connection pool internally).

Tests can override these dependencies via FastAPI's ``app.dependency_overrides``
hook; the M2 test fixtures do exactly that to swap in an in-memory engine
+ a per-test session.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.engine import Engine
from sqlmodel import Session

from content_stack.config import Settings


def get_settings(request: Request) -> Settings:
    """Resolve the ``Settings`` from the FastAPI app state.

    The lifespan startup hook stashes the active ``Settings`` on
    ``app.state.settings`` so we don't re-read env at every request.
    """
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:  # pragma: no cover — only triggers if lifespan didn't run
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="settings not initialised on app.state",
        )
    return settings


def get_engine(request: Request) -> Engine:
    """Resolve the SQLAlchemy ``Engine`` from app state."""
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:  # pragma: no cover — only when lifespan failed
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="engine not initialised on app.state",
        )
    return engine


def get_session(engine: Engine = Depends(get_engine)) -> Iterator[Session]:
    """Yield a per-request SQLModel ``Session``.

    Each request opens its own session bound to the shared engine. The
    ``with`` block guarantees ``close()`` runs even if the route raises;
    the repository layer drives ``commit()`` itself so we never autocommit
    on the dependency boundary (would defeat repo-level transaction
    semantics).
    """
    with Session(engine) as session:
        yield session


def get_if_match(if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> str | None:
    """Read the ``If-Match`` header for optimistic concurrency.

    Routes that support optimistic concurrency can compare this raw value
    against their own row version or timestamp.
    """
    return if_match


__all__ = [
    "get_engine",
    "get_if_match",
    "get_session",
    "get_settings",
]
