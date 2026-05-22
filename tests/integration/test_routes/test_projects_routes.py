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

    activated = api.post(f"/api/v1/projects/{project_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["data"]["is_active"] is True

    deleted = api.delete(f"/api/v1/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["is_active"] is False


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


def test_auth_status_includes_global_and_project_credentials(
    api: TestClient,
    project_id: int,
) -> None:
    from sqlmodel import Session

    from content_stack.repositories.projects import IntegrationCredentialRepository

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        IntegrationCredentialRepository(session).set(
            project_id=None,
            kind="anthropic",
            secret_payload=b"global-key",
        )

    created = api.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "project-key"}},
    )
    assert created.status_code == 201

    rows = api.get(f"/api/v1/projects/{project_id}/auth/status").json()["connections"]
    by_kind = {row["provider_key"]: row for row in rows}
    assert by_kind["firecrawl"]["project_id"] == project_id
    assert by_kind["anthropic"]["project_id"] is None
    assert "encrypted_payload" not in by_kind["firecrawl"]
    assert "config_json" not in by_kind["firecrawl"]
