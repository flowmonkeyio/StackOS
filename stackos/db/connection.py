"""Database engines for standalone SQLite and explicitly selected Postgres."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlmodel import Session, create_engine

from stackos.config import Settings

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


def database_url(settings: Settings) -> str:
    """Resolve the selected database without falling back between process roles."""
    if settings.process_role == "runtime":
        raise ValueError("runtime has no application database")
    if settings.process_role == "server":
        assert settings.database_url is not None
        url = make_url(settings.database_url.get_secret_value())
        return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    return f"sqlite:///{settings.db_path}"


def make_settings_engine(settings: Settings, *, echo: bool = False) -> Engine:
    """Use the same selected target for application sessions and migrations."""
    return make_engine(database_url(settings), echo=echo)


def make_engine(db_path: Path | str, *, echo: bool = False) -> Engine:
    """Build an engine, applying SQLite PRAGMAs only to SQLite connections.

    `check_same_thread=False` is set because FastAPI dispatches on a thread
    pool by default and SQLAlchemy manages its own pool already.
    """
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = make_url(f"sqlite:///{db_path}")
    else:
        url = make_url(db_path)
    if url.get_backend_name() == "postgresql":
        return create_engine(
            url.set(drivername="postgresql+psycopg"),
            echo=echo,
            pool_pre_ping=True,
            # Unqualified SQLModel tables live outside Supabase's exposed schemas.
            connect_args={"options": "-csearch_path=stackos,pg_catalog"},
        )
    if url.get_backend_name() != "sqlite":
        raise ValueError("application database must be SQLite or Postgres")
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
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
