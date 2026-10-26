"""Fixtures for REST-route integration tests.

We copy a genuinely migrated builtin catalog into an isolated tmp dir (via
the top-level ``settings`` fixture), run the real app lifespan, then expose:

- ``api`` — a ``TestClient`` with the bearer token pre-bound on the
  default headers so each test isn't repeating ``Authorization``.
- ``project_id`` — a freshly-created StackOS project.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from stackos.config import Settings
from stackos.server import create_app
from tests.helpers.prepared_app import prepare_app_database
from tests.helpers.sqlite_schema import mark_snapshot_catalog


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    """Authenticated TestClient — every request carries the bearer token."""
    snapshot = prepare_app_database(settings)
    app = create_app(settings)
    token = settings.token_path.read_text(encoding="utf-8").strip()
    with TestClient(app, base_url="http://127.0.0.1:5180") as client:
        mark_snapshot_catalog(app.state.engine, snapshot)
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


@pytest.fixture
def project_id(api: TestClient) -> int:
    """Create a project via REST + return its id."""
    resp = api.post(
        "/api/v1/projects",
        json={
            "slug": "betsage",
            "name": "BetSage",
            "domain": "betsage.com",
            "niche": "sportsbetting",
            "locale": "en-US",
        },
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["data"]["id"])
