"""Preset reuse must retain live source validation, precedence and caller isolation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from stackos.agents import loader as agent_loader
from stackos.skill_presets import loader as skill_loader


@pytest.fixture(params=["agent", "skill"])
def family(request, tmp_path, monkeypatch):
    kind = request.param
    module = agent_loader if kind == "agent" else skill_loader
    loader = module.AgentPresetLoader() if kind == "agent" else module.SkillPresetLoader()
    module._parsed_bundle.cache_clear()
    monkeypatch.setattr(module, "_clone_plugins_root", lambda: tmp_path / "plugins")
    monkeypatch.setattr(module, "_bundled_plugins_root", lambda: None)
    yield kind, module, loader
    module._parsed_bundle.cache_clear()


def _payload(kind: str, *, name: str = "First") -> dict:
    preset = {
        "schema_version": f"stackos.{kind}-preset.v1",
        "key": "demo.review",
        "name": name,
        "recommended_tools": ["tracker.brief"],
        "applies_to_workflows": ["demo.workflow"],
        "metadata_json": {"nested": {"notes": ["source"]}},
    }
    if kind == "agent":
        preset.update(
            role="review",
            workflow_roles=["review"],
            prompt_contract={"mission": "Read the evidence."},
        )
    else:
        preset["operating_contract"] = {"mission": "Read the evidence."}
    return preset


def _write(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump({"presets": [payload]}, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return text


@pytest.mark.parametrize("source", ["plugin", "bundled", "repo"])
def test_cached_presets_are_isolated_and_source_edits_are_revalidated(
    family, source, tmp_path, monkeypatch
):
    kind, module, loader = family
    root = tmp_path / source
    if source == "plugin":
        root = tmp_path / "plugins"
    if source == "bundled":
        monkeypatch.setattr(module, "_bundled_plugins_root", lambda: root)
    path = (
        tmp_path / ".stackos" / f"{kind}-presets/review.yaml"
        if source == "repo"
        else root / "demo" / f"{kind}-presets/review.yaml"
    )
    original = _write(path, _payload(kind))
    parse_name = f"parse_{kind}_preset_bundle_yaml"
    real_parse = getattr(module, parse_name)
    parsed = []

    def observe(text):
        parsed.append(text)
        return real_parse(text)

    monkeypatch.setattr(module, parse_name, observe)
    first = loader.describe_preset(key="demo.review", repo_root=str(tmp_path))
    first.preset.metadata_json["nested"]["notes"].append("caller")
    first.preset.recommended_tools.append("tracker.get")
    first.summary.applies_to_workflows.append("caller.workflow")
    first.summary.name = "Caller"
    second = loader.describe_preset(key="demo.review", repo_root=str(tmp_path))
    assert second.preset.metadata_json == {"nested": {"notes": ["source"]}}
    assert second.preset.recommended_tools == ["tracker.brief"]
    assert second.summary.applies_to_workflows == ["demo.workflow"]
    assert second.summary.name == "First"
    assert parsed == [original]

    before = path.stat()
    edited = _write(path, _payload(kind, name="Other"))
    assert len(edited) == len(original)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert loader.describe_preset(key="demo.review", repo_root=str(tmp_path)).preset.name == "Other"

    for invalid, message in (
        ({**_payload(kind), "metadata_json": {"api_key": "synthetic"}}, "secrets"),
        (
            {**_payload(kind), "project_adaptation": {"required": False}},
            "project-specific adaptation",
        ),
    ):
        _write(path, invalid)
        with pytest.raises(ValidationError, match=message):
            loader.describe_preset(key="demo.review", repo_root=str(tmp_path))
    _write(path, _payload(kind))
    assert loader.describe_preset(key="demo.review", repo_root=str(tmp_path)).preset.name == "First"


def test_preset_precedence_and_unrelated_source_validation_remain_live(family, tmp_path):
    kind, _module, loader = family
    plugin = tmp_path / "plugins/demo" / f"{kind}-presets/review.yaml"
    repo = tmp_path / ".stackos" / f"{kind}-presets/review.yaml"
    _write(plugin, _payload(kind))
    assert loader.describe_preset(key="demo.review").summary.source == "plugin"
    _write(repo, _payload(kind, name="Override"))

    listed = loader.list_presets(repo_root=str(tmp_path), include_shadowed=True).presets
    assert {(item.source, item.shadowed_by) for item in listed} == {
        ("plugin", "repo"),
        ("repo", None),
    }
    for summary in listed:
        described = loader.describe_preset(
            key="demo.review", source=summary.source, repo_root=str(tmp_path)
        )
        assert described.summary == summary
        summary.name = "Caller"
    assert (
        loader.describe_preset(key="demo.review", repo_root=str(tmp_path)).preset.name == "Override"
    )
    assert (
        loader.describe_preset(key="demo.review", repo_root=str(tmp_path / "other")).preset.name
        == "First"
    )
    repo.unlink()
    assert (
        loader.describe_preset(key="demo.review", repo_root=str(tmp_path)).summary.source
        == "plugin"
    )

    unrelated = {**_payload(kind), "key": "demo.invalid", "generic_preset": False}
    _write(plugin.with_name("invalid.yaml"), unrelated)
    with pytest.raises(ValidationError, match="generic_preset=true"):
        loader.describe_preset(key="demo.review")
