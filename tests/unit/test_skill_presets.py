"""Unit tests for StackOS skill preset contracts."""

from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

import stackos.agents.loader as agent_loader_module
import stackos.skill_presets.loader as loader_module
from stackos.agents import AgentPresetLoader
from stackos.operations.skill_presets import resolve_skill_preset_requirements
from stackos.skill_presets import SkillPresetLoader, parse_skill_preset_bundle_yaml
from stackos.skill_presets.schema import validate_skill_preset_obj
from stackos.workflows.template_schema import WorkflowSkillPresetRequirementSpec


def test_skill_preset_loader_lists_bundled_presets() -> None:
    listing = SkillPresetLoader().list_presets()
    keys = {item.key for item in listing.presets}

    assert "stackos.sdlc.delivery-orchestrator" in keys
    assert "stackos.workflow-orchestrator" in keys
    assert "branding.brand-orchestrator" in keys
    assert all(item.generic_preset for item in listing.presets)
    assert all(item.adaptation_required for item in listing.presets)
    by_key = {item.key: item for item in listing.presets}
    assert by_key["stackos.sdlc.delivery-orchestrator"].plugin_slug == "engineering"
    assert by_key["stackos.sdlc.delivery-orchestrator"].skill_type == ("main-agent-orchestration")
    assert by_key["branding.brand-orchestrator"].plugin_slug == "branding"
    assert by_key["stackos.workflow-orchestrator"].plugin_slug == "core"


def test_generic_workflow_orchestrator_keeps_the_normal_loop_small() -> None:
    loaded = SkillPresetLoader().describe_preset(key="stackos.workflow-orchestrator")
    contract = loaded.preset.operating_contract
    text = " ".join(
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

    assert loaded.preset.metadata_json["boundary"]["not_a_subagent"] is True
    assert loaded.preset.version == "0.1.5"
    assert len(loaded.preset.applies_to_workflows) == 23
    assert {"agency.setup", "agency.project-setup"} <= set(loaded.preset.applies_to_workflows)
    assert "seo.website-analysis" in loaded.preset.applies_to_workflows
    assert "structural, context, provider-route, and execution readiness" in text
    assert "prefer one ready provider route" in text
    assert "stop at a documented prepare-only or recommendation boundary" in text
    assert "do not make every optional branch mandatory" in text
    assert "without rediscovering" in text
    assert "without baked-in model or reasoning-effort defaults" in text
    assert "tracker acceptance, review adjudication and final claims with the main agent" in text
    assert "reject stale receipts as current proof" in text
    assert "isolate or serialize shared writes" in text
    assert "untrusted evidence" in text


def test_skill_preset_describe_includes_project_adaptation_contract() -> None:
    loaded = SkillPresetLoader().describe_preset(key="stackos.sdlc.delivery-orchestrator")
    preset = loaded.preset
    assert preset.version == "0.7.0"
    assert preset.project_adaptation.required
    assert preset.project_adaptation.do_not_use_verbatim
    assert "original request" in preset.project_adaptation.required_agent_action
    assert preset.metadata_json["boundary"]["not_a_subagent"] is True
    assert "runPlan.claimStep" in preset.recommended_tools
    assert preset.metadata_json["boundary"]["not_a_subagent"] is True


def test_sdlc_orchestrator_defines_bounded_delegation_and_acceptance() -> None:
    preset = SkillPresetLoader().describe_preset(key="stackos.sdlc.delivery-orchestrator").preset
    required = " ".join(preset.operating_contract.must_do)
    boundaries = " ".join(preset.operating_contract.must_not_do)
    assert "ambiguous" in required
    assert "four fresh configured sessions" in preset.operating_contract.mission
    assert "distinct planner, executor, reviewer, and test-writer sessions" in (
        " ".join(preset.operating_contract.responsibilities)
    )
    assert "fresh configured luna test writer" in required.lower()
    assert "rejection blocks test writing" in required.lower()
    assert "observe actual integrated behavior" in required.lower()
    assert "unit tests alone are insufficient" in required.lower()
    assert "Do not treat technical readiness as publication" in boundaries


def test_sdlc_orchestrator_preserves_model_authority_and_recovery_identity() -> None:
    preset = SkillPresetLoader().describe_preset(key="stackos.sdlc.delivery-orchestrator").preset
    required = " ".join(preset.operating_contract.must_do)
    assert "frozen plan" in required
    assert "Preserve scoped grants" in required
    assert "failed attempts and unknown effects" in required
    assert preset.metadata_json["state_owner"] == (
        "StackOS run; this preset is guidance, not the executable controller"
    )


def test_codex_sdlc_orchestrator_tracks_source_and_existing_project_references() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".codex/orchestrator/sdlc-delivery-orchestrator.md").read_text()
    assert "engineering.tracked-delivery` v0.7.0" in text
    assert "delivery run" in text
    config = tomllib.loads((root / ".codex/config.toml").read_text())
    assert all("orchestrator" not in role for role in config["agents"])
    refs = re.findall(r"`((?:AGENTS\.md|docs/[^`]+|plugins/[^`]+|\.codex/config\.toml))`", text)
    assert refs
    assert all((root / ref).is_file() for ref in refs)


