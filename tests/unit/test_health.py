"""Health endpoint smoke tests.

`/api/v1/health` is auth-whitelisted, so we hit it with no Authorization
header. The response shape is the M0 subset documented in `api/health.py`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from content_stack.config import Settings


def test_health_returns_m0_shape(client: TestClient) -> None:
    """GET /api/v1/health returns 200 + the M0 keys with sensible types."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "daemon_uptime_s",
        "db_status",
        "scheduler_running",
        "version",
        "milestone",
    }
    assert isinstance(body["daemon_uptime_s"], float | int)
    assert body["db_status"] in {"ok", "unreachable"}
    # M8: scheduler is now live — health surfaces True.
    assert body["scheduler_running"] is True
    assert body["version"] == "1.0.0"
    assert body["milestone"] == "M10"


def test_health_does_not_require_bearer_token(client: TestClient) -> None:
    """Health is whitelisted — no Authorization header should still succeed."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200


def test_unauthenticated_protected_path_returns_401(client: TestClient) -> None:
    """Protected /api/v1/* paths reject missing bearer with 401.

    Uses a route that exists in M2 — the middleware runs before routing,
    so a 401 from auth precedes the router. That ordering is what we're
    verifying.
    """
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").startswith("Bearer")


def test_authenticated_protected_path_passes_to_router(client: TestClient, auth_token: str) -> None:
    """A valid bearer token clears the middleware so the router sees the request.

    The M2 ``GET /api/v1/projects`` returns 200 + an empty page when no
    projects exist, which proves the middleware forwarded the request.
    """
    resp = client.get(
        "/api/v1/projects",
        headers={"authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total_estimate"] == 0


def test_static_root_is_public(client: TestClient) -> None:
    """The UI bundle path `/` is public; the browser must load it without a token."""
    resp = client.get("/")
    # Either the static-file mount returns the index.html (when ui_dist exists),
    # or the placeholder branch returns the "UI not built" message. Both are 200
    # and neither demands auth.
    assert resp.status_code == 200


def test_generated_assets_are_public(client: TestClient, settings: Settings) -> None:
    """Generated image URLs are public local assets, not bearer-protected API paths."""
    asset_dir = settings.generated_assets_dir / "openai-images"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "sample.webp").write_bytes(b"webp")

    resp = client.get("/generated-assets/openai-images/sample.webp")
    assert resp.status_code == 200
    assert resp.content == b"webp"


def test_openapi_json_is_public(client: TestClient) -> None:
    """OpenAPI schema is local-dev ergonomics; exposing it grants no access."""
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200


def test_openapi_exposes_ui_critical_contract_paths(client: TestClient) -> None:
    """OpenAPI advertises UI-critical routes that have drifted before."""
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]

    assert "get" in paths["/api/v1/projects/{project_id}/cost"]
    assert {"get", "post"} <= set(paths["/api/v1/projects/{project_id}/budgets"])
    assert {"get", "post"} <= set(paths["/api/v1/projects/{project_id}/resource-records"])
    assert "get" in paths["/api/v1/projects/{project_id}/action-calls"]
    assert "get" in paths["/api/v1/projects/{project_id}/workflow-templates"]
    assert "get" in paths["/api/v1/projects/{project_id}/run-plans"]
    assert "get" in paths["/api/v1/run-plans/{run_plan_id}"]
