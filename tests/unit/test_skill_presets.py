"""Unit tests for StackOS skill preset contracts."""

from __future__ import annotations

from pathlib import Path

import stackos.skill_presets.loader as loader_module
from stackos.operations.skill_presets import resolve_skill_preset_requirements
from stackos.skill_presets import SkillPresetLoader, parse_skill_preset_bundle_yaml
from stackos.skill_presets.schema import validate_skill_preset_obj
from stackos.workflows.template_schema import WorkflowSkillPresetRequirementSpec


def test_skill_preset_loader_lists_bundled_presets() -> None:
    listing = SkillPresetLoader().list_presets()
    keys = {item.key for item in listing.presets}

    assert "stackos.sdlc.delivery-orchestrator" in keys
    assert "branding.brand-orchestrator" in keys
    assert all(item.generic_preset for item in listing.presets)
    assert all(item.adaptation_required for item in listing.presets)
    by_key = {item.key: item for item in listing.presets}
    assert by_key["stackos.sdlc.delivery-orchestrator"].plugin_slug == "engineering"
    assert by_key["stackos.sdlc.delivery-orchestrator"].skill_type == ("main-agent-orchestration")
    assert by_key["branding.brand-orchestrator"].plugin_slug == "branding"


def test_skill_preset_describe_includes_project_adaptation_contract() -> None:
    loaded = SkillPresetLoader().describe_preset(key="stackos.sdlc.delivery-orchestrator")
    contract = loaded.preset.operating_contract
    action = loaded.preset.project_adaptation.required_agent_action.lower()
    contract_text = " ".join(
        [
            contract.mission,
            *contract.responsibilities,
            *contract.must_do,
            *contract.must_not_do,
            *contract.required_outputs,
            *contract.success_criteria,
            *contract.self_check,
        ]
    ).lower()

    assert loaded.preset.project_adaptation.required is True
    assert loaded.preset.project_adaptation.do_not_use_verbatim is True
    assert "delivery ledger" in contract_text
    assert "delivery calibration" in contract_text
    assert "smallest convincing proof" in contract_text
    assert "quality over speed" in contract_text
    assert "manual proof depth" in contract_text
    assert "full manual signoff" in contract_text
    assert "stable stackos browser profile_key" in contract_text
    assert "adjudicate reviewer claims" in contract_text
    assert "micro, standard, high-risk, or blocked" in contract_text
    assert "do not mechanically run every workflow phase" in contract_text
    assert "tracker" in contract_text
    assert "project" in action
    assert "calibrate the work" in action
    assert "subagent role" in contract_text
    assert "ticket start report" in contract_text
    assert "ticket end report" in contract_text
    assert "task closeout tldr" in contract_text
    calibration = loaded.preset.metadata_json["delivery_calibration"]
    assert calibration["required_before_execution"] is True
    assert set(calibration["lifecycle_depths"]) == {
        "micro",
        "standard",
        "high_risk",
        "blocked",
    }
    assert (
        "Broad suites require a stated risk reason"
        in calibration["proof_selection"]["broad_test_rule"]
    )
    assert calibration["proof_selection"]["examples"][0]["proof"] == (
        "YAML load plus skillPreset/agentPreset resolver smoke, not full repository tests."
    )
    assert calibration["agent_selection"]["principle"] == (
        "Specialist agents are conditional depth tools, not mandatory ceremony."
    )
    reporting = loaded.preset.metadata_json["progress_reporting"]
    assert reporting["ticket_start"]["fields"] == [
        "Starting: <short work boundary>",
        "Ticket: <n>/<total>",
    ]
    assert reporting["emoji_legend"]["pass_fixed"].startswith("✅")
    assert reporting["emoji_legend"]["active_blocker"].startswith("❌")
    assert reporting["emoji_legend"]["residual_risk"].startswith("⚠️")
    assert reporting["emoji_legend"]["info_rejected"].startswith("\u2139\ufe0f")
    assert "Agents/reviewers: <names or none>" in reporting["ticket_end"]["fields"]
    assert "Findings adjudicated" in reporting["ticket_end"]["fields"][-1]
    assert "active blocker" in reporting["ticket_end"]["fields"][-1]
    assert "synthesize useful TLDR content" in reporting["ticket_end"]["content_rule"]
    assert (
        "Calibration: <micro/standard/high-risk/blocked and why>" in reporting["task_end"]["fields"]
    )
    assert "One-brain architecture: ✅/❌" in reporting["task_end"]["fields"]
    assert reporting["task_end"]["style"]["no_long_paragraphs"] is True
    assert "do not restate every mechanical step" in reporting["task_end"]["content_rule"]