def test_codex_sdlc_orchestrator_preserves_local_execution_boundaries() -> None:
    text = " ".join(
        (Path(__file__).resolve().parents[2] / ".codex/orchestrator/sdlc-delivery-orchestrator.md")
        .read_text()
        .split()
    )
    assert "A fresh planner session" in text
    assert "A separate executor session" in text
    assert "A fresh reviewer session" in text
    assert "Only after approval" in text
    assert "same reviewer session receives only actual argv/output/exit evidence" in text
    assert "approve the outcome against the original requirements before handoff" in text
    assert "Ambiguity stops the affected work" in text
    assert "defaults are Astra for planning and review, Luna for implementation and tests" in text
    assert "original requirements and actual changed code only" in text
    assert "Frozen older plans keep their own contracts" in text
    assert "no publication or deployment authority" in text


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
    conditional_refs = [
        item.ref for item in loaded.preset.project_adaptation.conditional_context_refs
    ]

    assert loaded.summary.plugin_slug == "branding"
    assert loaded.preset.project_adaptation.required is True
    assert "workspace.startSession" in loaded.preset.recommended_tools
    assert "runPlan.start" in loaded.preset.recommended_tools
    assert "runPlan.getStep" in loaded.preset.recommended_tools
    assert "readiness.check" in loaded.preset.recommended_tools
    assert "agentPreset.resolveForWorkflow" in loaded.preset.recommended_tools
    assert "skillPreset.resolveForWorkflow" in loaded.preset.recommended_tools
    assert "tracker.createTask" in loaded.preset.recommended_tools
    assert "tracker.createTicket" in loaded.preset.recommended_tools
    assert "tracker.next" in loaded.preset.recommended_tools
    assert "tracker.updateTask" in loaded.preset.recommended_tools
    assert "tracker.updateTicket" in loaded.preset.recommended_tools
    assert "workflow_agent_requirements" in loaded.preset.project_adaptation.prompt_assembly_order
    assert "stackos:stackos" in refs
    assert "Level 2 branding overlay" in conditional_refs
    assert "branding.brand-foundation-setup" in loaded.preset.applies_to_workflows
    assert "claims-to-evidence map" in contract_text
    assert "canonical-first" in contract_text
    assert "publication_intent is stage or publish" in contract_text
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
    assert "evidence-backed blockers and repairs" in contract_text
    assert "next-workflow handoff" in contract_text
    assert "current tracker/run-plan context" in " ".join(contract.must_do)
    assert "one manual tracker task for the batch" in contract_text
    assert "exactly one content-production run at a time" in contract_text
    assert "Never execute article work in parallel" in contract_text
    assert "Do not create a separate portfolio brief" in contract_text
    assert "ready label is a claim" in contract_text
    assert "Keep coherence adjudication inside each article's existing angle" in contract_text
    assert "Do not add a batch-wide editorial review" in contract_text
    assert "parent batch task owns sequence and outcome tracking" in contract_text
    assert loaded.preset.metadata_json["evidence_lock"]["required_before_finalization"] is True


