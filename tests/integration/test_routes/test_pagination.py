"""Pagination wire-shape tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_pagination_defaults(api: TestClient) -> None:
    """Default page is ``limit=50`` (we don't have 50 projects, so cursor None)."""
    resp = api.get("/api/v1/projects")
    body = resp.json()
    assert resp.status_code == 200
    assert "items" in body
    assert "next_cursor" in body
    assert "total_estimate" in body
    # Empty universe — cursor None.
    assert body["items"] == []
    assert body["next_cursor"] is None
    assert body["total_estimate"] == 0


def test_pagination_limit_capped_at_200(api: TestClient) -> None:
    """``limit=999`` is rejected with 422 (FastAPI Query bound)."""
    resp = api.get("/api/v1/projects?limit=999")
    assert resp.status_code == 422


@pytest.mark.parametrize("has_project", [False, True])
def test_pagination_rejects_missing_ordered_cursor(api: TestClient, has_project: bool) -> None:
    """An absent anchor cannot define a position in stored project order."""
    if has_project:
        created = api.post(
            "/api/v1/projects",
            json={"slug": "present", "name": "Present", "domain": "present.test"},
        )
        assert created.status_code == 201
    resp = api.get("/api/v1/projects?after=999")
    assert resp.status_code == 422
    assert resp.json()["code"] == -32602
    assert "refresh the first page" in resp.json()["detail"]
    refreshed = api.get("/api/v1/projects")
    assert refreshed.status_code == 200
    assert refreshed.json()["total_estimate"] == int(has_project)


def test_pagination_rejects_cursor_that_no_longer_matches_filter(
    api: TestClient, project_id: int
) -> None:
    """An anchor that left the active list requires a fresh first page."""
    response = api.get("/api/v1/projects", params={"active_only": True})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [project_id]
    updated = api.patch(f"/api/v1/projects/{project_id}", json={"is_active": False})
    assert updated.status_code == 200
    stale = api.get("/api/v1/projects", params={"active_only": True, "after": project_id})
    assert stale.status_code == 422
    assert stale.json()["code"] == -32602
    assert "refresh the first page" in stale.json()["detail"]
    refreshed = api.get("/api/v1/projects", params={"active_only": True})
    assert refreshed.status_code == 200
    assert refreshed.json() == {"items": [], "next_cursor": None, "total_estimate": 0}


def test_pagination_traverses_pages(api: TestClient) -> None:
    """Walk stored order across pages, including a valid exhausted cursor."""
    ids = []
    for slug in ("p1", "p2", "p3"):
        resp = api.post(
            "/api/v1/projects",
            json={
                "slug": slug,
                "name": slug.upper(),
                "domain": f"{slug}.com",
                "locale": "en-US",
            },
        )
        assert resp.status_code == 201
        ids.append(resp.json()["data"]["id"])
    moved = api.post(
        "/api/v1/operations/project.move/call",
        json={"arguments": {"project_id": ids[2], "placement": "top", "scope": "catalog"}},
    )
    assert moved.status_code == 200
    page1 = api.get("/api/v1/projects?limit=2").json()
    assert [item["id"] for item in page1["items"]] == [ids[2], ids[0]]
    assert page1["next_cursor"] == ids[0]
    assert page1["total_estimate"] == 3
    page2 = api.get(f"/api/v1/projects?limit=2&after={page1['next_cursor']}").json()
    assert [item["id"] for item in page2["items"]] == [ids[1]]
    assert page2["next_cursor"] is None
    assert page2["total_estimate"] == 3
    exhausted = api.get("/api/v1/projects", params={"after": ids[1]})
    assert exhausted.status_code == 200
    assert exhausted.json() == {"items": [], "next_cursor": None, "total_estimate": 3}
