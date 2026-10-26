"""Shared fixtures for repository integration tests.

Each repository test gets a fresh in-memory SQLite engine + Session
copied from SQLModel's metadata schema and the real built-in catalog (rather than running
Alembic — these tests focus on the repository contract, not the
migration flow which has its own coverage in ``test_schema.py``).

The schema/catalog are initialized once per source generation and copied into
independent engines. Tests of catalog initialization or mutation use the
``fresh_catalog`` marker to start from empty metadata instead. A session-scoped
fixture wires ``configure_seed_path`` to a temporary seed file so encrypted
credential storage can be tested without leaking real secrets.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.crypto.aes_gcm import configure_seed_path
from stackos.crypto.seed import ensure_seed_file
from tests.helpers.sqlite_schema import make_test_memory_engine


@pytest.fixture(scope="session", autouse=True)
def _crypto_seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Configure a deterministic per-session seed file.

    M4: ``IntegrationCredentialRepository.set`` now calls into
    ``stackos.crypto.aes_gcm.encrypt``. That helper requires
    ``configure_seed_path`` to have been called at daemon startup. The
    autouse fixture mirrors what ``server.create_app`` does.
    """
    seed_dir = tmp_path_factory.mktemp("crypto-seed")
    seed_path = seed_dir / "seed.bin"
    ensure_seed_file(seed_path)
    configure_seed_path(seed_path)
    yield seed_path


@pytest.fixture
def session(request: pytest.FixtureRequest) -> Iterator[Session]:
    """Yield a fresh ``Session`` bound to an in-memory SQLite engine."""
    engine = make_test_memory_engine(
        catalog=request.node.get_closest_marker("fresh_catalog") is None
    )
    try:
        with Session(engine) as s:
            yield s
    finally:
        engine.dispose()


@pytest.fixture
def project_id(session: Session) -> int:
    """Create a project and return its id."""
    from stackos.repositories.projects import ProjectRepository

    repo = ProjectRepository(session)
    env = repo.create(
        slug="t-proj",
        name="Test Project",
        domain="example.com",
        locale="en-US",
    )
    pid = env.data.id
    assert pid is not None
    return pid
