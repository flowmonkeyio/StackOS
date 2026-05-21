"""Error-mapping tests — RepositoryError → HTTP."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_not_found_returns_404_with_envelope(api: TestClient) -> None:
    """``NotFoundError`` maps to 404 with the JSON-RPC envelope."""
    resp = api.get("/api/v1/projects/9999")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == -32004
    assert body["retryable"] is False
    assert "detail" in body


def test_validation_error_returns_422(api: TestClient) -> None:
    """Empty slug → 422 from pydantic body validation."""
    resp = api.post(
        "/api/v1/projects",
        json={"slug": "", "name": "x", "domain": "x.com", "locale": "en-US"},
    )
    assert resp.status_code == 422


def test_conflict_returns_409_on_duplicate_slug(api: TestClient) -> None:
    """``ConflictError`` (slug duplicate) maps to 409."""
    payload = {"slug": "dup", "name": "X", "domain": "x.com", "locale": "en-US"}
    resp1 = api.post("/api/v1/projects", json=payload)
    assert resp1.status_code == 201
    resp2 = api.post("/api/v1/projects", json=payload)
    assert resp2.status_code == 409
    assert resp2.json()["code"] == -32008


def test_project_slug_immutable_returns_422(api: TestClient, project_id: int) -> None:
    """Project ``slug`` PATCH is rejected with 422 (per audit B-27)."""
    resp = api.patch(f"/api/v1/projects/{project_id}", json={"slug": "new-slug"})
    # The repo raises ValidationError → 422.
    assert resp.status_code == 422
