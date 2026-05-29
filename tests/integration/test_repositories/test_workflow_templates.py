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
    support_described = repo.describe_template(
        key="engineering.customer-support-investigation",
        plugin_slug="engineering",
    )
    engineering_described = repo.describe_template(
        key="engineering.tracked-delivery",
        plugin_slug="engineering",
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
        "engineering.customer-support-investigation",
        "engineering.tracked-delivery",
    ]
    assert support_described.summary.plugin_slug == "engineering"
    assert support_described.spec.agent_requirements[0].agent_preset_ref == (
        "communications.workflow.customer-support-thread"
    )
    support_agent_refs = {
        item.agent_preset_ref for item in support_described.spec.agent_requirements
    }
    assert support_agent_refs == {
        "communications.workflow.customer-support-thread",
        "stackos.sdlc.support-investigation-analyst",
        "stackos.sdlc.codebase-explorer",
        "stackos.sdlc.planning",
        "stackos.sdlc.delivery-reviewer",
    }
    support_step_ids = [step.id for step in support_described.spec.steps]
    assert support_step_ids == [
        "intake-feedback",
        "establish-canonical-thread",
        "add-investigation-reaction",
        "read-full-thread",
        "ask-thread-clarifications",
        "investigate-support-issue",
        "post-support-conclusion-in-thread",
        "wait-for-thread-instructions",
        "create-delivery-task",
        "post-task-handoff-in-thread",
        "add-task-created-reaction",
        "conclude-and-handoff",
    ]
    assert support_described.spec.metadata_json["workflow_family"] == (
        "customer_support_investigation"
    )
    assert support_described.spec.metadata_json["handoff_workflow"] == (
        "engineering.tracked-delivery"
    )
    assert support_described.spec.metadata_json["canonical_surface"] == "slack_thread"
    assert support_described.spec.metadata_json["investigation_reaction"] == "eyes"
    assert support_described.spec.metadata_json["agent_subset"] == [
        "communications-customer-support-thread",
        "support-investigation-analyst",
        "codebase-explorer",
        "planning",
        "delivery-reviewer",
    ]
    policies = {policy.key for policy in support_described.spec.policies}
    assert "chat_reference_continuity" in policies
    assert "media_handoff_fidelity" in policies
    support_steps = {step.id: step for step in support_described.spec.steps}
    assert all(not step.approval_refs for step in support_steps.values())
    assert "source_media_refs" in support_steps["intake-feedback"].input_refs
    intake_instructions = " ".join(support_steps["intake-feedback"].instructions)
    assert "communicationTarget.resolve" in intake_instructions
    assert "media" in intake_instructions
    canonical_instructions = " ".join(support_steps["establish-canonical-thread"].instructions)
    assert "target resolution alone is not enough" in canonical_instructions
    assert "Preflight media forwarding" in canonical_instructions
    assert "same canonical Slack handoff" in canonical_instructions
    assert "must not send a separate `chat.postMessage` first" in canonical_instructions
    assert support_steps["establish-canonical-thread"].action_refs == [
        "post_canonical_message",
        "upload_canonical_media",
        "download_telegram_media",
    ]
    assert support_steps["ask-thread-clarifications"].depends_on == ["read-full-thread"]
    assert support_steps["ask-thread-clarifications"].action_refs == ["post_thread_reply"]
    assert "chat_reference_continuity" in (support_steps["create-delivery-task"].policy_refs)
    assert "media_handoff_fidelity" in (support_steps["create-delivery-task"].policy_refs)
    assert "source media refs" in " ".join(support_steps["create-delivery-task"].instructions)
    assert "references_json" in " ".join(support_steps["create-delivery-task"].instructions)
    assert support_steps["post-task-handoff-in-thread"].depends_on == ["create-delivery-task"]
    assert support_steps["add-task-created-reaction"].depends_on == ["post-task-handoff-in-thread"]
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
        key="engineering.customer-support-investigation",
        plugin_slug="engineering",
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
        workflow_key="engineering.customer-support-investigation",
        plugin_slug="engineering",
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
            "description": "Project-specific customer support investigation flow.",
            "steps": steps,
            "when_to_use": ["Customer feedback needs project-specific triage."],
        },
        metadata_json={"owner": "support"},
        created_by="unit-test",
    ).data
    described = repo.describe_template(
        project_id=project_id,
        key="engineering.customer-support-investigation",
        plugin_slug="engineering",
    )
    listed = repo.list_templates(project_id=project_id, plugin_slug="engineering")

    assert saved.workflow_key == "engineering.customer-support-investigation"
    assert saved.enabled is True
    assert described.project_extension is not None
    assert described.project_extension.id == saved.id
    assert described.spec.key == "engineering.customer-support-investigation"
    assert described.spec.description == "Project-specific customer support investigation flow."
    assert described.spec.when_to_use == ["Customer feedback needs project-specific triage."]
    described_canonical_step = next(
        step for step in described.spec.steps if step.id == "establish-canonical-thread"
    )
    assert described_canonical_step.title == "Establish Project Canonical Thread"
    support_summary = next(
        item
        for item in listed.templates
        if item.key == "engineering.customer-support-investigation"
    )
    assert support_summary.description == "Project-specific customer support investigation flow."
    assert support_summary.project_extension_id == saved.id
    assert support_summary.project_extension_enabled is True


def test_project_workflow_extension_validation_rejects_identity_change(
    session: Session,
    project_id: int,
) -> None:
    result = WorkflowTemplateLoader(session).validate_extension(
        project_id=project_id,
        workflow_key="engineering.customer-support-investigation",
        plugin_slug="engineering",
        template_overrides_json={"key": "engineering.other-support-flow"},
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
        workflow_key="engineering.customer-support-investigation",
        plugin_slug="engineering",
        template_overrides_json={
            "metadata": {"project_override": True},
            "agent_requirements": [
                {
                    "role": "support-investigation",
                    "requirement": "required",
                    "agent_preset_ref": "stackos.sdlc.support-investigation-analyst",
                    "purpose": "Investigate customer feedback with project-specific context.",
                    "applies_to_steps": ["investigate-support-issue"],
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
        key="engineering.customer-support-investigation",
        plugin_slug="engineering",
    )

    assert saved.template_overrides_json["metadata"]["project_override"] is True
    assert described.spec.metadata_json == {"project_override": True}
    assert [item.role for item in described.spec.agent_requirements] == ["support-investigation"]
    assert described.spec.agent_requirements[0].agent_preset_ref == (
        "stackos.sdlc.support-investigation-analyst"
    )
    assert described.spec.skill_requirements[0].skill_ref == "stackos:stackos"


def test_project_workflow_extension_validation_rejects_unknown_steps(
    session: Session,
    project_id: int,
) -> None:
    result = WorkflowTemplateLoader(session).validate_extension(
        project_id=project_id,
        workflow_key="engineering.customer-support-investigation",
        plugin_slug="engineering",
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
