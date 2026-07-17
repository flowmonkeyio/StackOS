"""Repository tests for StackOS workflow template loading/storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.context.repository.utils import _FIELD_MAP
from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.repositories.base import ConflictError
from stackos.workflows.run_plan_schema import run_plan_from_template
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
    media_performance_described = repo.describe_template(
        key="media-buying.performance-diagnosis",
        plugin_slug="media-buying",
    )
    communications_listing = repo.list_templates(plugin_slug="communications")
    communications_described = repo.describe_template(
        key="communications.inbox-review",
        plugin_slug="communications",
    )
    branding_listing = repo.list_templates(plugin_slug="branding")
    branding_content_described = repo.describe_template(
        key="branding.content-production",
        plugin_slug="branding",
    )
    branding_foundation_described = repo.describe_template(
        key="branding.brand-foundation-setup",
        plugin_slug="branding",
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
    assert [item.key for item in branding_listing.templates] == [
        "branding.brand-foundation-setup",
        "branding.content-production",
    ]
    assert branding_foundation_described.spec.public is not None
    assert branding_foundation_described.spec.experience is not None
    assert branding_foundation_described.spec.agent_requirements[0].agent_preset_ref == (
        "branding.profile-architect"
    )
    assert branding_foundation_described.spec.experience.handoffs[0].workflow_key == (
        "branding.content-production"
    )
    foundation_steps = {step.id: step for step in branding_foundation_described.spec.steps}
    assert foundation_steps["inventory-existing-foundation"].resource_refs == []
    assert foundation_steps["interview-and-sample-analysis"].resource_refs == []
    assert foundation_steps["draft-brand-foundation"].resource_refs == []
    assert foundation_steps["adversarial-voice-review"].resource_refs == []
    assert foundation_steps["finalize-and-persist"].resource_refs == ["brand_profile"]
    assert foundation_steps["verify-retrieval"].resource_refs == []
    foundation_policy_keys = {item.key for item in branding_foundation_described.spec.policies}
    assert {"orchestrator_gates_feedback", "handoff_is_not_execution"} <= (foundation_policy_keys)
    foundation_outputs = {item.key: item for item in branding_foundation_described.spec.outputs}
    assert "out_of_scope" in foundation_outputs["voice_review_report"].schema_data["required"]
    assert branding_content_described.summary.plugin_slug == "branding"
    assert branding_content_described.spec.metadata_json["default_branding_workflow"] is True
    assert branding_content_described.spec.metadata_json["workflow_family"] == (
        "content-production"
    )
    assert [step.id for step in branding_content_described.spec.steps] == [
        "interview-capture",
        "research-fact-collection",
        "angle-and-structure",
        "draft-canonical",
        "editorial-review",
        "sanitization-review",
        "produce-optional-images",
        "render-channel-packets",
        "finalize-and-record",
        "execute-publication",
    ]
    branding_content_agent_refs = {
        item.agent_preset_ref for item in branding_content_described.spec.agent_requirements
    }
    assert branding_content_agent_refs == {
        "branding.claim-auditor",
        "branding.channel-strategist",
        "branding.evidence-curator",
        "branding.narrative-writer",
        "branding.sanitization-reviewer",
        "branding.voice-reviewer",
    }
    branding_content_requirements = {
        item.agent_preset_ref: item.requirement
        for item in branding_content_described.spec.agent_requirements
    }
    assert branding_content_requirements["branding.evidence-curator"] == "recommended"
    branding_step_ids = [step.id for step in branding_content_described.spec.steps]
    branding_skill_preset = branding_content_described.spec.skill_preset_requirements[0]
    assert branding_skill_preset.skill_preset_ref == "branding.brand-orchestrator"
    assert branding_skill_preset.applies_to_steps == branding_step_ids
    assert "workflowExtension.selected_context_json" in " ".join(branding_skill_preset.setup_notes)
    assert "Level 2 project overlay" in " ".join(branding_skill_preset.setup_notes)
    assert "branding_overlay_ref" in {item.key for item in branding_content_described.spec.inputs}
    branding_resource_contracts = {
        item.resource for item in branding_content_described.spec.resource_contracts
    }
    assert "distribution-record" in branding_resource_contracts
    branding_content_actions = {
        item.key: item for item in branding_content_described.spec.action_contracts
    }
    assert branding_content_actions["image_generate"].action == "utils.image.generate"
    assert branding_content_actions["image_generate"].optional is True
    assert branding_content_actions["image_generate"].approval_ref is None
    assert branding_content_actions["publish_linkedin"].action == "publish.linkedin"
    assert branding_content_actions["publish_linkedin"].optional is True
    assert branding_content_actions["publish_x"].action == "publish.x"
    assert branding_content_actions["publish_x"].optional is True
    for action_key in ("publish_git_blog", "publish_x", "publish_linkedin", "publish_email"):
        assert branding_content_actions[action_key].approval_ref is None
    branding_content_steps = {step.id: step for step in branding_content_described.spec.steps}
    assert branding_content_steps["produce-optional-images"].action_refs == ["image_generate"]
    assert branding_content_steps["angle-and-structure"].depends_on == ["research-fact-collection"]
    assert "research_source_traceability" in {
        item.key for item in branding_content_described.spec.policies
    }
    assert "durable_resource_memory" in {
        item.key for item in branding_content_described.spec.policies
    }
    assert "publication_automation_first" in {
        item.key for item in branding_content_described.spec.policies
    }
    assert "orchestrator_gates_feedback" in {
        item.key for item in branding_content_described.spec.policies
    }
    assert "intent_scoped_terminal_condition" in {
        item.key for item in branding_content_described.spec.policies
    }
    branding_outputs = {item.key: item for item in branding_content_described.spec.outputs}
    assert "open_questions" in branding_outputs["research_pack"].schema_data["required"]
    assert "out_of_scope" in branding_outputs["editorial_review_report"].schema_data["required"]
    assert branding_content_steps["produce-optional-images"].approval_refs == []
    assert branding_content_steps["finalize-and-record"].approval_refs == []
    assert branding_content_steps["execute-publication"].approval_refs == []
    assert branding_content_described.spec.approval_gates == []
    assert branding_content_described.spec.metadata_json["artifact_grant_policy"] == "explicit"
    assert (
        "durable records, not a scratchpad"
        in (branding_content_described.spec.metadata_json["artifact_lifecycle_guidance"])
    )
    assert (
        "action.execute:image_generate" in (branding_content_described.spec.metadata_json["grants"])
    )
    assert (
        "action.execute:publish_linkedin"
        in (branding_content_described.spec.metadata_json["grants"])
    )
    assert "browser.page.call" in branding_content_described.spec.metadata_json["grants"]
    assert "browser.handle.call" in branding_content_described.spec.metadata_json["grants"]
    assert "browser.script.run" in branding_content_described.spec.metadata_json["grants"]
    assert "browser.script.inject" in branding_content_described.spec.metadata_json["grants"]
    assert "browser.page.screenshot" in branding_content_described.spec.metadata_json["grants"]
    branding_template_tool_grants = {
        item["step_id"]: set(item["tools"])
        for item in branding_content_described.spec.metadata_json["mcp_tool_grants"]
    }
    artifact_lifecycle_tools = {
        "artifact.create",
        "artifact.update",
        "artifact.archive",
        "artifact.supersede",
    }
    assert artifact_lifecycle_tools <= branding_template_tool_grants["produce-optional-images"]
    assert artifact_lifecycle_tools <= branding_template_tool_grants["render-channel-packets"]
    assert artifact_lifecycle_tools <= branding_template_tool_grants["finalize-and-record"]
    assert artifact_lifecycle_tools <= branding_template_tool_grants["execute-publication"]
    assert "decision.record" in branding_template_tool_grants["finalize-and-record"]
    assert "decision.record" in branding_template_tool_grants["execute-publication"]
    assert {
        "browser.runtime.status",
        "browser.method.manifest",
        "browser.profile.list",
        "browser.profile.create",
        "browser.session.list",
        "browser.session.start",
        "browser.session.status",
        "browser.session.stop",
        "browser.page.call",
        "browser.context.call",
        "browser.handle.call",
        "browser.script.run",
        "browser.script.inject",
        "browser.page.snapshot",
        "browser.page.screenshot",
    } <= branding_template_tool_grants["execute-publication"]
    assert "content_memory_index" in {item.key for item in branding_content_described.spec.outputs}
    assert "publication_jobs" in {item.key for item in branding_content_described.spec.outputs}
    assert "publication_bundle_ref" in {
        item.key for item in branding_content_described.spec.outputs
    }
    assert "distribution_record_refs" in {
        item.key for item in branding_content_described.spec.outputs
    }
    assert "publication_jobs" in branding_content_steps["render-channel-packets"].output_refs
    assert "publication_bundle_ref" in branding_content_steps["render-channel-packets"].output_refs
    assert "content_memory_index" in branding_content_steps["finalize-and-record"].output_refs
    assert "publication_jobs" in branding_content_steps["finalize-and-record"].output_refs
    assert "durable_resource_memory" in branding_content_steps["finalize-and-record"].policy_refs
    assert branding_content_steps["execute-publication"].depends_on == ["finalize-and-record"]
    assert branding_content_steps["execute-publication"].action_refs == [
        "publish_git_blog",
        "publish_x",
        "publish_linkedin",
        "publish_email",
    ]
    branding_run_plan = run_plan_from_template(
        branding_content_described,
        inputs_json={"operator_intent": "Turn a real operating lesson into content."},
    )
    execute_publication_grants = [
        grant
        for grant in branding_run_plan.grant_snapshot_json["mcp_tool_grants"]
        if grant["step_id"] == "execute-publication"
    ]
    execute_publication_tools = {
        tool
        for grant in execute_publication_grants
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }
    assert {
        "action.execute",
        "resource.upsert",
        "artifact.create",
        "artifact.update",
        "artifact.archive",
        "artifact.supersede",
        "browser.runtime.status",
        "browser.method.manifest",
        "browser.profile.list",
        "browser.profile.create",
        "browser.session.list",
        "browser.session.start",
        "browser.session.status",
        "browser.session.stop",
        "browser.page.call",
        "browser.context.call",
        "browser.handle.call",
        "browser.script.run",
        "browser.script.inject",
        "browser.page.snapshot",
        "browser.page.screenshot",
    } <= execute_publication_tools
    interview_grants = [
        grant
        for grant in branding_run_plan.grant_snapshot_json["mcp_tool_grants"]
        if grant["step_id"] == "interview-capture"
    ]
    interview_tools = {
        tool
        for grant in interview_grants
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }
    assert artifact_lifecycle_tools.isdisjoint(interview_tools)
    assert "distribution_record_refs" in branding_content_steps["execute-publication"].output_refs
    assert all(step.instructions for step in branding_content_described.spec.steps)
    image_step_text = branding_content_steps["produce-optional-images"].model_dump_json()
    assert "operator-supplied images" in image_step_text
    assert "without provider spend" in image_step_text
    publication_step_text = branding_content_steps["execute-publication"].model_dump_json()
    assert "operator-confirmed manual publication" in publication_step_text
    assert "do not require API or browser publication evidence" in publication_step_text
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
    }
    engineering_skill_refs = {
        item.skill_ref for item in engineering_described.spec.skill_requirements
    }
    assert engineering_skill_refs == {"stackos:stackos"}
    engineering_skill_preset_refs = {
        item.skill_preset_ref for item in engineering_described.spec.skill_preset_requirements
    }
    assert engineering_skill_preset_refs == {"stackos.sdlc.delivery-orchestrator"}
    engineering_setup_notes = "\n".join(
        engineering_described.spec.skill_requirements[0].setup_notes
    )
    assert ".codex/config.toml" in engineering_setup_notes
    assert ".codex/agents/*.toml" in engineering_setup_notes
    assert "operation.list" in engineering_setup_notes
    assert "resource.query" in engineering_setup_notes
    assert "resource.upsert" in engineering_setup_notes
    assert "artifact.create" not in engineering_setup_notes
    assert "decision.record" in engineering_setup_notes
    assert engineering_described.spec.metadata_json["artifact_grant_policy"] == "explicit"
    sdlc_skill_preset = next(
        item
        for item in engineering_described.spec.skill_preset_requirements
        if item.skill_preset_ref == "stackos.sdlc.delivery-orchestrator"
    )
    assert sdlc_skill_preset.requirement == "required"
    assert "global installed skill" in " ".join(sdlc_skill_preset.setup_notes)
    assert "workflow-specific skill presets" in " ".join(sdlc_skill_preset.setup_notes)
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
    assert engineering_described.spec.approval_gates == []
    assert all(not step.approval_refs for step in engineering_described.spec.steps)
    assert engineering_described.spec.metadata_json["workflow_family"] == "sdlc"
    assert engineering_described.spec.metadata_json["workflow_selection_invariant"] == (
        "lifecycle_intent_requires_run_plan_before_tracker_tickets"
    )
    assert engineering_described.spec.metadata_json["feedback_gatekeeper"] == "main_orchestrator"
    engineering_text = engineering_described.spec.model_dump_json()
    assert "workflow_selection_precedence" in engineering_text
    assert "quality_over_speed" in engineering_text
    assert "mandatory_flow_design" in engineering_text
    assert "agent_executed_flow_proof" in engineering_text
    assert "independent_closeout_verification" in engineering_text
    assert "orchestrator_feedback_gate" in engineering_text
    assert "workflow-backed run plan before creating tracker tickets" in engineering_text
    assert "direct tracker task and a later workflow task" in engineering_text
    define_step = next(
        step for step in engineering_described.spec.steps if step.id == "define-requirements"
    )
    define_text = define_step.model_dump_json()
    assert "user flows, data flows, system flows, and business-rule flows" in define_text
    assert "pre-change behavior or code path" in define_text
    plan_step = next(step for step in engineering_described.spec.steps if step.id == "plan-tickets")
    plan_text = plan_step.model_dump_json()
    assert "workflow task/run plan from the start" in plan_text
    assert "attachment/provenance only" in plan_text
    assert "pass run_plan_id and step_id at creation time" in plan_text
    assert "one root" in plan_text
    assert "tracker.updateTicket" in plan_text
    assert "activates edge" in plan_text
    assert "detached branches" in plan_text
    test_design_step = next(
        step for step in engineering_described.spec.steps if step.id == "design-tests"
    )
    test_design_text = test_design_step.model_dump_json()
    assert "manual proof depth" in test_design_text
    assert "quality_over_speed" in test_design_text
    assert "full manual signoff" in test_design_text
    assert "agent-executed" in test_design_text
    assert "Every changed or at-risk user/data/system/business flow" in test_design_text
    assert "stable StackOS browser profile_key" in test_design_text
    assert "unverified or incomplete proof plan" in test_design_text
    verify_step = next(
        step for step in engineering_described.spec.steps if step.id == "verify-delivery"
    )
    verify_text = verify_step.model_dump_json()
    assert "test plan" in verify_text
    assert "profile_key specified by the test plan" in verify_text
    assert "agent-owned E2E/manual flow scenarios" in verify_text
    review_step = next(
        step for step in engineering_described.spec.steps if step.id == "review-delivery"
    )
    review_text = review_step.model_dump_json()
    assert "one-brain ownership" in review_text
    assert "pre-change behavior or code path" in review_text
    assert "feedback as advisory" in review_text
    assert "over-engineer beyond the scoped deliverable" in review_text
    assert "only validated required fixes block closeout" in review_text
    audit_step = next(
        step for step in engineering_described.spec.steps if step.id == "audit-tracker"
    )
    assert "detached workflow step ticket" in audit_step.model_dump_json()
    assert "E2E/manual flow proof" in audit_step.model_dump_json()
    engineering_run_plan = run_plan_from_template(
        engineering_described,
        inputs_json={"goal": "verify engineering grants"},
    )
    engineering_granted_tools = {
        tool
        for grant in engineering_run_plan.grant_snapshot_json["mcp_tool_grants"]
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }
    assert "artifact.create" not in engineering_granted_tools
    assert engineering_described.spec.metadata_json["agent_subset"] == [
        "requirements-flow-definer",
        "codebase-explorer",
        "planning",
        "architecture",
        "test-designer",
        "delivery",
        "delivery-reviewer",
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
    performance_inputs = {item.key: item for item in media_performance_described.spec.inputs}
    assert {
        "goal",
        "review_scope",
        "review_window",
        "metric_targets",
        "attribution_assumptions",
    } <= {key for key, item in performance_inputs.items() if item.required}
    assert performance_inputs["metric_source"].schema_data["default"] == "stored_context"
    assert {item.approval_ref for item in media_performance_described.spec.action_contracts} == {
        None
    }
    assert media_performance_described.spec.approval_gates == []
    performance_steps = {step.id: step for step in media_performance_described.spec.steps}
    assert performance_steps["fetch_additional_metrics"].approval_refs == []
    assert all(step.success_criteria for step in performance_steps.values())
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


def test_builtin_workflow_preset_requirements_are_step_mapped(session: Session) -> None:
    repo = WorkflowTemplateLoader(session)

    for summary in repo.list_templates().templates:
        described = repo.describe_template(
            key=summary.key,
            plugin_slug=summary.plugin_slug,
        )
        step_ids = {step.id for step in described.spec.steps}
        for requirement in described.spec.agent_requirements:
            assert requirement.applies_to_steps, requirement.agent_preset_ref
            assert set(requirement.applies_to_steps) <= step_ids, requirement.agent_preset_ref
        for requirement in described.spec.skill_preset_requirements:
            if requirement.applies_to_steps:
                assert set(requirement.applies_to_steps) <= step_ids, requirement.skill_preset_ref
            else:
                assert requirement.skill_preset_ref == "stackos.workflow-orchestrator"


def test_builtin_public_workflows_have_reviewed_experience_contracts(session: Session) -> None:
    repo = WorkflowTemplateLoader(session)

    for summary in repo.list_templates().templates:
        described = repo.describe_template(
            key=summary.key,
            plugin_slug=summary.plugin_slug,
        )
        experience = described.spec.experience
        public = described.spec.public

        assert experience is not None, summary.key
        assert public is not None, summary.key
        assert experience.problem.strip(), summary.key
        assert experience.outcome.strip(), summary.key
        assert experience.operator_path, summary.key
        assert experience.agent_path, summary.key
        assert public.audience.strip(), summary.key
        assert public.prerequisites, summary.key
        assert public.proof, summary.key
        assert described.spec.skill_preset_requirements, summary.key
        assert described.spec.outputs, summary.key
        for context_requirement in described.spec.context_requirements:
            assert context_requirement.source in _FIELD_MAP, (
                f"{summary.key}:{context_requirement.id}"
            )
            assert set(context_requirement.fields) <= _FIELD_MAP[context_requirement.source], (
                f"{summary.key}:{context_requirement.id}"
            )
        for output in described.spec.outputs:
            assert output.description.strip(), f"{summary.key}:{output.key}"
            assert output.schema_data, f"{summary.key}:{output.key}"
            assert output.schema_data.get("type") == output.type, f"{summary.key}:{output.key}"


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


def test_project_workflow_extension_upsert_preserves_omitted_fields_by_default(
    session: Session,
    project_id: int,
) -> None:
    repo = WorkflowTemplateLoader(session)
    saved = repo.upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        input_defaults_json={"communication_route_ref": "communication-route:support-feedback"},
        selected_context_json={"audience": "support"},
        guardrails_json={"private_customer_data": False},
        metadata_json={"owner": "support"},
        created_by="unit-test",
    ).data

    patched = repo.upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        metadata_json={"owner": "ops"},
    ).data

    assert saved.selected_context_json == {"audience": "support"}
    assert patched.update_mode == "merge"
    assert patched.metadata_json == {"owner": "ops"}
    assert patched.input_defaults_json == {
        "communication_route_ref": "communication-route:support-feedback"
    }
    assert patched.selected_context_json == {"audience": "support"}
    assert patched.guardrails_json == {"private_customer_data": False}
    assert patched.changed_fields == ["metadata_json"]
    assert "selected_context_json" in patched.preserved_fields
    assert patched.cleared_fields == []

    cleared = repo.upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        clear_fields_json=["guardrails_json"],
    ).data

    assert cleared.guardrails_json == {}
    assert cleared.selected_context_json == {"audience": "support"}
    assert cleared.cleared_fields == ["guardrails_json"]

    replaced = repo.upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        update_mode="replace",
        metadata_json={"owner": "replace"},
    ).data

    assert replaced.metadata_json == {"owner": "replace"}
    assert replaced.selected_context_json == {}
    assert "selected_context_json" in replaced.cleared_fields
    assert replaced.warnings[0].code == "replace_cleared_omitted_fields"

    disabled = repo.upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        enabled=False,
    ).data
    disable_validation = repo.validate_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        metadata_json={"owner": "still-disabled"},
    )
    disabled_patched = repo.upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        metadata_json={"owner": "still-disabled"},
    ).data

    assert disabled.enabled is False
    assert disable_validation.valid is True
    assert disabled_patched.enabled is False

    invalid_clear = repo.validate_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        update_mode="replace",
        clear_fields_json=["guardrails_json"],
    )

    assert invalid_clear.valid is False
    assert invalid_clear.errors[0].code == "clear_fields_requires_merge"


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
            "skill_preset_requirements": [
                {
                    "skill_preset_ref": "stackos.sdlc.delivery-orchestrator",
                    "requirement": "required",
                    "purpose": "Use project-specific SDLC closeout reporting.",
                    "applies_to_steps": ["investigate-issue"],
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
    assert described.spec.skill_preset_requirements[0].skill_preset_ref == (
        "stackos.sdlc.delivery-orchestrator"
    )
    assert described.spec.skill_preset_requirements[0].purpose == (
        "Use project-specific SDLC closeout reporting."
    )


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


def test_website_seo_analysis_has_public_fallback_and_evidence_contract(
    session: Session,
) -> None:
    described = WorkflowTemplateLoader(session).describe_template(
        key="seo.website-analysis",
        plugin_slug="seo",
    )
    spec = described.spec
    assert spec.version == "0.3.0"

    assert [step.id for step in spec.steps] == [
        "scope-audit",
        "map-public-site",
        "collect-connected-evidence",
        "reconcile-and-diagnose",
        "review-and-prioritize",
        "finalize-and-store",
    ]
    assert all(step.instructions for step in spec.steps)
    assert all(step.success_criteria for step in spec.steps)
    actions = {item.key: item for item in spec.action_contracts}
    action_refs = {item.action for item in actions.values()}
    assert actions["public_web_read"].action == "utils.web.read"
    assert actions["public_web_read"].optional is False
    assert actions["public_sitemap_fetch"].action == "utils.sitemap.fetch"
    assert actions["public_sitemap_fetch"].optional is False
    assert all(
        actions[key].optional
        for key in actions
        if key not in {"public_web_read", "public_sitemap_fetch"}
    )
    assert "utils.web.crawl" not in action_refs
    assert "ga4.properties.run_realtime_report" not in action_refs
    assert "paa.extract" not in action_refs
    assert all(item.risk_level in {"read", "cost"} for item in actions.values())
    assert spec.approval_gates == []

    policies = {item.key for item in spec.policies}
    assert {
        "public_baseline_always_available",
        "use_ready_relevant_sources",
        "canonical_evidence_index",
        "canonical_reviewed_findings",
        "evidence_classification",
        "no_invented_measurements",
        "reconcile_provider_semantics",
        "no_generic_score",
        "analysis_only",
        "agent_decides_strategy",
    } <= policies
    resources = {item.resource for item in spec.resource_contracts}
    assert "website-seo-analysis" in resources
    outputs = {item.key: item for item in spec.outputs}
    finding_schema = outputs["seo_findings"].schema_data
    assert outputs["draft_findings"].schema_data == finding_schema
    finding_required = set(finding_schema["items"]["required"])
    assert {
        "type",
        "category",
        "evidence_class",
        "confidence",
        "affected_scope",
        "evidence_refs",
        "impact",
        "recommendation",
        "validation_path",
    } <= finding_required

    review_schema = outputs["review_summary"].schema_data
    assert {
        "reviewer_role",
        "reviewed_at",
        "dispositions",
        "unresolved_evidence_gaps",
        "residual_limitations",
    } <= set(review_schema["required"])
    disposition_schema = review_schema["properties"]["dispositions"]["items"]
    assert set(disposition_schema["properties"]["disposition"]["enum"]) == {
        "accepted",
        "revised",
        "rejected",
    }

    public_map_schema = outputs["public_site_map"].schema_data
    assert "observed_urls" not in public_map_schema["properties"]
    assert (
        public_map_schema["properties"]["representative_templates"]["items"]["additionalProperties"]
        is False
    )

    inventory_schema = outputs["site_inventory"].schema_data
    assert "representative_url_rows" in inventory_schema["required"]
    assert "url_rows" not in inventory_schema["properties"]
    assert (
        inventory_schema["properties"]["template_types"]["items"]["additionalProperties"] is False
    )
    assert inventory_schema["properties"]["url_set_counts"]["additionalProperties"] == {
        "type": "integer",
        "minimum": 0,
    }
    assert inventory_schema["properties"]["reconciliation"]["additionalProperties"] is False

    evidence_schema = outputs["evidence_index"].schema_data
    evidence_item = evidence_schema["items"]
    assert {
        "evidence_ref",
        "kind",
        "source",
        "captured_at",
        "lifecycle_state",
        "scope",
        "receipt_ref",
        "limitations",
    } <= set(evidence_item["required"])
    assert "response_file_refs" not in outputs["source_ledger"].schema_data["items"]["properties"]

    seo_plugin = next(item for item in BUILTIN_PLUGIN_MANIFESTS if item.slug == "seo")
    analysis_resource = next(
        item for item in seo_plugin.resources if item.key == "website-seo-analysis"
    ).schema_data
    resource_properties = analysis_resource["properties"]
    for output_key, resource_key in (
        ("source_ledger", "source_ledger"),
        ("evidence_index", "evidence_index"),
        ("review_summary", "review_summary"),
        ("executive_summary", "executive_summary"),
        ("prioritized_roadmap", "prioritized_actions"),
    ):
        assert outputs[output_key].schema_data == resource_properties[resource_key]
    assert "raw_provider_data_refs" not in resource_properties
    assert "evidence_index" in analysis_resource["required"]
    assert "review_summary" in analysis_resource["required"]
    assert analysis_resource["properties"]["status"]["enum"] == ["complete", "partial"]
    assert analysis_resource["properties"]["artifact_refs"]["required"] == ["final_report"]
    complete_rule = analysis_resource["allOf"][0]
    assert complete_rule["if"]["properties"]["status"] == {"const": "complete"}
    assert complete_rule["then"]["properties"]["artifact_refs"]["required"] == [
        "final_report",
        "site_inventory",
        "finding_register",
    ]

    run_plan = run_plan_from_template(
        described,
        inputs_json={"site_url": "https://example.org"},
    )
    assert all(step.instructions for step in run_plan.steps)
    assert all(step.success_criteria for step in run_plan.steps)
    grants = run_plan.grant_snapshot_json["mcp_tool_grants"]
    action_grants = {
        grant["step_id"]: grant for grant in grants if grant.get("tool") == "action.execute"
    }
    assert action_grants["map-public-site"]["action_refs"] == [
        "utils.web.read",
        "utils.sitemap.fetch",
        "utils.web.map",
        "utils.web.scrape",
    ]
    assert (
        "seo.search-console.search-analytics.query"
        in action_grants["collect-connected-evidence"]["action_refs"]
    )
    final_tools = {
        tool
        for grant in grants
        if grant["step_id"] == "finalize-and-store"
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }
    assert {"resource.upsert", "artifact.create"} <= final_tools
    final_resource_grants = [
        grant
        for grant in grants
        if grant["step_id"] == "finalize-and-store" and grant.get("tool") == "resource.upsert"
    ]
    assert final_resource_grants == [
        {
            "step_id": "finalize-and-store",
            "tool": "resource.upsert",
            "resource_key": "website-seo-analysis",
        }
    ]
    map_tools = {
        tool
        for grant in grants
        if grant["step_id"] == "map-public-site"
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }
    assert {"action.execute", "browser.session.start", "browser.page.snapshot"} <= map_tools
