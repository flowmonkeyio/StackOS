"""Repository tests for StackOS workflow template loading/storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.repositories.base import ConflictError
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec


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
    engineering_listing = repo.list_templates(plugin_slug="engineering")
    engineering_described = repo.describe_template(
        key="engineering.tracked-delivery",
        plugin_slug="engineering",
    )
    support_listing = repo.list_templates(plugin_slug="support")
    support_investigation_described = repo.describe_template(
        key="support.issue-investigation",
        plugin_slug="support",
    )
    support_handoff_described = repo.describe_template(
        key="support.delivery-task-handoff",
        plugin_slug="support",
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
    assert described.spec.agent_requirements[0].agent_preset_ref == (
        "stackos.workflow.project-memory-review"
    )
    assert described.spec.skill_requirements[0].skill_ref == "stackos:stackos"
    assert described.spec.steps[0].id == "clarify-goal"
    assert [item.key for item in engineering_listing.templates] == [
        "engineering.tracked-delivery",
    ]
    intake_described = repo.describe_template(
        key="communications.customer-feedback-intake",
        plugin_slug="communications",
    )
    assert intake_described.summary.plugin_slug == "communications"
    assert intake_described.spec.agent_requirements[0].agent_preset_ref == (
        "communications.workflow.customer-feedback-intake"
    )
    assert [step.id for step in intake_described.spec.steps] == [
        "capture-feedback",
        "establish-canonical-thread",
        "add-intake-reaction",
        "prepare-investigation-handoff",
    ]
    assert intake_described.spec.metadata_json["next_workflow"] == ("support.issue-investigation")
    intake_steps = {step.id: step for step in intake_described.spec.steps}
    assert "source_media_refs" in intake_steps["capture-feedback"].input_refs
    assert intake_steps["establish-canonical-thread"].action_refs == [
        "send_canonical_handoff",
        "upload_canonical_media",
        "download_source_media",
    ]
    assert "media_handoff_fidelity" in intake_steps["establish-canonical-thread"].policy_refs

    assert [item.key for item in support_listing.templates] == [
        "support.delivery-task-handoff",
        "support.issue-investigation",
    ]
    assert support_investigation_described.summary.plugin_slug == "support"
    support_investigation_agent_refs = {
        item.agent_preset_ref for item in support_investigation_described.spec.agent_requirements
    }
    assert support_investigation_agent_refs == {
        "support.workflow.issue-investigator",
        "stackos.sdlc.codebase-explorer",
    }
    assert [step.id for step in support_investigation_described.spec.steps] == [
        "read-canonical-thread",
        "clarify-missing-context",
        "investigate-issue",
        "post-support-conclusion",
    ]
    assert support_investigation_described.spec.metadata_json["previous_workflow"] == (
        "communications.customer-feedback-intake"
    )
    assert support_investigation_described.spec.metadata_json["next_workflow"] == (
        "support.delivery-task-handoff"
    )
    investigation_policies = {
        policy.key for policy in support_investigation_described.spec.policies
    }
    assert "full_thread_source_of_truth" in investigation_policies
    assert "no_task_creation_in_investigation" in investigation_policies
    investigation_steps = {step.id: step for step in support_investigation_described.spec.steps}
    assert investigation_steps["clarify-missing-context"].action_refs == ["post_thread_reply"]
    assert investigation_steps["investigate-issue"].depends_on == ["clarify-missing-context"]

    support_handoff_agent_refs = {
        item.agent_preset_ref for item in support_handoff_described.spec.agent_requirements
    }
    assert support_handoff_agent_refs == {
        "support.workflow.delivery-handoff",
        "stackos.sdlc.planning",
    }
    assert [step.id for step in support_handoff_described.spec.steps] == [
        "confirm-thread-instruction",
        "create-delivery-task",
        "post-task-handoff",
        "add-task-created-reaction",
    ]
    assert support_handoff_described.spec.metadata_json["previous_workflow"] == (
        "support.issue-investigation"
    )
    assert support_handoff_described.spec.metadata_json["next_workflow"] == (
        "engineering.tracked-delivery"
    )
    assert support_handoff_described.spec.metadata_json["agent_subset"] == [
        "support-delivery-handoff",
        "planning",
    ]
    handoff_steps = {step.id: step for step in support_handoff_described.spec.steps}
    assert "chat_reference_continuity" in handoff_steps["create-delivery-task"].policy_refs
    assert handoff_steps["post-task-handoff"].depends_on == ["create-delivery-task"]
    assert handoff_steps["add-task-created-reaction"].depends_on == ["post-task-handoff"]
    assert support_investigation_described.spec.metadata_json["agent_subset"] == [
        "support-issue-investigator",
        "codebase-explorer",
    ]
    assert engineering_described.summary.plugin_slug == "engineering"
    assert engineering_described.spec.agent_requirements[0].agent_preset_ref == (
        "stackos.sdlc.requirements-flow-definer"
    )
    engineering_agent_refs = {
        item.agent_preset_ref for item in engineering_described.spec.agent_requirements
    }
    assert engineering_agent_refs == {
        "stackos.sdlc.requirements-flow-definer",
        "stackos.sdlc.codebase-explorer",
        "stackos.sdlc.planning",
        "stackos.sdlc.architecture",
        "stackos.sdlc.test-designer",
        "stackos.sdlc.delivery",
        "stackos.sdlc.delivery-reviewer",
        "stackos.sdlc.release-ops",
    }
    assert engineering_described.spec.skill_requirements[0].skill_ref == "stackos:stackos"
    engineering_setup_notes = "\n".join(
        engineering_described.spec.skill_requirements[0].setup_notes
    )
    assert ".codex/config.toml" in engineering_setup_notes
    assert ".codex/agents/*.toml" in engineering_setup_notes
    assert "operation.list" in engineering_setup_notes
    assert "resource.query" in engineering_setup_notes
    assert "resource.upsert" in engineering_setup_notes
    assert "artifact.create" in engineering_setup_notes
    assert "decision.record" in engineering_setup_notes
    assert engineering_described.spec.steps[0].id == "scope-work"
    engineering_step_ids = [step.id for step in engineering_described.spec.steps]
    assert engineering_step_ids == [
        "scope-work",
        "define-requirements",
        "discover-impact",
        "plan-tickets",
        "design-approach",
        "review-design",
        "design-tests",
        "deliver-tickets",
        "verify-delivery",
        "review-delivery",
        "audit-tracker",
        "release-closeout",
    ]
    assert engineering_described.spec.metadata_json["workflow_family"] == "sdlc"
    plan_step = next(step for step in engineering_described.spec.steps if step.id == "plan-tickets")
    plan_text = plan_step.model_dump_json()
    assert "attachment only" in plan_text
    assert "bridge child tickets" in plan_text
    assert "detached branches" in plan_text
    audit_step = next(
        step for step in engineering_described.spec.steps if step.id == "audit-tracker"
    )
    assert "detached workflow step ticket" in audit_step.model_dump_json()
    assert engineering_described.spec.metadata_json["agent_subset"] == [
        "requirements-flow-definer",
        "codebase-explorer",
        "planning",
        "architecture",
        "test-designer",
        "delivery",
        "delivery-reviewer",
        "release-ops",
    ]
    assert [item.key for item in gtm_listing.templates] == [
        "gtm.account-research",
        "gtm.crm-hygiene-pass",
        "gtm.lead-enrichment-scoring",
        "gtm.outbound-sequence-preparation",
        "gtm.pipeline-risk-review",
    ]
    assert gtm_described.summary.plugin_slug == "gtm"
    assert gtm_described.spec.agent_requirements[0].agent_preset_ref == (
        "gtm.workflow.account-research"
    )
    assert gtm_described.spec.skill_requirements[0].skill_ref == "stackos:stackos"
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
    assert media_described.spec.agent_requirements[0].agent_preset_ref == (
        "media-buying.workflow.campaign-launch"
    )
    assert media_described.spec.skill_requirements[0].skill_ref == "stackos:stackos"
    assert media_described.spec.action_contracts[0].action == "meta.campaign.create"
    assert all("payload" not in step.model_dump_json() for step in media_described.spec.steps)
    assert [item.key for item in communications_listing.templates] == [
        "communications.callback-follow-up",
        "communications.customer-feedback-intake",
        "communications.inbox-review",
        "communications.outbound-notification",
        "communications.rich-telegram-reply",
    ]
    assert communications_described.summary.plugin_slug == "communications"
    assert communications_described.spec.agent_requirements[0].agent_preset_ref == (
        "communications.workflow.inbox-review"
    )
    assert communications_described.spec.skill_requirements[0].skill_ref == "stackos:stackos"
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


def test_project_workflow_extension_layers_over_base_template(
    session: Session,
    project_id: int,
) -> None:
    repo = WorkflowTemplateLoader(session)
    base = repo.describe_template(
        project_id=project_id,
        key="communications.customer-feedback-intake",
        plugin_slug="communications",
        include_extension=False,
    )
    steps = [step.model_dump(mode="json") for step in base.spec.steps]
    canonical_step = next(step for step in steps if step["id"] == "establish-canonical-thread")
    canonical_step["title"] = "Establish Project Canonical Thread"
    canonical_step["instructions"] = [
        "Use the project-specific communication route before starting investigation."
    ]

    saved = repo.upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        required_input_keys_json=["feedback_summary", "communication_route_ref"],
        input_defaults_json={
            "communication_route_ref": "communication-route:support-feedback",
            "canonical_slack_target_ref": "communication-target:support-triage",
        },
        selected_context_json={
            "communication": {
                "route_ref": "communication-route:support-feedback",
                "target_ref": "communication-target:support-triage",
                "surface_context": "Support triage channel; customer-visible data only.",
            }
        },
        guardrails_json={"copy_customer_private_data": False},
        step_overrides_json={
            "establish-canonical-thread": {
                "extra_instructions": [
                    "Use the configured support triage Slack target unless the operator "
                    "overrides it in the current thread."
                ],
                "metadata_json": {"target_selection_source": "project-extension"},
            }
        },
        template_overrides_json={
            "description": "Project-specific customer feedback intake flow.",
            "steps": steps,
            "when_to_use": ["Customer feedback needs project-specific triage."],
        },
        metadata_json={"owner": "support"},
        created_by="unit-test",
    ).data
    described = repo.describe_template(
        project_id=project_id,
        key="communications.customer-feedback-intake",
        plugin_slug="communications",
    )
    listed = repo.list_templates(project_id=project_id, plugin_slug="communications")

    assert saved.workflow_key == "communications.customer-feedback-intake"
    assert saved.enabled is True
    assert described.project_extension is not None
    assert described.project_extension.id == saved.id
    assert described.spec.key == "communications.customer-feedback-intake"
    assert described.spec.description == "Project-specific customer feedback intake flow."
    assert described.spec.when_to_use == ["Customer feedback needs project-specific triage."]
    described_canonical_step = next(
        step for step in described.spec.steps if step.id == "establish-canonical-thread"
    )
    assert described_canonical_step.title == "Establish Project Canonical Thread"
    support_summary = next(
        item for item in listed.templates if item.key == "communications.customer-feedback-intake"
    )
    assert support_summary.description == "Project-specific customer feedback intake flow."
    assert support_summary.project_extension_id == saved.id
    assert support_summary.project_extension_enabled is True

    deleted = repo.delete_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
    ).data
    assert deleted.deleted.id == saved.id
    assert (
        repo.get_extension(
            project_id=project_id,
            workflow_key="communications.customer-feedback-intake",
        )
        is None
    )


def test_project_workflow_extension_validation_rejects_identity_change(
    session: Session,
    project_id: int,
) -> None:
    result = WorkflowTemplateLoader(session).validate_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        template_overrides_json={"key": "communications.other-feedback-flow"},
    )

    assert result.valid is False
    assert result.errors[0].code == "workflow_key_mismatch"


def test_project_workflow_extension_overrides_aliases_and_agent_requirements(
    session: Session,
    project_id: int,
) -> None:
    repo = WorkflowTemplateLoader(session)

    saved = repo.upsert_extension(
        project_id=project_id,
        workflow_key="support.issue-investigation",
        plugin_slug="support",
        template_overrides_json={
            "metadata": {"project_override": True},
            "agent_requirements": [
                {
                    "role": "support-issue-investigator",
                    "requirement": "required",
                    "agent_preset_ref": "support.workflow.issue-investigator",
                    "purpose": "Investigate customer feedback with project-specific context.",
                    "applies_to_steps": ["investigate-issue"],
                    "handoff_notes": ["Use the workflow extension's project channel context."],
                }
            ],
            "skill_requirements": [
                {
                    "skill_ref": "stackos:stackos",
                    "requirement": "recommended",
                    "purpose": "Operate project workflow extensions and run plans.",
                    "setup_notes": [
                        "Read workflowExtension.get before creating a project-scoped run."
                    ],
                }
            ],
        },
        created_by="unit-test",
    ).data
    described = repo.describe_template(
        project_id=project_id,
        key="support.issue-investigation",
        plugin_slug="support",
    )

    assert saved.template_overrides_json["metadata"]["project_override"] is True
    assert described.spec.metadata_json == {"project_override": True}
    assert [item.role for item in described.spec.agent_requirements] == [
        "support-issue-investigator"
    ]
    assert described.spec.agent_requirements[0].agent_preset_ref == (
        "support.workflow.issue-investigator"
    )
    assert described.spec.skill_requirements[0].skill_ref == "stackos:stackos"


def test_project_workflow_extension_validation_rejects_unknown_steps(
    session: Session,
    project_id: int,
) -> None:
    result = WorkflowTemplateLoader(session).validate_extension(
        project_id=project_id,
        workflow_key="support.issue-investigation",
        plugin_slug="support",
        step_overrides_json={"missing-step": {"extra_instructions": ["Nope."]}},
    )

    assert result.valid is False
    assert result.errors[0].code == "unknown_step"


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
