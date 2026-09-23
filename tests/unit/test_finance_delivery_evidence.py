from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from jsonschema import ValidationError

from plugins.finance.scripts.finance_delivery import (
    API_ROUTE,
    MANUAL_ROUTE,
    invoice_snapshot_digest,
    reconcile_manual_invoice_delivery,
    validate_payment_presentation,
)
from plugins.finance.scripts.finance_workspace import _validate_history
from tests.helpers.finance_workspace import (
    AT,
    add_billing_fixture,
    initialize,
    material_billing_digest,
    material_record_digest,
    read_document,
    record,
    validate,
)

CHECKED = "2026-09-06T12:30:00Z"
EXPIRY = "2026-09-07T12:00:00Z"
SNAPSHOT = "a" * 64


def _fixture(tmp_path: Path, route: str = API_ROUTE) -> tuple[dict, Path, dict]:
    finance = tmp_path / "finance"
    document = read_document(initialize(finance))
    billing = add_billing_fixture(document)
    recipients = document["recipient_settings"][0]
    recipients["invoice_ref"] = "fixture:invoice"
    recipients["validity"]["expires_at"] = EXPIRY
    recipients["additional_recipients"] = {
        "state": "verified",
        "to": ["additional@example.test"],
        "cc": ["copy@example.test"],
    }
    billing["payment_presentation"] = {
        "footer_sha256": None,
        "payment_method_types": None,
        "online_payment_link_state": "absent",
        "review_evidence_ref": "presentation:fixture",
    }
    billing["digest_sha256"] = material_billing_digest(billing)
    binding = {
        "billing_version_ref": billing["record_id"],
        "billing_version": billing["version"],
        "billing_digest_sha256": billing["digest_sha256"],
        "recipient_settings_ref": recipients["record_id"],
        "recipient_digest_sha256": material_record_digest(recipients, "recipient_settings"),
        "invoice_snapshot_sha256": SNAPSHOT,
    }
    for name in ("presentation", "delivery"):
        raw = f"Synthetic retained {name} evidence; no real invoice or delivery".encode()
        relative = f"attachments/2026/09/{name}.txt"
        path = finance / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        document["sources"].append(
            record(
                f"source:{name}",
                source_kind="synthetic-verification",
                source_identity=f"fixture:{name}",
                attachment_refs=[f"attachment:{name}"],
                coverage_state="complete",
            )
        )
        document["attachments"].append(
            record(
                f"attachment:{name}",
                "retained",
                source_ref=f"source:{name}",
                role="evidence",
                relative_path=relative,
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
                media_type="text/plain",
                original_filename=f"{name}.txt",
            )
        )
    document["provider_observations"].append(
        record(
            "presentation:fixture",
            "verified",
            provider_ref="fixture:provider",
            account_ref="fixture:account",
            object_type="invoice",
            object_ref="fixture:invoice",
            customer_ref="fixture:provider-customer",
            observed_at=AT,
            observed_status="draft",
            source_refs=["source:presentation"],
            livemode=True,
            payment_presentation_evidence={
                **binding,
                "delivery_route": route,
                "verifier_ref": "fixture:reviewer",
                "verified_at": AT,
                "expires_at": EXPIRY,
                "online_payment_link_state": "absent",
                "verification_method": "recipient-visible-route",
                "attachment_refs": ["attachment:presentation"],
            },
        )
    )
    validate(document)
    return document, finance, binding


def _check(document: dict, finance: Path, **overrides) -> dict:
    args = dict(
        billing_version_ref="billing:fixture:v1",
        invoice_ref="fixture:invoice",
        account_ref="fixture:account",
        invoice_snapshot_sha256=SNAPSHOT,
        delivery_route=API_ROUTE,
        checked_at=CHECKED,
        finance=finance,
    )
    args.update(overrides)
    return validate_payment_presentation(document, **args)


