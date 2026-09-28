"""Existing carriers for shared guidance; agent judgment is rehearsed separately."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest
import yaml

from stackos.plugins.manifest import load_plugin_manifest_file
from stackos.workflows.run_plan_schema import run_plan_from_template
from stackos.workflows.template_loader import LoadedWorkflowTemplate, WorkflowTemplateSummaryOut
from stackos.workflows.template_schema import WorkflowTemplateSpec
from tests.unit.test_seo_workflow_contracts import opportunity, refresh

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests/fixtures/project_guidance_scenarios.yaml"
WORKFLOWS = {
    "seo.keyword-research": {"goal": "Find a useful next article"},
    "seo.content-refresh": {"refresh_goal": "Improve an existing guide"},
    "seo.website-analysis": {"site_url": "https://example.com/"},
    "branding.brand-foundation-setup": {"foundation_goal": "Review current foundation"},
    "branding.content-production": {"operator_intent": "Prepare the selected guide"},
    "marketing.campaign-production": {
        "product_description": "Email software",
        "product_photo_refs": ["artifact:photo"],
        "media_request": {"format": "image"},
    },
}


def workflow(key: str) -> dict:
    plugin, name = key.split(".", 1)
    return yaml.safe_load((ROOT / f"plugins/{plugin}/workflows/{name}.yaml").read_text())


def schema(key: str, collection: str, item_key: str) -> dict:
    return next(item["schema"] for item in workflow(key)[collection] if item["key"] == item_key)


def resource_schema(plugin: str, key: str) -> dict:
    manifest = load_plugin_manifest_file(ROOT / f"plugins/{plugin}/plugin.yaml")
    return next(item.schema_data for item in manifest.resources if item.key == key)


def instructions(key: str, step_id: str) -> str:
    step = next(item for item in workflow(key)["steps"] if item["id"] == step_id)
    return " ".join(step["instructions"])


def foundation_selection() -> dict:
    # A fixture carried by existing selected_context_json, not a new core schema.
    return {
        "owner_ref": "resource:101",
        "guide_ref": "artifact:201",
        "revision": "r3",
        "approval_ref": "decision:301",
        "retrieval_ref": "artifact:current-foundation-read",
        "applies_to": ["audience", "voice", "disclosure"],
    }


def handoff() -> dict:
    # Durable artifact contents; the receiving input carries only actual refs.
    return {
        "opportunity_ref": "resource:401",
        "opportunity_revision": "r2",
        "review_ref": "artifact:404",
        "adjudication_ref": "artifact:402",
        "evidence_index_ref": "artifact:403",
        "existing_page_refs": [],
        "disposition": "create",
        "gap_evidence": "Checked inventory has no setup guide for this reader task",
        "intended_scope": "One factual setup guide",
        "foundation": foundation_selection(),
        "next_owner": "branding.content-production",
    }


def indexed_artifact(ref: str, scope: dict) -> dict:
    return {
        "evidence_ref": "ev:guidance-handoff",
        "kind": "artifact",
        "source": "approved-project-guidance",
        "captured_at": "2026-09-24T12:00:00Z",
        "lifecycle_state": "current",
        "scope": scope,
        "receipt_ref": ref,
        "artifact_ref": ref,
        "limitations": [],
    }


@pytest.mark.parametrize("key", WORKFLOWS)
def test_selected_guidance_survives_existing_plan_carrier(key: str) -> None:
    spec = WorkflowTemplateSpec.model_validate(workflow(key))
    loaded = LoadedWorkflowTemplate(
        spec=spec,
        summary=WorkflowTemplateSummaryOut(
            key=spec.key,
            name=spec.name,
            version=spec.version,
            source="plugin",
            precedence=10,
            plugin_slug=key.split(".")[0],
        ),
    )
    selected = {"approved_foundation": foundation_selection()}
    plan = run_plan_from_template(
        loaded, inputs_json=WORKFLOWS[key], selected_context_json=selected
    )
    assert plan.selected_context_json == selected
    assert plan.inputs_json.keys() >= WORKFLOWS[key].keys()


def test_exact_seo_selection_fits_existing_branding_input_and_index() -> None:
    refs = {"refs": ["artifact:selected-opportunity-handoff", "resource:401"]}
    jsonschema.validate(refs, schema("branding.content-production", "inputs", "source_scope"))
    for key in ("seo.keyword-research", "seo.content-refresh"):
        jsonschema.validate(refs["refs"], schema(key, "inputs", "source_refs"))
        index_item = schema(key, "outputs", "evidence_packet")["properties"]["evidence_index"][
            "items"
        ]
        jsonschema.validate(indexed_artifact(refs["refs"][0], handoff()), index_item)
    source = instructions("seo.keyword-research", "store_opportunities")
    target = instructions("branding.content-production", "interview-capture")
    for required in (
        "source_scope.refs",
        "adjudication_ref",
        "evidence_index_ref",
        "existing_page_refs",
        "disposition",
        "intended scope",
        "next_owner",
        "packet_only",
    ):
        assert required in source
        assert required in target


def test_strict_opportunity_remains_unchanged_while_index_carries_guidance() -> None:
    row = {**opportunity(), "next_owner": "seo.content-refresh"}
    jsonschema.validate(row, resource_schema("seo", "keyword-opportunity"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {**row, "foundation_revision": "r3"}, resource_schema("seo", "keyword-opportunity")
        )


def test_verified_refresh_can_retain_pending_sync_in_existing_evidence() -> None:
    record = refresh()
    record["implementation"]["status"] = "verified"
    record["implementation"]["change_results"] = record["implementation"]["change_results"][:1]
    record["evidence_refs"].append("ev:guidance-handoff")
    sync = {
        "canonical_owner_ref": "resource:701",
        "candidate_ref": "artifact:604@sha-B",
        "foundation": foundation_selection(),
        "review_ref": "artifact:605",
        "implementation_refs": ["artifact:602", "artifact:603"],
        "synchronization_status": "pending",
        "next_owner": "branding.content-production",
        "authority_limit": "Current assignment permits assessment only",
    }
    index_item = schema("seo.content-refresh", "outputs", "evidence_packet")["properties"][
        "evidence_index"
    ]["items"]
    jsonschema.validate(indexed_artifact("artifact:pending-sync", sync), index_item)
    jsonschema.validate(record, resource_schema("seo", "content-refresh"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {**record, "synchronization_status": "pending"},
            resource_schema("seo", "content-refresh"),
        )
    closeout = instructions("seo.content-refresh", "record_outcome")
    receiver = instructions("branding.content-production", "interview-capture")
    assert "synchronization pending" in closeout
    assert "current grants" in receiver
    assert "no-op reasons" in receiver
    assert "Do not redraft, republish or replay" in receiver
    assert "Record publication skipped" in receiver


def test_version_bound_reviews_use_existing_resource_artifact_refs() -> None:
    log = [
        {
            "review_type": kind,
            "verdict": "cleared for candidate sha-A and selected foundation r3",
            "reviewer": f"independent-{kind}",
            "artifact_ref": f"artifact:{kind}-candidate-and-foundation-receipt",
            "reviewed_at": "2026-09-24T12:00:00Z",
        }
        for kind in ("claim", "voice", "sanitization")
    ]
    jsonschema.validate(
        log, resource_schema("branding", "content-piece")["properties"]["review_log"]
    )
    evidence = {
        "title": "Current candidate and foundation review",
        "evidence_type": "copy-verification",
        "summary": "Review binds sha-A and selected foundation r3",
        "status": "passed",
        "artifact_refs": ["artifact:campaign-candidate-and-foundation-receipt"],
    }
    jsonschema.validate(evidence, resource_schema("marketing", "campaign-evidence"))
    for key, step in (
        ("seo.content-refresh", "apply_or_prepare"),
        ("branding.content-production", "execute-publication"),
        ("marketing.campaign-production", "build-gallery"),
    ):
        text = instructions(key, step)
        assert "foundation" in text and "candidate" in text and "review" in text


@pytest.mark.parametrize("key", ["seo.content-refresh", "marketing.campaign-production"])
def test_material_copy_uses_existing_conditional_independent_role(key: str) -> None:
    role = next(
        item
        for item in workflow(key)["agent_requirements"]
        if item["agent_preset_ref"] == "branding.voice-reviewer"
    )
    assert role["requirement"] == "recommended"
    notes = " ".join(role["handoff_notes"])
    assert "Main requires" in notes
    assert "material" in notes and "branded copy" in notes
    assert "candidate" in notes and "foundation" in notes
    assert "without" in notes and "foundation setup" in notes


def test_marketing_retains_legacy_selection_and_requires_authority_for_owner_update() -> None:
    text = instructions("marketing.campaign-production", "intake-brief")
    assert "legacy marketing foundations remain valid" in text
    assert "do not require branding setup or migrate historical records" in text
    assert "capability, not authorization" in text
    assert "current grants" in text and "otherwise prepare an owner handoff" in text
    contract = next(
        item
        for item in workflow("marketing.campaign-production")["resource_contracts"]
        if item["key"] == "brand_profiles"
    )
    assert "competing canonical foundation" in contract["purpose"]


def test_rehearsal_corpus_separates_prompts_from_independent_rubric() -> None:
    corpus = yaml.safe_load(CASES.read_text())
    ids = [item["id"] for item in corpus["prompts"]]
    assert len(ids) == len(set(ids)) == 8
    assert set(ids) == set(corpus["rubric"])
    for prompt in corpus["prompts"]:
        assert set(prompt) == {"id", "role", "prompt"}
        assert prompt["role"] and prompt["prompt"]
        rubric = corpus["rubric"][prompt["id"]]
        assert set(rubric) == {"owner", "disposition", "required_checks", "reject"}
        assert rubric["required_checks"] and rubric["reject"]
