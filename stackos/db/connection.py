"""SQLAlchemy engine factory + WAL PRAGMAs.

Every new connection runs the PRAGMA block from PLAN.md F3. SQLite-on-disk
uses one `make_engine`; the in-memory engine for tests skips the file-only
mmap_size pragma since it's harmless but pointless on `:memory:`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

# PRAGMAs per PLAN.md "SQLite PRAGMAs (set at every connect)". We set them
# *every* connect because SQLite scopes most of these per-connection.
_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "5000"),
    ("foreign_keys", "ON"),
    ("temp_store", "MEMORY"),
    ("mmap_size", "268435456"),
)


def _attach_pragma_listener(engine: Engine) -> None:
    """Register a connect-time PRAGMA setter on `engine`.

    Listener is attached per-engine, not globally, so test engines using
    in-memory SQLite or a non-default config don't inherit the file-engine's
    settings unexpectedly.
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            for name, value in _PRAGMAS:
                cursor.execute(f"PRAGMA {name} = {value}")
        finally:
            cursor.close()


def make_engine(db_path: Path, *, echo: bool = False) -> Engine:
    """Build a SQLAlchemy engine for the on-disk SQLite DB.

    `check_same_thread=False` is set because FastAPI dispatches on a thread
    pool by default and SQLAlchemy manages its own pool already.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        echo=echo,
        connect_args={"check_same_thread": False},
    )
    _attach_pragma_listener(engine)
    return engine


def make_memory_engine(*, echo: bool = False) -> Engine:
    """Build an in-memory engine for tests; PRAGMAs still applied for parity."""
    engine = create_engine(
        "sqlite://",
        echo=echo,
        connect_args={"check_same_thread": False},
    )
    _attach_pragma_listener(engine)
    return engine


def get_session_factory(engine: Engine) -> Callable[[], Iterator[Session]]:
    """Return a callable that yields a Session — used as a FastAPI dep in M1+."""

    def _factory() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    return _factory
