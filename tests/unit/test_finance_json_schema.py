"""Structural contracts for the external, host-owned finance JSON format.

This is not a StackOS persistence service. Cross-record identity, arithmetic,
immutable history and filesystem custody are host checks, not JSON Schema claims.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "plugins/finance/templates/finance-workspace"
SCHEMA_PATH = WORKSPACE / "schemas/finance-v1.schema.json"
TEMPLATE_PATH = WORKSPACE / "finance.json"
COLLECTIONS = {
    "operating_settings",
    "business_profiles",
    "sources",
    "attachments",
    "receipts",
    "bookkeeping",
    "customer_mappings",
    "recipient_settings",
    "billing_versions",
    "provider_observations",
    "mutation_attempts",
    "settlements",
    "collection_decisions",
    "reconciliations",
    "reviews",
    "corrections",
    "exceptions",
    "cashflow_forecasts",
    "reserve_applications",
    "tax_packets",
    "handoffs",
    "write_proofs",
    "custody_events",
}
AT = "2026-09-06T12:00:00Z"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _errors(document: dict) -> list:
    validator = Draft202012Validator(_schema(), format_checker=FormatChecker())
    return list(validator.iter_errors(document))


def _record(record_id: str, status: str = "prepared") -> dict:
    return {
        "record_id": record_id,
        "created_at": AT,
        "updated_at": AT,
        "status": status,
        "status_history": [
            {
                "at": AT,
                "actor": "fixture:operator",
                "to_status": status,
                "reason": "Synthetic structural example, not real financial data.",
                "evidence_refs": ["fixture:source"],
            }
        ],
        "provenance": [{"kind": "fixture", "ref": "fixture:source", "observed_at": AT}],
        "gaps": [],
    }


def _receipt() -> dict:
    return {
        **_record("receipt:fixture", "retained"),
        "source_ref": "source:fixture",
        "attachment_refs": ["attachment:fixture"],
        "business_date": "2026-09-05",
        "merchant": "Synthetic merchant",
        "amount": {"amount_minor": 1250, "currency": "EUR"},
    }


def _document(collection: str, record: dict) -> dict:
    document = _template()
    document[collection].append(record)
    document["revision"] = 1
    return document


def test_bootstrap_is_empty_single_financial_master_without_business_placeholders() -> None:
    document = _template()
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert document["schema_version"] == "local-json-v1"
    assert document["revision"] == 0
    assert document["workspace"] == {
        "backend_key": "local-json",
        "recording_mode": "prepared/unposted",
    }
    assert set(document) == COLLECTIONS | {"schema_version", "revision", "workspace"}
    assert all(document[key] == [] for key in COLLECTIONS)
    assert not _errors(document)


def test_receipt_structural_validation_does_not_require_tax_or_advisor_setup() -> None:
    document = _document("receipts", _receipt())
    assert document["business_profiles"] == []
    assert document["tax_packets"] == []
    assert document["operating_settings"] == []
    assert not _errors(document)


@pytest.mark.parametrize("currency", ["USD", "EUR", "JPY"])
def test_money_uses_explicit_currency_and_integer_minor_units(currency: str) -> None:
    receipt = _receipt()
    receipt["amount"]["currency"] = currency
    assert not _errors(_document("receipts", receipt))


@pytest.mark.parametrize(
    "bad_money",
    [
        {"amount_minor": 12.5, "currency": "USD"},
        {"amount_minor": "1250", "currency": "USD"},
        {"amount_minor": True, "currency": "USD"},
        {"amount_minor": 1250},
        {"amount_minor": 1250, "currency": "usd"},
        {"amount_minor": 1250, "currency": "US"},
        {"amount_minor": 1250, "currency": "USD", "rounded_dollars": 12.5},
        None,
    ],
)
def test_ambiguous_money_is_rejected(bad_money: dict | None) -> None:
    receipt = _receipt()
    receipt["amount"] = bad_money
    assert _errors(_document("receipts", receipt))


def test_unknown_receipt_amount_is_omitted_with_explicit_gap_not_zero_or_null() -> None:
    receipt = _receipt()
    del receipt["amount"]
    receipt["gaps"] = [
        {"field": "amount", "reason": "Total is unreadable.", "source_refs": ["source:fixture"]}
    ]
    assert not _errors(_document("receipts", receipt))
    receipt["gaps"] = []
    assert _errors(_document("receipts", receipt))


@pytest.mark.parametrize("field", ["record_id", "created_at", "status_history", "provenance"])
def test_populated_records_require_identity_observation_and_history(field: str) -> None:
    receipt = _receipt()
    del receipt[field]
    assert _errors(_document("receipts", receipt))


@pytest.mark.parametrize("field", ["status_history", "provenance"])
def test_record_provenance_and_history_cannot_be_empty(field: str) -> None:
    receipt = _receipt()
    receipt[field] = []
    assert _errors(_document("receipts", receipt))


def test_closed_records_reject_untyped_financial_bags_and_secret_fields() -> None:
    receipt = _receipt()
    receipt["data"] = {"amount": "12.50"}
    assert _errors(_document("receipts", receipt))
    del receipt["data"]
    receipt["api_key"] = "fixture-not-a-real-key"
    assert _errors(_document("receipts", receipt))
    document = _template()
    document["workspace"]["tax_rate"] = 0.3
    assert _errors(document)


def test_dates_and_timestamps_are_validated_with_format_checking() -> None:
    receipt = _receipt()
    receipt["business_date"] = "2026-02-31"
    assert _errors(_document("receipts", receipt))
    receipt = _receipt()
    receipt["created_at"] = "yesterday"
    assert _errors(_document("receipts", receipt))


def _attachment() -> dict:
    return {
        **_record("attachment:fixture", "retained"),
        "source_ref": "source:fixture",
        "role": "original",
        "relative_path": "attachments/2026/09/rcpt-fixture-original.pdf",
        "sha256": "a" * 64,
        "bytes": 125,
        "media_type": "application/pdf",
        "original_filename": "untrusted-receipt.pdf",
    }


def test_attachment_records_retain_original_custody_fields() -> None:
    assert not _errors(_document("attachments", _attachment()))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("relative_path", "/tmp/receipt.pdf"),
        ("relative_path", "attachments/2026/09/../../receipt.pdf"),
        ("relative_path", "attachments/2026/99/receipt.pdf"),
        ("relative_path", "attachments/2026/09/../receipt.pdf"),
        ("sha256", "not-a-sha256"),
        ("bytes", -1),
    ],
)
def test_structurally_unsafe_attachment_metadata_is_rejected(field: str, value: object) -> None:
    attachment = _attachment()
    attachment[field] = value
    assert _errors(_document("attachments", attachment))


def test_quarantine_has_an_explicit_protected_ref_not_a_claimed_retained_path() -> None:
    attachment = _attachment()
    del attachment["relative_path"]
    attachment["quarantine_ref"] = "quarantine:fixture"
    attachment["status"] = "quarantined"
    attachment["status_history"][0]["to_status"] = "quarantined"
    attachment["gaps"] = [
        {"field": "readability", "reason": "Encrypted original.", "source_refs": ["source:fixture"]}
    ]
    assert not _errors(_document("attachments", attachment))
    attachment["relative_path"] = "attachments/2026/09/receipt.pdf"
    assert _errors(_document("attachments", attachment))


def _billing_version() -> dict:
    return {
        **_record("billing:fixture:v1", "proposed"),
        "version": 1,
        "digest_sha256": "b" * 64,
        "customer_mapping_ref": "customer:fixture:v1",
        "recipient_settings_ref": "recipients:fixture:v1",
        "currency": "EUR",
        "terms": {"days_until_due": 14},
        "lines": [
            {
                "line_id": "line:fixture:1",
                "description": "Synthetic work",
                "description_sha256": "c" * 64,
                "amount": {"amount_minor": 1250, "currency": "EUR"},
                "source_refs": ["source:fixture"],
            }
        ],
        "subtotal": {"amount_minor": 1250, "currency": "EUR"},
        "total": {"amount_minor": 1250, "currency": "EUR"},
        "source_refs": ["source:fixture"],
    }


def test_billing_versions_bind_exact_structured_scope_for_host_immutability_checks() -> None:
    billing = _billing_version()
    assert not _errors(_document("billing_versions", billing))
    for field in ("version", "digest_sha256", "lines", "recipient_settings_ref"):
        broken = copy.deepcopy(billing)
        del broken[field]
        assert _errors(_document("billing_versions", broken)), field


def test_billing_digest_description_defines_exact_material_and_canonical_serialization() -> None:
    description = _schema()["$defs"]["billing_version"]["description"]
    assert (
        "exactly version, customer_mapping_ref, recipient_settings_ref, currency, terms, "
        "lines, subtotal, total, source_refs, and supersedes_ref only when present"
    ) in description
    assert "Preserve complete nested values and array order" in description
    assert (
        "ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False"
    ) in description
    assert "Do not normalize strings" in description
    assert (
        "Exclude record_id, digest_sha256, metadata, status/history, provenance/gaps"
    ) in description
    assert "Approval separately binds record_id, version and this digest" in description


def test_cashflow_scenarios_have_exactly_thirteen_typed_rows_with_explicit_unknowns() -> None:
    weeks = [
        {
            "week_number": index + 1,
            "week_start": "2026-09-07",
            "source_refs": ["source:fixture"],
            "gaps": [
                {
                    "field": "opening_cash",
                    "reason": "Opening cash not yet supplied.",
                    "source_refs": ["source:fixture"],
                }
            ],
        }
        for index in range(13)
    ]
    forecast = {
        **_record("forecast:fixture:v1", "forecast-incomplete"),
        "version": 1,
        "as_of": "2026-09-06",
        "week_1_start": "2026-09-07",
        "source_refs": ["source:fixture"],
        "assumptions": [],
        "scenarios": {"base": weeks, "downside": copy.deepcopy(weeks)},
    }
    assert not _errors(_document("cashflow_forecasts", forecast))
    forecast["scenarios"]["base"].pop()
    assert _errors(_document("cashflow_forecasts", forecast))


def test_incomplete_annual_tax_packet_is_valid_without_invented_liability_or_adoption() -> None:
    packet = {
        **_record("tax:fixture:v1", "incomplete"),
        "version": 1,
        "tax_year": 2026,
        "as_of": "2026-09-06",
        "profile_refs": [],
        "annual_inputs": {},
        "source_refs": [],
        "obligations": [],
        "review_state": {
            "control": "not-reviewed",
            "advisor": "not-reviewed",
            "owner": "not-adopted",
        },
        "gaps": [
            {"field": "tax_treatment", "reason": "Classification unverified.", "source_refs": []}
        ],
    }
    assert not _errors(_document("tax_packets", packet))
    packet["annual_inputs"]["projected_annual_income"] = None
    assert _errors(_document("tax_packets", packet))


def test_every_collection_has_a_typed_record_contract_not_an_arbitrary_object() -> None:
    schema = _schema()
    for collection in COLLECTIONS:
        item_ref = schema["properties"][collection]["items"]["$ref"]
        definition = schema["$defs"][item_ref.rsplit("/", 1)[-1]]
        assert definition.get("unevaluatedProperties") is False, collection
        assert _errors(_document(collection, _record("fixture:bare"))), collection


def test_review_can_bind_an_unversioned_receipt_by_id_and_digest() -> None:
    review = {
        **_record("review:receipt:fixture", "approved"),
        "target_ref": "receipt:fixture",
        "target_digest_sha256": "a" * 64,
        "scope": ["receipt-custody"],
        "authority": "control-reviewer",
        "verdict": "approved",
        "actor": "fixture:reviewer",
        "decided_at": AT,
    }
    assert not _errors(_document("reviews", review))
    del review["target_digest_sha256"]
    assert _errors(_document("reviews", review))


def test_handoff_can_bind_an_unversioned_source_by_id_and_digest() -> None:
    handoff = {
        **_record("handoff:receipt:fixture", "prepared"),
        "handoff_id": "handoff:receipt:fixture",
        "from_workflow": "finance.receipt-intake",
        "to_workflow": "finance.bookkeeping-close",
        "source_packet_ref": "receipt:fixture",
        "source_digest_sha256": "a" * 64,
        "conditions": ["Custody verified; categorization remains prepared/unposted."],
        "owner_ref": "fixture:operator",
    }
    assert not _errors(_document("handoffs", handoff))
    handoff["source_digest_sha256"] = "bad-digest"
    assert _errors(_document("handoffs", handoff))


def _settlement() -> dict:
    return {
        **_record("settlement:fixture:v1", "proposed"),
        "version": 1,
        "source_ref": "source:fixture",
        "source_identity": "fixture:received-payment",
        "source_sha256": "a" * 64,
        "provider_ref": "provider:fixture",
        "account_ref": "account:fixture",
        "customer_ref": "customer:fixture",
        "invoice_ref": "invoice:fixture",
        "received_amount": {"amount_minor": 500, "currency": "USD"},
        "received_at": AT,
        "selected_route": "attach-payment",
        "payment_ref": "payment:fixture",
        "allocation": {
            "allocation_id": "allocation:fixture",
            "invoice_ref": "invoice:fixture",
            "amount": {"amount_minor": 500, "currency": "USD"},
            "match_evidence_refs": ["source:fixture"],
        },
        "recovery": {"state": "not-needed"},
    }


def test_settlement_material_scope_has_an_explicit_proposal_version() -> None:
    settlement = _settlement()
    assert not _errors(_document("settlements", settlement))
    settlement["supersedes_ref"] = "settlement:fixture:v0"
    assert not _errors(_document("settlements", settlement))
    del settlement["version"]
    assert _errors(_document("settlements", settlement))


def test_authority_digest_descriptions_preserve_material_gaps_and_exclude_outcomes() -> None:
    definitions = _schema()["$defs"]
    for record_type in ("tax_packet", "collection_decision", "settlement"):
        description = definitions[record_type]["description"]
        assert "gaps, supersedes_ref" in description
        assert "SHA-256 over the UTF-8 JSON object" in description
        assert "Approval separately binds record_id and version" in description
    assert "Exclude review_state" in definitions["tax_packet"]["description"]
    assert "Exclude send_outcome" in definitions["collection_decision"]["description"]
    assert (
        "payment_ref only for selected_route=attach-payment"
        in (definitions["settlement"]["description"])
    )
    for record_type in ("review", "handoff"):
        assert (
            "full target record except created_at, updated_at, status, status_history, "
            "review_refs, write_proof_refs and handoff_refs"
        ) in definitions[record_type]["description"]
        assert "Include record_id, provenance, gaps" in definitions[record_type]["description"]
