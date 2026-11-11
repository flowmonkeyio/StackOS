"""Source/adaptation coverage; behavioral compliance requires independent rehearsal."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from stackos.agents import AgentPresetLoader
from stackos.skill_presets import SkillPresetLoader

ROOT = Path(__file__).resolve().parents[2]
GUIDANCE_REF = "canonical project guidance"
FAMILIES = {
    "branding": 7,
    "communications": 5,
    "core": 2,
    "engineering": 8,
    "finance": 6,
    "gtm": 8,
    "marketing": 5,
    "media-buying": 5,
    "seo": 3,
    "support": 2,
    "trackbooth": 1,
}
MAIN_PRESETS = {
    "branding.brand-orchestrator",
    "stackos.workflow-orchestrator",
    "stackos.sdlc.delivery-orchestrator",
    "stackos.finance.department-orchestrator",
    "marketing.campaign-production-orchestrator",
}


def _assert_guidance_route(adaptation: object) -> None:
    refs = [item for item in adaptation.required_context_refs if item.ref == GUIDANCE_REF]
    assert len(refs) == 1
    # Required resolution is always present; individual brand sources remain conditional.
    assert "workflowExtension.selected_context_json" in refs[0].purpose
    assert "response_mode=raw" in refs[0].purpose
    assert "agentPreset.describe" in refs[0].purpose
    assert "skillPreset.describe" in refs[0].purpose
    assert adaptation.required is True
    assert adaptation.do_not_use_verbatim is True


@pytest.mark.parametrize("family,count", FAMILIES.items())
def test_every_role_resolves_the_common_guidance_owner(family: str, count: int) -> None:
    loader = AgentPresetLoader()
    listed = loader.list_presets(plugin_slug=family).presets
    assert len(listed) == count
    for summary in listed:
        loaded = loader.describe_preset(key=summary.key, plugin_slug=family)
        assert loaded.summary.plugin_slug == family
        _assert_guidance_route(loaded.preset.project_adaptation)


def test_all_main_presets_expose_the_same_guidance_route() -> None:
    loader = SkillPresetLoader()
    summaries = loader.list_presets().presets
    assert {item.key for item in summaries} == MAIN_PRESETS
    for summary in summaries:
        _assert_guidance_route(loader.describe_preset(key=summary.key).preset.project_adaptation)


def test_existing_local_adaptations_track_resolved_source_identity() -> None:
    agents = AgentPresetLoader()
    for path in sorted((ROOT / ".codex/agents").glob("*.toml")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"Source preset: ([\w.-]+) v(\d+\.\d+\.\d+)", text)
        assert match, path
        source = agents.describe_preset(key=match[1]).preset
        assert match[2] == source.version, path
        local = tomllib.loads(text)
        assert "canonical-project-guidance-consumption" in local["developer_instructions"]
        if source.domain == "branding":
            assert "model" not in local
            assert "model_reasoning_effort" not in local

    skills = SkillPresetLoader()
    for path in sorted((ROOT / ".codex/orchestrator").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"Source skill preset: `([\w.-]+)` v(\d+\.\d+\.\d+)", text)
        assert match, path
        assert match[2] == skills.describe_preset(key=match[1]).preset.version, path
        assert "canonical-project-guidance-consumption" in text


def test_family_aliases_keep_one_shared_adaptation() -> None:
    # Core and engineering deliberately have distinct existing adaptation groups.
    for family in FAMILIES.keys() - {"core", "engineering", "trackbooth"}:
        paths = list((ROOT / "plugins" / family / "agent-presets").glob("*.yaml"))
        assert len(paths) == 1
        roles = yaml.safe_load(paths[0].read_text(encoding="utf-8"))["presets"]
        assert len({id(role["project_adaptation"]) for role in roles}) == 1


def _assert_bounded_correction_method(text: str) -> None:
    text = " ".join(text.lower().split())
    for obligation in (
        "terminal-managed",
        "verifier/reviewer",
        "full-read/merge",
        "target/source ticket refs",
        "source attempt",
        "inspected candidate",
        "failed criterion/check",
        "required outcome",
        "affected recheck",
        "outside step result_json",
        "repair_count",
        "complete -> in-progress -> not-started",
        "tracker.updateticket",
        "earliest affected bound gate",
        "tracker.reopen",
        "explicit step_id and reason",
        "final mutation",
        "then immediately return repair",
        "fresh persisted correction outranks old approved review",
        "separate descendant commit",
        "independent exact-sha review",
        "without fallback",
        "followups",
    ):
        assert obligation in text, obligation
    assert "mirror repair stops" not in text


def test_sdlc_correction_method_is_scoped_to_verification_roles() -> None:
    loader = AgentPresetLoader()
    shared_local = set()
    for role in (
        "requirements-flow-definer",
        "codebase-explorer",
        "planning",
        "architecture",
        "test-designer",
        "delivery",
        "delivery-reviewer",
    ):
        preset = loader.describe_preset(key=f"stackos.sdlc.{role}").preset
        shared = preset.project_adaptation.required_agent_action
        assert "Only an explicitly assigned automatic verifier/reviewer" in shared
        assert "Other roles return findings without feedback/reopen mutations" in shared
        assert "full-read/merge" not in shared
        local = tomllib.loads((ROOT / f".codex/agents/sdlc-{role}.toml").read_text())
        assert "model" not in local and "model_reasoning_effort" not in local
        instructions = local["developer_instructions"]
        if role in {"delivery", "delivery-reviewer"}:
            source = " ".join(preset.prompt_contract.must_do)
            _assert_bounded_correction_method(source)
            _assert_bounded_correction_method(instructions)
            shared_local.add(
                next(
                    line
                    for line in instructions.splitlines()
                    if line.startswith(
                        "- Only for an explicitly assigned automatic verifier/reviewer"
                    )
                )
            )
        else:
            assert "full-read/merge" not in instructions
            assert "complete -> in-progress -> not-started" not in instructions
            assert "return findings without feedback/reopen mutations" in instructions
            assert "full-read/merge" not in " ".join(preset.prompt_contract.must_do)
    assert len(shared_local) == 1


def test_orchestrator_and_workflow_carry_correction_method_without_new_phase() -> None:
    skill = SkillPresetLoader().describe_preset(key="stackos.sdlc.delivery-orchestrator").preset
    _assert_bounded_correction_method(" ".join(skill.operating_contract.must_do))
    _assert_bounded_correction_method(
        (ROOT / ".codex/orchestrator/sdlc-delivery-orchestrator.md").read_text()
    )
    workflow = yaml.safe_load(
        (ROOT / "plugins/engineering/workflows/tracked-delivery.yaml").read_text()
    )
    recovery = next(p for p in workflow["policies"] if p["key"] == "recovery_reconciliation")
    _assert_bounded_correction_method(recovery["description"])
    steps = {step["id"]: step for step in workflow["steps"]}
    for key in ("verify-delivery", "review-delivery"):
        assert "recovery_reconciliation" in steps[key]["policy_refs"]
        assert "recovery_reconciliation" in " ".join(steps[key]["instructions"])
    assert not any("correction" in key or "repair" in key for key in steps)


@pytest.mark.parametrize(
    "relative_path",
    [
        "plugins/engineering/skill-presets/sdlc.yaml",
        ".codex/orchestrator/sdlc-delivery-orchestrator.md",
        "docs/task-tracker.md",
        "docs/agent-presets.md",
        "docs/agent-operating-model.md",
    ],
)
def test_instruction_adoption_preserves_running_snapshots_and_activation_owner(
    relative_path: str,
) -> None:
    text = " ".join((ROOT / relative_path).read_text().lower().split())
    assert "instruction-only saved-default adoption may occur during a running task" in text
    assert "after independent source/local" in text
    assert "queued/running" in text and "frozen" in text
    assert "complete/stopped" in text or "completed/stopped" in text
    assert "receipts" in text and "sessions" in text and "repair allowance" in text
    assert "main-owned after task closure" in text
    assert "old-job completion" not in text


def test_manual_incident_linkage_reuses_existing_evidence_owners() -> None:
    text = " ".join((ROOT / "docs/feedback-resolution-spec.md").read_text().split())
    assert "`followups`/`followup_resolutions`" in text
    assert "return the linkage in their receipt" in text
    assert "do not activate followups as work or grant lifecycle writes" in text
    assert "source attempt/candidate" in text
    assert "Pending consumption stays pending" in text
    assert "Required current defects remain blockers" in text
    assert "one-off QA scratch outside Git" in text


def test_workspace_ui_paths_use_current_human_destinations() -> None:
    from stackos.repositories.workspace_guidance import _ui_paths

    assert _ui_paths(None) == {"projects": "/"}
    assert _ui_paths(7) == {
        "home": "/projects/7",
        "connections": "/projects/7/connections",
        "tasks": "/projects/7/tasks",
        "plugins": "/projects/7/plugins",
        "library": "/projects/7/library",
        "agents": "/projects/7/agents",
    }
