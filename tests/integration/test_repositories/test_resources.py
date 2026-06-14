"""Repository tests for generic StackOS resources and artifacts."""

from __future__ import annotations

from sqlmodel import Session

from stackos.repositories.resources import ArtifactRepository, ResourceRepository


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
    artifact = (
        ArtifactRepository(session)
        .create(
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
        )
        .data
    )

    assert artifact.plugin_slug == "utils"
    assert artifact.status == "draft"
    assert artifact.metadata_json == {
        "width": 1024,
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
    }
    assert artifact.provenance_json == {"provider": "openai-images", "access_token": "[redacted]"}

    page = ArtifactRepository(session).query(project_id=project_id, kind="image")
    assert [item.id for item in page.items] == [artifact.id]


def test_artifact_lifecycle_update_supersede_and_archive(
    session: Session,
    project_id: int,
) -> None:
    repo = ArtifactRepository(session)
    original = repo.create(
        project_id=project_id,
        plugin_slug="utils",
        kind="document",
        uri="/durable/content-packet-v1.json",
        metadata_json={"version": 1, "nested": {"status": "rough"}},
    ).data

    updated = repo.update(
        original.id,
        project_id=project_id,
        fields={"status", "metadata_patch_json", "provenance_patch_json"},
        status="approved",
        metadata_patch_json={
            "nested": {"status": "approved"},
            "api_key": "hidden",
        },
        provenance_patch_json={"reviewer": "operator", "access_token": "hidden"},
    ).data

    assert updated.id == original.id
    assert updated.status == "approved"
    assert updated.metadata_json == {
        "version": 1,
        "nested": {"status": "approved"},
        "api_key": "[redacted]",
    }
    assert updated.provenance_json == {
        "reviewer": "operator",
        "access_token": "[redacted]",
    }
    assert updated.updated_at >= original.updated_at

    replacement = repo.create(
        project_id=project_id,
        plugin_slug="utils",
        kind="document",
        uri="/durable/content-packet-v2.json",
        status="approved",
    ).data
    superseded = repo.supersede(
        original.id,
        replacement_artifact_id=replacement.id,
        project_id=project_id,
        reason="new approved packet",
    ).data

    assert superseded.status == "superseded"
    assert superseded.superseded_by_artifact_id == replacement.id
    assert superseded.metadata_json["lifecycle"]["replacement_artifact_id"] == replacement.id
    assert superseded.metadata_json["lifecycle"]["supersede_reason"] == "new approved packet"
    assert [item.id for item in repo.query(project_id=project_id).items] == [replacement.id]
    assert [item.id for item in repo.query(project_id=project_id, include_inactive=True).items] == [
        original.id,
        replacement.id,
    ]
    assert [item.id for item in repo.query(project_id=project_id, status="superseded").items] == [
        original.id
    ]

    archived = repo.archive(
        replacement.id,
        project_id=project_id,
        reason="operator cleanup",
    ).data

    assert archived.status == "archived"
    assert archived.metadata_json["lifecycle"]["archive_reason"] == "operator cleanup"
    assert repo.query(project_id=project_id).items == []
    assert [item.id for item in repo.query(project_id=project_id, status="archived").items] == [
        replacement.id
    ]
