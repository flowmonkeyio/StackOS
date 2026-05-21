"""REST route tests for StackOS plugin/catalog endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_plugin_catalog_routes(api: TestClient) -> None:
    plugins = api.get("/api/v1/plugins")
    assert plugins.status_code == 200
    assert [p["slug"] for p in plugins.json()] == ["core", "seo", "utils"]

    catalog = api.get("/api/v1/catalog/utils")
    assert catalog.status_code == 200
    assert catalog.json()["plugin"]["slug"] == "utils"
    assert {r["key"] for r in catalog.json()["resources"]} >= {"generated-image", "web-document"}

    providers = api.get("/api/v1/providers", params={"plugin_slug": "utils"})
    assert providers.status_code == 200
    assert {p["key"] for p in providers.json()} >= {"openai-images", "firecrawl"}

    capability = api.get(
        "/api/v1/capabilities/seo-content",
        params={"plugin_slug": "seo"},
    )
    assert capability.status_code == 200
    assert capability.json()["plugin_slug"] == "seo"


def test_project_plugin_enable_disable_routes(api: TestClient, project_id: int) -> None:
    enabled = api.post(f"/api/v1/projects/{project_id}/plugins/utils/enable", json={})
    assert enabled.status_code == 200
    assert enabled.json()["data"]["enabled"] is True

    annotated = api.get("/api/v1/plugins", params={"project_id": project_id}).json()
    utils = next(p for p in annotated if p["slug"] == "utils")
    assert utils["enabled_for_project"] is True

    disabled = api.post(f"/api/v1/projects/{project_id}/plugins/utils/disable")
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False