def _manual_fixture(tmp_path: Path) -> tuple[dict, Path, dict]:
    document, finance, binding = _fixture(tmp_path, MANUAL_ROUTE)
    exact = {**binding, "invoice_ref": "fixture:invoice", "account_ref": "fixture:account"}
    document["reviews"].append(
        record(
            "approval:manual-send",
            "approved",
            target_ref=binding["billing_version_ref"],
            target_version=binding["billing_version"],
            target_digest_sha256=binding["billing_digest_sha256"],
            scope=["owner-invoice-send"],
            authority="owner",
            verdict="approved",
            actor="fixture:owner",
            decided_at="2026-09-06T12:10:00Z",
            delivery_authorization={**exact, "delivery_route": MANUAL_ROUTE, "expires_at": EXPIRY},
        )
    )
    document["provider_observations"].append(
        record(
            "delivery:fixture",
            "verified",
            provider_ref="fixture:provider",
            account_ref="fixture:account",
            object_type="invoice",
            object_ref="fixture:invoice",
            customer_ref="fixture:provider-customer",
            observed_at="2026-09-06T12:21:00Z",
            observed_status="open",
            livemode=True,
            source_refs=["source:delivery"],
            invoice_delivery_evidence={
                **binding,
                "delivery_route": MANUAL_ROUTE,
                "verifier_ref": "fixture:independent-reviewer",
                "verified_at": "2026-09-06T12:22:00Z",
                "sent_at": "2026-09-06T12:20:00Z",
                "sent_by_ref": "fixture:owner",
                "outcome": "sent",
                "evidence_kind": "recipient-received-message",
                "attachment_refs": ["attachment:delivery"],
            },
        )
    )
    document["reconciliations"].append(
        record(
            "reconciliation:manual",
            "verified",
            period={"start": "2026-09-06", "end": "2026-09-06"},
            account_refs=["fixture:account"],
            source_refs=["source:delivery"],
            matched_refs=["fixture:invoice"],
            unmatched_refs=[],
            differences=[],
            manual_invoice_delivery={
                **exact,
                "outcome": "verified-sent",
                "owner_authorization_ref": "approval:manual-send",
                "delivery_evidence_ref": "delivery:fixture",
            },
        )
    )
    validate(document)
    return document, finance, binding


def _reconcile(document: dict, finance: Path) -> dict:
    return reconcile_manual_invoice_delivery(
        document, reconciliation_ref="reconciliation:manual", checked_at=CHECKED, finance=finance
    )


