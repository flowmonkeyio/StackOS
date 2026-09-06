"""Fixture-only repository tests for the curated Stripe finance transport."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from jsonschema import Draft7Validator
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.stripe import STRIPE_ACTION_SPECS, STRIPE_OPERATION, StripeActionConnector
from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    Credential,
    CredentialUsageEvent,
    ProviderObjectReference,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from stackos.repositories.secrets import PayloadSecretRepository
from stackos.secret_refs import SECRET_REF_SENTINEL
from tests.helpers.stripe import (
    stripe_balance_transaction,
    stripe_charge,
    stripe_customer,
    stripe_dispute,
    stripe_invoice,
    stripe_invoice_item,
    stripe_refund,
)

STRIPE_ROOT = "https://api.stripe.com/v1"
STRIPE_SECRET = "sk_test_stripe_connector_sentinel"
CORRELATION_TOKEN = "0123456789abcdef0123456789abcdef"


def test_stripe_named_fixtures_match_pinned_schema_fragment() -> None:
    """Independent pinned schema evidence, not runtime-derived expected fields."""
    fragment = json.loads(
        (Path(__file__).parents[2] / "helpers" / "stripe_selected_schema.json").read_text()
    )
    assert fragment["api_version"] == "2026-08-26.dahlia"
    for name, builder in {
        "customer": stripe_customer,
        "invoice": stripe_invoice,
        "invoiceitem": stripe_invoice_item,
        "charge": stripe_charge,
        "balance_transaction": stripe_balance_transaction,
        "refund": stripe_refund,
        "dispute": stripe_dispute,
    }.items():
        Draft7Validator(fragment["schemas"][name]).validate(builder())


# Selected operationally critical required properties, independently reviewed
# against the pinned public OpenAPI schema named in tests.helpers.stripe.
# amount_paid_off_stripe remains optional pending the documented expansion
# discrepancy. Nullable/optional lifecycle values are tested separately.
_REQUIRED_OBSERVATIONS = [
    (
        stripe_customer,
        "customers.retrieve",
        "customer_ref",
        "stripe.customer",
        ("created", "livemode"),
    ),
    (
        stripe_invoice,
        "invoices.retrieve",
        "invoice_ref",
        "stripe.invoice",
        (
            "amount_due",
            "amount_paid",
            "amount_remaining",
            "total",
            "subtotal",
            "amount_overpaid",
            "currency",
            "collection_method",
            "auto_advance",
            "created",
            "livemode",
            "customer",
        ),
    ),
    (
        stripe_invoice_item,
        "invoice-items.list",
        "invoice_ref",
        "stripe.invoice",
        ("amount", "currency", "date", "proration", "livemode", "customer"),
    ),
    (
        stripe_charge,
        "charges.retrieve",
        "charge_ref",
        "stripe.charge",
        (
            "amount",
            "amount_captured",
            "amount_refunded",
            "currency",
            "paid",
            "refunded",
            "livemode",
            "status",
            "created",
        ),
    ),
    (
        stripe_balance_transaction,
        "balance-transactions.retrieve",
        "balance_transaction_ref",
        "stripe.balance-transaction",
        (
            "amount",
            "fee",
            "net",
            "currency",
            "type",
            "reporting_category",
            "status",
            "available_on",
            "created",
            "balance_type",
        ),
    ),
    (
        stripe_refund,
        "refunds.retrieve",
        "refund_ref",
        "stripe.refund",
        ("amount", "currency", "created"),
    ),
    (
        stripe_dispute,
        "disputes.retrieve",
        "dispute_ref",
        "stripe.dispute",
        (
            "amount",
            "currency",
            "status",
            "reason",
            "created",
            "livemode",
            "charge",
            "balance_transactions",
        ),
    ),
]


@pytest.mark.parametrize(
    ("builder", "suffix", "input_key", "object_type", "missing"),
    [
        (builder, suffix, input_key, object_type, field)
        for builder, suffix, input_key, object_type, fields in _REQUIRED_OBSERVATIONS
        for field in fields
    ],
)
def test_stripe_pinned_required_observation_missing_is_failed_action(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    builder,
    suffix: str,
    input_key: str,
    object_type: str,
    missing: str,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    body = builder()
    del body[missing]
    safe_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type=object_type,
        provider_id=body["id"],
    )
    response = (
        {"object": "list", "has_more": False, "data": [body]} if suffix.endswith(".list") else body
    )
    httpx_mock.add_response(method="GET", json=response)
    with pytest.raises(ConflictError) as failed:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref=f"finance.stripe.{suffix}",
            input_json={input_key: safe_ref},
        )
    assert failed.value.data["provider_error"]["reason_code"] == "malformed_response"
    call = session.exec(select(ActionCall).order_by(ActionCall.id.desc())).first()
    assert call is not None and call.status == ActionCallStatus.FAILED
    assert call.response_json["provider_error"]["reason_code"] == "malformed_response"


@pytest.mark.parametrize("selected", ["charge", "payment_intent", "payment_record"])
@pytest.mark.parametrize("null_link", [False, True])
def test_stripe_invoice_payment_unavailable_link_preserves_page(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    selected: str,
    null_link: bool,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_link_gap",
    )
    allocation = {
        "id": "inpay_link_gap",
        "object": "invoice_payment",
        "amount_requested": 100,
        "amount_paid": None,
        "created": 1700000000,
        "currency": "usd",
        "invoice": "in_link_gap",
        "is_default": False,
        "livemode": False,
        "payment": {"type": selected, **({selected: None} if null_link else {})},
        "status": "open",
        "status_transitions": {},
    }
    httpx_mock.add_response(
        method="GET", json={"object": "list", "has_more": True, "data": [allocation]}
    )
    page = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoice-payments.list",
        input_json={"invoice_ref": invoice_ref},
    ).output_json["data"]
    item = page["items"][0]
    assert item["payment_type"] == selected
    assert item["payment_ref_state"] == ("null" if null_link else "missing")
    assert item["amount_requested"] == 100 and item["amount_paid"] is None
    assert page["next_page_cursor"] == item["invoice_payment_ref"]
    if null_link:
        assert item[f"{selected}_ref"] is None
    else:
        assert f"{selected}_ref" not in item


def test_stripe_reconciliation_source_and_adjustment_links_are_safe(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    transaction = stripe_balance_transaction(source=stripe_charge(id="ch_source"))
    httpx_mock.add_response(
        method="GET", json={"object": "list", "has_more": False, "data": [transaction]}
    )
    row = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.balance-transactions.list",
        input_json={},
    ).output_json["data"]["items"][0]
    assert dict(httpx_mock.get_requests()[-1].url.params) == {
        "limit": "25",
        "expand[]": "data.source",
    }
    assert row["balance_type"] == "payments" and row["source_type"] == "charge"
    assert row["source_state"] == "available" and row["source_ref"].startswith("provider-object:")
    assert "ch_source" not in json.dumps(row)
    refund_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.refund",
        provider_id="re_failed",
    )
    httpx_mock.add_response(
        method="GET",
        json=stripe_refund(
            id="re_failed", status="failed", failure_balance_transaction="txn_reversal"
        ),
    )
    refund = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.refunds.retrieve",
        input_json={"refund_ref": refund_ref},
    ).output_json["data"]
    assert refund["failure_balance_transaction_ref"].startswith("provider-object:")
    dispute_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.dispute",
        provider_id="dp_links",
    )
    httpx_mock.add_response(
        method="GET",
        json=stripe_dispute(
            id="dp_links", balance_transactions=[stripe_balance_transaction(id="txn_reversal")]
        ),
    )
    dispute = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.disputes.retrieve",
        input_json={"dispute_ref": dispute_ref},
    ).output_json["data"]
    assert dispute["balance_transaction_refs"] == [refund["failure_balance_transaction_ref"]]


@pytest.mark.parametrize(
    ("suffix", "builder", "field", "bad"),
    [
        ("invoices", stripe_invoice, "status", "PRIVATE-UNKNOWN"),
        ("charges", stripe_charge, "status", "PRIVATE-UNKNOWN"),
        ("balance-transactions", stripe_balance_transaction, "type", "PRIVATE-UNKNOWN"),
        ("balance-transactions", stripe_balance_transaction, "status", "PRIVATE-UNKNOWN"),
        ("balance-transactions", stripe_balance_transaction, "balance_type", "PRIVATE-UNKNOWN"),
        ("refunds", stripe_refund, "status", "PRIVATE-UNKNOWN"),
        ("refunds", stripe_refund, "reason", "PRIVATE-UNKNOWN"),
        ("disputes", stripe_dispute, "status", "PRIVATE-UNKNOWN"),
    ],
)
def test_stripe_reviewed_lifecycle_values_reject_unknown_enum(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    suffix: str,
    builder,
    field: str,
    bad: str,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    body = builder(**{field: bad})
    kind = {
        "invoices": "invoice",
        "charges": "charge",
        "balance-transactions": "balance-transaction",
        "refunds": "refund",
        "disputes": "dispute",
    }[suffix]
    key = kind.replace("-", "_") + "_ref"
    safe_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type=f"stripe.{kind}",
        provider_id=body["id"],
    )
    httpx_mock.add_response(method="GET", json=body)
    with pytest.raises(ConflictError) as failed:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref=f"finance.stripe.{suffix}.retrieve",
            input_json={key: safe_ref},
        )
    assert failed.value.data["provider_error"]["reason_code"] == "malformed_response"
    assert "PRIVATE-UNKNOWN" not in json.dumps(failed.value.data)


@pytest.mark.parametrize("field", ["reported_by", "processor_type"])
def test_stripe_payment_record_rejects_unknown_selected_enum(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    field: str,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    record_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-record",
        provider_id="pr_recovery_list",
    )
    body = _payment_record_list_fixture()
    if field == "reported_by":
        body[field] = "PRIVATE-UNKNOWN"
    else:
        body["processor_details"]["type"] = "PRIVATE-UNKNOWN"
    httpx_mock.add_response(method="GET", json=body)
    with pytest.raises(ConflictError) as failed:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.payment-records.retrieve",
            input_json={"payment_record_ref": record_ref},
        )
    assert failed.value.data["provider_error"]["reason_code"] == "malformed_response"


def test_stripe_payment_record_customer_is_not_an_expandable_field(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    record_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-record",
        provider_id="pr_recovery_list",
    )
    body = _payment_record_list_fixture()
    body["customer_details"]["customer"] = stripe_customer()
    httpx_mock.add_response(method="GET", json=body)
    with pytest.raises(ConflictError) as failed:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.payment-records.retrieve",
            input_json={"payment_record_ref": record_ref},
        )
    assert failed.value.data["provider_error"]["reason_code"] == "malformed_response"


@pytest.mark.parametrize(
    ("source", "state"),
    [
        (None, "null"),
        ("ch_unexpanded", "unexpanded"),
        ({"id": "po_hidden", "object": "payout", "description": "PRIVATE SOURCE"}, "unsupported"),
        (stripe_refund(), "available"),
        (stripe_dispute(), "available"),
    ],
)
def test_stripe_balance_source_states_never_invent_linkage(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    source: object,
    state: str,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    httpx_mock.add_response(
        method="GET",
        json={
            "object": "list",
            "has_more": True,
            "data": [stripe_balance_transaction(source=source)],
        },
    )
    page = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.balance-transactions.list",
        input_json={},
    ).output_json["data"]
    row = page["items"][0]
    assert row["source_state"] == state
    assert page["next_page_cursor"] == row["balance_transaction_ref"]
    if state == "available":
        assert row["source_ref"].startswith("provider-object:")
    else:
        assert "source_ref" not in row and "source_type" not in row
    assert "PRIVATE" not in json.dumps(page) and "po_hidden" not in json.dumps(page)


@pytest.mark.parametrize(
    "transactions", [None, ["txn_raw"], [{"id": "txn_bad", "object": "refund"}]]
)
def test_stripe_dispute_rejects_malformed_balance_transaction_array(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    transactions: object,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    dispute_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.dispute",
        provider_id="dp_fixture",
    )
    httpx_mock.add_response(method="GET", json=stripe_dispute(balance_transactions=transactions))
    with pytest.raises(ConflictError) as failed:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.disputes.retrieve",
            input_json={"dispute_ref": dispute_ref},
        )
    assert failed.value.data["provider_error"]["reason_code"] == "malformed_response"


def _payment_record_list_fixture(
    identifier: str = "pr_recovery_list", payment_reference: str | None = " bank-e\u0301-001 "
) -> dict:
    money = {"value": 400, "currency": "usd"}
    zero = {"value": 0, "currency": "usd"}
    return {
        "id": identifier,
        "object": "payment_record",
        "amount": money,
        "amount_authorized": money,
        "amount_requested": money,
        "amount_guaranteed": money,
        "amount_canceled": zero,
        "amount_failed": zero,
        "amount_refunded": zero,
        "created": 1788476460,
        "livemode": False,
        "reported_by": "self",
        "customer_details": {
            "customer": "cus_recovery_list",
            "email": "private-list@example.test",
            "name": "PRIVATE LIST CUSTOMER",
        },
        "processor_details": {"type": "custom", "custom": {"payment_reference": payment_reference}},
        "description": "PRIVATE PAYMENT DESCRIPTION",
        "metadata": {"private": "PRIVATE LIST METADATA"},
        "payment_method_details": {"type": "custom", "custom": {"display_name": "PRIVATE METHOD"}},
    }


def test_stripe_payment_record_known_ref_recovery_preserves_safe_evidence(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    """A ref recovered from retained evidence permits retrieval, never a replacement report."""
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    record_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-record",
        provider_id="pr_recovery_list",
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_records/pr_recovery_list",
        json=_payment_record_list_fixture(),
    )
    retrieved = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.payment-records.retrieve",
        input_json={"payment_record_ref": record_ref},
        idempotency_key="read-key-is-not-sent-to-stripe",
    )
    record = retrieved.output_json["data"]
    assert record["payment_record_ref"] == record_ref
    assert record["customer_ref"].startswith("provider-object:")
    assert record["amount"] == record["amount_guaranteed"] == {"value": 400, "currency": "usd"}
    assert (
        record["payment_reference_sha256"]
        == hashlib.sha256(" bank-e\u0301-001 ".encode()).hexdigest()
    )
    requests = httpx_mock.get_requests()[1:]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].headers["Stripe-Version"] == "2026-08-26.dahlia"
    assert "Idempotency-Key" not in requests[0].headers
    assert requests[0].content == b""
    calls = session.exec(select(ActionCall)).all()
    assert len(calls) == 1
    assert calls[0].action_key == "stripe.payment-records.retrieve"
    serialized = json.dumps(
        {
            "output": retrieved.model_dump(mode="json"),
            "audit": [call.response_json for call in calls],
            "reference_names": [
                ref.display_name for ref in session.exec(select(ProviderObjectReference)).all()
            ],
        }
    )
    for private in (
        STRIPE_SECRET,
        "pr_recovery_list",
        "cus_recovery_list",
        " bank-e\u0301-001 ",
        "private-list@example.test",
        "PRIVATE LIST CUSTOMER",
        "PRIVATE PAYMENT DESCRIPTION",
        "PRIVATE LIST METADATA",
        "PRIVATE METHOD",
    ):
        assert private not in serialized


@pytest.mark.parametrize("payment_reference", [None, ""])
def test_stripe_payment_record_retrieve_preserves_missing_or_empty_reference(
    session: Session, project_id: int, httpx_mock: HTTPXMock, payment_reference: str | None
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    record_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-record",
        provider_id="pr_recovery_list",
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_records/pr_recovery_list",
        json=_payment_record_list_fixture(payment_reference=payment_reference),
    )
    result = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.payment-records.retrieve",
        input_json={"payment_record_ref": record_ref},
    )
    digest = result.output_json["data"]["payment_reference_sha256"]
    assert digest == (hashlib.sha256(b"").hexdigest() if payment_reference == "" else None)


@pytest.mark.parametrize("wrong_binding", ["account", "type"])
def test_stripe_payment_record_retrieve_rejects_wrong_ref_before_http(
    session: Session, project_id: int, httpx_mock: HTTPXMock, wrong_binding: str
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    cursor = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-record",
        provider_id="pr_other_binding",
    )
    row = session.exec(
        select(ProviderObjectReference).where(ProviderObjectReference.safe_ref == cursor)
    ).one()
    if wrong_binding == "account":
        row.provider_account_id = "acct_other"
    else:
        row.object_type = "stripe.payment-intent"
    session.add(row)
    session.commit()
    with pytest.raises(ConflictError):
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.payment-records.retrieve",
            input_json={"payment_record_ref": cursor},
        )
    assert len(httpx_mock.get_requests()) == 1  # Only the isolated fixture account probe.


@pytest.mark.parametrize(
    "payload",
    [
        {"customer_ref": "provider-object:customer"},
        {"created_after": 1},
        {"created_before": 2},
        {"expand": ["customer"]},
        {"metadata": {"key": "value"}},
        {"payment_reference": "bank-001"},
        {"starting_after": "pr_raw"},
        {"ending_before": "pr_raw"},
        {"page_cursor": "pr_raw"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
    ],
)
def test_stripe_payment_record_list_stays_deferred_for_unsupported_inputs(
    session: Session, project_id: int, payload: dict
) -> None:
    result = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="finance.stripe.payment-records.list",
        input_json=payload,
    )
    assert result.valid is False
    issue = next(issue for issue in result.issues if issue.code == "execution_deferred")
    assert "temporarily unavailable in StackOS" in issue.message
    assert "never create a replacement report" in issue.message
    assert session.exec(select(ActionCall)).all() == []


@pytest.mark.parametrize(
    "malformation",
    [
        "object",
        "data",
        "item",
        "has_more",
        "empty_more",
        "amount",
        "reference",
    ],
)
def test_stripe_enabled_reads_reject_malformed_response_without_empty_success(
    session: Session, project_id: int, httpx_mock: HTTPXMock, malformation: str
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    if malformation in {"amount", "reference"}:
        record_ref = _safe_ref(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            object_type="stripe.payment-record",
            provider_id="pr_recovery_list",
        )
        item = _payment_record_list_fixture()
        body = item
        action_ref = "finance.stripe.payment-records.retrieve"
        payload = {"payment_record_ref": record_ref}
        url = f"{STRIPE_ROOT}/payment_records/pr_recovery_list"
    else:
        item = stripe_invoice()
        body = {"object": "list", "data": [item], "has_more": False}
        action_ref = "finance.stripe.invoices.list"
        payload = {}
        url = f"{STRIPE_ROOT}/invoices?limit=25"
    if malformation == "object":
        body["object"] = "payment_record"
    elif malformation == "data":
        body["data"] = None
    elif malformation == "item":
        body["data"] = ["invalid"]
    elif malformation == "has_more":
        body["has_more"] = "false"
    elif malformation == "empty_more":
        body.update(data=[], has_more=True)
    elif malformation == "amount":
        item["amount_guaranteed"] = {"value": True, "currency": "usd"}
    else:
        item["processor_details"]["custom"]["payment_reference"] = {"private": "bad"}
    httpx_mock.add_response(method="GET", url=url, json=body)
    with pytest.raises(ConflictError) as failure:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref=action_ref,
            input_json=payload,
        )
    assert failure.value.data["provider_error"]["reason_code"] == "malformed_response"
    assert failure.value.data["provider_error"]["outcome_unknown"] is False
    call = session.exec(
        select(ActionCall).where(ActionCall.status == ActionCallStatus.FAILED)
    ).one()
    assert call.response_json["provider_error"]["reason_code"] == "malformed_response"
    assert "PRIVATE LIST" not in json.dumps(call.response_json)


@pytest.mark.parametrize("status_code", [403, 429, 500])
def test_stripe_invoice_list_keeps_safe_provider_errors(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    monkeypatch.setattr("stackos.integrations._base.asyncio.sleep", AsyncMock())
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices?limit=25",
        status_code=status_code,
        headers={"Request-Id": "req_list_failure", "Retry-After": "3"},
        json={"error": {"type": "api_error", "message": f"PRIVATE ERROR {STRIPE_SECRET}"}},
        is_reusable=True,
    )
    with pytest.raises(ConflictError) as failure:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.invoices.list",
            input_json={},
        )
    assert failure.value.data["provider_status_code"] == status_code
    assert failure.value.data["provider_error"]["request_id"] == "req_list_failure"
    assert failure.value.data["provider_error"]["outcome_unknown"] is False
    serialized = json.dumps(failure.value.data)
    assert "PRIVATE ERROR" not in serialized and STRIPE_SECRET not in serialized
    call = session.exec(
        select(ActionCall).where(ActionCall.status == ActionCallStatus.FAILED)
    ).one()
    assert call.response_json["provider_status_code"] == status_code
    assert "PRIVATE ERROR" not in json.dumps(call.response_json)
    assert len(httpx_mock.get_requests()) == (2 if status_code == 403 else 5)


def test_stripe_unknown_endpoint_preserves_diagnostic_and_log_in_error_and_audit(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    log_url = (
        "https://dashboard.stripe.com/acct_Fixture/test/workbench/logs?object=req_route_fixture"
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices?limit=1",
        status_code=404,
        headers={"Request-Id": "req_route_fixture"},
        json={
            "error": {
                "type": "invalid_request_error",
                "message": "Unrecognized request URL (GET: /v1/invoices). Please see "
                "https://stripe.com/docs or we can help at https://support.stripe.com/.",
                "request_log_url": log_url,
            }
        },
    )
    with pytest.raises(ConflictError) as failure:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.invoices.list",
            input_json={"limit": 1},
        )
    error = failure.value.data["provider_error"]
    assert error["reason_code"] == "endpoint_not_recognized"
    assert error["message"] == "Unrecognized request URL (GET: /v1/invoices)."
    assert error["message_redacted"] is True
    assert error["request_log_url"] == log_url
    assert error["request_id"] == "req_route_fixture"
    assert error["outcome_unknown"] is False
    assert error["retry_safe"] is True
    assert "endpoint" in error["next_action"]
    call = session.exec(
        select(ActionCall).where(ActionCall.status == ActionCallStatus.FAILED)
    ).one()
    assert call.response_json["provider_status_code"] == 404
    assert call.response_json["provider_error"] == error
    assert STRIPE_SECRET not in json.dumps(
        {"error": failure.value.data, "audit": call.response_json}
    )
    assert len(httpx_mock.get_requests()) == 2  # Credential probe plus exactly one failed GET.


@pytest.mark.parametrize(
    "log_url",
    [
        "https://dashboard.stripe.com/claim/private-claim-token",
        "https://dashboard.stripe.com.evil.test/test/workbench/logs?object=req_safe",
        "https://evil.test@dashboard.stripe.com/test/workbench/logs?object=req_safe",
        "http://dashboard.stripe.com/test/workbench/logs?object=req_safe",
        "https://dashboard.stripe.com:443/test/workbench/logs?object=req_safe",
        "https://dashboard.stripe.com/test/workbench/logs?object=req_other",
        "https://dashboard.stripe.com/test/workbench/logs?object=req_safe&token=private",
        "https://dashboard.stripe.com/test/workbench/logs?object=req_safe#private",
        "https://dashboard.stripe.com/test/workbench/logs?object=%72eq_safe",
        "https://dashboard.stripe.com/acct_sk_test_private/test/workbench/logs?object=req_safe",
    ],
)
def test_stripe_error_rejects_unsafe_or_mismatched_log_links(log_url: str) -> None:
    from stackos.integrations.stripe import StripeIntegration, _safe_provider_error

    response = httpx.Response(
        404,
        headers={"Request-Id": "req_safe"},
        json={
            "error": {
                "type": "invalid_request_error",
                "message": "Unrecognized request URL (GET: /v1/customers/cus_private). "
                f"{STRIPE_SECRET} https://dashboard.stripe.com/claim/private-claim-token",
                "request_log_url": log_url,
            }
        },
        request=httpx.Request("GET", f"{STRIPE_ROOT}/payment_records?limit=1"),
    )
    error = StripeIntegration._provider_error(response)
    assert error["message"] == "Unrecognized request URL (GET: /v1/payment_records)."
    assert error["message_redacted"] is True
    assert "request_log_url" not in error
    assert _safe_provider_error(error) == error
    assert "cus_private" not in json.dumps(error)
    assert STRIPE_SECRET not in json.dumps(error)


def test_stripe_private_error_keeps_correlated_log_but_withholds_message() -> None:
    from stackos.integrations.stripe import StripeIntegration, _safe_provider_error

    log_url = "https://dashboard.stripe.com/test/workbench/logs?object=req_safe"
    response = httpx.Response(
        400,
        headers={"Request-Id": "req_safe"},
        json={
            "error": {
                "message": "PRIVATE CUSTOMER fixture@example.test cus_private bank-reference",
                "request_log_url": log_url,
            }
        },
        request=httpx.Request("GET", f"{STRIPE_ROOT}/customers/cus_private"),
    )
    error = StripeIntegration._provider_error(response)
    assert "message" not in error
    assert error["message_redacted"] is True
    assert error["request_log_url"] == log_url
    assert _safe_provider_error(error) == error
    assert "PRIVATE CUSTOMER" not in json.dumps(error)
    for request_id in (None, "req_other", STRIPE_SECRET):
        assert "request_log_url" not in _safe_provider_error(response.json(), request_id=request_id)


def test_stripe_invoice_read_distinguishes_frozen_and_current_recipient_and_changed_due_date(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_recipient",
    )
    approved_email = "approved@example.test"
    approved_hash = hashlib.sha256(approved_email.encode("utf-8")).hexdigest()
    invoice = {
        **stripe_invoice(),
        "id": "in_recipient",
        "object": "invoice",
        "status": "draft",
        "collection_method": "send_invoice",
        "due_date": 2000000000,
        "customer_email": approved_email,
        "customer": {
            **stripe_customer(),
            "id": "cus_recipient",
            "object": "customer",
            "email": approved_email,
            "name": "PRIVATE CUSTOMER",
            "metadata": {"private": "PRIVATE MEMO"},
        },
    }
    httpx_mock.add_response(method="GET", json=invoice)
    read = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoices.retrieve",
        input_json={"invoice_ref": invoice_ref},
    )
    original = read.output_json["data"]
    assert original["invoice_customer_email_sha256"] == approved_hash
    assert original["current_customer_email_sha256"] == approved_hash
    assert original["recipient_scope"] == "primary-email-fields-only"
    assert original["due_date"] == 2000000000
    assert dict(httpx_mock.get_requests()[-1].url.params) == {"expand[]": "customer"}
    assert approved_email not in json.dumps(read.model_dump(mode="json"))
    assert "PRIVATE" not in json.dumps(read.model_dump(mode="json"))
    # The finalized invoice snapshot stays unchanged while the actual customer
    # primary email and invoice due date change outside this agent occurrence.
    changed = {
        **invoice,
        "status": "open",
        "due_date": 2000086400,
        "customer": {**invoice["customer"], "email": "changed@example.test"},
    }
    httpx_mock.add_response(method="GET", json=changed)
    reread = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="finance.stripe.invoices.retrieve",
            credential_ref=credential_ref,
            input_json={"invoice_ref": invoice_ref},
            output_policy_json={"mode": "always_file"},
        )
    ).data
    saved = Path(reread.output_json["file"]["path"]).read_text(encoding="utf-8")
    observed = json.loads(saved)["response"]["output_json"]["data"]
    assert observed["invoice_customer_email_sha256"] == approved_hash
    assert observed["current_customer_email_sha256"] != approved_hash
    assert observed["due_date"] != original["due_date"]
    assert observed["customer_ref"] == original["customer_ref"]
    assert "approved@example.test" not in saved and "changed@example.test" not in saved
    assert "PRIVATE" not in saved
    assert all(request.method == "GET" for request in httpx_mock.get_requests())


@pytest.mark.parametrize(
    "customer",
    [
        "cus_unexpanded",
        {"id": "cus_deleted", "object": "customer", "deleted": True},
        {"id": "cus_deleted", "object": "customer", "deleted": True, "email": "stale@example.test"},
    ],
)
def test_stripe_invoice_read_does_not_invent_missing_current_recipient_or_due_terms(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    customer: object,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_missing_recipient",
    )
    httpx_mock.add_response(
        method="GET",
        json={
            **stripe_invoice(),
            "id": "in_missing_recipient",
            "object": "invoice",
            "customer": customer,
            "customer_email": None,
            "due_date": None,
            "collection_method": "send_invoice",
        },
    )
    result = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoices.retrieve",
        input_json={"invoice_ref": invoice_ref},
    )
    observed = result.output_json["data"]
    assert observed["invoice_customer_email_sha256"] is None
    assert observed["current_customer_email_sha256"] is None
    assert observed["due_date"] is None
    assert observed["recipient_scope"] == "primary-email-fields-only"


def _stripe_credential_ref(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> str:
    """Store/test a fake key so account-bound refs have a verified account."""

    PluginRepository(session).get_plugin("finance")
    credential_ref = (
        AuthRepository(session)
        .store_credential(
            attach_project_id=project_id,
            provider_key="stripe",
            auth_method_key="api_key",
            display_name="Stripe test account",
            fields={"api_key": STRIPE_SECRET},
        )
        .data.credential_ref
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/account",
        headers={"Request-Id": "req_account"},
        json={
            "id": "acct_test_finance",
            "object": "account",
            "country": "US",
            "business_profile": {"name": "Fixture Business"},
        },
    )
    tested = asyncio.run(
        AuthRepository(session).test(project_id=project_id, credential_ref=credential_ref)
    ).data
    assert tested.ok is True
    assert tested.metadata["evidence"]["account"]["provider_account_id"] == "acct_test_finance"
    assert "livemode" not in tested.metadata["evidence"]["account"]
    return credential_ref


@pytest.mark.parametrize(
    ("status_code", "claimable", "reason_code", "retryable"),
    [
        (403, True, "claimable_key_restricted", False),
        (403, False, "permission_denied", False),
        (401, False, "authentication_failed", False),
        (401, True, "authentication_failed", False),
        (429, False, "rate_limited", True),
        (500, False, "provider_unavailable", True),
        (503, False, "provider_unavailable", True),
        (None, False, "network_error", True),
    ],
)
def test_stripe_account_probe_preserves_safe_failure_diagnostics_and_audit(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int | None,
    claimable: bool,
    reason_code: str,
    retryable: bool,
) -> None:
    # Exercise the real retry loop without sleeping or sharing rate-bucket time.
    monkeypatch.setattr("stackos.integrations._base.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("stackos.integrations._base.TokenBucket.acquire", AsyncMock())
    PluginRepository(session).get_plugin("finance")
    repo = AuthRepository(session)
    credential_ref = repo.store_credential(
        attach_project_id=project_id,
        provider_key="stripe",
        auth_method_key="api_key",
        display_name="Stripe diagnostic fixture",
        fields={"api_key": STRIPE_SECRET},
    ).data.credential_ref
    claim_url = "https://dashboard.stripe.com/claim/fixture-claim-secret"
    message = f"PRIVATE PROVIDER MESSAGE {STRIPE_SECRET} {claim_url}"
    if claimable:
        message = f"This is a claimable sandbox key with limited permissions. {message}"
    expected_calls = 4 if retryable else 1
    for _ in range(expected_calls):
        if status_code is None:
            httpx_mock.add_exception(
                httpx.ConnectError("fixture network unavailable"),
                method="GET",
                url=f"{STRIPE_ROOT}/account",
            )
        else:
            httpx_mock.add_response(
                method="GET",
                url=f"{STRIPE_ROOT}/account",
                status_code=status_code,
                headers={"Request-Id": "req_diagnostic", "Retry-After": "0"},
                json={"error": {"type": "invalid_request_error", "message": message}},
            )

    out = asyncio.run(repo.test(project_id=project_id, credential_ref=credential_ref)).data

    assert out.ok is False
    assert out.status == "failed"
    assert out.retryable is retryable
    assert out.metadata["reason_code"] == reason_code
    assert out.metadata["stage"] == "auth.test"
    assert out.next_action
    assert out.summary != "stripe credential test failed at test"
    if claimable and status_code == 403:
        assert "claimable sandbox key" in out.summary
        assert "claim" in out.next_action.lower()
    if status_code is not None:
        assert out.metadata["provider_status_code"] == status_code
        assert out.metadata["provider_error"]["request_id"] == "req_diagnostic"
        assert out.metadata["provider_error"]["type"] == "invalid_request_error"
    else:
        assert "provider_status_code" not in out.metadata
        assert "request_id" not in out.metadata.get("provider_error", {})
    if status_code == 429:
        assert out.metadata["retry_after"] == 0.0
    assert len(httpx_mock.get_requests()) == expected_calls
    assert all(request.method == "GET" for request in httpx_mock.get_requests())
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.status == "connected"  # A failed probe is not revocation.
    usage = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.operation == "account.test")
    ).one()
    assert usage.metadata_json["result"] == out.model_dump(mode="json")
    assert usage.metadata_json["ok"] is False
    assert usage.metadata_json["metadata"] == out.metadata
    rendered = json.dumps({"output": out.model_dump(mode="json"), "audit": usage.metadata_json})
    for forbidden in (STRIPE_SECRET, claim_url, "fixture-claim-secret", "PRIVATE PROVIDER MESSAGE"):
        assert forbidden not in rendered
    assert "message" not in out.metadata.get("provider_error", {})


@pytest.mark.parametrize(
    ("status_code", "claimable", "reason_code"),
    [
        (403, True, "claimable_key_restricted"),
        (403, False, "permission_denied"),
        (401, False, "authentication_failed"),
    ],
)
def test_stripe_action_auth_failure_is_actionable_without_changing_write_recovery(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    status_code: int,
    claimable: bool,
    reason_code: str,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    message = "PRIVATE ACTION ERROR https://dashboard.stripe.com/claim/fixture-claim-secret"
    if claimable:
        message = f"This is a claimable sandbox key with limited permissions. {message}"
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        status_code=status_code,
        headers={"Request-Id": "req_action_auth"},
        json={"error": {"type": "invalid_request_error", "message": message}},
    )
    with pytest.raises(ConflictError) as failure:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.customers.create",
            input_json={
                "email": _payload_secret_marker(session, project_id, "fixture@example.test")
            },
            idempotency_key="finance-auth-diagnostic-fixture",
        )
    error = failure.value.data["provider_error"]
    assert failure.value.data["provider_status_code"] == status_code
    assert error["reason_code"] == reason_code
    assert error["request_id"] == "req_action_auth"
    assert error["summary"]
    assert error["next_action"]
    assert error["outcome_unknown"] is False
    assert error["retry_safe"] is False
    assert "fresh idempotency key" in error["recovery"]
    assert len([request for request in httpx_mock.get_requests() if request.method == "POST"]) == 1
    call = session.exec(
        select(ActionCall).where(ActionCall.status == ActionCallStatus.FAILED)
    ).one()
    assert call.response_json["provider_error"]["reason_code"] == reason_code
    rendered = json.dumps({"failure": failure.value.data, "audit": call.response_json})
    for forbidden in (STRIPE_SECRET, "PRIVATE ACTION ERROR", "fixture-claim-secret"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "unsafe_detail",
    [
        STRIPE_SECRET,
        f"req_{STRIPE_SECRET}",
        "rkcs_test_fixture_sentinel",
        "req_rkcs_test_fixture_sentinel",
        "PRIVATE PROVIDER DETAIL",
        "https://dashboard.stripe.com/claim/fixture-claim-secret",
        "https://docs.stripe.com.evil.test/error-codes",
        "https://docs.stripe.com/error-codes?claim=fixture-claim-secret",
        "https://docs.stripe.com/error-codes#fixture-claim-secret",
        "x" * 501,
    ],
)
def test_stripe_account_probe_rejects_unsafe_structured_error_fields(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    unsafe_detail: str,
) -> None:
    PluginRepository(session).get_plugin("finance")
    repo = AuthRepository(session)
    credential_ref = repo.store_credential(
        attach_project_id=project_id,
        provider_key="stripe",
        auth_method_key="api_key",
        display_name="Stripe unsafe diagnostic fixture",
        fields={"api_key": STRIPE_SECRET},
    ).data.credential_ref
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/account",
        status_code=403,
        headers={
            "Request-Id": unsafe_detail,
            "Stripe-Rate-Limited-Reason": unsafe_detail,
        },
        json={
            "error": dict.fromkeys(
                ("type", "code", "decline_code", "param", "doc_url", "message"), unsafe_detail
            )
        },
    )
    out = asyncio.run(repo.test(project_id=project_id, credential_ref=credential_ref)).data
    assert out.metadata["reason_code"] == "permission_denied"
    assert out.metadata["provider_status_code"] == 403
    error = out.metadata["provider_error"]
    for field in ("type", "code", "decline_code", "param", "doc_url", "request_id"):
        assert field not in error
    assert "rate_limited_reason" not in error
    usage = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.operation == "account.test")
    ).one()
    assert unsafe_detail not in json.dumps(usage.metadata_json)
    assert STRIPE_SECRET not in json.dumps(usage.metadata_json)


def test_stripe_account_probe_keeps_bounded_documented_error_fields(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    PluginRepository(session).get_plugin("finance")
    repo = AuthRepository(session)
    credential_ref = repo.store_credential(
        attach_project_id=project_id,
        provider_key="stripe",
        auth_method_key="api_key",
        display_name="Stripe safe diagnostic fixture",
        fields={"api_key": STRIPE_SECRET},
    ).data.credential_ref
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/account",
        status_code=400,
        headers={"Request-Id": "req_Safe123", "Stripe-Rate-Limited-Reason": "endpoint-rate"},
        json={
            "error": {
                "type": "invalid_request_error",
                "code": "parameter_unknown",
                "decline_code": "insufficient_funds",
                "param": "payment_method_options[card][request_three_d_secure]",
                "doc_url": "https://stripe.com/docs/error-codes/parameter-unknown",
            }
        },
    )
    out = asyncio.run(repo.test(project_id=project_id, credential_ref=credential_ref)).data
    assert out.retryable is False
    error = out.metadata["provider_error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "parameter_unknown"
    assert error["decline_code"] == "insufficient_funds"
    assert error["param"] == "payment_method_options[card][request_three_d_secure]"
    assert error["request_id"] == "req_Safe123"
    assert error["rate_limited_reason"] == "endpoint-rate"
    assert error["doc_url"] == "https://docs.stripe.com/error-codes"


@pytest.mark.parametrize(
    ("request_id", "expected"),
    [
        ("req_Safe123", "req_Safe123"),
        (STRIPE_SECRET, None),
        (f"req_{STRIPE_SECRET}", None),
        ("rkcs_test_fixture_sentinel", None),
        ("req_rkcs_test_fixture_sentinel", None),
        ("https://dashboard.stripe.com/claim/fixture-claim-secret", None),
        ("PRIVATE PROVIDER DETAIL", None),
    ],
)
def test_stripe_action_success_request_id_uses_same_safe_diagnostic_boundary(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    request_id: str,
    expected: str | None,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance",
        headers={"Request-Id": request_id},
        json={"object": "balance", "livemode": False, "available": [], "pending": []},
    )
    out = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.balance.retrieve",
        input_json={},
    )
    assert out.metadata_json.get("request_id") == expected
    call = session.exec(select(ActionCall)).one()
    if expected is None:
        assert request_id not in json.dumps(out.model_dump(mode="json"))
        assert request_id not in json.dumps(call.response_json)


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("3", "3"),
        ("0.25", "0.25"),
        ("0", "0"),
        (STRIPE_SECRET, None),
        ("https://dashboard.stripe.com/claim/fixture-claim-secret", None),
        ("PRIVATE PROVIDER DETAIL", None),
        ("-1", None),
        ("NaN", None),
        ("Infinity", None),
        ("1" * 41, None),
    ],
)
def test_stripe_action_success_retry_after_is_bounded_numeric_metadata(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    retry_after: str,
    expected: str | None,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance",
        headers={"Retry-After": retry_after},
        json={"object": "balance", "livemode": False, "available": [], "pending": []},
    )
    out = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.balance.retrieve",
        input_json={},
    )
    assert out.metadata_json.get("retry_after") == expected
    call = session.exec(select(ActionCall)).one()
    if expected is None:
        assert "retry_after" not in out.metadata_json
        if len(retry_after) > 8:
            assert retry_after not in json.dumps(out.model_dump(mode="json"))
            assert retry_after not in json.dumps(call.response_json)


def _execute(
    session: Session,
    *,
    project_id: int,
    action_ref: str,
    credential_ref: str,
    input_json: dict[str, object],
    idempotency_key: str | None = None,
):
    return asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json=input_json,
            credential_ref=credential_ref,
            idempotency_key=idempotency_key,
        )
    ).data


def _safe_ref(
    session: Session,
    *,
    project_id: int,
    credential_ref: str,
    object_type: str,
    provider_id: str,
    display_name: str | None = None,
) -> str:
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    return ProviderObjectReferenceRepository(session, project_id=project_id).upsert(
        credential=credential,
        object_type=object_type,
        provider_object_id=provider_id,
        display_name=display_name,
    )


def _payload_secret_marker(
    session: Session,
    project_id: int,
    value: str,
) -> dict[str, str]:
    stored = PayloadSecretRepository(session).set(project_id=project_id, value=value)
    return {"$secret_ref": stored.secret_ref}


def test_stripe_invoice_recovery_filters_correlation_and_manual_finalization(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_recovery",
    )
    invoice = {
        **stripe_invoice(),
        "id": "in_recovery",
        "object": "invoice",
        "customer": "cus_recovery",
        "status": "draft",
        "auto_advance": False,
        "total": 12300,
        "subtotal": 12300,
        "metadata": {"stackos_correlation": CORRELATION_TOKEN, "private": "private memo"},
    }
    httpx_mock.add_response(method="POST", url=f"{STRIPE_ROOT}/invoices", json=invoice)
    created = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoices.create",
        input_json={
            "currency": "usd",
            "customer_ref": customer_ref,
            "collection_method": "send_invoice",
            "days_until_due": 30,
            "correlation_key": CORRELATION_TOKEN,
        },
        idempotency_key="invoice-recovery-create",
    )
    create_form = parse_qs(httpx_mock.get_requests()[-1].content.decode())
    assert create_form["metadata[stackos_correlation]"] == [CORRELATION_TOKEN]
    assert create_form["auto_advance"] == ["false"]
    assert created.output_json["data"]["correlation_matches"] is True
    invoice_ref = created.output_json["data"]["invoice_ref"]
    httpx_mock.add_response(
        method="GET",
        json={
            "object": "list",
            "has_more": True,
            "data": [
                invoice,
                {**invoice, "id": "in_other", "metadata": {"stackos_correlation": "f" * 32}},
                {**invoice, "id": "in_missing", "metadata": {}},
            ],
        },
    )
    page = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoices.list",
        input_json={
            "customer_ref": customer_ref,
            "created_gte": 100,
            "created_lte": 200,
            "correlation_key": CORRELATION_TOKEN,
            "limit": 3,
        },
    )
    assert dict(httpx_mock.get_requests()[-1].url.params) == {
        "limit": "3",
        "customer": "cus_recovery",
        "created[gte]": "100",
        "created[lte]": "200",
    }
    rows = page.output_json["data"]["items"]
    assert [row["correlation_matches"] for row in rows] == [True, False, False]
    assert page.output_json["data"]["next_page_cursor"] == rows[-1]["invoice_ref"]
    assert rows[0]["total"] == rows[0]["subtotal"] == 12300
    assert rows[0]["auto_advance"] is False
    assert "private memo" not in json.dumps(page.model_dump(mode="json"))
    assert "stackos_correlation" not in json.dumps(page.output_json)
    httpx_mock.add_response(
        method="GET", url=f"{STRIPE_ROOT}/invoices/in_recovery?expand%5B%5D=customer", json=invoice
    )
    retrieved = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoices.retrieve",
        input_json={"invoice_ref": invoice_ref, "correlation_key": CORRELATION_TOKEN},
    )
    assert retrieved.output_json["data"]["correlation_matches"] is True
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_recovery/finalize",
        json={**invoice, "status": "open"},
    )
    _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoices.finalize",
        input_json={"invoice_ref": invoice_ref},
        idempotency_key="invoice-recovery-finalize",
    )
    assert parse_qs(httpx_mock.get_requests()[-1].content.decode()) == {"auto_advance": ["false"]}


@pytest.mark.parametrize(
    "description", ["", "Private Client: café ☕", " Private Client: café ☕ "]
)
def test_stripe_invoice_items_read_uses_exact_digest_and_typed_pagination(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    description: str,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_items",
    )
    item = {
        **stripe_invoice_item(),
        "id": "ii_items",
        "object": "invoiceitem",
        "invoice": "in_items",
        "customer": "cus_items",
        "amount": 12300,
        "currency": "usd",
        "description": description,
    }
    httpx_mock.add_response(method="GET", json={"object": "list", "has_more": True, "data": [item]})
    page = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoice-items.list",
        input_json={"invoice_ref": invoice_ref},
    )
    row = page.output_json["data"]["items"][0]
    assert row["description_sha256"] == hashlib.sha256(description.encode("utf-8")).hexdigest()
    assert "description" not in row
    assert page.output_json["data"]["next_page_cursor"] == row["invoice_item_ref"]
    assert dict(httpx_mock.get_requests()[-1].url.params) == {"limit": "25", "invoice": "in_items"}
    if description:
        assert description not in json.dumps(page.model_dump(mode="json"), ensure_ascii=False)
    httpx_mock.add_response(method="GET", json={"object": "list", "has_more": False, "data": []})
    _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoice-items.list",
        input_json={"customer_ref": row["customer_ref"], "page_cursor": row["invoice_item_ref"]},
    )
    assert dict(httpx_mock.get_requests()[-1].url.params) == {
        "limit": "25",
        "starting_after": "ii_items",
        "customer": "cus_items",
    }


def test_stripe_dispute_reads_are_scoped_and_exclude_evidence(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    charge_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.charge",
        provider_id="ch_disputed",
    )
    dispute = {
        **stripe_dispute(),
        "id": "du_fixture",
        "object": "dispute",
        "charge": "ch_disputed",
        "payment_intent": "pi_disputed",
        "status": "needs_response",
        "reason": "fraudulent",
        "amount": 12300,
        "currency": "usd",
        "created": 100,
        "livemode": False,
        "evidence": {"customer_email_address": "private@example.test"},
        "metadata": {"private": "private dispute memo"},
    }
    httpx_mock.add_response(
        method="GET", json={"object": "list", "has_more": True, "data": [dispute]}
    )
    page = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.disputes.list",
        input_json={"charge_ref": charge_ref},
    )
    row = page.output_json["data"]["items"][0]
    assert row["status"] == "needs_response"
    assert row["charge_ref"] == charge_ref
    assert page.output_json["data"]["next_page_cursor"] == row["dispute_ref"]
    assert dict(httpx_mock.get_requests()[-1].url.params) == {
        "limit": "25",
        "charge": "ch_disputed",
    }
    assert "private" not in json.dumps(page.model_dump(mode="json"))
    httpx_mock.add_response(method="GET", url=f"{STRIPE_ROOT}/disputes/du_fixture", json=dispute)
    retrieved = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.disputes.retrieve",
        input_json={"dispute_ref": row["dispute_ref"]},
    )
    assert retrieved.output_json["data"] == row
    httpx_mock.add_response(method="GET", json={"object": "list", "has_more": False, "data": []})
    _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.disputes.list",
        input_json={
            "payment_intent_ref": row["payment_intent_ref"],
            "page_cursor": row["dispute_ref"],
        },
    )
    assert dict(httpx_mock.get_requests()[-1].url.params) == {
        "limit": "25",
        "starting_after": "du_fixture",
        "payment_intent": "pi_disputed",
    }


@pytest.mark.parametrize(
    ("action", "payload", "expected_code"),
    [
        ("stripe.invoice-items.list", {}, "one_of"),
        (
            "stripe.invoice-items.list",
            {"invoice_ref": "provider-object:a", "customer_ref": "provider-object:b"},
            "one_of",
        ),
        ("stripe.disputes.list", {}, "one_of"),
        (
            "stripe.disputes.list",
            {"charge_ref": "provider-object:a", "payment_intent_ref": "provider-object:b"},
            "one_of",
        ),
        ("stripe.disputes.retrieve", {"dispute_ref": "du_raw"}, "safe_ref_required"),
        ("stripe.invoices.list", {"created_gte": 200, "created_lte": 100}, "range"),
        ("stripe.invoices.list", {"correlation_key": "customer@example.test"}, "format"),
        ("stripe.invoices.list", {"created_gte": True}, "range"),
        (
            "stripe.invoices.finalize",
            {"invoice_ref": "provider-object:a", "auto_advance": True},
            "additional_property",
        ),
    ],
)
def test_stripe_recovery_inputs_reject_ambiguous_or_unsafe_selectors(
    session: Session,
    project_id: int,
    action: str,
    payload: dict,
    expected_code: str,
) -> None:
    result = ActionRepository(session).validate(
        project_id=project_id,
        action_ref=f"finance.{action}",
        input_json=payload,
        idempotency_key="validation-fixture",
    )
    assert expected_code in {item.code for item in result.issues}


def test_stripe_post_normalization_failure_preserves_unknown_outcome(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    email = _payload_secret_marker(session, project_id, "private@example.test")
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        json={"id": "cus_created", "object": "customer"},
    )

    def fail_normalization(*args, **kwargs):
        raise RuntimeError("private post-HTTP reference persistence failure")

    monkeypatch.setattr(ProviderObjectReferenceRepository, "upsert", fail_normalization)
    with pytest.raises(ConflictError) as failure:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.customers.create",
            input_json={"email": email},
            idempotency_key="normalization-unknown",
        )
    assert failure.value.data["provider_error"]["outcome_unknown"] is True
    assert failure.value.data["provider_error"]["retry_safe"] is False
    assert "private post-HTTP" not in str(failure.value.data)
    call = session.exec(
        select(ActionCall).where(ActionCall.idempotency_key == "normalization-unknown")
    ).one()
    assert call.response_json["outcome_unknown"] is True


def test_stripe_short_description_recovers_from_independent_read_without_repeating_post(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_short",
    )
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_short",
    )
    description = _payload_secret_marker(session, project_id, "a")
    item = {
        **stripe_invoice_item(),
        "id": "ii_short",
        "object": "invoiceitem",
        "customer": "cus_short",
        "invoice": "in_short",
        "amount": 100,
        "currency": "usd",
        "description": "a",
    }
    httpx_mock.add_response(method="POST", url=f"{STRIPE_ROOT}/invoiceitems", json=item)
    created = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoice-items.create",
        input_json={
            "customer_ref": customer_ref,
            "invoice_ref": invoice_ref,
            "amount": 100,
            "currency": "usd",
            "description": description,
        },
        idempotency_key="short-description-create",
    )
    # Existing generic redaction may alter otherwise safe POST echo fields.
    assert "[redacted]" in json.dumps(created.output_json)
    httpx_mock.add_response(
        method="GET", json={"object": "list", "has_more": False, "data": [item]}
    )
    reread = _execute(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        action_ref="finance.stripe.invoice-items.list",
        input_json={"invoice_ref": invoice_ref},
    )
    actual = reread.output_json["data"]["items"][0]
    assert actual["invoice_ref"] == invoice_ref
    assert actual["customer_ref"] == customer_ref
    assert actual["amount"] == 100 and actual["currency"] == "usd"
    assert actual["description_sha256"] == hashlib.sha256(b"a").hexdigest()
    assert "description" not in actual
    assert actual["invoice_item_ref"].startswith("provider-object:")
    assert len([request for request in httpx_mock.get_requests() if request.method == "POST"]) == 1


def test_stripe_item_description_stays_out_of_response_file_and_reference_names(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path: Path,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_private",
    )
    _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice-item",
        provider_id="ii_private",
        display_name="PRIVATE LINE",
    )
    httpx_mock.add_response(
        method="GET",
        json={
            "object": "list",
            "has_more": False,
            "data": [
                {
                    **stripe_invoice_item(),
                    "id": "ii_private",
                    "object": "invoiceitem",
                    "description": "PRIVATE LINE",
                    "invoice": "in_private",
                },
            ],
        },
    )
    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="finance.stripe.invoice-items.list",
            credential_ref=credential_ref,
            input_json={"invoice_ref": invoice_ref},
            output_policy_json={"mode": "always_file"},
        )
    ).data
    saved = Path(result.output_json["file"]["path"]).read_text(encoding="utf-8")
    assert "PRIVATE LINE" not in saved
    assert hashlib.sha256(b"PRIVATE LINE").hexdigest() in saved
    assert "PRIVATE LINE" not in json.dumps(result.model_dump(mode="json"))
    reference = session.exec(
        select(ProviderObjectReference).where(
            ProviderObjectReference.provider_object_id == "ii_private"
        )
    ).one()
    assert not reference.display_name


@pytest.mark.parametrize("wrong_binding", ["account", "type"])
def test_stripe_recovery_cursor_rejects_other_account_or_object_type_before_http(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    wrong_binding: str,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    charge_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.charge",
        provider_id="ch_scoped",
    )
    cursor = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.dispute",
        provider_id="du_wrong_binding",
    )
    row = session.exec(
        select(ProviderObjectReference).where(ProviderObjectReference.safe_ref == cursor)
    ).one()
    if wrong_binding == "account":
        row.provider_account_id = "acct_other"
    else:
        row.object_type = "stripe.invoice-item"
    session.add(row)
    session.commit()
    with pytest.raises(ConflictError):
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
            action_ref="finance.stripe.disputes.list",
            input_json={"charge_ref": charge_ref, "page_cursor": cursor},
        )
    assert len(httpx_mock.get_requests()) == 1  # Account probe only.


@pytest.mark.parametrize(
    ("action_key", "field"),
    [
        ("stripe.customers.create", "email"),
        ("stripe.customers.create", "name"),
        ("stripe.customers.create", "description"),
        ("stripe.customers.list", "email"),
        ("stripe.invoices.create", "description"),
        ("stripe.invoice-items.create", "description"),
    ],
)
def test_stripe_sensitive_text_requires_payload_secret_projection(
    action_key: str,
    field: str,
) -> None:
    spec = STRIPE_ACTION_SPECS[action_key]
    connector = StripeActionConnector()

    def validate(value: str) -> list:
        return connector.validate(
            ActionConnectorRequest(
                project_id=1,
                plugin_slug="finance",
                action_key=action_key,
                action_ref=f"finance.{action_key}",
                provider_key="stripe",
                operation=STRIPE_OPERATION,
                input_json={field: value},
                config_json={
                    "stripe": {
                        "method": spec.method,
                        "path": spec.path,
                        "api_version": "2026-08-26.dahlia",
                    }
                },
                dry_run=True,
                idempotency_key="finance-sensitive-text-fixture",
            )
        )

    raw_issues = validate("customer@example.test")
    assert any(
        item.path == f"$.{field}" and item.code == "payload_secret_ref_required"
        for item in raw_issues
    )
    projected_issues = validate(SECRET_REF_SENTINEL)
    assert not any(item.code == "payload_secret_ref_required" for item in projected_issues)


def test_stripe_draft_invoice_lifecycle_uses_safe_refs_and_idempotency(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    payload_secrets = PayloadSecretRepository(session)
    email_ref = payload_secrets.set(project_id=project_id, value="customer@example.test").secret_ref
    name_ref = payload_secrets.set(project_id=project_id, value="Customer").secret_ref
    invoice_description_ref = payload_secrets.set(
        project_id=project_id, value="September advisory"
    ).secret_ref
    line_description_ref = payload_secrets.set(
        project_id=project_id, value="Advisory services"
    ).secret_ref
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        headers={"Request-Id": "req_customer"},
        json={
            **stripe_customer(),
            "id": "cus_fixture",
            "object": "customer",
            "created": 1,
            "livemode": False,
        },
    )
    customer = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.customers.create",
        credential_ref=credential_ref,
        input_json={
            "email": {"$secret_ref": email_ref},
            "name": {"$secret_ref": name_ref},
        },
        idempotency_key="finance-customer-fixture-1",
    )
    customer_ref = customer.output_json["data"]["customer_ref"]
    customer_request = httpx_mock.get_requests()[-1]
    assert customer_request.headers["Authorization"] == f"Bearer {STRIPE_SECRET}"
    assert customer_request.headers["Stripe-Version"] == "2026-08-26.dahlia"
    assert customer_request.headers["Idempotency-Key"] == "finance-customer-fixture-1"
    assert parse_qs(customer_request.content.decode()) == {
        "email": ["customer@example.test"],
        "name": ["Customer"],
    }
    assert "cus_fixture" not in json.dumps(customer.output_json)
    assert "customer@example.test" not in json.dumps(customer.output_json)
    assert customer.action_call.request_json == {
        "email": {"$secret_ref": email_ref},
        "name": {"$secret_ref": name_ref},
    }

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices",
        json={
            **stripe_invoice(),
            "id": "in_fixture",
            "object": "invoice",
            "customer": "cus_fixture",
            "status": "draft",
            "collection_method": "send_invoice",
            "amount_due": 0,
            "amount_paid": 0,
            "amount_remaining": 0,
            "currency": "usd",
            "livemode": False,
        },
    )
    invoice = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.create",
        credential_ref=credential_ref,
        input_json={
            "currency": "usd",
            "customer_ref": customer_ref,
            "collection_method": "send_invoice",
            "days_until_due": 14,
            "description": {"$secret_ref": invoice_description_ref},
        },
        idempotency_key="finance-invoice-fixture-1",
    )
    invoice_ref = invoice.output_json["data"]["invoice_ref"]
    invoice_request = httpx_mock.get_requests()[-1]
    assert parse_qs(invoice_request.content.decode()) == {
        "customer": ["cus_fixture"],
        "collection_method": ["send_invoice"],
        "days_until_due": ["14"],
        "currency": ["usd"],
        "auto_advance": ["false"],
        "description": ["September advisory"],
    }
    assert invoice_request.headers["Idempotency-Key"] == "finance-invoice-fixture-1"

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoiceitems",
        json={
            **stripe_invoice_item(),
            "id": "ii_fixture",
            "object": "invoiceitem",
            "customer": "cus_fixture",
            "invoice": "in_fixture",
            "amount": 125000,
            "currency": "usd",
            "description": "Advisory services",
            "livemode": False,
        },
    )
    line = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoice-items.create",
        credential_ref=credential_ref,
        input_json={
            "customer_ref": customer_ref,
            "invoice_ref": invoice_ref,
            "amount": 125000,
            "currency": "usd",
            "description": {"$secret_ref": line_description_ref},
        },
        idempotency_key="finance-invoice-line-fixture-1",
    )
    line_request = httpx_mock.get_requests()[-1]
    assert parse_qs(line_request.content.decode()) == {
        "customer": ["cus_fixture"],
        "invoice": ["in_fixture"],
        "amount": ["125000"],
        "currency": ["usd"],
        "description": ["Advisory services"],
    }
    assert line.output_json["data"]["invoice_ref"] == invoice_ref
    assert "description" not in line.output_json["data"]
    assert (
        line.output_json["data"]["description_sha256"]
        == hashlib.sha256(b"Advisory services").hexdigest()
    )
    assert line.action_call.request_json["description"] == {"$secret_ref": line_description_ref}
    assert line.action_call.request_json["amount"] == 125000
    assert line.action_call.response_json["data"]["amount"] == 125000

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_fixture/finalize",
        json={
            **stripe_invoice(),
            "id": "in_fixture",
            "object": "invoice",
            "customer": "cus_fixture",
            "status": "open",
            "collection_method": "send_invoice",
            "amount_due": 125000,
            "amount_paid": 0,
            "amount_remaining": 125000,
            "currency": "usd",
        },
    )
    _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.finalize",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
        idempotency_key="finance-invoice-finalize-fixture-1",
    )
    finalize_request = httpx_mock.get_requests()[-1]
    assert finalize_request.headers["Idempotency-Key"] == "finance-invoice-finalize-fixture-1"
    assert parse_qs(finalize_request.content.decode()) == {"auto_advance": ["false"]}


def test_stripe_pagination_reuses_final_typed_reference_and_payment_evidence(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance_transactions?expand%5B%5D=data.source&limit=1",
        json={
            "object": "list",
            "has_more": True,
            "data": [
                {
                    **stripe_balance_transaction(),
                    "id": "txn_fixture_1",
                    "object": "balance_transaction",
                    "amount": 10000,
                    "fee": 320,
                    "net": 9680,
                    "currency": "usd",
                    "type": "charge",
                    "status": "available",
                    "available_on": 2,
                    "created": 1,
                }
            ],
        },
    )
    first = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.balance-transactions.list",
        credential_ref=credential_ref,
        input_json={"limit": 1},
    )
    item = first.output_json["data"]["items"][0]
    cursor = first.output_json["data"]["next_page_cursor"]
    assert cursor == item["balance_transaction_ref"]
    assert {"amount", "fee", "net"} <= set(item)
    assert "txn_fixture_1" not in json.dumps(first.output_json)

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance_transactions?expand%5B%5D=data.source&limit=1&starting_after=txn_fixture_1",
        json={"object": "list", "has_more": False, "data": []},
    )
    second = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.balance-transactions.list",
        credential_ref=credential_ref,
        input_json={"limit": 1, "page_cursor": cursor},
    )
    assert second.output_json["data"] == {"items": [], "has_more": False}
    second_request = httpx_mock.get_requests()[-1]
    assert second_request.url.params["starting_after"] == "txn_fixture_1"


def test_stripe_invoice_number_never_enters_safe_output_audit_or_reference_display_name(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    customer_identifying_invoice_number = "ACME-CUSTOMER-JANE-2026-0001"
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_customer_number_fixture",
        display_name=customer_identifying_invoice_number,
    )
    legacy_reference = session.exec(
        select(ProviderObjectReference).where(ProviderObjectReference.safe_ref == invoice_ref)
    ).one()
    assert legacy_reference.display_name == customer_identifying_invoice_number

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices/in_customer_number_fixture?expand%5B%5D=customer",
        json={
            **stripe_invoice(),
            "id": "in_customer_number_fixture",
            "object": "invoice",
            "number": customer_identifying_invoice_number,
            "status": "open",
            "collection_method": "send_invoice",
            "amount_due": 10000,
            "amount_paid": 0,
            "amount_remaining": 10000,
            "currency": "usd",
        },
    )

    retrieved = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.retrieve",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
    )

    serialized_output = json.dumps(retrieved.output_json)
    serialized_audit = json.dumps(retrieved.action_call.response_json)
    assert customer_identifying_invoice_number not in serialized_output
    assert customer_identifying_invoice_number not in serialized_audit
    assert "number" not in retrieved.output_json["data"]

    reference = session.exec(
        select(ProviderObjectReference).where(ProviderObjectReference.safe_ref == invoice_ref)
    ).one()
    assert reference.object_type == "stripe.invoice"
    assert reference.display_name is None


def test_stripe_read_surface_covers_customer_invoice_payment_charge_refund_and_balance(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_known",
    )
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_known",
    )
    charge_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.charge",
        provider_id="ch_known",
    )
    payment_intent_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-intent",
        provider_id="pi_known",
    )
    balance_transaction_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.balance-transaction",
        provider_id="txn_known",
    )
    refund_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.refund",
        provider_id="re_known",
    )

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers/cus_known",
        json={"id": "cus_known", "object": "customer", "deleted": True},
    )
    customer = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.customers.retrieve",
        credential_ref=credential_ref,
        input_json={"customer_ref": customer_ref},
    )
    assert customer.output_json["data"]["deleted"] is True

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers?limit=1&email=existing%40example.test",
        json={
            "object": "list",
            "has_more": True,
            "data": [
                {**stripe_customer(), "id": "cus_existing", "object": "customer", "created": 1}
            ],
        },
    )
    listed_customers = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.customers.list",
        credential_ref=credential_ref,
        input_json={
            "email": _payload_secret_marker(session, project_id, "existing@example.test"),
            "limit": 1,
        },
    )
    assert listed_customers.output_json["data"]["items"][0]["customer_ref"].startswith(
        "provider-object:"
    )
    customer_cursor = listed_customers.output_json["data"]["next_page_cursor"]
    assert customer_cursor == listed_customers.output_json["data"]["items"][0]["customer_ref"]
    httpx_mock.add_response(
        method="GET",
        url=(
            f"{STRIPE_ROOT}/customers?limit=1&starting_after=cus_existing&"
            "email=existing%40example.test"
        ),
        json={"object": "list", "has_more": False, "data": []},
    )
    assert _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.customers.list",
        credential_ref=credential_ref,
        input_json={
            "email": _payload_secret_marker(session, project_id, "existing@example.test"),
            "limit": 1,
            "page_cursor": customer_cursor,
        },
    ).output_json["data"] == {"items": [], "has_more": False}

    invoice_body = {
        **stripe_invoice(),
        "id": "in_known",
        "object": "invoice",
        "customer": "cus_known",
        "status": "open",
        "collection_method": "send_invoice",
        "amount_due": 10000,
        "amount_paid": 0,
        "amount_remaining": 10000,
        "currency": "usd",
    }
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices/in_known?expand%5B%5D=customer",
        json=invoice_body,
    )
    retrieved_invoice = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.retrieve",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
    )
    assert retrieved_invoice.output_json["data"]["status"] == "open"

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices?limit=1&status=open",
        json={"object": "list", "has_more": False, "data": [invoice_body]},
    )
    invoice_page = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.list",
        credential_ref=credential_ref,
        input_json={"limit": 1, "status": "open"},
    )
    assert invoice_page.output_json["data"]["items"][0]["invoice_ref"] == invoice_ref

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_known/send",
        headers={"Idempotent-Replayed": "true", "Request-Id": "req_send"},
        json=invoice_body,
    )
    sent = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.send",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
        idempotency_key="finance-invoice-send-fixture-1",
    )
    assert sent.metadata_json["idempotent_replayed"] is True

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoice_payments?limit=2&invoice=in_known",
        json={
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "inpay_known",
                    "object": "invoice_payment",
                    "invoice": "in_known",
                    "amount_requested": 10000,
                    "amount_paid": 10000,
                    "created": 1_700_000_000,
                    "currency": "usd",
                    "is_default": False,
                    "livemode": False,
                    "status": "paid",
                    "status_transitions": {"canceled_at": None, "paid_at": 1_700_000_001},
                    "payment": {"type": "payment_intent", "payment_intent": "pi_known"},
                },
                {
                    "id": "inpay_payment_record",
                    "object": "invoice_payment",
                    "invoice": "in_known",
                    "amount_requested": 10000,
                    "amount_paid": 10000,
                    "created": 1_700_000_000,
                    "currency": "usd",
                    "is_default": False,
                    "livemode": False,
                    "status": "paid",
                    "status_transitions": {"canceled_at": None, "paid_at": 1_700_000_001},
                    "payment": {"type": "payment_record", "payment_record": "pr_known"},
                },
            ],
        },
    )
    payments = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoice-payments.list",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref, "limit": 2},
    )
    payment = payments.output_json["data"]["items"][0]
    assert payment["invoice_ref"] == invoice_ref
    assert payment["payment_type"] == "payment_intent"
    assert payment["payment_intent_ref"] == payment_intent_ref
    payment_record = payments.output_json["data"]["items"][1]
    assert payment_record["payment_type"] == "payment_record"
    assert payment_record["payment_record_ref"].startswith("provider-object:")

    charge_body = {
        **stripe_charge(),
        "id": "ch_known",
        "object": "charge",
        "amount": 10000,
        "amount_captured": 10000,
        "amount_refunded": 0,
        "currency": "usd",
        "paid": True,
        "status": "succeeded",
        "refunded": False,
        "balance_transaction": "txn_known",
        "payment_intent": "pi_known",
    }
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/charges?limit=1&payment_intent=pi_known",
        json={"object": "list", "has_more": False, "data": [charge_body]},
    )
    charges = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.charges.list",
        credential_ref=credential_ref,
        input_json={"limit": 1, "payment_intent_ref": payment_intent_ref},
    )
    charge = charges.output_json["data"]["items"][0]
    assert charge["charge_ref"] == charge_ref
    assert charge["balance_transaction_ref"] == balance_transaction_ref

    httpx_mock.add_response(method="GET", url=f"{STRIPE_ROOT}/charges/ch_known", json=charge_body)
    assert (
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.charges.retrieve",
            credential_ref=credential_ref,
            input_json={"charge_ref": charge_ref},
        ).output_json["data"]["charge_ref"]
        == charge_ref
    )

    balance_transaction_body = {
        **stripe_balance_transaction(),
        "id": "txn_known",
        "object": "balance_transaction",
        "amount": 10000,
        "fee": 320,
        "net": 9680,
        "currency": "usd",
        "type": "charge",
        "status": "available",
        "available_on": 2,
        "created": 1,
    }
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance_transactions/txn_known?expand%5B%5D=source",
        json=balance_transaction_body,
    )
    balance_transaction = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.balance-transactions.retrieve",
        credential_ref=credential_ref,
        input_json={"balance_transaction_ref": balance_transaction_ref},
    )
    assert balance_transaction.output_json["data"]["net"] == 9680

    refund_body = {
        **stripe_refund(),
        "id": "re_known",
        "object": "refund",
        "amount": 1000,
        "currency": "usd",
        "status": "succeeded",
        "charge": "ch_known",
        "balance_transaction": "txn_known",
    }
    httpx_mock.add_response(method="GET", url=f"{STRIPE_ROOT}/refunds/re_known", json=refund_body)
    assert (
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.refunds.retrieve",
            credential_ref=credential_ref,
            input_json={"refund_ref": refund_ref},
        ).output_json["data"]["charge_ref"]
        == charge_ref
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/refunds?limit=1&charge=ch_known",
        json={"object": "list", "has_more": False, "data": [refund_body]},
    )
    assert (
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.refunds.list",
            credential_ref=credential_ref,
            input_json={"limit": 1, "charge_ref": charge_ref},
        ).output_json["data"]["items"][0]["refund_ref"]
        == refund_ref
    )

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance",
        json={
            "object": "balance",
            "livemode": False,
            "available": [{"amount": 9680, "currency": "usd"}],
            "pending": [{"amount": 1000, "currency": "usd"}],
        },
    )
    balance = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.balance.retrieve",
        credential_ref=credential_ref,
        input_json={},
    )
    assert balance.output_json["data"]["available"] == [{"amount": 9680, "currency": "usd"}]


def test_stripe_invoice_payment_requires_typed_allocation_evidence(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_allocation_validation",
    )
    payment_intent_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-intent",
        provider_id="pi_allocation_validation",
    )
    allocation = {
        "id": "inpay_allocation_validation",
        "object": "invoice_payment",
        "amount_requested": 1_000,
        "amount_paid": None,
        "created": 1_700_000_000,
        "currency": "usd",
        "invoice": "in_allocation_validation",
        "is_default": False,
        "livemode": False,
        "payment": {
            "type": "payment_intent",
            "payment_intent": "pi_allocation_validation",
        },
        "status": "open",
        "status_transitions": {"canceled_at": None, "paid_at": None},
    }
    url = f"{STRIPE_ROOT}/invoice_payments?limit=25&invoice=in_allocation_validation"
    httpx_mock.add_response(
        method="GET",
        url=url,
        json={"object": "list", "has_more": False, "data": [allocation]},
    )
    valid = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoice-payments.list",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
    ).output_json["data"]["items"][0]
    assert valid["invoice_ref"] == invoice_ref
    assert valid["payment_intent_ref"] == payment_intent_ref
    assert valid["amount_paid"] is None
    assert valid["status_transitions"] == {"canceled_at": None, "paid_at": None}

    for malformed in (
        {**allocation, "amount_requested": "1000"},
        {**allocation, "invoice": []},
        {**allocation, "payment": {"type": "payment_intent", "payment_intent": 123}},
        {**allocation, "status": "unknown"},
        {**allocation, "status_transitions": "not-an-object"},
    ):
        httpx_mock.add_response(
            method="GET",
            url=url,
            json={"object": "list", "has_more": False, "data": [malformed]},
        )
        with pytest.raises(ConflictError) as invalid_allocation:
            _execute(
                session,
                project_id=project_id,
                action_ref="finance.stripe.invoice-payments.list",
                credential_ref=credential_ref,
                input_json={"invoice_ref": invoice_ref},
            )
        assert (
            invalid_allocation.value.data["provider_error"]["reason_code"] == "malformed_response"
        )
        assert invalid_allocation.value.data["provider_error"]["outcome_unknown"] is False


@pytest.mark.parametrize(
    ("object_type", "action_ref", "provider_id", "url", "body"),
    [
        (
            "stripe.invoice",
            "finance.stripe.invoices.retrieve",
            "in_invalid_present",
            f"{STRIPE_ROOT}/invoices/in_invalid_present?expand%5B%5D=customer",
            {
                **stripe_invoice(),
                "id": "in_invalid_present",
                "object": "invoice",
                "amount_due": "1000",
                "auto_advance": False,
                "collection_method": "send_invoice",
                "created": 1_700_000_000,
                "currency": "usd",
                "livemode": False,
                "status": "open",
            },
        ),
        (
            "stripe.invoice",
            "finance.stripe.invoices.retrieve",
            "in_invalid_null",
            f"{STRIPE_ROOT}/invoices/in_invalid_null?expand%5B%5D=customer",
            {
                **stripe_invoice(),
                "id": "in_invalid_null",
                "object": "invoice",
                "amount_paid_off_stripe": None,
            },
        ),
        (
            "stripe.invoice",
            "finance.stripe.invoices.retrieve",
            "in_invalid_null_overpaid",
            f"{STRIPE_ROOT}/invoices/in_invalid_null_overpaid?expand%5B%5D=customer",
            {
                **stripe_invoice(),
                "id": "in_invalid_null_overpaid",
                "object": "invoice",
                "amount_overpaid": None,
            },
        ),
        (
            "stripe.invoice",
            "finance.stripe.invoices.retrieve",
            "in_invalid_null_collection",
            f"{STRIPE_ROOT}/invoices/in_invalid_null_collection?expand%5B%5D=customer",
            {
                **stripe_invoice(),
                "id": "in_invalid_null_collection",
                "object": "invoice",
                "collection_method": None,
            },
        ),
        (
            "stripe.invoice",
            "finance.stripe.invoices.retrieve",
            "in_invalid_null_advance",
            f"{STRIPE_ROOT}/invoices/in_invalid_null_advance?expand%5B%5D=customer",
            {
                **stripe_invoice(),
                "id": "in_invalid_null_advance",
                "object": "invoice",
                "auto_advance": None,
            },
        ),
        (
            "stripe.charge",
            "finance.stripe.charges.retrieve",
            "ch_invalid_present",
            f"{STRIPE_ROOT}/charges/ch_invalid_present",
            {**stripe_charge(), "id": "ch_invalid_present", "object": "charge", "amount": True},
        ),
        (
            "stripe.charge",
            "finance.stripe.charges.retrieve",
            "ch_invalid_null",
            f"{STRIPE_ROOT}/charges/ch_invalid_null",
            {**stripe_charge(), "id": "ch_invalid_null", "object": "charge", "status": None},
        ),
        (
            "stripe.balance-transaction",
            "finance.stripe.balance-transactions.retrieve",
            "txn_invalid_present",
            f"{STRIPE_ROOT}/balance_transactions/txn_invalid_present?expand%5B%5D=source",
            {
                **stripe_balance_transaction(),
                "id": "txn_invalid_present",
                "object": "balance_transaction",
                "net": "1000",
            },
        ),
        (
            "stripe.balance-transaction",
            "finance.stripe.balance-transactions.retrieve",
            "txn_invalid_null",
            f"{STRIPE_ROOT}/balance_transactions/txn_invalid_null?expand%5B%5D=source",
            {
                **stripe_balance_transaction(),
                "id": "txn_invalid_null",
                "object": "balance_transaction",
                "reporting_category": None,
            },
        ),
        (
            "stripe.balance-transaction",
            "finance.stripe.balance-transactions.retrieve",
            "txn_invalid_null_type",
            f"{STRIPE_ROOT}/balance_transactions/txn_invalid_null_type?expand%5B%5D=source",
            {
                **stripe_balance_transaction(),
                "id": "txn_invalid_null_type",
                "object": "balance_transaction",
                "type": None,
            },
        ),
        (
            "stripe.balance-transaction",
            "finance.stripe.balance-transactions.retrieve",
            "txn_invalid_null_status",
            f"{STRIPE_ROOT}/balance_transactions/txn_invalid_null_status?expand%5B%5D=source",
            {
                **stripe_balance_transaction(),
                "id": "txn_invalid_null_status",
                "object": "balance_transaction",
                "status": None,
            },
        ),
        (
            "stripe.refund",
            "finance.stripe.refunds.retrieve",
            "re_invalid_present",
            f"{STRIPE_ROOT}/refunds/re_invalid_present",
            {**stripe_refund(), "id": "re_invalid_present", "object": "refund", "amount": "1000"},
        ),
        (
            "stripe.dispute",
            "finance.stripe.disputes.retrieve",
            "dp_invalid_present",
            f"{STRIPE_ROOT}/disputes/dp_invalid_present",
            {**stripe_dispute(), "id": "dp_invalid_present", "object": "dispute", "livemode": 0},
        ),
        (
            "stripe.dispute",
            "finance.stripe.disputes.retrieve",
            "dp_invalid_null",
            f"{STRIPE_ROOT}/disputes/dp_invalid_null",
            {**stripe_dispute(), "id": "dp_invalid_null", "object": "dispute", "reason": None},
        ),
        (
            "stripe.dispute",
            "finance.stripe.disputes.retrieve",
            "dp_invalid_null_status",
            f"{STRIPE_ROOT}/disputes/dp_invalid_null_status",
            {
                **stripe_dispute(),
                "id": "dp_invalid_null_status",
                "object": "dispute",
                "status": None,
            },
        ),
    ],
)
def test_stripe_safe_projections_reject_malformed_present_values(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    object_type: str,
    action_ref: str,
    provider_id: str,
    url: str,
    body: dict[str, object],
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    safe_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type=object_type,
        provider_id=provider_id,
    )
    input_key = {
        "stripe.invoice": "invoice_ref",
        "stripe.charge": "charge_ref",
        "stripe.balance-transaction": "balance_transaction_ref",
        "stripe.refund": "refund_ref",
        "stripe.dispute": "dispute_ref",
    }[object_type]
    httpx_mock.add_response(method="GET", url=url, json=body)
    with pytest.raises(ConflictError) as malformed:
        _execute(
            session,
            project_id=project_id,
            action_ref=action_ref,
            credential_ref=credential_ref,
            input_json={input_key: safe_ref},
        )
    assert malformed.value.data["provider_error"]["reason_code"] == "malformed_response"
    assert malformed.value.data["provider_error"]["outcome_unknown"] is False


def test_stripe_remaining_selected_projections_reject_malformed_present_values(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_selected_projection",
    )
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_selected_projection",
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoiceitems?limit=25&invoice=in_selected_projection",
        json={
            "object": "list",
            "has_more": False,
            "data": [
                {
                    **stripe_invoice_item(),
                    "id": "ii_invalid_present",
                    "object": "invoiceitem",
                    "amount": "1000",
                }
            ],
        },
    )
    with pytest.raises(ConflictError) as invalid_invoice_item:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.invoice-items.list",
            credential_ref=credential_ref,
            input_json={"invoice_ref": invoice_ref},
        )
    assert invalid_invoice_item.value.data["provider_error"]["reason_code"] == "malformed_response"

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers/cus_selected_projection",
        json={
            **stripe_customer(),
            "id": "cus_selected_projection",
            "object": "customer",
            "created": "1700000000",
        },
    )
    with pytest.raises(ConflictError) as invalid_customer:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.retrieve",
            credential_ref=credential_ref,
            input_json={"customer_ref": customer_ref},
        )
    assert invalid_customer.value.data["provider_error"]["reason_code"] == "malformed_response"

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance",
        json={
            "object": "balance",
            "livemode": False,
            "available": [
                {
                    "amount": 1_000,
                    "currency": "usd",
                    "source_types": {"card": "1000"},
                }
            ],
            "pending": [],
        },
    )
    with pytest.raises(ConflictError) as invalid_balance:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.balance.retrieve",
            credential_ref=credential_ref,
            input_json={},
        )
    assert invalid_balance.value.data["provider_error"]["reason_code"] == "malformed_response"


def test_stripe_post_rejects_missing_idempotency_and_ambiguous_failure_is_safe(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    with pytest.raises(ValidationError) as missing_key:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.create",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "customer@example.test")
            },
        )
    assert missing_key.value.data["issues"][0]["path"] == "$.idempotency_key"

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        headers={"Request-Id": "req_ambiguous"},
        status_code=500,
        json={
            "id": "cus_raw_server_id",
            "error": {
                "type": "api_error",
                "code": "api_error",
                "message": "raw server detail must not escape",
            },
        },
    )
    with pytest.raises(ConflictError) as failed:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.create",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "customer@example.test")
            },
            idempotency_key="finance-ambiguous-fixture-1",
        )
    provider_error = failed.value.data["provider_error"]
    assert failed.value.data["provider_status_code"] == 500
    assert provider_error["type"] == "api_error"
    assert provider_error["request_id"] == "req_ambiguous"
    assert provider_error["outcome_unknown"] is True
    assert provider_error["retry_safe"] is False
    assert "24-hour retention window" in provider_error["recovery"]
    assert "raw server detail" not in json.dumps(failed.value.data)
    assert "cus_raw_server_id" not in json.dumps(failed.value.data)
    assert STRIPE_SECRET not in json.dumps(failed.value.data)
    failed_call = session.exec(
        select(ActionCall).where(ActionCall.status == ActionCallStatus.FAILED)
    ).one()
    assert failed_call.response_json is not None
    assert failed_call.response_json["outcome_unknown"] is True
    assert failed_call.response_json["provider_error"]["code"] == "api_error"
    assert "cus_raw_server_id" not in json.dumps(failed_call.response_json)

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        status_code=400,
        json={"error": {"type": "invalid_request_error", "param": "email"}},
    )
    with pytest.raises(ConflictError) as invalid_request:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.create",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "customer@example.test")
            },
            idempotency_key="finance-known-4xx-fixture-1",
        )
    known_error = invalid_request.value.data["provider_error"]
    assert known_error["outcome_unknown"] is False
    assert known_error["retry_safe"] is False
    assert "fresh idempotency key" in known_error["recovery"]

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        status_code=429,
        headers={
            "Request-Id": "req_rate_limited",
            "Retry-After": "3",
            "Stripe-Should-Retry": "true",
            "Stripe-Rate-Limited-Reason": "endpoint-rate",
        },
        json={"error": {"type": "rate_limit_error"}},
    )
    with pytest.raises(ConflictError) as rate_limited:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.create",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "customer@example.test")
            },
            idempotency_key="finance-rate-limit-fixture-1",
        )
    rate_error = rate_limited.value.data["provider_error"]
    assert rate_error["request_id"] == "req_rate_limited"
    assert rate_error["should_retry"] is True
    assert rate_error["rate_limited_reason"] == "endpoint-rate"
    assert rate_error["retry_after"] == 3.0

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        json={"id": "cus_malformed", "object": "not_customer"},
    )
    with pytest.raises(ConflictError) as malformed:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.create",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "customer@example.test")
            },
            idempotency_key="finance-malformed-post-fixture-1",
        )
    malformed_error = malformed.value.data["provider_error"]
    assert malformed_error["reason_code"] == "malformed_response"
    assert malformed_error["outcome_unknown"] is True
    assert malformed_error["retry_safe"] is False
    assert "exact same Idempotency-Key" in malformed_error["recovery"]

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        json=[],
    )
    with pytest.raises(ConflictError) as non_mapping:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.create",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "customer@example.test")
            },
            idempotency_key="finance-non-mapping-post-fixture-1",
        )
    non_mapping_error = non_mapping.value.data["provider_error"]
    assert non_mapping_error["reason_code"] == "malformed_response"
    assert non_mapping_error["outcome_unknown"] is True
    assert non_mapping_error["retry_safe"] is False
    assert "retrieve/list" in non_mapping_error["recovery"]

    httpx_mock.add_exception(httpx.ConnectError("fixture connection interruption"))
    with pytest.raises(ConflictError) as network_failure:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.create",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "customer@example.test")
            },
            idempotency_key="finance-network-fixture-1",
        )
    network_error = network_failure.value.data["provider_error"]
    assert network_error["outcome_unknown"] is True
    assert network_error["retry_safe"] is True
    assert "24-hour retention window" in network_error["recovery"]
    assert "exact same Idempotency-Key" in network_error["recovery"]
    assert "fixture connection interruption" not in json.dumps(network_failure.value.data)


def test_stripe_invoice_item_requires_explicit_invoice_and_description(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="finance.stripe.invoice-items.create",
        credential_ref=credential_ref,
        input_json={"customer_ref": "provider-object:customer", "amount": 1, "currency": "usd"},
        idempotency_key="finance-line-validation-fixture-1",
    )
    paths = {entry.path for entry in validation.issues}
    assert {"$.invoice_ref", "$.description"} <= paths


def test_stripe_rejects_malformed_read_shapes_without_inventing_empty_results(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers?limit=1&email=existing%40example.test",
        json={
            "object": "customer",
            "has_more": False,
            "data": [{"id": "cus_invalid_list", "object": "customer"}],
        },
    )
    with pytest.raises(ConflictError) as invalid_list:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.customers.list",
            credential_ref=credential_ref,
            input_json={
                "email": _payload_secret_marker(session, project_id, "existing@example.test"),
                "limit": 1,
            },
        )
    assert invalid_list.value.data["provider_error"]["reason_code"] == "malformed_response"
    assert invalid_list.value.data["provider_error"]["outcome_unknown"] is False

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/balance",
        json={
            "object": "balance",
            "livemode": False,
            "available": [],
            "pending": "not-an-array",
        },
    )
    with pytest.raises(ConflictError) as invalid_balance:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.balance.retrieve",
            credential_ref=credential_ref,
            input_json={},
        )
    assert invalid_balance.value.data["provider_error"]["reason_code"] == "malformed_response"
    assert invalid_balance.value.data["provider_error"]["outcome_unknown"] is False


def test_stripe_settlement_supports_full_and_partial_verified_bank_deposits(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    """A provider-only fixture for the report -> attach settlement path.

    The workflow owns evidence/matching/approval decisions. This fixture proves
    only that one verified full deposit and two verified partial deposits have
    distinct Stripe transport calls and safe reconciliation projections.
    """

    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_settlement",
    )
    full_invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_full_settlement",
    )
    partial_invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_partial_settlement",
    )

    def payment_record(
        *, identifier: str, amount: int, payment_reference: str
    ) -> dict[str, object]:
        money = {"currency": "usd", "value": amount}
        return {
            "id": identifier,
            "object": "payment_record",
            "amount": money,
            "amount_authorized": money,
            "amount_canceled": {"currency": "usd", "value": 0},
            "amount_failed": {"currency": "usd", "value": 0},
            "amount_guaranteed": money,
            "amount_refunded": {"currency": "usd", "value": 0},
            "amount_requested": money,
            "created": 1_700_000_010,
            "customer_details": {
                "customer": "cus_settlement",
                "email": "customer@example.test",
                "name": "PRIVATE CUSTOMER",
            },
            "livemode": False,
            "latest_payment_attempt_record": "par_settlement",
            "payment_method_details": {
                "type": "custom",
                "custom": {"display_name": "Bank transfer"},
            },
            "processor_details": {
                "type": "custom",
                "custom": {"payment_reference": payment_reference},
            },
            "reported_by": "self",
        }

    def invoice(*, identifier: str, paid: int, remaining: int, status: str) -> dict[str, object]:
        return {
            **stripe_invoice(),
            "id": identifier,
            "object": "invoice",
            "customer": "cus_settlement",
            "status": status,
            "collection_method": "send_invoice",
            "currency": "usd",
            "amount_due": 1_000,
            "amount_paid": paid,
            "amount_paid_off_stripe": paid,
            "amount_overpaid": 0,
            "amount_remaining": remaining,
            "total": 1_000,
            "subtotal": 1_000,
            "auto_advance": False,
            "created": 1_700_000_010,
            "livemode": False,
        }

    def invoice_payment(
        *, identifier: str, invoice_identifier: str, amount: int, payment_record: str
    ) -> dict[str, object]:
        return {
            "id": identifier,
            "object": "invoice_payment",
            "invoice": invoice_identifier,
            "amount_requested": amount,
            "amount_paid": amount,
            "created": 1_700_000_010,
            "currency": "usd",
            "is_default": False,
            "livemode": False,
            "status": "paid",
            "status_transitions": {"canceled_at": None, "paid_at": 1_700_000_011},
            "payment": {
                "type": "payment_record",
                "payment_record": payment_record,
            },
        }

    full_reference = "bank-full-0001"
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/payment_records/report_payment",
        json=payment_record(
            identifier="pr_full_settlement", amount=1_000, payment_reference=full_reference
        ),
    )
    full_record = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.payment-records.report",
        credential_ref=credential_ref,
        input_json={
            "customer_ref": customer_ref,
            "amount": 1_000,
            "currency": "usd",
            "initiated_at": 1_700_000_000,
            "guaranteed_at": 1_700_000_010,
            "payment_reference": _payload_secret_marker(session, project_id, full_reference),
        },
        idempotency_key="finance-report-full-settlement-1",
    )
    full_record_data = full_record.output_json["data"]
    assert full_record_data["amount_guaranteed"] == {"currency": "usd", "value": 1_000}
    assert full_record_data["customer_ref"] == customer_ref
    assert (
        full_record_data["payment_reference_sha256"]
        == hashlib.sha256(full_reference.encode("utf-8")).hexdigest()
    )
    assert full_reference not in json.dumps(full_record.model_dump(mode="json"))
    assert "PRIVATE CUSTOMER" not in json.dumps(full_record.model_dump(mode="json"))
    assert parse_qs(httpx_mock.get_requests()[-1].content.decode()) == {
        "amount_requested[currency]": ["usd"],
        "amount_requested[value]": ["1000"],
        "initiated_at": ["1700000000"],
        "outcome": ["guaranteed"],
        "guaranteed[guaranteed_at]": ["1700000010"],
        "payment_method_details[type]": ["custom"],
        "payment_method_details[custom][display_name]": ["Bank transfer"],
        "processor_details[type]": ["custom"],
        "processor_details[custom][payment_reference]": [full_reference],
        "customer_details[customer]": ["cus_settlement"],
    }
    assert httpx_mock.get_requests()[-1].headers["Stripe-Version"] == "2026-08-26.dahlia"

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_records/pr_full_settlement",
        json=payment_record(
            identifier="pr_full_settlement", amount=1_000, payment_reference=full_reference
        ),
    )
    full_record_readback = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.payment-records.retrieve",
        credential_ref=credential_ref,
        input_json={"payment_record_ref": full_record_data["payment_record_ref"]},
    )
    assert (
        full_record_readback.output_json["data"]["payment_record_ref"]
        == full_record_data["payment_record_ref"]
    )

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_full_settlement/attach_payment",
        json=invoice(identifier="in_full_settlement", paid=1_000, remaining=0, status="paid"),
    )
    full_attached = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.attach-payment",
        credential_ref=credential_ref,
        input_json={
            "invoice_ref": full_invoice_ref,
            "payment_record_ref": full_record_data["payment_record_ref"],
        },
        idempotency_key="finance-attach-full-settlement-1",
    )
    assert full_attached.output_json["data"]["status"] == "paid"
    assert full_attached.output_json["data"]["amount_remaining"] == 0
    assert parse_qs(httpx_mock.get_requests()[-1].content.decode()) == {
        "payment_record": ["pr_full_settlement"]
    }

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices/in_full_settlement?expand%5B%5D=customer",
        json=invoice(identifier="in_full_settlement", paid=1_000, remaining=0, status="paid"),
    )
    full_invoice_readback = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.retrieve",
        credential_ref=credential_ref,
        input_json={"invoice_ref": full_invoice_ref},
    )
    full_invoice_data = full_invoice_readback.output_json["data"]
    assert full_invoice_data["invoice_ref"] == full_invoice_ref
    assert full_invoice_data["customer_ref"] == customer_ref
    assert full_invoice_data["status"] == "paid"
    assert full_invoice_data["amount_paid_off_stripe"] == 1_000
    assert full_invoice_data["amount_overpaid"] == 0
    assert full_invoice_data["amount_remaining"] == 0
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoice_payments?limit=25&invoice=in_full_settlement",
        json={
            "object": "list",
            "has_more": False,
            "data": [
                invoice_payment(
                    identifier="inpay_full_settlement",
                    invoice_identifier="in_full_settlement",
                    amount=1_000,
                    payment_record="pr_full_settlement",
                )
            ],
        },
    )
    full_payments_readback = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoice-payments.list",
        credential_ref=credential_ref,
        input_json={"invoice_ref": full_invoice_ref},
    )
    assert full_payments_readback.output_json["data"]["has_more"] is False
    assert [
        item["payment_record_ref"] for item in full_payments_readback.output_json["data"]["items"]
    ] == [full_record_data["payment_record_ref"]]

    partial_record_refs: list[str] = []
    partial_amounts = (400, 600)
    partial_paid = 0
    for index, amount in enumerate(partial_amounts, start=1):
        payment_reference = f"bank-partial-000{index}"
        record_identifier = f"pr_partial_settlement_{index}"
        httpx_mock.add_response(
            method="POST",
            url=f"{STRIPE_ROOT}/payment_records/report_payment",
            json=payment_record(
                identifier=record_identifier,
                amount=amount,
                payment_reference=payment_reference,
            ),
        )
        reported = _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.payment-records.report",
            credential_ref=credential_ref,
            input_json={
                "customer_ref": customer_ref,
                "amount": amount,
                "currency": "usd",
                "initiated_at": 1_700_000_100 + index,
                "guaranteed_at": 1_700_000_200 + index,
                "payment_reference": _payload_secret_marker(session, project_id, payment_reference),
            },
            idempotency_key=f"finance-report-partial-settlement-{index}",
        )
        partial_record_refs.append(reported.output_json["data"]["payment_record_ref"])
        httpx_mock.add_response(
            method="GET",
            url=f"{STRIPE_ROOT}/payment_records/{record_identifier}",
            json=payment_record(
                identifier=record_identifier,
                amount=amount,
                payment_reference=payment_reference,
            ),
        )
        partial_record_readback = _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.payment-records.retrieve",
            credential_ref=credential_ref,
            input_json={"payment_record_ref": partial_record_refs[-1]},
        )
        assert (
            partial_record_readback.output_json["data"]["payment_record_ref"]
            == partial_record_refs[-1]
        )
        partial_paid += amount
        httpx_mock.add_response(
            method="POST",
            url=f"{STRIPE_ROOT}/invoices/in_partial_settlement/attach_payment",
            json=invoice(
                identifier="in_partial_settlement",
                paid=partial_paid,
                remaining=1_000 - partial_paid,
                status="open" if index == 1 else "paid",
            ),
        )
        attached = _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.invoices.attach-payment",
            credential_ref=credential_ref,
            input_json={
                "invoice_ref": partial_invoice_ref,
                "payment_record_ref": partial_record_refs[-1],
            },
            idempotency_key=f"finance-attach-partial-settlement-{index}",
        )
        assert attached.output_json["data"]["amount_remaining"] == 1_000 - partial_paid
        assert attached.output_json["data"]["status"] == ("open" if index == 1 else "paid")
        httpx_mock.add_response(
            method="GET",
            url=(f"{STRIPE_ROOT}/invoices/in_partial_settlement?expand%5B%5D=customer"),
            json=invoice(
                identifier="in_partial_settlement",
                paid=partial_paid,
                remaining=1_000 - partial_paid,
                status="open" if index == 1 else "paid",
            ),
        )
        partial_invoice_readback = _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.invoices.retrieve",
            credential_ref=credential_ref,
            input_json={"invoice_ref": partial_invoice_ref},
        )
        assert (
            partial_invoice_readback.output_json["data"]["amount_paid_off_stripe"] == partial_paid
        )
        assert (
            partial_invoice_readback.output_json["data"]["amount_remaining"] == 1_000 - partial_paid
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{STRIPE_ROOT}/invoice_payments?limit=25&invoice=in_partial_settlement",
            json={
                "object": "list",
                "has_more": False,
                "data": [
                    invoice_payment(
                        identifier="inpay_partial_settlement_1",
                        invoice_identifier="in_partial_settlement",
                        amount=partial_amounts[0],
                        payment_record="pr_partial_settlement_1",
                    ),
                    *(
                        [
                            invoice_payment(
                                identifier="inpay_partial_settlement_2",
                                invoice_identifier="in_partial_settlement",
                                amount=partial_amounts[1],
                                payment_record="pr_partial_settlement_2",
                            )
                        ]
                        if index == 2
                        else []
                    ),
                ],
            },
        )
        partial_payments_readback = _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.invoice-payments.list",
            credential_ref=credential_ref,
            input_json={"invoice_ref": partial_invoice_ref},
        )
        assert partial_payments_readback.output_json["data"]["has_more"] is False
        assert [
            item["payment_record_ref"]
            for item in partial_payments_readback.output_json["data"]["items"]
        ] == partial_record_refs

    assert all("/send" not in str(request.url) for request in httpx_mock.get_requests())


def test_stripe_settlement_attach_unknown_recovers_known_report_without_repeating_it(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    """A failed attach is reconciled through reads, never a second report POST."""

    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_recovery_settlement",
    )
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_recovery_settlement",
    )
    payment_reference = "bank-recovery-0001"
    payment_record_body = {
        "id": "pr_recovery_settlement",
        "object": "payment_record",
        "amount": {"currency": "usd", "value": 1_000},
        "amount_authorized": {"currency": "usd", "value": 1_000},
        "amount_canceled": {"currency": "usd", "value": 0},
        "amount_failed": {"currency": "usd", "value": 0},
        "amount_guaranteed": {"currency": "usd", "value": 1_000},
        "amount_refunded": {"currency": "usd", "value": 0},
        "amount_requested": {"currency": "usd", "value": 1_000},
        "created": 1_700_000_500,
        "customer_details": {"customer": "cus_recovery_settlement"},
        "livemode": False,
        "processor_details": {
            "type": "custom",
            "custom": {"payment_reference": payment_reference},
        },
        "reported_by": "self",
    }
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/payment_records/report_payment",
        json=payment_record_body,
    )
    reported = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.payment-records.report",
        credential_ref=credential_ref,
        input_json={
            "customer_ref": customer_ref,
            "amount": 1_000,
            "currency": "usd",
            "initiated_at": 1_700_000_400,
            "guaranteed_at": 1_700_000_500,
            "payment_reference": _payload_secret_marker(session, project_id, payment_reference),
        },
        idempotency_key="finance-report-recovery-settlement-1",
    )
    payment_record_ref = reported.output_json["data"]["payment_record_ref"]

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_recovery_settlement/attach_payment",
        headers={"Request-Id": "req_attach_recovery"},
        status_code=500,
        json={"error": {"type": "api_error", "code": "api_error"}},
    )
    with pytest.raises(ConflictError) as attach_failure:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.invoices.attach-payment",
            credential_ref=credential_ref,
            input_json={
                "invoice_ref": invoice_ref,
                "payment_record_ref": payment_record_ref,
            },
            idempotency_key="finance-attach-recovery-settlement-1",
        )
    assert attach_failure.value.data["provider_error"]["outcome_unknown"] is True
    assert attach_failure.value.data["provider_error"]["retry_safe"] is False

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_records/pr_recovery_settlement",
        json=payment_record_body,
    )
    record_readback = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.payment-records.retrieve",
        credential_ref=credential_ref,
        input_json={"payment_record_ref": payment_record_ref},
    )
    assert record_readback.output_json["data"]["payment_record_ref"] == payment_record_ref

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices/in_recovery_settlement?expand%5B%5D=customer",
        json={
            **stripe_invoice(),
            "id": "in_recovery_settlement",
            "object": "invoice",
            "customer": "cus_recovery_settlement",
            "status": "open",
            "collection_method": "send_invoice",
            "currency": "usd",
            "amount_due": 1_000,
            "amount_paid": 0,
            "amount_paid_off_stripe": 0,
            "amount_overpaid": 0,
            "amount_remaining": 1_000,
            "auto_advance": False,
            "created": 1_700_000_500,
            "livemode": False,
        },
    )
    invoice_readback = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.retrieve",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
    )
    assert invoice_readback.output_json["data"]["status"] == "open"
    assert invoice_readback.output_json["data"]["amount_remaining"] == 1_000

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoice_payments?limit=25&invoice=in_recovery_settlement",
        json={"object": "list", "has_more": False, "data": []},
    )
    payments_readback = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoice-payments.list",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
    )
    assert payments_readback.output_json["data"] == {"items": [], "has_more": False}

    requests = httpx_mock.get_requests()
    report_requests = [
        request for request in requests if "/payment_records/report_payment" in str(request.url)
    ]
    assert len(report_requests) == 1
    assert report_requests[0].headers["Idempotency-Key"] == "finance-report-recovery-settlement-1"
    assert all("/send" not in str(request.url) for request in requests)


def test_stripe_settlement_actions_read_safe_evidence_and_mark_only_full_paid_out_of_band(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_read_settlement",
    )
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_read_settlement",
    )
    payment_intent_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-intent",
        provider_id="pi_read_settlement",
    )
    payment_record_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-record",
        provider_id="pr_read_settlement",
    )

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_intents/pi_read_settlement",
        json={
            "id": "pi_read_settlement",
            "object": "payment_intent",
            "created": 1_700_000_000,
            "status": "succeeded",
            "amount": 700,
            "amount_received": 700,
            "currency": "usd",
            "customer": "cus_read_settlement",
            "latest_charge": "ch_read_settlement",
            "livemode": False,
            "client_secret": "must-not-leak",
        },
    )
    payment_intent = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.payment-intents.retrieve",
        credential_ref=credential_ref,
        input_json={"payment_intent_ref": payment_intent_ref},
    )
    intent_data = payment_intent.output_json["data"]
    assert intent_data["status"] == "succeeded"
    assert intent_data["customer_ref"] == customer_ref
    assert intent_data["latest_charge_ref"].startswith("provider-object:")
    assert "must-not-leak" not in json.dumps(payment_intent.model_dump(mode="json"))

    external_reference = "bank-read-0001"
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_records/pr_read_settlement",
        json={
            "id": "pr_read_settlement",
            "object": "payment_record",
            "amount": {"currency": "usd", "value": 700},
            "amount_authorized": {"currency": "usd", "value": 700},
            "amount_canceled": {"currency": "usd", "value": 0},
            "amount_failed": {"currency": "usd", "value": 0},
            "amount_guaranteed": {"currency": "usd", "value": 700},
            "amount_refunded": {"currency": "usd", "value": 0},
            "amount_requested": {"currency": "usd", "value": 700},
            "customer_details": {
                "customer": "cus_read_settlement",
                "email": "private@example.test",
            },
            "processor_details": {
                "type": "custom",
                "custom": {"payment_reference": external_reference},
            },
            "created": 1_700_000_000,
            "reported_by": "self",
            "livemode": False,
        },
    )
    payment_record = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.payment-records.retrieve",
        credential_ref=credential_ref,
        input_json={"payment_record_ref": payment_record_ref},
    )
    record_data = payment_record.output_json["data"]
    assert record_data["amount_guaranteed"] == {"currency": "usd", "value": 700}
    assert record_data["customer_ref"] == customer_ref
    assert (
        record_data["payment_reference_sha256"]
        == hashlib.sha256(external_reference.encode("utf-8")).hexdigest()
    )
    assert external_reference not in json.dumps(payment_record.model_dump(mode="json"))
    assert "private@example.test" not in json.dumps(payment_record.model_dump(mode="json"))

    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_read_settlement/pay",
        json={
            **stripe_invoice(),
            "id": "in_read_settlement",
            "object": "invoice",
            "customer": "cus_read_settlement",
            "status": "paid",
            "collection_method": "send_invoice",
            "currency": "usd",
            "amount_due": 700,
            "amount_paid": 700,
            "amount_paid_off_stripe": 700,
            "amount_overpaid": 0,
            "amount_remaining": 0,
            "livemode": False,
        },
    )
    marked = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.invoices.mark-paid-out-of-band",
        credential_ref=credential_ref,
        input_json={"invoice_ref": invoice_ref},
        idempotency_key="finance-mark-out-of-band-1",
    )
    assert marked.output_json["data"]["amount_paid_off_stripe"] == 700
    assert marked.output_json["data"]["amount_overpaid"] == 0
    assert parse_qs(httpx_mock.get_requests()[-1].content.decode()) == {
        "paid_out_of_band": ["true"]
    }


def test_stripe_payment_intent_allows_sparse_response_and_rejects_malformed_present_money(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    payment_intent_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-intent",
        provider_id="pi_sparse",
    )
    sparse = {
        "id": "pi_sparse",
        "object": "payment_intent",
        "created": 1_700_000_000,
        "livemode": False,
        "status": "succeeded",
    }
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_intents/pi_sparse",
        json=sparse,
    )
    result = _execute(
        session,
        project_id=project_id,
        action_ref="finance.stripe.payment-intents.retrieve",
        credential_ref=credential_ref,
        input_json={"payment_intent_ref": payment_intent_ref},
    )
    assert result.output_json["data"] == {
        "payment_intent_ref": payment_intent_ref,
        "created": 1_700_000_000,
        "livemode": False,
        "status": "succeeded",
    }

    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/payment_intents/pi_sparse",
        json={**sparse, "amount": "700"},
    )
    with pytest.raises(ConflictError) as malformed:
        _execute(
            session,
            project_id=project_id,
            action_ref="finance.stripe.payment-intents.retrieve",
            credential_ref=credential_ref,
            input_json={"payment_intent_ref": payment_intent_ref},
        )
    assert malformed.value.data["provider_error"]["reason_code"] == "malformed_response"
    assert malformed.value.data["provider_error"]["outcome_unknown"] is False


@pytest.mark.parametrize(
    ("action", "payload", "expected_code"),
    [
        ("stripe.invoices.attach-payment", {"invoice_ref": "provider-object:invoice"}, "one_of"),
        (
            "stripe.invoices.attach-payment",
            {
                "invoice_ref": "provider-object:invoice",
                "payment_intent_ref": "provider-object:intent",
                "payment_record_ref": "provider-object:record",
            },
            "one_of",
        ),
        (
            "stripe.payment-records.report",
            {
                "customer_ref": "provider-object:customer",
                "amount": 0,
                "currency": "USD",
                "initiated_at": 2,
                "guaranteed_at": 1,
                "payment_reference": "not-a-secret-marker",
            },
            "range",
        ),
        (
            "stripe.payment-records.report",
            {
                "customer_ref": "provider-object:customer",
                "amount": 100,
                "currency": "usd",
                "initiated_at": 1,
                "guaranteed_at": 2,
                "payment_reference": "not-a-secret-marker",
            },
            "payload_secret_ref_required",
        ),
    ],
)
def test_stripe_settlement_validation_rejects_ambiguous_or_unverified_inputs(
    session: Session,
    project_id: int,
    action: str,
    payload: dict,
    expected_code: str,
) -> None:
    result = ActionRepository(session).validate(
        project_id=project_id,
        action_ref=f"finance.{action}",
        input_json=payload,
        idempotency_key="finance-settlement-validation",
    )
    assert expected_code in {item.code for item in result.issues}


@pytest.mark.parametrize(
    ("action_ref", "input_json", "url", "body"),
    [
        (
            "finance.stripe.invoices.mark-paid-out-of-band",
            {"invoice_ref": "invoice_ref"},
            f"{STRIPE_ROOT}/invoices/in_unknown/pay",
            {"id": "in_unknown", "object": "invoice"},
        ),
        (
            "finance.stripe.invoices.attach-payment",
            {"invoice_ref": "invoice_ref", "payment_intent_ref": "payment_intent_ref"},
            f"{STRIPE_ROOT}/invoices/in_unknown/attach_payment",
            {"id": "in_unknown", "object": "invoice"},
        ),
        (
            "finance.stripe.payment-records.report",
            {
                "customer_ref": "customer_ref",
                "amount": 100,
                "currency": "usd",
                "initiated_at": 1,
                "guaranteed_at": 2,
                "payment_reference": "payment_reference",
            },
            f"{STRIPE_ROOT}/payment_records/report_payment",
            {"id": "pr_unknown", "object": "payment_record"},
        ),
    ],
)
def test_stripe_settlement_post_normalization_failure_is_outcome_unknown(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    action_ref: str,
    input_json: dict[str, str | int],
    url: str,
    body: dict[str, str],
) -> None:
    credential_ref = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.invoice",
        provider_id="in_unknown",
    )
    customer_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_id="cus_unknown",
    )
    payment_intent_ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-intent",
        provider_id="pi_unknown",
    )
    resolved_input = {
        key: (
            invoice_ref
            if value == "invoice_ref"
            else customer_ref
            if value == "customer_ref"
            else payment_intent_ref
            if value == "payment_intent_ref"
            else _payload_secret_marker(session, project_id, "bank-normalization-fixture-reference")
            if value == "payment_reference"
            else value
        )
        for key, value in input_json.items()
    }
    httpx_mock.add_response(method="POST", url=url, json=body)

    def fail_normalization(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("fixture post-response normalization failure")

    monkeypatch.setattr("stackos.actions.stripe._safe_response", fail_normalization)
    with pytest.raises(ConflictError) as failure:
        _execute(
            session,
            project_id=project_id,
            action_ref=action_ref,
            credential_ref=credential_ref,
            input_json=resolved_input,
            idempotency_key=f"finance-unknown-{action_ref.rsplit('.', 1)[-1]}",
        )
    assert failure.value.data["provider_error"]["outcome_unknown"] is True
    assert failure.value.data["provider_error"]["retry_safe"] is False
    if action_ref == "finance.stripe.payment-records.report":
        recovery = failure.value.data["provider_error"]["recovery"]
        assert "finance.stripe.payment-records.retrieve" in recovery
        assert "payment_reference_sha256" in recovery
        assert "listing is temporarily unavailable in StackOS" in recovery
        assert "A missing ref or uncertain evidence requires owner/provider recovery" in recovery
        assert "never create a replacement report" in recovery
