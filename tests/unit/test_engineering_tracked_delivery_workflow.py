"""Contract tests for the generic engineering tracked-delivery workflow."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from stackos.workflows.template_schema import validate_workflow_template_obj

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / "plugins/engineering/workflows/tracked-delivery.yaml"


def _workflow() -> dict:
    loaded = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _output(workflow: dict, key: str) -> dict:
    return next(item for item in workflow["outputs"] if item["key"] == key)


def _verification_summary() -> dict:
    return {
        "checks": [
            {
                "check_id": "workflow-contract",
                "kind": "automated",
                "required": True,
                "status": "passed",
                "executed": True,
                "scope": ["engineering.tracked-delivery"],
                "checked_at": "2026-07-16T21:05:00Z",
                "evidence_refs": ["engineering-evidence-1"],
                "command_or_run_ref": (
                    "uv run pytest tests/unit/test_engineering_tracked_delivery_workflow.py -q"
                ),
                "counts": {"passed": 4, "failed": 0, "skipped": 0},
                "lifecycle_state": "current",
            }
        ],
        "failed_checks": [],
        "required_unexecuted": [],
        "coverage_gaps": [],
        "validity_status": "valid",
    }


def _impact_map() -> dict:
    return {
        "data_model": [],
        "operations": [],
        "adapters": [],
        "permissions": [],
        "ui": [],
        "tests": ["tests/unit/test_engineering_tracked_delivery_workflow.py"],
        "docs": [],
        "removals": [],
        "ownership": {
            "canonical_owner": "plugins/engineering/workflows/tracked-delivery.yaml",
            "existing_pattern": "Built-in versioned workflow template",
            "active_consumers": ["workflow template loader"],
            "intended_change": "extend",
        },
        "scope_deltas": {},
    }


def test_tracked_delivery_v030_keeps_one_key_and_twelve_steps() -> None:
    workflow = _workflow()
    validation = validate_workflow_template_obj(workflow)

    assert validation.valid is True, validation.errors
    assert workflow["key"] == "engineering.tracked-delivery"
    assert workflow["version"] == "0.3.0"
    assert [step["id"] for step in workflow["steps"]] == [
        "scope-work",
        "define-requirements",
        "discover-impact",
        "plan-tickets",
        "design-approach",
        "review-design",
        "design-tests",
        "deliver-tickets",
        "verify-delivery",
        "review-delivery",
        "audit-tracker",
        "release-closeout",
    ]
    stackos_skill = next(
        item for item in workflow["skill_requirements"] if item["skill_ref"] == "stackos:stackos"
    )
    assert stackos_skill["requirement"] == "required"
    requirements = {item["role"]: item["requirement"] for item in workflow["agent_requirements"]}
    assert requirements["planning"] == "required"
    assert requirements["delivery"] == "required"
    assert requirements["architecture"] == "recommended"
    assert requirements["test-designer"] == "recommended"
    assert requirements["delivery-reviewer"] == "recommended"
    selection_text = " ".join(workflow["when_to_use"]).lower()
    assert "follow the sdlc" in selection_text
    assert "release-grade" in selection_text


def test_tracked_delivery_is_guidance_driven_with_concise_truth_contracts() -> None:
    workflow = _workflow()
    policies = {item["key"]: item["description"] for item in workflow["policies"]}

    assert "agents still decide" in policies["agent_decides_strategy"].lower()
    assert "project pattern" in policies["scope_drift_control"].lower() or (
        "planned ownership" in policies["scope_drift_control"].lower()
    )
    assert "for repository-bound delivery" in policies["release_candidate_lock"].lower()
    assert "when evidence suggests" in policies["finding_lifecycle"].lower()

    scope_required = set(_output(workflow, "scope_summary")["schema"]["required"])
    assert {
        "delivery_mode",
        "risk_classification",
        "operator_constraints",
        "forbidden_environments",
        "forbidden_actions",
        "deferred_gates",
        "release_authority",
        "constraint_revision",
    } <= scope_required

    requirements_required = set(_output(workflow, "requirements_brief")["schema"]["required"])
    assert {"effective_contract", "contradictions"} <= requirements_required

    impact_schema = _output(workflow, "impact_map")["schema"]
    assert {"ownership", "scope_deltas"} <= set(impact_schema["required"])
    assert "root_cause_analysis" not in impact_schema["required"]

    delivery_required = set(_output(workflow, "delivery_summary")["schema"]["required"])
    assert "release_candidate" not in delivery_required
    assert "tdd_receipts" not in delivery_required

    review_required = set(_output(workflow, "review_summary")["schema"]["required"])
    assert "pattern_audits" not in review_required
    assert "delivery_root_cause" not in review_required

    for key in ("design_review", "tracker_audit", "release_summary"):
        assert _output(workflow, key)["required"] is True


def test_bug_root_cause_contract_is_conditional_but_structured_when_used() -> None:
    workflow = _workflow()
    schema = _output(workflow, "impact_map")["schema"]
    validator = Draft202012Validator(schema)
    impact = _impact_map()

    assert list(validator.iter_errors(impact)) == []

    incomplete_bug_analysis = deepcopy(impact)
    incomplete_bug_analysis["root_cause_analysis"] = {"root_cause_owner": "writer"}
    assert list(validator.iter_errors(incomplete_bug_analysis))

    complete_bug_analysis = deepcopy(impact)
    complete_bug_analysis["root_cause_analysis"] = {
        "root_cause_owner": "writer",
        "bad_state_origin": "producer",
        "persisted_data_assessment": "No persisted bad state",
        "repair_boundary": "producer validation",
        "compatibility_exception": "None",
        "deletion_condition": "Not applicable",
    }
    assert list(validator.iter_errors(complete_bug_analysis)) == []


def test_verification_validity_requires_no_failed_or_required_unexecuted_checks() -> None:
    workflow = _workflow()
    schema = _output(workflow, "verification_summary")["schema"]
    validator = Draft202012Validator(schema)
    valid = _verification_summary()

    assert list(validator.iter_errors(valid)) == []

    failed = deepcopy(valid)
    failed["failed_checks"] = ["workflow-contract"]
    assert list(validator.iter_errors(failed))

    incomplete = deepcopy(valid)
    incomplete["required_unexecuted"] = ["release-signoff"]
    assert list(validator.iter_errors(incomplete))

    false_valid = deepcopy(valid)
    false_valid["checks"][0]["status"] = "failed"
    assert list(validator.iter_errors(false_valid))

    optional_failure = deepcopy(valid)
    optional_failure["checks"][0]["required"] = False
    optional_failure["checks"][0]["status"] = "failed"
    assert list(validator.iter_errors(optional_failure))

    contradictory_counts = deepcopy(valid)
    contradictory_counts["checks"][0]["counts"]["failed"] = 1
    assert list(validator.iter_errors(contradictory_counts))

    stale_required_proof = deepcopy(valid)
    stale_required_proof["checks"][0]["lifecycle_state"] = "historical"
    assert list(validator.iter_errors(stale_required_proof))


def test_dedicated_review_and_release_outputs_require_explicit_disposition() -> None:
    workflow = _workflow()
    review_schema = _output(workflow, "design_review")["schema"]
    release_schema = _output(workflow, "release_summary")["schema"]

    waived_review = {
        "disposition": "waived",
        "reason": "Operator explicitly waived independent design review.",
        "blockers": [],
        "repairs": [],
        "residual_risk": [],
        "evidence_refs": ["operator-message-1"],
    }
    assert list(Draft202012Validator(review_schema).iter_errors(waived_review)) == []

    missing_reason = deepcopy(waived_review)
    missing_reason.pop("reason")
    assert list(Draft202012Validator(review_schema).iter_errors(missing_reason))

    held_release = {
        "decision": "held",
        "reason": "Release authority has not been granted.",
        "authority": {
            "release": "not_granted",
            "deployment": "not_granted",
            "production_writes": "not_granted",
        },
        "changed_behavior": [],
        "verification_refs": ["verification-1"],
        "deployment_state": {"status": "not_authorized"},
        "recovery_plan": {"summary": "No release side effect occurred."},
        "residual_risk": [],
    }
    assert list(Draft202012Validator(release_schema).iter_errors(held_release)) == []

    missing_authority = deepcopy(held_release)
    missing_authority.pop("authority")
    assert list(Draft202012Validator(release_schema).iter_errors(missing_authority))
