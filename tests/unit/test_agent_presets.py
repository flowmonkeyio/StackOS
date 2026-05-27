"""Unit tests for StackOS agent preset contracts."""

from __future__ import annotations

import asyncio

from stackos.agents import AgentPresetLoader, parse_agent_preset_bundle_yaml
from stackos.agents.schema import validate_agent_preset_obj
from stackos.operations.agent_presets import AgentPresetDescribeInput, agent_preset_describe


def test_agent_preset_loader_lists_bundled_roles() -> None:
    listing = AgentPresetLoader().list_presets()
    keys = {item.key for item in listing.presets}

    assert len(keys) == 23
    assert "stackos.sdlc.planning" in keys
    assert "stackos.workflow.project-memory-review" in keys
    assert "seo.workflow.keyword-research" in keys
    assert "media-buying.workflow.campaign-launch" in keys
    assert "gtm.workflow.account-research" in keys
    assert "communications.workflow.rich-telegram-reply" in keys
    assert all(item.generic_preset for item in listing.presets)
    assert all(item.adaptation_required for item in listing.presets)
    by_key = {item.key: item for item in listing.presets}
    assert by_key["stackos.workflow.project-memory-review"].plugin_slug == "core"
    assert by_key["stackos.sdlc.planning"].plugin_slug == "engineering"


def test_agent_preset_describe_includes_tracker_adaptation_guidance() -> None:
    loaded = AgentPresetLoader().describe_preset(key="stackos.sdlc.planning")

    assert loaded.preset.project_adaptation.required is True
    assert loaded.preset.project_adaptation.do_not_use_verbatim is True
    assert "tracker" in loaded.preset.project_adaptation.required_agent_action.lower()
    assert "dependencies" in " ".join(loaded.preset.prompt_contract.must_do).lower()


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

    assert "engineering.tracked-delivery" in guidance
    assert "normal workflow path" in guidance
    assert "host/project-specific" in guidance
    assert ".codex/config.toml" in guidance
    assert ".codex/agents/*.toml" in guidance
    assert "workspace.updateprofile" in guidance
    assert "resource.query" in guidance
    assert "resource.upsert" in guidance
    assert "artifact.create" in guidance
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
