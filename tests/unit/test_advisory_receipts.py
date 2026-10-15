"""Synthetic pure consistency proof, not trusted real-artifact attestation.

No server fixtures, persistent store, commands, providers or live owner are used.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from stackos.workflows.advisory_receipts import (
    RECEIPT_SCHEMA_VERSION,
    AdvisoryReceipt,
    ReceiptBinding,
    check_advisory_receipt,
)

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
BINDING = {
    "project_id": 14,
    "canonical_item_id": "ticket:114",
    "controller_run_id": 11,
    "run_plan_id": 22,
    "run_plan_step_id": 33,
    "attempt_run_id": 44,
    "owner_ref": "synthetic-owner",
    "owner_epoch": 2,
    "owner_revision": 3,
    "scope_digest": "f" * 64,
    "repository_ref": "synthetic://stackos",
    "source_revision": "a" * 40,
    "candidate_revision": "b" * 40,
    "branch_ref": "synthetic/advisory",
    "candidate_sha256": "c" * 64,
    "obligation_id": "synthetic-obligation",
    "assessment_contract_revision": "synthetic-contract-v1",
    "verifier_revision": "synthetic-verifier-v1",
    "artifact": {
        "artifact_id": 55,
        "uri": "synthetic://evidence",
        "sha256": "d" * 64,
    },
}


def fixture() -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "binding": deepcopy(BINDING),
        "producer_ref": "synthetic-producer",
        "producer_provenance_ref": "synthetic://provenance",
        "captured_at": NOW.isoformat().replace("+00:00", "Z"),
        "receipt_artifact_id": 66,
        "outcome": "passed",
        "gaps": [],
    }


def check(payload: dict, **kwargs):
    options = {
        "expected": ReceiptBinding.model_validate(BINDING),
        "now": NOW,
        "max_age": timedelta(minutes=5),
    }
    options.update(kwargs)
    return check_advisory_receipt(json.dumps(payload), **options)


def test_matching_same_scope_reuse_is_idempotent_but_never_attested_or_authorized():
    first = check(fixture())
    assert first.consistent
    assert first == check(fixture())
    assert first.granted_authorities == ()
    assert set(first.unproven_checks) == {
        "producer_authentication",
        "canonical_owner_currentness",
        "artifact_content_attestation",
        "approval_provenance",
        "durable_retention",
    }


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("project_id", 15),
        ("canonical_item_id", "ticket:115"),
        ("controller_run_id", 12),
        ("run_plan_id", 23),
        ("run_plan_step_id", 34),
        ("attempt_run_id", 45),
        ("attempt_run_id", None),
        ("owner_ref", "other-owner"),
        ("owner_epoch", 1),
        ("owner_revision", 4),
        ("scope_digest", "9" * 64),
        ("repository_ref", "synthetic://other-repo"),
        ("source_revision", "e" * 40),
        ("candidate_revision", "e" * 40),
        ("branch_ref", "other-branch"),
        ("candidate_sha256", "e" * 64),
        ("obligation_id", "unrelated-obligation"),
        ("assessment_contract_revision", "v2"),
        ("verifier_revision", "v2"),
        ("artifact", {**BINDING["artifact"], "sha256": "e" * 64}),
        ("artifact", {**BINDING["artifact"], "artifact_id": 56}),
        ("artifact", {**BINDING["artifact"], "uri": "synthetic://other"}),
    ],
)
def test_each_wrong_binding_is_unproven(field, replacement):
    payload = fixture()
    payload["binding"][field] = replacement
    result = check(payload)
    assert not result.consistent
    assert [(d.kind, d.field) for d in result.diagnostics] == [
        ("binding_mismatch", f"binding.{field}")
    ]
    assert result.granted_authorities == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", True),
        ("owner_epoch", "2"),
        ("owner_epoch", 0),
        ("attempt_run_id", -1),
        ("run_plan_step_id", 2**63),
        ("canonical_item_id", "MYB-114"),
        ("canonical_item_id", "ticket:01"),
        ("canonical_item_id", "ticket:9223372036854775808"),
        ("canonical_item_id", "ticket:١١٤"),
        ("owner_ref", " "),
        ("scope_digest", "short"),
        ("scope_digest", "g" * 64),
        ("scope_digest", "F" * 64),
        ("repository_ref", ""),
        ("branch_ref", " padded "),
        ("source_revision", "HEAD"),
        ("candidate_revision", "B" * 40),
        ("candidate_sha256", "c" * 63),
        ("assessment_contract_revision", "\n"),
    ],
)
def test_malformed_bindings_are_rejected_without_coercion(field, value):
    payload = fixture()
    payload["binding"][field] = value
    result = check(payload)
    assert not result.consistent
    assert "malformed" in {d.kind for d in result.diagnostics}


@pytest.mark.parametrize("field", list(BINDING))
def test_missing_binding_is_not_defaulted(field):
    payload = fixture()
    del payload["binding"][field]
    assert not check(payload).consistent


@pytest.mark.parametrize(
    ("captured", "expected_kind"),
    [
        (NOW - timedelta(minutes=5), None),
        (NOW - timedelta(minutes=5, microseconds=1), "stale"),
        (NOW + timedelta(microseconds=1), "future_capture"),
        (NOW.replace(tzinfo=None), "malformed"),
    ],
)
def test_capture_age_boundary_and_timezone(captured, expected_kind):
    payload = fixture()
    payload["captured_at"] = captured.isoformat()
    result = check(payload)
    assert [d.kind for d in result.diagnostics] == (
        [] if expected_kind is None else [expected_kind]
    )


@pytest.mark.parametrize("version", ["stackos.advisory-receipt.v2", "legacy", 1])
def test_unknown_version_is_unproven_without_fallback(version):
    payload = fixture()
    payload["schema_version"] = version
    assert [d.kind for d in check(payload).diagnostics] == ["unsupported_version"]


@pytest.mark.parametrize("raw", ["{", "null", "[]", "{}"])
def test_invalid_json_or_record_shape_is_unproven(raw):
    result = check_advisory_receipt(
        raw,
        expected=ReceiptBinding.model_validate(BINDING),
        now=NOW,
        max_age=timedelta(minutes=5),
    )
    assert not result.consistent
    assert result.receipt is None


@pytest.mark.parametrize("where", ["receipt", "binding", "artifact"])
def test_forged_provenance_authority_flags_are_not_schema_fields(where):
    payload = fixture()
    target = payload if where == "receipt" else payload["binding"]
    if where == "artifact":
        target = target["artifact"]
    target["authenticated"] = True
    result = check(payload)
    assert not result.consistent
    assert result.granted_authorities == ()


@pytest.mark.parametrize(
    "outcome", ["passed", "failed", "blocked", "skipped", "not_run", "unknown", "error"]
)
def test_recorded_outcomes_and_gaps_are_preserved_without_authority(outcome):
    payload = fixture()
    payload["outcome"] = outcome
    payload["gaps"] = [
        {"kind": "unsupported", "detail": "No semantic outcome assessment."},
        {"kind": "manual_required", "detail": "Human artifact review required."},
        {"kind": "unknown", "detail": "Producer is not authenticated."},
        {"kind": "error", "detail": "Original recorded assessment error."},
    ]
    result = check(payload)
    assert result.consistent
    assert result.receipt.model_dump(mode="json") == payload
    assert result.granted_authorities == ()


def test_standalone_receipt_keeps_link_when_transient_step_view_is_discarded():
    # Illustrative in-memory retention only: no actual rewind or durable-store proof.
    retained_json = json.dumps(fixture())
    transient_step_result = {"receipt_artifact_id": 66}
    transient_step_result.clear()
    restored = AdvisoryReceipt.model_validate_json(retained_json)
    assert restored.receipt_artifact_id == 66
    assert restored.binding.artifact.sha256 == "d" * 64
    assert "durable_retention" in check(fixture()).unproven_checks


@pytest.mark.parametrize(
    "options",
    [
        {"now": NOW.replace(tzinfo=None)},
        {"max_age": timedelta(0)},
        {"max_age": timedelta(seconds=-1)},
    ],
)
def test_invalid_freshness_policy_is_unproven(options):
    result = check(fixture(), **options)
    assert [d.kind for d in result.diagnostics] == ["invalid_check_context"]


def test_validated_records_are_immutable_and_attempt_inapplicability_is_exact():
    payload = fixture()
    payload["binding"]["attempt_run_id"] = None
    expected = ReceiptBinding.model_validate(payload["binding"])
    result = check(payload, expected=expected)
    assert result.consistent
    with pytest.raises(ValidationError):
        result.receipt.binding.owner_epoch = 3
