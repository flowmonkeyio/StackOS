"""SEO workflow contracts: evidence, scoped changes, recovery, and legacy records."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest
import yaml

from stackos.plugins.manifest import load_plugin_manifest_file
from stackos.workflows.run_plan_schema import run_plan_from_template
from stackos.workflows.template_loader import LoadedWorkflowTemplate, WorkflowTemplateSummaryOut
from stackos.workflows.template_schema import WorkflowTemplateSpec

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins/seo/plugin.yaml"
WORKFLOWS = ROOT / "plugins/seo/workflows"
VERSIONED = (
    "keyword-opportunity",
    "link-opportunity",
    "search-performance-snapshot",
    "content-refresh",
)


def resource_schema(key: str) -> dict:
    manifest = load_plugin_manifest_file(PLUGIN)
    return next(resource.schema_data for resource in manifest.resources if resource.key == key)


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / f"{name}.yaml").read_text())


def generated_plan(name: str, *, mode: str = "prepare"):
    spec = WorkflowTemplateSpec.model_validate(workflow(name))
    loaded = LoadedWorkflowTemplate(
        spec=spec,
        summary=WorkflowTemplateSummaryOut(
            key=spec.key,
            name=spec.name,
            version=spec.version,
            source="plugin",
            precedence=10,
            plugin_slug="seo",
        ),
    )
    inputs = {"goal": "Find useful opportunities", "evidence_sources": ["exports"]}
    if name == "content-refresh":
        inputs = {
            "refresh_goal": "Improve existing pages",
            "content_ref": "https://example.com/welcome",
            "evidence_sources": ["exports"],
            "mode": mode,
        }
        if mode == "assess":
            inputs["prior_refresh_ref"] = "resource:previous-refresh"
    return run_plan_from_template(loaded, inputs_json=inputs)


def step_grants(plan, step_id: str) -> list[dict]:
    return [
        grant
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
        if grant["step_id"] == step_id
    ]


def granted_tools(plan, step_id: str) -> set[str]:
    return {
        tool
        for grant in step_grants(plan, step_id)
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }


def output_schema(name: str, key: str) -> dict:
    return next(item["schema"] for item in workflow(name)["outputs"] if item["key"] == key)


@pytest.mark.parametrize("key", VERSIONED)
@pytest.mark.parametrize(
    "historical",
    [
        {},
        {"schemaVersion": "legacy-v7", "collections": {"rows": []}},
        {"schema_version": "custom", "unreviewed": True},
    ],
)
def test_unversioned_history_remains_readable_and_updatable(key: str, historical: dict) -> None:
    schema = resource_schema(key)
    jsonschema.validate(historical, schema)
    jsonschema.validate({**historical, "operator_note": "Retained historical context"}, schema)


@pytest.mark.parametrize("key", VERSIONED)
def test_explicit_unknown_or_incomplete_current_contract_cannot_fall_back_to_history(
    key: str,
) -> None:
    schema = resource_schema(key)
    for marker in ("unknown", None, f"stackos.seo.{key}.v1"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"contract_version": marker}, schema)


def test_current_workflow_outputs_require_a_version_and_cannot_accept_history() -> None:
    for name, key in (
        ("keyword-research", "opportunity_summary"),
        ("keyword-research", "evidence_packet"),
        ("content-refresh", "refresh_summary"),
        ("content-refresh", "change_proposal"),
        ("content-refresh", "evidence_packet"),
        ("content-refresh", "diagnosis"),
    ):
        schema = output_schema(name, key)
        for historical in ({}, {"schemaVersion": "legacy", "collections": []}):
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(historical, schema)


@pytest.mark.parametrize("key", VERSIONED)
def test_records_reference_one_package_index_instead_of_copying_it(key: str) -> None:
    current = resource_schema(key)["$defs"]["current"]
    assert "evidence_index_ref" in current["required"]
    assert "evidence_refs" in current["required"]
    assert "evidence_index" not in current["properties"]
    assert current["additionalProperties"] is False


def evidence() -> list[dict]:
    return [
        {
            "evidence_ref": "ev:page",
            "kind": "artifact",
            "source": "public-page",
            "captured_at": "2026-09-24T12:00:00Z",
            "lifecycle_state": "current",
            "scope": {"url": "https://example.com/welcome"},
            "receipt_ref": "artifact:page-1",
            "artifact_ref": "artifact:page-1",
            "limitations": [],
        }
    ]


def opportunity() -> dict:
    return {
        "contract_version": "stackos.seo.keyword-opportunity.v1",
        "intent": "Plan the emails after signup",
        "reader_task": "Build a welcome sequence",
        "business_fit": "The product sends welcome sequences",
        "existing_page_refs": ["https://example.com/welcome"],
        "disposition": "refresh",
        "rationale": "Existing guide covers the intent but lacks an example",
        "confidence": "medium",
        "next_validation": "Review the guide and current SERP",
        "evidence_refs": ["ev:page"],
        "evidence_index_ref": "artifact:evidence-packet",
    }


def test_opportunities_require_real_gap_for_create_and_allow_non_creation_decisions() -> None:
    schema = resource_schema("keyword-opportunity")
    for disposition in ("refresh", "link", "investigate_overlap", "defer", "reject"):
        jsonschema.validate({**opportunity(), "disposition": disposition}, schema)
    creation = {**opportunity(), "disposition": "create", "existing_page_refs": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(creation, schema)
    jsonschema.validate(
        {**creation, "gap_evidence": "Inventory and SERP show an unmet installation question"},
        schema,
    )
    for missing in (
        "reader_task",
        "business_fit",
        "existing_page_refs",
        "evidence_refs",
        "confidence",
        "next_validation",
    ):
        incomplete = opportunity()
        incomplete.pop(missing)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(incomplete, schema)


def link() -> dict:
    return {
        "contract_version": "stackos.seo.link-opportunity.v1",
        "change_id": "link-1",
        "source_url": "https://example.com/template",
        "destination_url": "https://example.com/welcome",
        "locator": "section:after-signup paragraph:2",
        "existing_text": "Plan what follows the first email.",
        "proposed_text": "Plan your welcome sequence after the first email.",
        "anchor_text": "welcome sequence",
        "reason_to_click": "See the complete sequence",
        "source_version_ref": "artifact:source-v1",
        "scope": "page",
        "checks": {
            "existing_link": "absent",
            "destination": "verified",
            "shared_template_scope": "not-shared",
            "evidence_refs": ["ev:page"],
        },
        "status": "proposed",
        "evidence_refs": ["ev:page"],
        "evidence_index_ref": "artifact:evidence-packet",
    }


def test_link_proposal_preserves_exact_edit_and_preflight_evidence() -> None:
    schema = resource_schema("link-opportunity")
    jsonschema.validate(link(), schema)
    for missing in (
        "existing_text",
        "proposed_text",
        "source_version_ref",
        "destination_url",
        "checks",
    ):
        incomplete = link()
        incomplete.pop(missing)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(incomplete, schema)


def snapshot() -> dict:
    return {
        "contract_version": "stackos.seo.search-performance-snapshot.v1",
        "source": "export",
        "source_ref": "artifact:ga4-export",
        "property_ref": "properties/123",
        "captured_at": "2026-09-24T12:00:00Z",
        "window": {
            "start": "2026-08-01",
            "end": "2026-08-28",
            "timezone": "America/Los_Angeles",
            "data_state": "final",
        },
        "filters": {
            "search_type": "web",
            "country": "US",
            "device": "all",
            "brand_scope": "non-brand",
            "other": {},
        },
        "dimensions": ["landingPage"],
        "cohort": {
            "definition": "Google organic sessions",
            "scope": "session",
            "entry_page_semantics": "Session landing page, not first lifetime acquisition",
        },
        "url_mapping": {
            "strategy": "Canonical URL map",
            "mapping_ref": "artifact:canonical-map",
            "unmatched_count": 0,
            "limitations": [],
        },
        "metric_definitions": [
            {
                "name": "signups",
                "unit": "events",
                "denominator": {
                    "metric": "sessions",
                    "value": 400,
                    "scope": "same organic session cohort",
                    "limitation": None,
                },
                "attribution_model": "session",
                "attribution_window": "reporting window",
            }
        ],
        "metrics": [{"name": "signups", "value": 0, "availability": "measured"}],
        "coverage": {
            "status": "complete",
            "row_count": 1,
            "total_rows": 1,
            "pagination": "Complete export",
        },
        "sample": {
            "status": "sufficient",
            "rationale": "Reviewed volume for the stated comparison",
        },
        "comparability": {"status": "comparable", "limitations": []},
        "comparison_status": "ready",
        "evidence_index_ref": "artifact:evidence-packet",
        "evidence_refs": ["ev:page"],
        "limitations": [],
    }


def test_missing_measurement_is_not_zero_and_inconclusive_states_are_preserved() -> None:
    schema = resource_schema("search-performance-snapshot")
    jsonschema.validate(snapshot(), schema)
    missing = snapshot()
    missing["metrics"][0] = {"name": "signups", "value": None, "availability": "missing"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)
    missing["comparison_status"] = "inconclusive"
    jsonschema.validate(missing, schema)
    missing["metrics"][0]["value"] = 0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)
    for field, state in (
        ("sample", "insufficient"),
        ("coverage", "truncated"),
        ("comparability", "incomparable"),
    ):
        limited = snapshot()
        limited[field]["status"] = state
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(limited, schema)
        limited["comparison_status"] = "inconclusive"
        jsonschema.validate(limited, schema)


def test_measurement_metadata_is_explicit() -> None:
    schema = resource_schema("search-performance-snapshot")
    for missing in (
        "source_ref",
        "property_ref",
        "captured_at",
        "window",
        "filters",
        "dimensions",
        "cohort",
        "url_mapping",
        "metric_definitions",
        "coverage",
    ):
        incomplete = snapshot()
        incomplete.pop(missing)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(incomplete, schema)


def test_unknown_denominator_needs_a_limitation_and_actual_zero_stays_measured() -> None:
    schema = resource_schema("search-performance-snapshot")
    packet = snapshot()
    packet["metric_definitions"][0]["denominator"]["value"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)
    packet["metric_definitions"][0]["denominator"]["limitation"] = "Export omitted sessions"
    jsonschema.validate(packet, schema)
    assert packet["metrics"][0] == {"name": "signups", "value": 0, "availability": "measured"}


def refresh() -> dict:
    return {
        "contract_version": "stackos.seo.content-refresh.v1",
        "mode": "apply",
        "content_refs": ["https://example.com/template", "https://example.com/welcome"],
        "goal": "Improve useful navigation",
        "diagnosis_ref": "artifact:diagnosis",
        "proposal_ref": "artifact:proposal-v1",
        "proposal_version": "sha256:reviewed-proposal",
        "review": {
            "reviewer_ref": "agent:independent",
            "reviewed_at": "2026-09-24T12:00:00Z",
            "adjudicator_ref": "agent:main",
            "adjudication_ref": "artifact:adjudication",
            "dispositions": [
                {
                    "change_id": "link-1",
                    "disposition": "accepted",
                    "rationale": "Useful next step",
                    "evidence_refs": ["ev:page"],
                }
            ],
        },
        "implementation": {
            "status": "partial",
            "change_results": [
                {
                    "change_id": "link-1",
                    "attempt_id": "attempt-1",
                    "status": "verified",
                    "receipt_refs": ["artifact:commit"],
                    "verification_refs": ["artifact:readback"],
                    "recovery_note": "Keep applied change",
                },
                {
                    "change_id": "link-2",
                    "attempt_id": "attempt-2",
                    "status": "unknown",
                    "receipt_refs": ["artifact:timeout"],
                    "verification_refs": [],
                    "recovery_note": "Reconcile current page before any retry",
                },
            ],
            "operator_approval_ref": "operator:approved-scope",
        },
        "outcome": {
            "status": "pending",
            "summary": "Implementation is partial; no search effect claimed",
            "baseline_snapshot_refs": ["resource:baseline"],
            "followup_snapshot_refs": [],
            "limitations": ["No post-change observation window"],
        },
        "followup": {
            "status": "scheduled",
            "due_at": "2026-10-22T12:00:00Z",
            "next_mode": "assess",
            "next_action": "Reconcile unknown change, then assess comparable metrics",
        },
        "evidence_index_ref": "artifact:evidence-packet",
        "evidence_refs": ["ev:page"],
    }


def test_partial_refresh_preserves_receipts_and_separates_delayed_outcome() -> None:
    schema = resource_schema("content-refresh")
    jsonschema.validate(refresh(), schema)
    packet = refresh()
    packet["implementation"]["change_results"][0]["receipt_refs"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)
    packet = refresh()
    packet["implementation"]["change_results"][1]["receipt_refs"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)
    packet = refresh()
    packet["outcome"]["status"] = "observed"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)


def test_aggregate_verified_cannot_hide_an_unknown_edit() -> None:
    schema = resource_schema("content-refresh")
    packet = refresh()
    packet["implementation"]["status"] = "verified"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)
    packet["implementation"]["change_results"][1] = {
        "change_id": "link-2",
        "status": "skipped",
        "receipt_refs": [],
        "verification_refs": [],
        "recovery_note": "The exact equivalent link already exists; no edit attempted",
    }
    jsonschema.validate(packet, schema)


def test_prepare_cannot_claim_applied_work_and_assess_carries_prior_run_ref() -> None:
    schema = resource_schema("content-refresh")
    packet = refresh()
    packet["mode"] = "prepare"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)
    packet = refresh()
    packet["mode"] = "assess"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)
    packet["prior_refresh_ref"] = "resource:prior-refresh"
    jsonschema.validate(packet, schema)


def test_prepared_packet_routes_to_implementation_before_assessment() -> None:
    schema = resource_schema("content-refresh")
    packet = refresh()
    packet["mode"] = "prepare"
    packet["implementation"] = {
        "status": "prepared",
        "change_results": [],
    }
    packet["followup"] = {
        "status": "blocked",
        "due_at": None,
        "next_mode": "apply",
        "next_action": "Review the implementation route and authorize the current proposal",
    }
    jsonschema.validate(packet, schema)
    packet["followup"]["next_mode"] = "assess"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)


def test_scheduled_assessment_requires_applied_change_and_concrete_due_date() -> None:
    schema = resource_schema("content-refresh")
    packet = refresh()
    packet["followup"]["due_at"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)
    packet = refresh()
    packet["implementation"]["change_results"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(packet, schema)


@pytest.mark.parametrize("mode", ["prepare", "apply", "assess"])
def test_generated_default_refresh_plan_has_no_unconditional_approval_block(mode: str) -> None:
    plan = generated_plan("content-refresh", mode=mode)
    assert plan.approvals == []
    assert all(step.approval_refs == [] for step in plan.steps)


def test_generated_export_research_plan_has_no_cost_approval_block() -> None:
    plan = generated_plan("keyword-research")
    assert plan.approvals == []
    assert all(step.approval_refs == [] for step in plan.steps)
    assert {"artifact.create", "artifact.supersede"} <= granted_tools(plan, "review_opportunities")
    assert "artifact.supersede" not in granted_tools(plan, "store_opportunities")


@pytest.mark.parametrize("name", ["keyword-research", "content-refresh"])
def test_generated_grants_retain_index_before_dependent_records(name: str) -> None:
    plan = generated_plan(name)
    assert "artifact.create" in granted_tools(plan, "collect_evidence")
    assert "resource.upsert" in granted_tools(plan, "collect_evidence")
    assert "artifact.supersede" not in granted_tools(plan, "collect_evidence")
    collect = next(step for step in plan.steps if step.id == "collect_evidence")
    instructions = " ".join(collect.instructions)
    assert "snapshot_refs" in instructions
    assert "source_ledger" in instructions and "evidence_index" in instructions
    assert "evidence_index_ref" in output_schema(name, "evidence_packet")["required"]


def test_generated_refresh_grants_cover_preflight_readback_and_index_replacement() -> None:
    plan = generated_plan("content-refresh")
    for step_id in ("apply_or_prepare", "verify_changes"):
        action_refs = {
            action_ref
            for grant in step_grants(plan, step_id)
            if grant.get("tool") == "action.execute"
            for action_ref in grant["action_refs"]
        }
        assert action_refs == {"utils.web.read"}
    assert {"artifact.create", "artifact.supersede"} <= granted_tools(plan, "assess_outcome")
    assert "artifact.supersede" not in granted_tools(plan, "record_outcome")
    assert "artifact.supersede" not in granted_tools(plan, "apply_or_prepare")
    assert "artifact.supersede" not in granted_tools(plan, "verify_changes")


@pytest.mark.parametrize(
    ("name", "version"),
    [("keyword-research", "0.2.1"), ("content-refresh", "0.2.1"), ("website-analysis", "0.6.1")],
)
def test_templates_remain_valid_with_only_existing_native_action_refs(
    name: str, version: str
) -> None:
    data = workflow(name)
    spec = WorkflowTemplateSpec.model_validate(data)
    assert spec.version == version
    for output in spec.outputs:
        jsonschema.Draft202012Validator.check_schema(output.schema_data)
    assert all(action.risk_level in {"read", "cost"} for action in spec.action_contracts)
    assert not any(action.action and "publish" in action.action for action in spec.action_contracts)


@pytest.mark.parametrize("name", ["keyword-research", "content-refresh"])
def test_native_measurement_reads_are_optional_and_export_route_remains_available(
    name: str,
) -> None:
    data = workflow(name)
    actions = {action["action"]: action for action in data["action_contracts"]}
    for ref in (
        "search-console.search-analytics.query",
        "ga4.properties.metadata.get",
        "ga4.properties.run_report",
        "utils.web.read",
    ):
        assert actions[ref]["optional"] is True
    assert all(item["optional"] for item in data["auth_requirements"])
    assert all(action["optional"] for action in data["action_contracts"])
    assert "export" in str(data["inputs"]).lower()
    assert data["metadata"]["artifact_grant_policy"] == "explicit"


def test_refresh_flow_exposes_review_before_execution_and_assess_without_edit_grants() -> None:
    data = workflow("content-refresh")
    mode = next(item for item in data["inputs"] if item["key"] == "mode")
    assert mode["default"] == "prepare"
    assert mode["schema"]["enum"] == ["prepare", "apply", "assess"]
    steps = {step["id"]: step for step in data["steps"]}
    assert "review_changes" in steps["apply_or_prepare"]["depends_on"]
    assert steps["apply_or_prepare"].get("action_refs", []) == ["public_web_read"]
    assert steps["assess_outcome"].get("action_refs", []) == []
    assert "delivery-reviewer" in [
        item["role"] for item in data["agent_requirements"] if item["requirement"] == "required"
    ]
    execution = " ".join(steps["apply_or_prepare"]["instructions"]).lower()
    assert "assess" in execution and "never" in execution
    assert "unknown" in execution and "retry" in execution
    grants = data["metadata"]["mcp_tool_grants"]
    assert all(
        grant["step_id"]
        in {"collect_evidence", "review_changes", "assess_outcome", "record_outcome"}
        for grant in grants
    )


def test_analysis_keeps_one_inventory_and_reviewed_findings_with_no_site_writes() -> None:
    data = workflow("website-analysis")
    assert "analysis_only" in [item["key"] for item in data["policies"]]
    assert data["approval_gates"] == []
    outputs = {item["key"]: item for item in data["outputs"]}
    assert "url_rows" not in outputs["public_site_map"]["schema"]["properties"]
    assert "representative_url_rows" in outputs["site_inventory"]["schema"]["required"]
    assert outputs["draft_findings"]["schema"] == outputs["seo_findings"]["schema"]
    assert "review_summary" in outputs
    assert all(item["risk_level"] in {"read", "cost"} for item in data["action_contracts"])
