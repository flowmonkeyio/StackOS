"""Repository tests for StackOS workflow template loading/storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from content_stack.repositories.base import ConflictError
from content_stack.workflows.template_loader import WorkflowTemplateLoader
from content_stack.workflows.template_schema import WorkflowTemplateSpec


def _project_template(key: str = "company.project-memory-review") -> WorkflowTemplateSpec:
    return WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": key,
            "name": "Company Project Memory Review",
            "version": "0.1.0",
            "context_requirements": [
                {
                    "id": "learnings",
                    "source": "learnings",
                    "fields": ["statement"],
                    "max_items": 5,
                }
            ],
            "steps": [
                {
                    "id": "review",
                    "title": "Review company memory",
                    "context_refs": ["learnings"],
                    "output_refs": ["summary"],
                }
            ],
            "outputs": [{"key": "summary", "type": "object"}],
        }
    )


def _write_repo_override(root: Path, *, name: str = "Repo Override") -> Path:
    path = root / ".stackos" / "workflows" / "project-memory-review.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"""
schema_version: stackos.workflow-template.v1
key: core.project-memory-review
name: {name}
version: 0.1.0
steps:
  - id: review
    title: Review from repo
outputs:
  - key: summary
    type: object
""",
        encoding="utf-8",
    )
    return path


def test_builtin_templates_can_be_listed_and_described(session: Session) -> None:
    repo = WorkflowTemplateLoader(session)

    listing = repo.list_templates(plugin_slug="core")
    described = repo.describe_template(key="core.project-memory-review")
    gtm_listing = repo.list_templates(plugin_slug="gtm")
    gtm_described = repo.describe_template(
        key="gtm.account-research",
        plugin_slug="gtm",
    )
    media_listing = repo.list_templates(plugin_slug="media-buying")
    media_described = repo.describe_template(
        key="media-buying.campaign-launch",
        plugin_slug="media-buying",
    )
    communications_listing = repo.list_templates(plugin_slug="communications")
    communications_described = repo.describe_template(
        key="communications.inbox-review",
        plugin_slug="communications",
    )

    assert [item.key for item in listing.templates] == ["core.project-memory-review"]
    assert described.summary.source == "plugin"
    assert described.spec.context_requirements
    assert described.spec.steps[0].id == "clarify-goal"
    assert [item.key for item in gtm_listing.templates] == [
        "gtm.account-research",
        "gtm.crm-hygiene-pass",
        "gtm.lead-enrichment-scoring",
        "gtm.outbound-sequence-preparation",
        "gtm.pipeline-risk-review",
    ]
    assert gtm_described.summary.plugin_slug == "gtm"
    assert gtm_described.spec.action_contracts[0].action == "web.read"
    assert all("payload" not in step.model_dump_json() for step in gtm_described.spec.steps)
    assert [item.key for item in media_listing.templates] == [
        "media-buying.budget-reallocation-review",
        "media-buying.campaign-launch",
        "media-buying.creative-variant-generation",
        "media-buying.landing-page-creative-experiment",
        "media-buying.performance-diagnosis",
    ]
    assert media_described.summary.plugin_slug == "media-buying"
    assert media_described.spec.action_contracts[0].action == "meta.campaign.create"
    assert all("payload" not in step.model_dump_json() for step in media_described.spec.steps)
    assert [item.key for item in communications_listing.templates] == [
        "communications.callback-follow-up",
        "communications.inbox-review",
        "communications.outbound-notification",
        "communications.rich-telegram-reply",
    ]
    assert communications_described.summary.plugin_slug == "communications"
    assert communications_described.spec.action_contracts[0].action == "imap.messages.search"
    assert all(
        "payload" not in step.model_dump_json() for step in communications_described.spec.steps
    )


def test_repo_templates_override_plugin_templates(session: Session, tmp_path: Path) -> None:
    _write_repo_override(tmp_path)
    repo = WorkflowTemplateLoader(session)

    effective = repo.describe_template(
        key="core.project-memory-review",
        repo_root=str(tmp_path),
    )
    all_versions = repo.list_templates(repo_root=str(tmp_path), include_shadowed=True).templates

    assert effective.summary.source == "repo"
    assert effective.summary.name == "Repo Override"
    shadowed = [item for item in all_versions if item.key == "core.project-memory-review"]
    assert {item.source for item in shadowed} == {"plugin", "repo"}
    assert next(item for item in shadowed if item.source == "plugin").shadowed_by == "repo"


def test_project_templates_save_and_fork_without_execution(
    session: Session,
    project_id: int,
) -> None:
    repo = WorkflowTemplateLoader(session)

    saved = repo.save_project_template(
        project_id=project_id,
        spec=_project_template(),
        created_by="unit-test",
    ).data
    forked = repo.fork_template(
        project_id=project_id,
        key="core.project-memory-review",
        new_key="company.project-memory-review-fork",
    ).data

    assert saved.summary.source == "project"
    assert saved.summary.project_id == project_id
    assert forked.spec.based_on is not None
    assert forked.spec.based_on.key == "core.project-memory-review"
    assert all("payload" not in step.model_dump_json() for step in saved.spec.steps)


def test_template_save_requires_new_version_for_changed_content(
    session: Session,
    project_id: int,
) -> None:
    repo = WorkflowTemplateLoader(session)
    spec = _project_template()
    repo.save_project_template(project_id=project_id, spec=spec)
    changed = _project_template()
    changed.description = "Changed without version bump"

    with pytest.raises(ConflictError):
        repo.save_project_template(project_id=project_id, spec=changed)


def test_project_template_storage_rejects_runtime_payloads(
    session: Session,
    project_id: int,
) -> None:
    data = _project_template("company.bad-runtime-template").model_dump(mode="json")
    data["metadata_json"] = {"provider_object_id": "campaign_123"}

    result = WorkflowTemplateLoader(session).validate_template(template_json=data)

    assert result.valid is False
    assert "provider object ids" in result.errors[0].message
