"""Unit tests for StackOS agent preset contracts."""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

import yaml

from stackos.agents import AgentPresetLoader, parse_agent_preset_bundle_yaml
from stackos.agents.schema import validate_agent_preset_obj
from stackos.operations.agent_presets import (
    AgentPresetDescribeInput,
    agent_preset_describe,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

LOCAL_CODEX_AGENT_PRESETS = {
    "sdlc_requirements_flow_definer": (
        "agents/sdlc-requirements-flow-definer.toml",
        "stackos.sdlc.requirements-flow-definer",
    ),
    "sdlc_codebase_explorer": (
        "agents/sdlc-codebase-explorer.toml",
        "stackos.sdlc.codebase-explorer",
    ),
    "sdlc_planning": (
        "agents/sdlc-planning.toml",
        "stackos.sdlc.planning",
    ),
    "sdlc_architecture": (
        "agents/sdlc-architecture.toml",
        "stackos.sdlc.architecture",
    ),
    "sdlc_test_designer": (
        "agents/sdlc-test-designer.toml",
        "stackos.sdlc.test-designer",
    ),
    "sdlc_delivery": (
        "agents/sdlc-delivery.toml",
        "stackos.sdlc.delivery",
    ),
    "sdlc_delivery_reviewer": (
        "agents/sdlc-delivery-reviewer.toml",
        "stackos.sdlc.delivery-reviewer",
    ),
}


def test_codex_local_sdlc_agents_track_engineering_presets() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / "plugins/engineering/workflows/tracked-delivery.yaml").read_text(
            encoding="utf-8"
        )
    )
    workflow_refs = {item["agent_preset_ref"] for item in workflow["agent_requirements"]}
    expected_refs = {preset for _config_path, preset in LOCAL_CODEX_AGENT_PRESETS.values()}

    assert workflow_refs == expected_refs

    config = tomllib.loads((REPO_ROOT / ".codex/config.toml").read_text(encoding="utf-8"))
    assert set(config["agents"]) == set(LOCAL_CODEX_AGENT_PRESETS)

    for agent_name, (config_file, preset_ref) in LOCAL_CODEX_AGENT_PRESETS.items():
        assert config["agents"][agent_name]["config_file"] == config_file
        local_path = REPO_ROOT / ".codex" / config_file
        local_text = local_path.read_text(encoding="utf-8")

        assert f"Source preset: {preset_ref} v0.1.0" in local_text
        assert "Workflow: engineering.tracked-delivery" in local_text
        assert "Keep aligned with plugins/engineering/agent-presets/sdlc.yaml." in local_text
        tomllib.loads(local_text)

    test_designer_text = (REPO_ROOT / ".codex/agents/sdlc-test-designer.toml").read_text(
        encoding="utf-8"
    )
    reviewer_text = (REPO_ROOT / ".codex/agents/sdlc-delivery-reviewer.toml").read_text(
        encoding="utf-8"
    )
    planning_text = (REPO_ROOT / ".codex/agents/sdlc-planning.toml").read_text(encoding="utf-8")
    orchestrator_text = (REPO_ROOT / ".codex/orchestrator/sdlc-delivery-orchestrator.md").read_text(
        encoding="utf-8"
    )

    assert "manual proof depth" in test_designer_text
    assert "full manual signoff" in test_designer_text
    assert "agent-executable E2E/manual proof scenario matrix" in test_designer_text
    assert "changed user/data/system/business flows" in test_designer_text
    assert "stable StackOS browser `profile_key`" in test_designer_text
    assert "test-design verification status" in test_designer_text
    assert "evidence-backed validity status" in reviewer_text
    assert "claims to verify" in reviewer_text
    assert "independent closeout checks" in reviewer_text
    assert "before the change, what changed, what exists now" in reviewer_text
    assert "without concrete evidence" in reviewer_text
    assert "planned persistent `profile_key`" in reviewer_text
    assert "include_graph=true" in planning_text
    assert "detached branches" in planning_text
    assert "Source skill preset: `stackos.sdlc.delivery-orchestrator` v0.1.0" in (orchestrator_text)
    assert "not a subagent" in orchestrator_text
    assert "Quality beats speed" in orchestrator_text
    assert "StackOS browser `profile_key`" in orchestrator_text
    assert "Reviewer and verifier outputs are advisory claims" in orchestrator_text
    assert "Subagents can" in orchestrator_text
    assert "over-engineering risk" in orchestrator_text
    assert "Every non-micro delivery needs an explicit flow design" in orchestrator_text
    assert "E2E/manual flow scenarios are agent-executed" in orchestrator_text
    assert "one-brain ownership" in orchestrator_text


