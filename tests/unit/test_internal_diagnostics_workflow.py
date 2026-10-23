"""The diagnostic method is workflow data, not a second incident engine."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from stackos.workflows.template_schema import validate_workflow_template_obj
from tests.helpers.internal_diagnostics import STAGES, receipt, workflow


def output_schema(key: str) -> dict:
    return next(item["schema"] for item in workflow()["outputs"] if item["key"] == key)


def test_internal_diagnostics_is_valid_bounded_workflow_data() -> None:
    spec = workflow()
    result = validate_workflow_template_obj(spec)
    assert result.valid, result.errors
    assert [step["id"] for step in spec["steps"]] == [stage for stage, _ in STAGES]
    assert not spec.get("action_contracts")
    assert spec["metadata"]["artifact_grant_policy"] == "explicit"
    assert all(g["tool"] == "artifact.create" for g in spec["metadata"]["mcp_tool_grants"])
    assert {r["resource"] for r in spec["resource_contracts"]} == {"support-investigation"}
    assert all("slack" not in str(r).lower() for r in spec.get("auth_requirements", []))


@pytest.mark.parametrize("stage,key", STAGES)
def test_recorded_real_incident_packets_validate(stage: str, key: str) -> None:
    schema = output_schema(key)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(receipt(stage))


@pytest.mark.parametrize("field", ["incident_key", "attempts", "runtime", "next_action", "cursor"])
def test_incomplete_resume_packet_cannot_pass(field: str) -> None:
    packet = receipt("resume-provenance")
    del packet[field]
    assert list(Draft202012Validator(output_schema("incident_resume")).iter_errors(packet))


def test_recorded_evidence_cannot_claim_live_fix_or_repair() -> None:
    final = receipt("verify-escalate")
    final["status"] = "verified_resolved"
    final["verification"]["symptom_check"] = "passed"
    final["verification"]["gap_codes"] = []
    final["next_action"]["disposition"] = "close"
    assert list(Draft202012Validator(output_schema("incident_receipt")).iter_errors(final))

    repair = receipt("propose-remediation")
    repair["remediation"]["disposition"] = "attempted"
    assert list(Draft202012Validator(output_schema("remediation_receipt")).iter_errors(repair))


@pytest.mark.parametrize("origin", ["unverified", "local_build", "unavailable"])
def test_live_receipt_rejects_unverified_or_source_runtime(origin: str) -> None:
    packet = receipt("resume-provenance")
    packet["mode"] = "live"
    packet["runtime"]["installed_package"] = origin
    assert list(Draft202012Validator(output_schema("incident_resume")).iter_errors(packet))


def test_attempted_live_remediation_requires_authority_and_identity() -> None:
    packet = receipt("propose-remediation")
    packet["mode"] = "live"
    packet["runtime"] = {
        "installed_package": "verified_published",
        "package_ref": "test-only:published-package",
        "executable_ref": "test-only:packaged-executable",
        "version": "test-version",
        "package_source_revision": None,
        "fingerprint_sha256": "a" * 64,
        "release_evidence_ref": "test-only:release-evidence",
    }
    packet["remediation"]["disposition"] = "attempted"
    validator = Draft202012Validator(output_schema("remediation_receipt"))
    assert list(validator.iter_errors(packet))
    packet["boundaries"].update(live_repair_authorized=True, authority_ref="test-only:approval")
    packet["remediation"].update(
        operation_ref="test-only:repair",
        target_ref="test-only:target",
        attempt_ref="test-only:attempt",
    )
    validator.validate(packet)


def test_receipt_has_no_freeform_log_or_response_body_slots() -> None:
    packet = receipt("resume-provenance")
    for key in ("raw_log", "environment", "response_body"):
        leaked = deepcopy(packet)
        leaked[key] = "must-not-be-persisted"
        assert list(Draft202012Validator(output_schema("incident_resume")).iter_errors(leaked))


def test_verified_resolution_requires_current_reconciled_evidence() -> None:
    final = receipt("verify-escalate")
    final["mode"] = "live"
    final["runtime"] = {
        "installed_package": "verified_published",
        "package_ref": "test-only:published-package",
        "executable_ref": "test-only:packaged-executable",
        "version": "test-version",
        "package_source_revision": None,
        "fingerprint_sha256": "a" * 64,
        "release_evidence_ref": "test-only:release-evidence",
    }
    final["status"] = "verified_resolved"
    final["next_action"]["disposition"] = "close"
    final["verification"].update(symptom_check="passed", gap_codes=[], cause_status="unknown")
    validator = Draft202012Validator(output_schema("incident_receipt"))
    assert list(validator.iter_errors(final))
    final["verification"].update(
        cause_status="bounded_uncertainty",
        current_effect_status="reconciled",
        observed_at="2026-10-23T00:00:00Z",
        runtime_fingerprint_sha256="a" * 64,
    )
    validator.validate(final)
    for key, bad_value in (
        ("cause_status", "unknown"),
        ("current_effect_status", "unresolved"),
        ("observed_at", None),
        ("runtime_fingerprint_sha256", None),
    ):
        broken = deepcopy(final)
        broken["verification"][key] = bad_value
        assert list(validator.iter_errors(broken))


def test_semantic_duties_are_not_misrepresented_as_schema_guarantees() -> None:
    policies = {item["key"]: item["description"] for item in workflow()["policies"]}
    assert "Schema validation does not prove" in policies["preserve-effects-and-budgets"]
    assert "schema shape is not a privacy guarantee" in policies["minimum-safe-evidence"]
