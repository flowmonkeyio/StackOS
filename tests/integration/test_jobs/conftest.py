"""Shared fixtures for the M8 jobs/scheduler integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from stackos.crypto.aes_gcm import configure_seed_path
from stackos.crypto.seed import ensure_seed_file
from tests.helpers.sqlite_schema import make_test_memory_engine


@pytest.fixture(scope="session", autouse=True)
def _crypto_seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    seed_dir = tmp_path_factory.mktemp("jobs-crypto-seed")
    seed_path = seed_dir / "seed.bin"
    ensure_seed_file(seed_path)
    configure_seed_path(seed_path)
    yield seed_path


@pytest.fixture
def engine() -> Iterator[object]:
    """In-memory SQLite engine with the full schema."""
    eng = make_test_memory_engine()
    try:
        yield eng
    finally:
        eng.dispose()