def test_agent_preset_loader_lists_bundled_roles() -> None:
    listing = AgentPresetLoader().list_presets()
    keys = {item.key for item in listing.presets}

    assert len(keys) == 41
    assert "stackos.sdlc.requirements-flow-definer" in keys
    assert "stackos.sdlc.codebase-explorer" in keys
    assert "stackos.sdlc.planning" in keys
    assert "marketing.campaign.brief-analyst" in keys
    assert "marketing.campaign.creative-director" in keys
    assert "marketing.campaign.media-producer" in keys
    assert "marketing.campaign.landing-page-builder" in keys
    assert "marketing.campaign.visual-signoff-reviewer" in keys
    assert "support.workflow.issue-investigator" in keys
    assert "support.workflow.delivery-handoff" in keys
    assert "stackos.sdlc.test-designer" in keys
    assert "communications.workflow.customer-feedback-intake" in keys
    assert "stackos.workflow.project-memory-review" in keys
    assert "seo.workflow.keyword-research" in keys
    assert "media-buying.workflow.campaign-launch" in keys
    assert "gtm.workflow.account-research" in keys
    assert "communications.workflow.rich-telegram-reply" in keys
    assert "trackbooth.agent-api-operator" in keys
    assert "stackos.workflow.workflow-author" in keys
    assert "branding.evidence-curator" in keys
    assert "branding.narrative-writer" in keys
    assert "branding.channel-strategist" in keys
    assert "branding.claim-auditor" in keys
    assert "branding.voice-reviewer" in keys
    assert "branding.sanitization-reviewer" in keys
    assert "trackbooth.workflow-author" not in keys
    assert all(item.generic_preset for item in listing.presets)
    assert all(item.adaptation_required for item in listing.presets)
    by_key = {item.key: item for item in listing.presets}
    assert by_key["stackos.workflow.project-memory-review"].plugin_slug == "core"
    assert by_key["stackos.sdlc.planning"].plugin_slug == "engineering"
    assert by_key["marketing.campaign.media-producer"].plugin_slug == "marketing"
    assert by_key["support.workflow.issue-investigator"].plugin_slug == "support"
    assert by_key["support.workflow.delivery-handoff"].plugin_slug == "support"
    assert by_key["communications.workflow.customer-feedback-intake"].plugin_slug == (
        "communications"
    )
    assert by_key["stackos.sdlc.requirements-flow-definer"].plugin_slug == "engineering"
    assert by_key["trackbooth.agent-api-operator"].plugin_slug == "trackbooth"
    assert by_key["stackos.workflow.workflow-author"].plugin_slug == "core"
    assert by_key["branding.claim-auditor"].plugin_slug == "branding"


