"""Project setup route tests for the clean StackOS core."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_project_returns_envelope(api: TestClient) -> None:
    resp = api.post(
        "/api/v1/projects",
        json={
            "slug": "site-a",
            "name": "Site A",
            "domain": "a.com",
            "locale": "en-US",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["project_id"] == body["data"]["id"]
    assert body["data"]["slug"] == "site-a"


def test_project_crud_routes(api: TestClient, project_id: int) -> None:
    patch = api.patch(f"/api/v1/projects/{project_id}", json={"name": "New Name"})
    assert patch.status_code == 200
    assert patch.json()["data"]["name"] == "New Name"

    deleted = api.delete(f"/api/v1/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["is_active"] is False


def test_project_hard_delete_route_removes_row(api: TestClient) -> None:
    created = api.post(
        "/api/v1/projects",
        json={
            "slug": "hard-delete-route",
            "name": "Hard Delete Route",
            "domain": "hard-delete-route.example",
            "locale": "en-US",
        },
    )
    assert created.status_code == 201
    project_id = created.json()["data"]["id"]

    deleted = api.delete(f"/api/v1/projects/{project_id}", params={"hard": "true"})
    assert deleted.status_code == 200
    assert api.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_list_projects_pagination_and_missing(api: TestClient) -> None:
    listing = api.get("/api/v1/projects")
    assert listing.status_code == 200
    assert listing.json()["items"] == []

    missing = api.get("/api/v1/projects/999")
    assert missing.status_code == 404


def test_schedule_create_then_disable(api: TestClient, project_id: int) -> None:
    created = api.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={"kind": "weekly-review", "cron_expr": "0 3 * * *", "enabled": True},
    )
    assert created.status_code == 200
    job_id = created.json()["data"]["id"]

    disabled = api.delete(f"/api/v1/projects/{project_id}/schedules/{job_id}")
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False


def test_budget_set_then_get(api: TestClient, project_id: int) -> None:
    created = api.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "dataforseo", "monthly_budget_usd": 25.0},
    )
    assert created.status_code == 200

    fetched = api.get(f"/api/v1/projects/{project_id}/budgets/dataforseo")
    assert fetched.status_code == 200
    assert fetched.json()["monthly_budget_usd"] == 25.0

    listing = api.get(f"/api/v1/projects/{project_id}/budgets")
    assert listing.status_code == 200
    assert [row["kind"] for row in listing.json()] == ["dataforseo"]