def test_exact_recipient_visible_presentation_is_read_only(tmp_path: Path) -> None:
    document, finance, _binding = _fixture(tmp_path)
    before = copy.deepcopy(document)
    result = _check(document, finance)
    assert result["status"] == "verified"
    assert result["delivery_route"] == API_ROUTE
    assert document == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("invoice_ref", "fixture:other-invoice"),
        ("account_ref", "fixture:other-account"),
        ("invoice_snapshot_sha256", "b" * 64),
        ("delivery_route", MANUAL_ROUTE),
        ("checked_at", EXPIRY),
        ("checked_at", "2026-09-05T12:00:00Z"),
    ],
)
def test_presentation_blocks_mismatch_and_stale_context(
    tmp_path: Path, field: str, value: str
) -> None:
    document, finance, _binding = _fixture(tmp_path)
    with pytest.raises(ValueError):
        _check(document, finance, **{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("billing_version", 2),
        ("billing_digest_sha256", "b" * 64),
        ("recipient_settings_ref", "missing:recipient"),
        ("recipient_digest_sha256", "b" * 64),
        ("verification_method", "dashboard-preview"),
        ("verification_method", "api-readback"),
        ("expires_at", "2026-09-06T12:01:00Z"),
        ("attachment_refs", ["missing:attachment"]),
        ("verifier_ref", ""),
        ("verified_at", ""),
    ],
)
def test_presentation_rejects_unresolved_unverified_or_mismatched_proof(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    document, finance, _binding = _fixture(tmp_path)
    document["provider_observations"][0]["payment_presentation_evidence"][field] = value
    with pytest.raises((ValueError, ValidationError)):
        _check(document, finance)


@pytest.mark.parametrize("state", ["unknown", "visible"])
def test_prepared_unknown_can_be_retained_but_cannot_deliver(tmp_path: Path, state: str) -> None:
    document, finance, _binding = _fixture(tmp_path)
    billing = document["billing_versions"][0]
    billing["payment_presentation"]["online_payment_link_state"] = state
    del billing["payment_presentation"]["review_evidence_ref"]
    billing["digest_sha256"] = material_billing_digest(billing)
    document["provider_observations"] = []
    validate(document)
    with pytest.raises(ValueError, match="visible or unknown"):
        _check(document, finance)


def test_absent_requires_resolved_evidence_and_retained_original(tmp_path: Path) -> None:
    document, finance, _binding = _fixture(tmp_path)
    document["provider_observations"] = []
    with pytest.raises(ValueError, match="unresolved"):
        validate(document)
    document, finance, _binding = _fixture(tmp_path / "other")
    original = finance / "attachments/2026/09/presentation.txt"
    original.write_bytes(b"altered proof")
    with pytest.raises(ValueError, match="hash/size"):
        _check(document, finance)
    original.unlink()
    with pytest.raises(FileNotFoundError):
        _check(document, finance)


def test_expired_history_stays_readable_but_cannot_authorize_delivery(tmp_path: Path) -> None:
    document, finance, _binding = _fixture(tmp_path)
    document["provider_observations"][0]["payment_presentation_evidence"]["expires_at"] = (
        "2026-09-06T12:01:00Z"
    )
    validate(document)
    with pytest.raises(ValueError, match="stale"):
        _check(document, finance)


@pytest.mark.parametrize(
    "field", ["primary_email", "additional_recipients", "verified_at", "verifier_ref"]
)
def test_every_recipient_is_bound_and_must_be_verified(tmp_path: Path, field: str) -> None:
    document, finance, _binding = _fixture(tmp_path)
    recipients = document["recipient_settings"][0]
    if field == "additional_recipients":
        recipients[field] = {"state": "unknown"}
    else:
        recipients.pop(field)
    document["provider_observations"][0]["payment_presentation_evidence"][
        "recipient_digest_sha256"
    ] = material_record_digest(recipients, "recipient_settings")
    with pytest.raises((ValueError, ValidationError)):
        _check(document, finance)


def test_manual_delivery_completes_without_mutation_or_api_send_record(tmp_path: Path) -> None:
    document, finance, _binding = _manual_fixture(tmp_path)
    before = copy.deepcopy(document)
    result = _reconcile(document, finance)
    assert result["status"] == result["delivery_state"] == "manual-sent"
    assert result["delivery_route"] == MANUAL_ROUTE
    assert result["manual_delivery_reconciliation_ref"] == "reconciliation:manual"
    assert "action_call_refs" not in result
    assert document["mutation_attempts"] == []
    assert document == before
    assert _reconcile(document, finance) == result


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "missing-authorization",
        "missing-evidence",
        "wrong-invoice",
        "wrong-version",
        "wrong-recipients",
        "wrong-route",
        "late-authorization",
        "expired-authorization",
        "not-owner",
        "self-verified",
        "test-mode",
        "delivery-unknown",
        "not-send-approval",
        "wrong-snapshot",
        "future-proof",
        "revoked-authorization",
        "missing-original",
        "recipient-expiry",
    ],
)
def test_manual_delivery_keeps_uncertain_or_mismatched_outcomes_unresolved(
    tmp_path: Path, change: str
) -> None:
    document, finance, _binding = _manual_fixture(tmp_path)
    entry = document["reconciliations"][0]["manual_invoice_delivery"]
    approval = document["reviews"][0]
    observation = document["provider_observations"][1]
    proof = observation["invoice_delivery_evidence"]
    if change == "unknown":
        entry["outcome"] = "unknown"
    elif change == "missing-authorization":
        entry.pop("owner_authorization_ref")
    elif change == "missing-evidence":
        entry.pop("delivery_evidence_ref")
    elif change == "wrong-invoice":
        entry["invoice_ref"] = "fixture:another-invoice"
    elif change == "wrong-version":
        entry["billing_version"] = 2
    elif change == "wrong-recipients":
        approval["delivery_authorization"]["recipient_digest_sha256"] = "b" * 64
    elif change == "wrong-route":
        approval["delivery_authorization"]["delivery_route"] = API_ROUTE
    elif change == "late-authorization":
        approval["decided_at"] = CHECKED
    elif change == "expired-authorization":
        approval["delivery_authorization"]["expires_at"] = "2026-09-06T12:15:00Z"
    elif change == "not-owner":
        approval["authority"] = "operator"
    elif change == "self-verified":
        proof["verifier_ref"] = proof["sent_by_ref"]
    elif change == "test-mode":
        observation["livemode"] = False
    elif change == "delivery-unknown":
        proof["outcome"] = "unknown"
    elif change == "not-send-approval":
        approval["scope"] = ["owner-invoice-finalization"]
    elif change == "wrong-snapshot":
        proof["invoice_snapshot_sha256"] = "b" * 64
    elif change == "future-proof":
        proof["verified_at"] = EXPIRY
    elif change == "revoked-authorization":
        approval["status"] = approval["status_history"][-1]["to_status"] = "revoked"
    elif change == "missing-original":
        (finance / "attachments/2026/09/delivery.txt").unlink()
    elif change == "recipient-expiry":
        document["recipient_settings"][0]["validity"]["expires_at"] = "2026-09-06T12:15:00Z"
    with pytest.raises((ValueError, ValidationError, FileNotFoundError)):
        _reconcile(document, finance)
    assert document["mutation_attempts"] == []