def test_agent_preset_describe_includes_tracker_adaptation_guidance() -> None:
    loaded = AgentPresetLoader().describe_preset(key="stackos.sdlc.planning")
    contract = loaded.preset.prompt_contract
    shared_action = loaded.preset.project_adaptation.required_agent_action.lower()
    contract_text = " ".join(
        [
            *contract.responsibilities,
            *contract.must_do,
            *contract.handoff_outputs,
            *contract.success_criteria,
            *contract.self_check,
        ]
    )

    assert loaded.preset.project_adaptation.required is True
    assert loaded.preset.project_adaptation.do_not_use_verbatim is True
    assert "tracker" in shared_action
    assert "tracker.reopen" in loaded.preset.recommended_tools
    assert "tracker.reopen" in shared_action
    assert "tracker.createticket" not in shared_action
    assert "dependencies" in " ".join(contract.must_do).lower()
    assert "workflow-backed run plan before tracker.createtask" in contract_text.lower()
    assert "direct tracker tasks only" in contract_text.lower()
    assert "canonical workflow-backed task/run plan" in contract_text.lower()
    assert "pass run_plan_id and step_id together" in contract_text.lower()
    assert "never retry tracker.createticket with only one" in contract_text.lower()

    test_designer = AgentPresetLoader().describe_preset(key="stackos.sdlc.test-designer")
    test_designer_text = " ".join(
        [
            *test_designer.preset.prompt_contract.responsibilities,
            *test_designer.preset.prompt_contract.must_do,
            *test_designer.preset.prompt_contract.must_not_do,
            *test_designer.preset.prompt_contract.handoff_outputs,
            *test_designer.preset.prompt_contract.success_criteria,
            *test_designer.preset.prompt_contract.self_check,
        ]
    ).lower()
    assert "manual proof depth" in test_designer_text
    assert "full manual signoff" in test_designer_text
    assert "production risk" in test_designer_text
    assert "stable stackos browser profile_key" in test_designer_text
    assert 'vague "smoke test"' in test_designer_text

    reviewer = AgentPresetLoader().describe_preset(key="stackos.sdlc.delivery-reviewer")
    reviewer_text = " ".join(
        [
            *reviewer.preset.prompt_contract.responsibilities,
            *reviewer.preset.prompt_contract.must_do,
            *reviewer.preset.prompt_contract.must_not_do,
            *reviewer.preset.prompt_contract.handoff_outputs,
            *reviewer.preset.prompt_contract.success_criteria,
            *reviewer.preset.prompt_contract.self_check,
        ]
    ).lower()
    assert "tracker.get with run_plan_id and include_graph=true" in reviewer_text
    assert "delivery/test/docs branches that bypass the workflow spine" in reviewer_text
    assert "claims to verify" in reviewer_text
    assert "without concrete evidence" in reviewer_text
    assert "planned persistent profile_key" in reviewer_text


def test_customer_support_thread_preset_requires_route_and_media_fidelity() -> None:
    loaded = AgentPresetLoader().describe_preset(
        key="communications.workflow.customer-feedback-intake"
    )
    contract_text = " ".join(
        [
            *loaded.preset.prompt_contract.responsibilities,
            *loaded.preset.prompt_contract.must_do,
            *loaded.preset.prompt_contract.must_not_do,
            *loaded.preset.prompt_contract.self_check,
        ]
    )

    assert "communicationTarget.resolve" in contract_text
    assert "route approval" in contract_text
    assert "every inbound media item" in contract_text
    assert "partial forwarding" in contract_text


