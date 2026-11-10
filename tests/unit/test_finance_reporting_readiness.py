"""Read-only legacy readiness projections, not inferred financial recognition."""

import copy
import json

import pytest

from tests.helpers.finance_workspace import money, record
from tests.unit.test_finance_management import (
    QUERY,
    fixture_document,
    management,
    metadata,
    snapshot,
)


def test_legacy_readiness_is_full_population_without_adopting_metadata():
    module = management()
    document = fixture_document()
    document["operating_settings"] = [
        row for row in document["operating_settings"] if row["scope"] == "account"
    ]
    document["receipts"].append(
        record(
            "receipt:legacy",
            "prepared",
            merchant="Legacy merchant",
            source_ref="source:bank",
            amount=money(1200),
            attachment_refs=[],
        )
    )
    before = copy.deepcopy(document)
    projected = module.query(snapshot(document), {**QUERY, "limit": 1})
    readiness = projected["reporting_readiness"]
    assert len(projected["rows"]) == 1
    assert readiness["selected_record_count"] == 9
    assert readiness["counts_by_collection"] == {"bookkeeping": 8, "receipts": 1}
    assert readiness["bookkeeping_count"] == readiness["missing_metadata_count"] == 8
    assert readiness["supported_bookkeeping_count"] == 0
    assert readiness["excluded_bookkeeping_count"] == 8
    assert readiness["missing_precision_count"] == 9
    assert readiness["undated_count"] == 1
    assert readiness["state"] == "needs_review"
    assert readiness["coverage_issues"] == ["missing_or_conflicting_reporting_policy"]
    assert projected["record_labels"]["receipt:legacy"] == "Legacy merchant · Date not recorded"
    assert all(metric["spend"]["amount"] is None for metric in projected["metrics_by_currency"])
    assert all(metric["revenue"]["amount"] is None for metric in projected["metrics_by_currency"])
    assert document == before


def test_readiness_and_labels_follow_selection_without_leaking_other_records():
    module = management()
    document = fixture_document()
    document["receipts"].append(
        record(
            "receipt:outside",
            "prepared",
            merchant="Hidden merchant",
            business_date="2025-01-01",
            source_ref="source:bank",
            amount=money(1200),
            attachment_refs=[],
        )
    )
    projected = module.query(snapshot(document), {**QUERY, "kind": "income", "limit": 1})
    assert projected["reporting_readiness"]["selected_record_count"] == 2
    assert projected["reporting_readiness"]["supported_bookkeeping_count"] == 2
    assert projected["reporting_readiness"]["excluded_bookkeeping_count"] == 0
    assert projected["reporting_readiness"]["missing_metadata_count"] == 0
    assert projected["reporting_readiness"]["missing_precision_count"] == 0
    assert set(projected["record_labels"]) == {"book:revenue", "book:refund"}
    assert "Hidden merchant" not in json.dumps(projected)
    empty = module.query(snapshot(document), {**QUERY, "q": "no-such-record"})
    assert empty["reporting_readiness"]["state"] == "empty"
    assert empty["record_labels"] == {}


def test_readiness_preserves_approved_metrics():
    projected = management().query(snapshot(fixture_document()), QUERY)
    assert projected["reporting_readiness"]["supported_bookkeeping_count"] == 8
    assert projected["reporting_readiness"]["excluded_bookkeeping_count"] == 0
    assert projected["reporting_readiness"]["missing_precision_count"] == 0
    assert projected["metrics_by_currency"][0]["spend"]["amount"]["minor"] == "11000"
    assert projected["metrics_by_currency"][0]["revenue"]["amount"]["minor"] == "95000"


@pytest.mark.parametrize("issue", ["stale_precision", "precision_conflict", "unapproved_precision"])
def test_display_convention_never_overrides_precision_issue(monkeypatch, issue):
    module = management()
    monkeypatch.setattr(module, "_precision", lambda *args: (None, issue))
    amount, actual_issue = module._display_amount(None, {}, {"amount": money(1234)}, None)
    assert actual_issue == issue
    assert amount == {"minor": "1234", "currency": "USD", "exponent": None}