def test_typed_evidence_and_authorization_cannot_be_rewritten(tmp_path: Path) -> None:
    document, _finance, _binding = _manual_fixture(tmp_path)
    for collection, field in (
        ("provider_observations", "payment_presentation_evidence"),
        ("reviews", "delivery_authorization"),
        ("reconciliations", "manual_invoice_delivery"),
    ):
        changed = copy.deepcopy(document)
        changed[collection][0][field]["invoice_snapshot_sha256"] = "b" * 64
        with pytest.raises(ValueError, match="immutable"):
            _validate_history(document, changed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "unresolved"),
        ("unmatched_refs", ["external:uncertain"]),
        (
            "differences",
            [
                {
                    "reason": "Unresolved synthetic difference",
                    "source_refs": ["source:delivery"],
                    "resolved": False,
                }
            ],
        ),
        ("account_refs", ["fixture:other-account"]),
        ("matched_refs", ["fixture:other-invoice"]),
        ("source_refs", []),
    ],
)
def test_manual_reconciliation_cannot_hide_parent_uncertainty(
    tmp_path: Path, field: str, value
) -> None:
    document, finance, _binding = _manual_fixture(tmp_path)
    reconciliation = document["reconciliations"][0]
    reconciliation[field] = value
    if field == "status":
        reconciliation["status_history"][-1]["to_status"] = value
    with pytest.raises((ValueError, ValidationError)):
        _reconcile(document, finance)


@pytest.mark.parametrize("state", ["draft", "unknown", "void", None])
def test_manual_delivery_rejects_contradictory_invoice_lifecycle(tmp_path: Path, state) -> None:
    document, finance, _binding = _manual_fixture(tmp_path)
    document["provider_observations"][1]["observed_status"] = state
    with pytest.raises((ValueError, ValidationError)):
        _reconcile(document, finance)


def test_dashboard_proof_cannot_be_relabelled_as_api_route(tmp_path: Path) -> None:
    document, finance, _binding = _fixture(tmp_path, MANUAL_ROUTE)
    with pytest.raises(ValueError, match="route mismatch"):
        _check(document, finance)


def test_snapshot_digest_binds_provider_material_and_complete_lines() -> None:
    invoice = {
        "invoice_ref": "fixture:invoice",
        "customer_ref": "fixture:customer",
        "currency": "usd",
        "subtotal": 1000,
        "total": 1000,
        "collection_method": "send_invoice",
        "auto_advance": False,
        "livemode": True,
        "invoice_customer_email_sha256": "a" * 64,
        "invoice_customer_name_sha256": "b" * 64,
        "footer_sha256": None,
        "payment_settings": {"payment_method_types": ["customer_balance"]},
        "status": "draft",
    }
    item = {
        "invoice_item_ref": "fixture:item",
        "invoice_ref": "fixture:invoice",
        "customer_ref": "fixture:customer",
        "amount": 1000,
        "currency": "usd",
        "description_sha256": "c" * 64,
        "quantity": 1,
    }
    original = invoice_snapshot_digest(invoice, [item], complete=True)
    for field, value in (
        ("footer_sha256", "d" * 64),
        ("total", 2000),
        ("invoice_customer_name_sha256", "e" * 64),
        ("payment_settings", {"payment_method_types": ["card"]}),
        ("description_sha256", "f" * 64),
        ("custom_fields", [{"name_sha256": "a" * 64, "value_sha256": "b" * 64}]),
        ("customer_tax_ids", [{"type": "eu_vat", "value_sha256": "c" * 64}]),
    ):
        assert invoice_snapshot_digest({**invoice, field: value}, [item], complete=True) != original
    assert invoice_snapshot_digest(invoice, [{**item, "amount": 2000}], complete=True) != original
    assert (
        invoice_snapshot_digest(
            {**invoice, "status": "open", "request_id": "ignored"}, [item], complete=True
        )
        == original
    )
    with pytest.raises(ValueError, match="complete"):
        invoice_snapshot_digest(invoice, [item], complete=False)
    with pytest.raises(ValueError, match="duplicate"):
        invoice_snapshot_digest(invoice, [item, item], complete=True)
