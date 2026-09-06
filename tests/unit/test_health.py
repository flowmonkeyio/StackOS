"""Health endpoint smoke tests.

`/api/v1/health` is auth-whitelisted, so we hit it with no Authorization
header. The response shape is the M0 subset documented in `api/health.py`.
"""

from __future__ import annotations

import errno
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from stackos import __version__
from stackos.config import Settings


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
    assert body["version"] == __version__
    assert body["milestone"] == "M10"


def test_health_does_not_require_bearer_token(client: TestClient) -> None:
    """Health is whitelisted — no Authorization header should still succeed."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200


def test_health_exposes_request_timing_without_query_data(client: TestClient) -> None:
    resp = client.get("/api/v1/health?probe=private-value")

    assert resp.status_code == 200
    assert resp.headers["server-timing"].startswith("stackos-http;dur=")
    assert float(resp.headers["x-stackos-request-duration-ms"]) >= 0
    assert "private-value" not in resp.headers["server-timing"]


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


@pytest.fixture
def imap_transfer_files(settings: Settings) -> Iterator[None]:
    """Create representative raw staging files beneath the reserved subtree."""
    transfer_dir = settings.generated_assets_dir / "imap-transfers/project-1/transfer-1"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    (transfer_dir / "original.eml").write_bytes(b"private original")
    (transfer_dir / "attachment-001").write_bytes(b"private attachment")
    yield


@pytest.mark.parametrize("filename", ["original.eml", "attachment-001"])
@pytest.mark.parametrize(
    "request_path",
    [
        "/generated-assets/imap-transfers/project-1/transfer-1/{filename}",
        "/generated-assets//imap-transfers/project-1/transfer-1/{filename}",
        "/generated-assets/public/../imap-transfers/project-1/transfer-1/{filename}",
        # HTTPX preserves the encoded segment until ASGI decoding; StaticFiles
        # must then normalize it before the guard evaluates the first component.
        "/generated-assets/public/%2e%2e/imap-transfers/project-1/transfer-1/{filename}",
        "/generated-assets/%69map-transfers/project-1/transfer-1/{filename}",
    ],
    ids=[
        "normal",
        "double-slash",
        "dot-segment",
        "encoded-dot-segment",
        "percent-encoded",
    ],
)
def test_imap_transfer_staging_is_never_http_served(
    client: TestClient,
    auth_token: str,
    imap_transfer_files: None,
    request_path: str,
    filename: str,
) -> None:
    """Normalized staging paths stay private even with daemon bearer authority."""
    path = request_path.format(filename=filename)

    assert client.get(path).status_code == 404
    assert client.get(path, headers={"authorization": f"Bearer {auth_token}"}).status_code == 404


@pytest.mark.parametrize("filename", ["original.eml", "attachment-001"])
def test_imap_transfer_staging_is_not_served_through_symlink_alias(
    client: TestClient,
    settings: Settings,
    auth_token: str,
    imap_transfer_files: None,
    filename: str,
) -> None:
    """A public-looking alias cannot expose a resolved staging target."""
    alias = settings.generated_assets_dir / "receipt-preview"
    try:
        alias.symlink_to(
            settings.generated_assets_dir / "imap-transfers/project-1/transfer-1",
            target_is_directory=True,
        )
    except NotImplementedError as exc:
        pytest.skip(f"platform cannot create a directory symlink: {exc}")
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.ENOTSUP, errno.EPERM}:
            pytest.skip(f"platform cannot create a directory symlink: {exc}")
        raise

    path = f"/generated-assets/receipt-preview/{filename}"
    assert client.get(path).status_code == 404
    assert client.get(path, headers={"authorization": f"Bearer {auth_token}"}).status_code == 404


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
