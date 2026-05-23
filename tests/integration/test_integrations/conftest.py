"""Shared fixtures for the M4 integration-wrapper tests.

Each test gets:

- A deterministic ``seed.bin`` configured for the AES-GCM round trip.
- A fresh in-memory SQLite DB with the schema applied (so wrappers that
  hit ``IntegrationBudgetRepository`` / ``RunStepCallRepository`` see
  rows they expect).
- Reset token-bucket registry so QPS math is deterministic across tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from stackos.crypto.aes_gcm import configure_seed_path
from stackos.crypto.seed import ensure_seed_file
from stackos.db.connection import make_memory_engine
from stackos.integrations._rate_limit import reset_buckets


@pytest.fixture(scope="session", autouse=True)
def _crypto_seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Configure a per-session seed file; matches the repository conftest."""
    seed_dir = tmp_path_factory.mktemp("integrations-crypto-seed")
    seed_path = seed_dir / "seed.bin"
    ensure_seed_file(seed_path)
    configure_seed_path(seed_path)
    yield seed_path


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets() -> Iterator[None]:
    """Drop every token bucket before each test (state is process-level)."""
    reset_buckets()
    yield
    reset_buckets()


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace ``asyncio.sleep`` inside the integration retry loop with a no-op.

    The base class' exponential backoff is 0.5s → 1s → 2s → 4s; with
    real sleeps a 4-retry test runs ~7.5s which blows the 35s budget
    fast. We're not testing wall-clock semantics — tests assert that
    the loop *runs the right number of HTTP calls* — so the sleeps are
    safe to no-op.
    """
    import asyncio as asyncio_module

    async def _zero_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio_module, "sleep", _zero_sleep)
    yield


@pytest.fixture
def session() -> Iterator[Session]:
    """Yield a fresh SQLModel ``Session`` bound to an in-memory engine."""
    engine = make_memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def project_id(session: Session) -> int:
    """Create a project so dependent rows have a valid FK."""
    from stackos.repositories.projects import ProjectRepository

    repo = ProjectRepository(session)
    env = repo.create(slug="t-int", name="T", domain="x", locale="en")
    pid = env.data.id
    assert pid is not None
    return pid