def test_display_convention_only_for_unreviewed_usd(monkeypatch):
    module = management()
    monkeypatch.setattr(module, "_precision", lambda *args: (None, None))
    amount, issue = module._display_amount(None, {}, {"amount": money(1234)}, None)
    assert issue is None
    assert amount["exponent"] == 2
    assert amount["precision_basis"] == "currency-convention"
    assert "not been reviewed" in amount["precision_warning"]
    foreign, _ = module._display_amount(None, {}, {"amount": money(1234, "JPY")}, None)
    assert foreign["exponent"] is None
    assert "precision_basis" not in foreign
    monkeypatch.setattr(module, "_precision", lambda *args: (3, None))
    reviewed, _ = module._display_amount(None, {}, {"amount": money(1234)}, None)
    assert reviewed == {"minor": "1234", "currency": "USD", "exponent": 3}


def test_undated_label_uses_safe_original_filename_without_inferred_type():
    row = {
        "counterparty": "MongoDB",
        "business_purpose": "",
        "collection": "receipts",
        "date": None,
        "documents": [
            {"document_type": "unidentified", "filename": "/private/invoices/invoice-2026-09.pdf"},
            {"document_type": "receipt", "filename": "not-an-invoice.pdf"},
        ],
    }
    assert management()._record_label(row) == "MongoDB · invoice-2026-09.pdf · Date not recorded"


def test_display_fallback_keeps_dated_receipt_in_review_filter():
    document = fixture_document()
    document["receipts"].append(
        record(
            "receipt:dated",
            "prepared",
            merchant="MongoDB",
            business_date="2026-09-04",
            source_ref="source:bank",
            amount=money(1200),
            attachment_refs=[],
        )
    )
    result = management().query(
        snapshot(document), {**QUERY, "kind": "receipt", "needs_review": True}
    )
    assert [row["record_ref"] for row in result["rows"]] == ["receipt:dated"]
    assert result["reporting_readiness"]["missing_precision_count"] == 1


def test_linked_receipt_merchant_requires_unanimous_active_labels():
    module = management()
    document = fixture_document()
    for index, merchant in enumerate(["MongoDB", "MongoDB"]):
        document["receipts"].append(
            record(
                f"receipt:merchant{index}",
                "prepared",
                merchant=merchant,
                source_ref="source:bank",
                amount=money(1200),
                attachment_refs=[],
            )
        )
    bookkeeping = {"receipt_refs": ["receipt:merchant0", "receipt:merchant1"]}
    assert module._receipt_merchant(module.CurrentRecords(document), bookkeeping) == "MongoDB"
    document["receipts"][-1]["merchant"] = "Other merchant"
    assert module._receipt_merchant(module.CurrentRecords(document), bookkeeping) == ""
    document["receipts"][-1]["merchant"] = ""
    assert module._receipt_merchant(module.CurrentRecords(document), bookkeeping) == ""


def test_reporting_defaults_use_only_unanimous_valid_metadata_without_changing_query():
    document = fixture_document()
    document["operating_settings"] = []
    document["reviews"] = []
    assert management().query(snapshot(document), QUERY)["reporting_defaults"] is None
    first = document["bookkeeping"][0]
    metadata(document, first, recognition_basis="accrual", date_basis="issue")
    result = management().query(snapshot(document), QUERY)
    assert result["reporting_defaults"] == {"recognition_basis": "accrual", "date_basis": "issue"}
    assert result["query"]["recognition_basis"] == "cash"
    assert result["query"]["date_basis"] == "transaction"
    assert result["reporting_readiness"]["supported_bookkeeping_count"] == 0
    metadata(document, document["bookkeeping"][1])
    assert management().query(snapshot(document), QUERY)["reporting_defaults"] is None
    document["bookkeeping"][1]["date"] = "2026-09-04"  # stale second metadata cannot set defaults
    assert (
        management().query(snapshot(document), QUERY)["reporting_defaults"]["date_basis"] == "issue"
    )
    document["operating_settings"][0]["status"] = "proposed"
    assert management().query(snapshot(document), QUERY)["reporting_defaults"] is None
