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