def test_branding_skill_preset_names_evidence_lock_and_level2_boundary() -> None:
    loaded = SkillPresetLoader().describe_preset(key="branding.brand-orchestrator")
    contract = loaded.preset.operating_contract
    contract_text = " ".join(
        [
            contract.mission,
            *contract.responsibilities,
            *contract.must_do,
            *contract.must_not_do,
            *contract.required_outputs,
            *contract.success_criteria,
            *contract.self_check,
        ]
    )
    refs = [item.ref for item in loaded.preset.project_adaptation.required_context_refs]

    assert loaded.summary.plugin_slug == "branding"
    assert loaded.preset.project_adaptation.required is True
    assert "workspace.startSession" in loaded.preset.recommended_tools
    assert "runPlan.start" in loaded.preset.recommended_tools
    assert "readiness.check" in loaded.preset.recommended_tools
    assert "agentPreset.resolveForWorkflow" in loaded.preset.recommended_tools
    assert "skillPreset.resolveForWorkflow" in loaded.preset.recommended_tools
    assert "workflow_agent_requirements" in loaded.preset.project_adaptation.prompt_assembly_order
    assert "stackos:stackos" in refs
    assert "Level 2 branding overlay" in refs
    assert "claims-to-evidence map" in contract_text
    assert "canonical-first" in contract_text
    assert "automation-first after approval" in contract_text
    assert (
        "API integration, browser-assisted platform UI, site/admin UI, or local script"
        in contract_text
    )
    assert "content memory index" in contract_text
    assert "publication jobs" in contract_text
    assert "distribution records" in contract_text
    assert "without reading this chat" in contract_text
    assert "durable artifacts" in contract_text
    assert "not use StackOS artifacts as a scratchpad" in contract_text
    assert "operator-supplied media" in contract_text
    assert "operator-confirmed manual publication" in contract_text
    assert "artifact.update" in loaded.preset.recommended_tools
    assert "artifact.archive" in loaded.preset.recommended_tools
    assert "artifact.supersede" in loaded.preset.recommended_tools
    assert "Do not collapse claim, voice, and sanitization review" in contract_text
    assert "current tracker/run-plan context" in " ".join(contract.must_do)
    assert loaded.preset.metadata_json["evidence_lock"]["required_before_approval"] is True


def test_skill_preset_loader_reads_bundled_plugin_assets_without_clone_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundled_root = tmp_path / "plugins"
    preset_file = bundled_root / "engineering" / "skill-presets" / "sdlc.yaml"
    preset_file.parent.mkdir(parents=True)
    preset_file.write_text(
        """
presets:
  - schema_version: stackos.skill-preset.v1
    key: stackos.sdlc.bundled-test
    name: Bundled Test
    applies_to_workflows:
      - engineering.tracked-delivery
    operating_contract:
      mission: Test bundled package asset loading
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader_module, "_clone_plugins_root", lambda: None)
    monkeypatch.setattr(loader_module, "_bundled_plugins_root", lambda: bundled_root)

    listing = SkillPresetLoader().list_presets(workflow_key="engineering.tracked-delivery")

    assert [item.key for item in listing.presets] == ["stackos.sdlc.bundled-test"]
    assert listing.presets[0].plugin_slug == "engineering"
    assert listing.presets[0].source == "plugin"


def test_skill_preset_resolution_reports_optional_and_unresolved_refs() -> None:
    requirements = [
        WorkflowSkillPresetRequirementSpec(
            skill_preset_ref="stackos.sdlc.delivery-orchestrator",
            requirement="optional",
        ),
        WorkflowSkillPresetRequirementSpec(
            skill_preset_ref="stackos.sdlc.missing",
            requirement="required",
            purpose="Exercise unresolved diagnostics.",
        ),
    ]

    required, _recommended, optional, unresolved = resolve_skill_preset_requirements(
        requirements,
        repo_root=None,
        include_optional=False,
    )

    assert required == []
    assert optional == []
    assert unresolved[0].skill_preset_ref == "stackos.sdlc.missing"
    assert unresolved[0].requirement == "required"

    _required, _recommended, optional, _unresolved = resolve_skill_preset_requirements(
        requirements[:1],
        repo_root=None,
        include_optional=True,
    )

    assert optional[0].preset.summary.key == "stackos.sdlc.delivery-orchestrator"


def test_skill_preset_validation_rejects_verbatim_or_sensitive_contracts() -> None:
    invalid = {
        "schema_version": "stackos.skill-preset.v1",
        "key": "stackos.sdlc.invalid",
        "name": "Invalid",
        "generic_preset": False,
        "operating_contract": {"mission": "Do work"},
    }

    result = validate_skill_preset_obj(invalid)

    assert result.valid is False
    assert "generic_preset" in result.errors[0].message

    secret_result = validate_skill_preset_obj(
        {
            "schema_version": "stackos.skill-preset.v1",
            "key": "stackos.sdlc.invalid",
            "name": "Invalid",
            "operating_contract": {"mission": "Do work"},
            "metadata_json": {"api_key": "do-not-store"},
        }
    )

    assert secret_result.valid is False
    assert "must not contain secrets" in secret_result.errors[0].message


def test_skill_preset_bundle_parser_accepts_bundle() -> None:
    text = """
presets:
  - schema_version: stackos.skill-preset.v1
    key: stackos.sdlc.test
    name: Test Skill Preset
    operating_contract:
      mission: Test mission
"""

    presets = parse_skill_preset_bundle_yaml(text)

    assert len(presets) == 1
    assert presets[0].key == "stackos.sdlc.test"
