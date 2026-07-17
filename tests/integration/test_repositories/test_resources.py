"""Repository tests for generic StackOS resources and artifacts."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from stackos.repositories.base import ValidationError
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


def test_website_seo_analysis_resource_requires_durable_reviewed_evidence(
    session: Session,
    project_id: int,
) -> None:
    repo = ResourceRepository(session)
    valid = {
        "site_url": "https://example.org",
        "analyzed_at": "2026-07-16T22:00:00Z",
        "status": "partial",
        "source_ledger": [
            {
                "source": "public-browser",
                "evaluated_at": "2026-07-16T21:30:00Z",
                "status": "used",
                "evidence_class": "public-observation",
                "coverage": "Homepage and one representative page",
                "limitations": ["Sampled coverage"],
                "evidence_refs": ["evidence-public-home"],
            }
        ],
        "evidence_index": [
            {
                "evidence_ref": "evidence-public-home",
                "kind": "browser-receipt",
                "source": "public-browser",
                "captured_at": "2026-07-16T21:30:00Z",
                "lifecycle_state": "current",
                "scope": {"urls": ["https://example.org/"]},
                "receipt_ref": "browser-receipt-17",
                "limitations": [],
            }
        ],
        "review_summary": {
            "reviewer_role": "delivery-reviewer",
            "reviewed_at": "2026-07-16T21:50:00Z",
            "dispositions": [],
            "unresolved_evidence_gaps": [],
            "residual_limitations": ["Sampled coverage"],
        },
        "executive_summary": {
            "overall_state": "Partial public baseline",
            "strengths": [],
            "primary_risks": [],
            "recommended_sequence": [],
            "evidence_quality": {"coverage": "sampled"},
            "limitations": ["Sampled coverage"],
        },
        "finding_counts": {"total": 0, "measured": 0, "observed": 0, "inferred": 0},
        "prioritized_actions": [],
        "limitations": ["Sampled coverage"],
        "artifact_refs": {"final_report": "artifact:seo-report-1"},
    }

    created = repo.upsert_record(
        project_id=project_id,
        plugin_slug="seo",
        resource_key="website-seo-analysis",
        external_id="example-org-2026-07-16",
        title="Example.org SEO analysis",
        data_json=valid,
    ).data
    assert created.data_json["status"] == "partial"

    invalid_path_ref = {**valid, "raw_provider_data_refs": ["/tmp/provider.json"]}
    with pytest.raises(ValidationError):
        repo.upsert_record(
            project_id=project_id,
            plugin_slug="seo",
            resource_key="website-seo-analysis",
            external_id="invalid-path-ref",
            title="Invalid path evidence",
            data_json=invalid_path_ref,
        )

    invalid_action_receipt = {
        **valid,
        "evidence_index": [
            {
                "evidence_ref": "evidence-gsc",
                "kind": "action-receipt",
                "source": "google-search-console",
                "captured_at": "2026-07-16T21:30:00Z",
                "lifecycle_state": "current",
                "scope": {"property": "sc-domain:example.org"},
                "receipt_ref": "action-call:12",
                "action_ref": "seo.search-console.search-analytics.query",
                "limitations": [],
            }
        ],
    }
    with pytest.raises(ValidationError):
        repo.upsert_record(
            project_id=project_id,
            plugin_slug="seo",
            resource_key="website-seo-analysis",
            external_id="invalid-action-receipt",
            title="Missing action call id",
            data_json=invalid_action_receipt,
        )

    invalid_complete = {**valid, "status": "complete"}
    with pytest.raises(ValidationError):
        repo.upsert_record(
            project_id=project_id,
            plugin_slug="seo",
            resource_key="website-seo-analysis",
            external_id="invalid-complete",
            title="Incomplete complete package",
            data_json=invalid_complete,
        )


def test_resource_upsert_enforces_plugin_resource_schema(
    session: Session,
    project_id: int,
) -> None:
    repo = ResourceRepository(session)
    valid = {
        "schema_version": "stackos.engineering-evidence.v2",
        "title": "Focused verification",
        "evidence_type": "automated-test",
        "evidence_mode": "executed",
        "repository_bound": True,
        "summary": "The focused checks passed.",
        "status": "passed",
        "lifecycle_state": "current",
        "repository_state": {
            "head_commit": "a" * 40,
            "staged_diff_checksum": f"sha256:{'b' * 64}",
            "unstaged_diff_checksum": f"sha256:{'c' * 64}",
            "untracked_manifest_checksum": f"sha256:{'d' * 64}",
            "migration_head": None,
            "generated_contract_revision": None,
            "captured_at": "2026-07-16T21:00:00Z",
        },
        "execution": {
            "status": "executed",
            "command_or_run_ref": (
                "uv run pytest tests/integration/test_repositories/test_resources.py -q"
            ),
            "pass_count": 1,
            "fail_count": 0,
            "skipped_count": 0,
            "output_checksum": f"sha256:{'e' * 64}",
            "executed_at": "2026-07-16T21:05:00Z",
        },
        "scope": {
            "covered_paths": ["stackos/repositories/resources.py"],
            "flow_refs": ["resource-write"],
            "ticket_keys": ["engineering-evidence-contract"],
            "run_plan_id": 211,
        },
        "checked_at": "2026-07-16T21:05:00Z",
    }

    created = repo.upsert_record(
        project_id=project_id,
        plugin_slug="engineering",
        resource_key="engineering-evidence",
        external_id="focused-verification",
        data_json=valid,
    ).data
    assert created.data_json["schema_version"] == "stackos.engineering-evidence.v2"

    with pytest.raises(ValidationError, match="resource data") as exc_info:
        repo.upsert_record(
            project_id=project_id,
            plugin_slug="engineering",
            resource_key="engineering-evidence",
            external_id="invalid-verification",
            data_json={"title": "Unsupported assertion"},
        )

    assert exc_info.value.data["resource_key"] == "engineering-evidence"
    assert {issue["path"] for issue in exc_info.value.data["issues"]} >= {
        "$.schema_version",
        "$.evidence_mode",
        "$.scope",
    }


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
