"""Shared fixtures for the M8 jobs/scheduler integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from content_stack.crypto.aes_gcm import configure_seed_path
from content_stack.crypto.seed import ensure_seed_file
from content_stack.db.connection import make_memory_engine


@pytest.fixture(scope="session", autouse=True)
def _crypto_seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    seed_dir = tmp_path_factory.mktemp("jobs-crypto-seed")
    seed_path = seed_dir / "seed.bin"
    ensure_seed_file(seed_path)
    configure_seed_path(seed_path)
    yield seed_path


def _emit_partial_indexes(engine: object) -> None:
    statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_idempotency "
        "ON idempotency_keys(project_id, tool_name, idempotency_key)",
    ]
    with engine.begin() as conn:  # type: ignore[attr-defined]
        for s in statements:
            conn.execute(text(s))


@pytest.fixture
def engine() -> Iterator[object]:
    """In-memory SQLite engine with the full schema."""
    eng = make_memory_engine()
    SQLModel.metadata.create_all(eng)
    _emit_partial_indexes(eng)
    yield eng