def test_finance_orchestrator_keeps_financial_authority_external() -> None:
    loaded = SkillPresetLoader().describe_preset(key="stackos.finance.department-orchestrator")
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
    ).lower()
    refs = [item.ref for item in loaded.preset.project_adaptation.required_context_refs]
    conditional_refs = [
        item.ref for item in loaded.preset.project_adaptation.conditional_context_refs
    ]

    assert loaded.summary.plugin_slug == "finance"
    assert loaded.preset.version == "0.8.1"
    assert loaded.preset.skill_type == "main-agent-orchestration"
    assert loaded.preset.project_adaptation.required is True
    assert loaded.preset.project_adaptation.do_not_use_verbatim is True
    assert set(loaded.preset.applies_to_workflows) == {
        "finance.receipt-intake",
        "finance.bookkeeping-close",
        "finance.payment-request",
        "finance.payment-request-followups",
        "finance.cashflow-management",
        "finance.tax-estimates",
        "finance.management-review",
    }
    assert {
        "project instruction entrypoints and scoped guidance",
        "stackos:stackos",
        "finance-plugin:workflows",
    } <= set(refs)
    assert "finance-plugin:references/local-workspace-contract.md" in conditional_refs
    assert "finance-plugin:references/imap-host-handoff-contract.md" in conditional_refs
    assert loaded.preset.metadata_json["boundary"]["not_a_subagent"] is True
    assert loaded.preset.metadata_json["boundary"]["finance_system_of_record"] == (
        "external-backend"
    )
    assert {
        "resource.upsert",
        "artifact.create",
        "artifact.update",
        "communication.send",
        "communication.reply",
    }.isdisjoint(loaded.preset.recommended_tools)
    assert "selected project-local backend" in contract_text
    assert "prepared/unposted" in contract_text
    assert "selective dispatch" in contract_text
    assert "one high-reasoning owner" in contract_text
    assert "one external-record writer" in contract_text
    assert "fresh approval occurrence" in contract_text
    assert "opening cash plus receipts minus cash payments equals closing cash" in contract_text
    assert "base and downside" in contract_text
    assert "paid, voided, disputed, paused, corrected, and active-promise" in contract_text
    assert "incomplete/awaiting-advisor packet" in contract_text
    assert "cpa/ea review where the workflow says so" in contract_text
    assert "do not create a stackos ledger" in contract_text
    assert "do not use generic communication.send" in contract_text
    assert "settlement-only occurrence" in contract_text
    assert "report/attach/mark" in contract_text
    assert "finance state" in contract_text
    for required in (
        "finance.json",
        "sole authoritative",
        "mutable setup",
        "finance.md",
        "csv",
        "record_id",
        "unrelated",
        "immutable",
    ):
        assert required in contract_text
    assert loaded.preset.metadata_json["execution"]["role_selection"] == "selective"
    assert loaded.preset.metadata_json["execution"]["one_writer_external_record"] is True


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


def test_branding_writing_reference_resolves_from_bundled_preset_origins(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package_config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert (
        package_config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]["plugins"]
        == "stackos/_assets/plugins"
    )
    bundled_root = tmp_path / "stackos" / "_assets" / "plugins"
    shutil.copytree(repo_root / "plugins" / "branding", bundled_root / "branding")
    for module in (loader_module, agent_loader_module):
        monkeypatch.setattr(module, "_clone_plugins_root", lambda: None)
        monkeypatch.setattr(module, "_bundled_plugins_root", lambda: bundled_root)

    reference = "branding-plugin:references/natural-writing.md"
    orchestrator = SkillPresetLoader().describe_preset(key="branding.brand-orchestrator")
    assert reference not in {
        item.ref for item in orchestrator.preset.project_adaptation.required_context_refs
    }
    assert reference in {
        item.ref for item in orchestrator.preset.project_adaptation.conditional_context_refs
    }
    origins = [orchestrator.summary.origin_path]
    for role in ("evidence-curator", "channel-strategist", "narrative-writer", "voice-reviewer"):
        loaded = AgentPresetLoader().describe_preset(key=f"branding.{role}")
        assert reference in loaded.preset.model_dump_json()
        origins.append(loaded.summary.origin_path)
    for origin_path in origins:
        assert origin_path is not None
        origin = Path(origin_path)
        assert origin.is_relative_to(bundled_root)
        resolved = (origin.parent / ".." / "references" / "natural-writing.md").resolve()
        assert resolved == bundled_root / "branding" / "references" / "natural-writing.md"
        assert resolved.read_text(encoding="utf-8").strip()


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
