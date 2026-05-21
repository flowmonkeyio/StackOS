"""Fixtures for REST-route integration tests.

We build the FastAPI app against an isolated tmp dir (via the top-level
``settings`` fixture) and ask its lifespan to create the schema, then
expose:

- ``api`` — a ``TestClient`` with the bearer token pre-bound on the
  default headers so each test isn't repeating ``Authorization``.
- ``project_id`` — a freshly-created StackOS project.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from content_stack.config import Settings
from content_stack.server import create_app


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    """Authenticated TestClient — every request carries the bearer token."""
    app = create_app(settings)
    token = settings.token_path.read_text(encoding="utf-8").strip()
    with TestClient(app, base_url="http://127.0.0.1:5180") as client:
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
