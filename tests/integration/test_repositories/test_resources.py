"""Repository tests for generic StackOS resources and artifacts."""

from __future__ import annotations

from sqlmodel import Session

from content_stack.repositories.resources import ArtifactRepository, ResourceRepository


def test_resource_catalog_and_record_upsert(session: Session, project_id: int) -> None:
    repo = ResourceRepository(session)

    resources = repo.list_resources(plugin_slug="core")
    assert {resource.key for resource in resources} >= {"learning", "experiment"}

    created = repo.upsert_record(
        project_id=project_id,
        plugin_slug="core",
        resource_key="learning",
        external_id="lesson-1",
        title="Hook lesson",
        data_json={
            "body": "Specific hooks beat vague hooks.",
            "api_key": "should-not-leak",
        },
        provenance_json={"source": "run-1", "access_token": "should-not-leak"},
    ).data
    assert created.resource_key == "learning"
    assert created.data_json["api_key"] == "[redacted]"
    assert created.provenance_json == {"source": "run-1", "access_token": "[redacted]"}

    updated = repo.upsert_record(
        project_id=project_id,
        plugin_slug="core",
        resource_key="learning",
        external_id="lesson-1",
        title="Hook lesson updated",
        data_json={"body": "Numbers beat vague hooks."},
    ).data

    assert updated.id == created.id
    assert updated.data_json["body"] == "Numbers beat vague hooks."

    page = repo.query_records(project_id=project_id, plugin_slug="core", resource_key="learning")
    assert [record.id for record in page.items] == [created.id]


def test_artifact_create_query_and_redaction(session: Session, project_id: int) -> None:
    artifact = ArtifactRepository(session).create(
        project_id=project_id,
        plugin_slug="utils",
        kind="image",
        uri="/generated-assets/example.png",
        metadata_json={
            "width": 1024,
            "api_key": "secret-value",
            "nested": {"authorization": "Bearer nope"},
        },
        provenance_json={"provider": "openai-images", "access_token": "tok"},
    ).data

    assert artifact.plugin_slug == "utils"
    assert artifact.metadata_json == {
        "width": 1024,
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
    }
    assert artifact.provenance_json == {"provider": "openai-images", "access_token": "[redacted]"}

    page = ArtifactRepository(session).query(project_id=project_id, kind="image")
    assert [item.id for item in page.items] == [artifact.id]
