"""Fresh test databases copied from an immutable, empty metadata schema.

Repository/connector tests exercise SQLModel metadata, while migration and
daemon-startup tests continue to construct their own databases. Only schema
bytes are shared: every caller owns a new engine, connection, and transaction.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import lru_cache

from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

import stackos.db.models  # noqa: F401 -- register the complete application metadata
from stackos.db.connection import make_memory_engine


@lru_cache(maxsize=1)
def _empty_schema() -> bytes:
    engine = make_memory_engine()
    try:
        SQLModel.metadata.create_all(engine)
        with engine.connect() as connection:
            return connection.connection.driver_connection.serialize()
    finally:
        engine.dispose()


def make_test_memory_engine() -> Engine:
    """Return an empty schema with the normal memory-engine connection options."""
    schema = _empty_schema()
    engine = make_memory_engine()
    try:
        # Deserializing directly into the destination resets SQLite's synchronous
        # PRAGMA. Back up from a short-lived source to preserve factory options.
        with closing(sqlite3.connect(":memory:")) as source, engine.connect() as connection:
            source.deserialize(schema)
            source.backup(connection.connection.driver_connection)
    except BaseException:
        engine.dispose()
        raise
    return engine
