"""Static contract tests for the minimal agency setup package."""

from __future__ import annotations

from pathlib import Path

import yaml

from stackos.plugins.manifest import load_plugin_manifest_file
from stackos.workflows.template_schema import validate_workflow_template_obj

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "agency"
WORKFLOWS = PLUGIN / "workflows"

EXPECTED_WORKFLOW_KEYS = {"agency.setup", "agency.project-setup"}


def _workflows() -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path
        loaded[payload["key"]] = payload
    return loaded


def _by_key(items: list[dict]) -> dict[str, dict]:
    return {item["key"]: item for item in items}


def test_agency_yaml_has_no_silently_overridden_mapping_keys() -> None:
    def check(node: yaml.Node, path: Path) -> None:
        if isinstance(node, yaml.MappingNode):
            keys = [key.value for key, _ in node.value]
            assert len(keys) == len(set(keys)), (path, node.start_mark.line + 1, keys)
            for _, child in node.value:
                check(child, path)
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                check(child, path)

    for path in sorted(PLUGIN.rglob("*.yaml")):
        node = yaml.compose(path.read_text(encoding="utf-8"))
        assert node is not None, path
        check(node, path)


def test_agency_manifest_declares_only_minimal_context_resources() -> None:
    manifest = load_plugin_manifest_file(PLUGIN / "plugin.yaml")

    assert manifest.slug == "agency"
    assert manifest.display_order == 19
    assert manifest.providers == []
    assert manifest.actions == []
    resources = {resource.key: resource for resource in manifest.resources}
    assert set(resources) == {"agency-profile", "project-context"}

    profile = resources["agency-profile"]
    assert profile.config["record_kind"] == "agency-profile"
    assert "nested StackOS-project registry" in profile.config["agent_guidance"]
    assert profile.ui_schema["table_columns"] == ["agency_name", "agency_workspace_ref"]

    project_context = resources["project-context"]
    assert project_context.config["record_kind"] == "client-project-context"
    assert "does not create a nested StackOS project" in project_context.config["agent_guidance"]
    assert set(project_context.schema_data["required"]) == {
        "engagement_ref",
        "engagement_name",
        "client_ref",
        "description",
    }
    association = project_context.schema_data["properties"]["finance_workspace_association_ref"]
    assert "navigation pointer" in association["description"]
    assert "finance authority" in association["description"]


def test_agency_workflows_are_minimal_explicit_onboarding_contracts() -> None:
    workflows = _workflows()

    assert set(workflows) == EXPECTED_WORKFLOW_KEYS
    for workflow_key, workflow in workflows.items():
        validation = validate_workflow_template_obj(workflow)
        assert validation.valid, (workflow_key, validation.errors)
        assert workflow["metadata"]["artifact_grant_policy"] == "explicit"
        assert workflow.get("action_contracts", []) == []
        assert workflow.get("auth_requirements", []) == []
        assert workflow.get("approval_gates", []) == []
        assert workflow["agent_requirements"] == []
        assert [item["skill_ref"] for item in workflow["skill_requirements"]] == ["stackos:stackos"]
        assert [item["skill_preset_ref"] for item in workflow["skill_preset_requirements"]] == [
            "stackos.workflow-orchestrator"
        ]
        assert workflow["skill_preset_requirements"][0]["requirement"] == "required"
        policy_text = " ".join(policy["description"] for policy in workflow["policies"])
        assert "infrastructure setup is read-only" in policy_text
        assert "explicitly authorized onboarding run" in policy_text
        assert "never started implicitly" in policy_text
        assert all("artifact" not in step["id"] for step in workflow["steps"])

    agency = workflows["agency.setup"]
    assert _by_key(agency["resource_contracts"])["agency_profile"]["resource"] == "agency-profile"
    assert _by_key(agency["inputs"])["agency_profile_ref"]["required"] is True

    project = workflows["agency.project-setup"]
    assert (
        _by_key(project["resource_contracts"])["project_context"]["resource"] == "project-context"
    )
    project_inputs = _by_key(project["inputs"])
    assert project_inputs["agency_profile_ref"]["required"] is False
    assert project_inputs["finance_workspace_association_ref"]["required"] is False
    resolve = next(step for step in project["steps"] if step["id"] == "resolve-supplied-facts")
    resolve_text = " ".join(resolve["instructions"]).lower()
    assert "absence of agency_profile_ref is valid standalone" in resolve_text
    assert "profitability calculations" in resolve_text


def test_agency_workflows_grant_only_context_and_their_declared_resource_write() -> None:
    workflows = _workflows()

    expected = {
        "agency.setup": ("persist-profile", "agency_profile", "agency-profile"),
        "agency.project-setup": (
            "persist-project-context",
            "project_context",
            "project-context",
        ),
    }
    for workflow_key, (persist_step, contract_key, resource_key) in expected.items():
        workflow = workflows[workflow_key]
        resource_contracts = _by_key(workflow["resource_contracts"])
        assert resource_contracts[contract_key]["resource"] == resource_key
        write_steps = [step for step in workflow["steps"] if step.get("resource_refs")]
        assert len(write_steps) == 1
        assert write_steps[0]["id"] == persist_step
        assert write_steps[0]["resource_refs"] == [contract_key]
        assert "grants" not in workflow["metadata"]
        assert "artifact.create" not in str(workflow["metadata"])
        assert "action.execute" not in str(workflow)


def test_project_setup_resolves_an_optional_agency_association_in_current_project() -> None:
    project = _workflows()["agency.project-setup"]
    contexts = {item["id"]: item for item in project["context_requirements"]}
    agency_profiles = contexts["existing_agency_profiles"]

    assert agency_profiles["source"] == "resources"
    assert agency_profiles["filters"] == {
        "plugin_slug": "agency",
        "resource_keys": ["agency-profile"],
    }
    inventory = next(
        step for step in project["steps"] if step["id"] == "inventory-existing-context"
    )
    resolve = next(step for step in project["steps"] if step["id"] == "resolve-supplied-facts")
    assert "existing_agency_profiles" in inventory["context_refs"]
    assert "existing_agency_profiles" in resolve["context_refs"]
    assert "same bound StackOS project" in " ".join(resolve["instructions"])
    assert "stop before writing" in " ".join(project["failure_handling"])