def test_branding_agent_presets_enforce_role_separation_and_adaptation() -> None:
    loader = AgentPresetLoader()
    curator = loader.describe_preset(key="branding.evidence-curator")
    writer = loader.describe_preset(key="branding.narrative-writer")
    claim = loader.describe_preset(key="branding.claim-auditor")
    voice = loader.describe_preset(key="branding.voice-reviewer")
    sanitization = loader.describe_preset(key="branding.sanitization-reviewer")
    strategist = loader.describe_preset(key="branding.channel-strategist")

    claim_text = " ".join(
        [
            claim.preset.prompt_contract.mission,
            *claim.preset.prompt_contract.must_do,
            *claim.preset.prompt_contract.must_not_do,
            *claim.preset.prompt_contract.self_check,
        ]
    )
    curator_text = " ".join(
        [
            curator.preset.prompt_contract.mission,
            *curator.preset.prompt_contract.must_do,
            *curator.preset.prompt_contract.must_not_do,
        ]
    )
    strategist_text = " ".join(
        [
            strategist.preset.prompt_contract.mission,
            *strategist.preset.prompt_contract.must_do,
            *strategist.preset.prompt_contract.must_not_do,
        ]
    )
    claim_refs = [item.ref for item in claim.preset.project_adaptation.required_context_refs]

    assert claim.summary.plugin_slug == "branding"
    assert claim.preset.project_adaptation.required is True
    assert "stackos:stackos" in claim_refs
    assert "Level 2 branding overlay" in claim_refs
    assert "Default unsupported or untraceable claims to cut" in claim_text
    assert "Do not publish or decide routing" in claim_text
    assert (
        "Do not publish, send, click final external publication controls, "
        "or operate provider actions" in strategist_text
    )
    assert "branding.content-production" in sanitization.preset.applies_to_workflows
    assert sanitization.preset.applies_to_workflows == ["branding.content-production"]
    assert "action.execute" in curator.preset.recommended_tools
    assert "artifact.update" in curator.preset.recommended_tools
    assert "artifact.archive" in writer.preset.recommended_tools
    assert "artifact.supersede" in strategist.preset.recommended_tools
    assert "action.execute" not in strategist.preset.recommended_tools
    assert "decision.record" in strategist.preset.recommended_tools
    mutating_tools = {
        "resource.upsert",
        "artifact.create",
        "artifact.update",
        "artifact.archive",
        "artifact.supersede",
        "decision.record",
        "action.execute",
        "runPlan.claimStep",
        "runPlan.recordStep",
    }
    for reviewer in (claim, voice, sanitization):
        assert mutating_tools.isdisjoint(reviewer.preset.recommended_tools)
    assert "artifact clutter" in curator_text
    assert "Do not create a new artifact for each draft revision" in " ".join(
        writer.preset.prompt_contract.must_not_do
    )


def test_trackbooth_agent_preset_names_runtime_sync_and_stackos_boundaries() -> None:
    loaded = AgentPresetLoader().describe_preset(key="trackbooth.agent-api-operator")
    contract = loaded.preset.prompt_contract
    contract_text = " ".join(
        [
            contract.mission,
            *contract.responsibilities,
            *contract.must_do,
            *contract.must_not_do,
            *contract.handoff_inputs,
            *contract.handoff_outputs,
            *contract.success_criteria,
            *contract.self_check,
        ]
    )
    refs = [item.ref for item in loaded.preset.project_adaptation.required_context_refs]
    conditional_refs = [
        item.ref for item in loaded.preset.project_adaptation.conditional_context_refs
    ]

    assert loaded.summary.plugin_slug == "trackbooth"
    assert loaded.preset.project_adaptation.required is True
    assert loaded.preset.project_adaptation.do_not_use_verbatim is True
    assert "plugins/trackbooth/plugin.yaml" in refs
    assert "plugins/trackbooth/agent-api/README.md" in refs
    assert "docs/integration-contracts/trackbooth.md" in conditional_refs
    assert "trackbooth.catalog.sync" in contract_text
    assert "trackbooth.api.*" in contract_text
    assert "api_base_url" in contract_text
    assert "X-Acting-As-Account" in contract_text
    assert "StackOS actions as the only agent-facing Trackbooth interface" in contract_text
    assert "Do not make direct HTTP requests to Trackbooth" in contract_text
    assert "construct Trackbooth URLs" in contract_text


