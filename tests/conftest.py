"""Shared pytest fixtures.

We isolate every test from the user's real `~/.local/state/stackos/`
by pointing `Settings.data_dir` and `Settings.state_dir` at a tmp dir.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stackos.config import Settings
from stackos.server import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Build a Settings pointed at tmp dirs so tests don't touch real paths."""
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    return Settings(
        host="127.0.0.1",
        port=5180,
        data_dir=data_dir,
        state_dir=state_dir,
    )


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    """Build a FastAPI app bound to the isolated settings fixture."""
    return create_app(settings)


@pytest.fixture
def auth_token(settings: Settings) -> str:
    """Return the bearer token written to the isolated state dir at app build."""
    return settings.token_path.read_text(encoding="utf-8").strip()


@pytest.fixture
def client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    """TestClient with lifespan enabled so startup/shutdown actually run.

    `base_url` is set to a loopback address so the `Host:` header host-check
    middleware accepts the synthetic requests; the integration test
    `test_host_header_check_rejects_non_loopback` overrides per-request.
    """
    with TestClient(app, base_url="http://127.0.0.1:5180") as c:
        yield c
