"""REST route tests for generic StackOS resources and artifacts."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_resource_record_routes(api: TestClient, project_id: int) -> None:
    resources = api.get("/api/v1/resources", params={"plugin_slug": "core"})
    assert resources.status_code == 200
    assert {row["key"] for row in resources.json()} >= {"learning", "experiment"}

    created = api.post(
        f"/api/v1/projects/{project_id}/resource-records",
        json={
            "plugin_slug": "core",
            "resource_key": "learning",
            "external_id": "lesson-1",
            "title": "Learning",
            "data_json": {"body": "Keep the workflow flexible.", "api_key": "secret"},
        },
    )
    assert created.status_code == 200
    record = created.json()["data"]
    assert record["resource_key"] == "learning"

    fetched = api.get(f"/api/v1/resource-records/{record['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["data_json"]["body"] == "Keep the workflow flexible."
    assert fetched.json()["data_json"]["api_key"] == "[redacted]"

    listing = api.get(
        f"/api/v1/projects/{project_id}/resource-records",
        params={"plugin_slug": "core", "resource_key": "learning"},
    )
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [record["id"]]


def test_artifact_routes_redact_metadata(api: TestClient, project_id: int) -> None:
    created = api.post(
        f"/api/v1/projects/{project_id}/artifacts",
        json={
            "plugin_slug": "utils",
            "kind": "image",
            "uri": "/generated-assets/asset.png",
            "metadata_json": {"width": 1024, "api_key": "secret"},
            "provenance_json": {"provider": "openai-images", "token": "secret"},
        },
    )
    assert created.status_code == 201
    artifact = created.json()["data"]
    assert artifact["metadata_json"]["api_key"] == "[redacted]"
    assert artifact["provenance_json"]["token"] == "[redacted]"

    fetched = api.get(f"/api/v1/artifacts/{artifact['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["uri"] == "/generated-assets/asset.png"

    listing = api.get(f"/api/v1/projects/{project_id}/artifacts", params={"kind": "image"})
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [artifact["id"]]