def test_generic_workflow_author_preset_teaches_workflow_generation_boundary() -> None:
    loaded = AgentPresetLoader().describe_preset(key="stackos.workflow.workflow-author")
    contract = loaded.preset.prompt_contract
    contract_text = " ".join(
        [
            contract.mission,
            *contract.responsibilities,
            *contract.must_do,
            *contract.must_not_do,
            *contract.handoff_inputs,
            *contract.handoff_outputs,
            *contract.success_criteria,
            *contract.self_check,
        ]
    )
    refs = [item.ref for item in loaded.preset.project_adaptation.required_context_refs]
    conditional_refs = [
        item.ref for item in loaded.preset.project_adaptation.conditional_context_refs
    ]

    assert loaded.summary.plugin_slug == "core"
    assert loaded.preset.project_adaptation.required is True
    assert "AGENTS.md" in refs
    assert "stackos:stackos" in refs
    assert "project-local docs and skills" in refs
    assert "docs/workflow-templates.md" in conditional_refs
    assert "provider or plugin integration contract" in conditional_refs
    assert "current action inventory" in conditional_refs
    assert "workflowTemplate.validate" in loaded.preset.recommended_tools
    assert "workflowExtension.upsert" in loaded.preset.recommended_tools
    assert "runPlan.create" in loaded.preset.recommended_tools
    assert "workflowTemplate.save" not in loaded.preset.recommended_tools
    assert "workflow authoring brief" in loaded.preset.project_adaptation.required_agent_action
    assert (
        "run plan, a workflow extension, or a reusable project workflow template" in contract_text
    )
    assert "workflowTemplate.save" in contract_text
    assert "local-admin authority" in contract_text
    assert "Do not embed raw API keys" in contract_text
    lower_contract_text = contract_text.lower()
    assert "trackbooth" not in lower_contract_text
    assert "x-acting-as-account" not in lower_contract_text
    assert "do not make direct http requests" not in lower_contract_text


def test_agent_preset_required_refs_do_not_assume_stackos_docs_in_customer_repo() -> None:
    loader = AgentPresetLoader()

    for summary in loader.list_presets().presets:
        preset = loader.describe_preset(key=summary.key).preset
        refs = [item.ref for item in preset.project_adaptation.required_context_refs]
        assert "stackos:stackos" in refs
        assert all(not ref.startswith("docs/") for ref in refs), (
            f"{summary.key} requires repo-local StackOS docs: {refs}"
        )


def test_agent_preset_setup_guidance_names_host_and_toolbox_boundaries() -> None:
    described = asyncio.run(
        agent_preset_describe(
            AgentPresetDescribeInput(key="stackos.sdlc.planning"),
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        )
    )
    guidance = " ".join(described.setup_guidance).lower()
    action = described.project_adaptation.required_agent_action.lower()

    assert "communications.customer-feedback-intake" in guidance
    assert "support.issue-investigation" in guidance
    assert "support.delivery-task-handoff" in guidance
    assert "engineering.tracked-delivery" in guidance
    assert "normal workflow path" in guidance
    assert "host/project-specific" in guidance
    assert ".codex/config.toml" in guidance
    assert ".codex/agents/*.toml" in guidance
    assert "workspace.updateprofile" in guidance
    assert "source media forwarding" in guidance
    assert "before tracker.createtask or tracker.createticket" in guidance
    assert "direct tracker tasks only" in guidance
    assert "resource.query" in guidance
    assert "resource.upsert" in guidance
    assert "artifact.create" not in guidance
    assert "decision.record" in guidance
    assert "toolbox.describe" in guidance
    assert "toolbox.call" in guidance
    assert "toolbox.describe" in action
    assert "contracts, not a daemon registry" in action


def test_agent_preset_bundle_parser_accepts_multi_preset_yaml() -> None:
    presets = parse_agent_preset_bundle_yaml(
        """
presets:
  - schema_version: stackos.agent-preset.v1
    key: demo.agent
    name: Demo Agent
    role: demo
    workflow_roles: [demo]
    recommended_tools: [tracker.brief]
    prompt_contract:
      mission: Demo mission.
"""
    )

    assert [item.key for item in presets] == ["demo.agent"]
    assert presets[0].project_adaptation.required is True


def test_agent_preset_schema_rejects_secret_looking_values() -> None:
    result = validate_agent_preset_obj(
        {
            "schema_version": "stackos.agent-preset.v1",
            "key": "demo.agent",
            "name": "Demo Agent",
            "role": "demo",
            "workflow_roles": ["demo"],
            "metadata": {"api_key": "real-value"},
            "prompt_contract": {"mission": "Demo mission."},
        }
    )

    assert result.valid is False
    assert "must not contain secrets" in result.errors[0].message
