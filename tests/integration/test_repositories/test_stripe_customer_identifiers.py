"""Synthetic Stripe memo, complete-field replacement and customer tax-ID proof."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.db.models import ProviderObjectReference
from stackos.repositories.base import ConflictError, ValidationError
from tests.helpers.stripe import stripe_customer, stripe_invoice
from tests.integration.test_repositories.test_stripe_actions import (
    STRIPE_ROOT,
    _execute,
    _payload_secret_marker,
    _safe_ref,
    _stripe_credential_ref,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tax_id(**changes: object) -> dict:
    return {
        "id": "txi_synthetic_1",
        "object": "tax_id",
        "customer": "cus_synthetic",
        "type": "eu_vat",
        "value": "DE123456789",
        "country": "DE",
        "created": 1790140800,
        "livemode": False,
        "verification": {"status": "pending", "verified_name": None, "verified_address": None},
        **changes,
    }


def _setup(
    session: Session, project_id: int, httpx_mock: HTTPXMock, kind: str = "customer"
) -> tuple[str, str]:
    credential = _stripe_credential_ref(session, project_id, httpx_mock)
    ref = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential,
        object_type=f"stripe.{kind}",
        provider_id="cus_synthetic" if kind == "customer" else "in_synthetic",
    )
    return credential, ref


@pytest.mark.parametrize("memo", ["Synthetic project memo", ""])
def test_invoice_memo_only_and_clear_need_independent_readback(
    session: Session, project_id: int, httpx_mock: HTTPXMock, memo: str
) -> None:
    credential, invoice_ref = _setup(session, project_id, httpx_mock, "invoice")
    payload = {
        "invoice_ref": invoice_ref,
        "description": _payload_secret_marker(session, project_id, memo) if memo else "",
    }
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_synthetic",
        json=stripe_invoice(id="in_synthetic", status="open", description=memo),
    )
    updated = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.invoices.update",
        input_json=payload,
        idempotency_key="synthetic-memo",
    )
    assert parse_qs(httpx_mock.get_requests()[-1].content.decode(), keep_blank_values=True) == {
        "description": [memo]
    }
    assert updated.output_json["data"]["description_sha256"] == _digest(memo)
    assert updated.action_call.request_json == payload
    # A separate GET can contradict the mutation echo. The connector reports it
    # rather than labeling the update verified or repeating the POST.
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices/in_synthetic?expand%5B%5D=customer",
        json=stripe_invoice(
            id="in_synthetic", status="open", description="Provider readback differs"
        ),
    )
    read = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.invoices.retrieve",
        input_json={"invoice_ref": invoice_ref, "include_business_details": True},
    )
    assert (
        read.output_json["data"]["description_sha256"]
        != updated.output_json["data"]["description_sha256"]
    )
    assert (
        read.output_json["data"]["business_details"]["description"] == "Provider readback differs"
    )
    assert len([r for r in httpx_mock.get_requests() if r.method == "POST"]) == 1


@pytest.mark.parametrize("kind", ["customer", "invoice"])
@pytest.mark.parametrize("clear", [False, True])
def test_custom_fields_send_complete_ordered_replacement_or_explicit_clear(
    session: Session, project_id: int, httpx_mock: HTTPXMock, kind: str, clear: bool
) -> None:
    credential, ref = _setup(session, project_id, httpx_mock, kind)
    fields = (
        []
        if clear
        else [
            {"name": "Project", "value": "Synthetic retained project"},
            {"name": "Registration", "value": "REG-SYNTHETIC"},
        ]
    )
    marked = [
        {key: _payload_secret_marker(session, project_id, value) for key, value in field.items()}
        for field in fields
    ]
    payload = {
        f"{kind}_ref": ref,
        **(
            {"invoice_settings": {"custom_fields": marked}}
            if kind == "customer"
            else {"custom_fields": marked}
        ),
    }
    path = "customers/cus_synthetic" if kind == "customer" else "invoices/in_synthetic"
    obj = (
        stripe_customer(
            id="cus_synthetic",
            invoice_settings={"custom_fields": fields, "default_payment_method": "pm_private_omit"},
        )
        if kind == "customer"
        else stripe_invoice(id="in_synthetic", custom_fields=fields)
    )
    httpx_mock.add_response(method="POST", url=f"{STRIPE_ROOT}/{path}", json=obj)
    updated = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref=f"finance.stripe.{kind}s.update",
        input_json=payload,
        idempotency_key="synthetic-custom-fields",
    )
    prefix = "invoice_settings[custom_fields]" if kind == "customer" else "custom_fields"
    expected_form = (
        {prefix: [""]}
        if clear
        else {
            f"{prefix}[{i}][{key}]": [entry[key]]
            for i, entry in enumerate(fields)
            for key in ("name", "value")
        }
    )
    assert (
        parse_qs(httpx_mock.get_requests()[-1].content.decode(), keep_blank_values=True)
        == expected_form
    )
    expected_hashes = [
        {f"{key}_sha256": _digest(entry[key]) for key in ("name", "value")} for entry in fields
    ]
    data = updated.output_json["data"]
    assert (data["invoice_settings"] if kind == "customer" else data)[
        "custom_fields"
    ] == expected_hashes
    assert "REG-SYNTHETIC" not in json.dumps(updated.model_dump(mode="json"))
    url = f"{STRIPE_ROOT}/{path}" + ("?expand%5B%5D=customer" if kind == "invoice" else "")
    httpx_mock.add_response(method="GET", url=url, json=obj)
    observed = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref=f"finance.stripe.{kind}s.retrieve",
        input_json={f"{kind}_ref": ref, "include_business_details": True},
    ).output_json["data"]
    details = observed["business_details"]
    assert (details["invoice_settings"] if kind == "customer" else details)[
        "custom_fields"
    ] == fields
    assert "pm_private_omit" not in json.dumps(observed)
    assert (observed["invoice_settings"] if kind == "customer" else observed)[
        "custom_fields"
    ] == expected_hashes


def test_tax_id_pages_support_exact_duplicate_preflight_and_customer_bound_cursor(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    credential, customer = _setup(session, project_id, httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers/cus_synthetic/tax_ids?limit=1",
        json={"object": "list", "has_more": True, "data": [_tax_id()]},
    )
    first = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.customers.tax-ids.list",
        input_json={"customer_ref": customer, "limit": 1},
    ).output_json["data"]
    assert first["has_more"] is True
    assert first["items"][0]["value_sha256"] == _digest("DE123456789")
    assert first["items"][0]["verification"]["status"] == "pending"
    cursor = first["next_page_cursor"]
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers/cus_synthetic/tax_ids?limit=1&starting_after=txi_synthetic_1",
        json={
            "object": "list",
            "has_more": False,
            "data": [_tax_id(id="txi_synthetic_2", value="DE987654321", verification=None)],
        },
    )
    second = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.customers.tax-ids.list",
        input_json={"customer_ref": customer, "limit": 1, "page_cursor": cursor},
    ).output_json["data"]
    assert second["has_more"] is False and "next_page_cursor" not in second
    assert second["items"][0]["verification"] is None
    assert second["items"][0]["value_sha256"] == _digest("DE987654321")
    assert "DE987654321" not in json.dumps(second)
    other = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential,
        object_type="stripe.customer",
        provider_id="cus_other_synthetic",
    )
    before = len(httpx_mock.get_requests())
    for action, extra in (("list", {"page_cursor": cursor}), ("retrieve", {"tax_id_ref": cursor})):
        with pytest.raises(ConflictError) as failure:
            _execute(
                session,
                project_id=project_id,
                credential_ref=credential,
                action_ref=f"finance.stripe.customers.tax-ids.{action}",
                input_json={"customer_ref": other, **extra},
            )
        assert "selected customer" in json.dumps(failure.value.data)
    assert len(httpx_mock.get_requests()) == before


def test_tax_id_create_is_single_idempotent_post_and_readback_observes_actual_verification(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    credential, customer = _setup(session, project_id, httpx_mock)
    payload = {
        "customer_ref": customer,
        "type": "eu_vat",
        "value": _payload_secret_marker(session, project_id, "DE123456789"),
    }
    httpx_mock.add_response(
        method="POST", url=f"{STRIPE_ROOT}/customers/cus_synthetic/tax_ids", json=_tax_id()
    )
    created = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.customers.tax-ids.create",
        input_json=payload,
        idempotency_key="synthetic-tax-id",
    )
    assert created.output_json["data"]["verification"]["status"] == "pending"
    assert parse_qs(httpx_mock.get_requests()[-1].content.decode()) == {
        "type": ["eu_vat"],
        "value": ["DE123456789"],
    }
    replay = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.customers.tax-ids.create",
        input_json=payload,
        idempotency_key="synthetic-tax-id",
    )
    assert replay.output_json == created.output_json
    assert len([r for r in httpx_mock.get_requests() if r.method == "POST"]) == 1
    assert "DE123456789" not in json.dumps(created.model_dump(mode="json"))
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers/cus_synthetic/tax_ids/txi_synthetic_1",
        json=_tax_id(
            verification={
                "status": "verified",
                "verified_name": "Synthetic Company",
                "verified_address": None,
            }
        ),
    )
    read = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.customers.tax-ids.retrieve",
        input_json={
            "customer_ref": customer,
            "tax_id_ref": created.output_json["data"]["tax_id_ref"],
            "include_business_details": True,
        },
    ).output_json["data"]
    assert read["verification"] == {
        "status": "verified",
        "verified_name_sha256": _digest("Synthetic Company"),
        "verified_address_sha256": None,
    }
    assert read["business_details"]["value"] == "DE123456789"


@pytest.mark.parametrize(
    "invoice_fields",
    [
        {},
        {"description": None, "custom_fields": None, "customer_tax_ids": None},
        {
            "description": "",
            "custom_fields": [],
            "customer_tax_ids": [{"type": "eu_vat", "value": None}],
        },
    ],
)
def test_invoice_identifier_snapshot_preserves_missing_null_and_empty(
    session: Session, project_id: int, httpx_mock: HTTPXMock, invoice_fields: dict
) -> None:
    credential, invoice = _setup(session, project_id, httpx_mock, "invoice")
    obj = stripe_invoice(id="in_synthetic")
    for key in ("description", "custom_fields", "customer_tax_ids"):
        obj.pop(key, None)
    obj.update(invoice_fields)
    httpx_mock.add_response(
        method="GET", url=f"{STRIPE_ROOT}/invoices/in_synthetic?expand%5B%5D=customer", json=obj
    )
    read = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.invoices.retrieve",
        input_json={"invoice_ref": invoice},
    ).output_json["data"]
    for key in ("description", "custom_fields", "customer_tax_ids"):
        safe_key = "description_sha256" if key == "description" else key
        if key not in invoice_fields:
            assert safe_key not in read
        elif invoice_fields[key] is None:
            assert read[safe_key] is None
    if invoice_fields.get("customer_tax_ids"):
        assert read["customer_tax_ids"] == [{"type": "eu_vat", "value_sha256": None}]
        assert "tax_id_ref" not in read["customer_tax_ids"][0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "private plaintext"),
        ("description", "x" * 1501),
        ("custom_fields", [{"name": "private", "value": "private"}]),
        ("custom_fields", [{"name": "N", "value": "V"}] * 5),
    ],
)
def test_memo_and_custom_fields_reject_unreviewed_input_before_http(
    session: Session, project_id: int, httpx_mock: HTTPXMock, field: str, value: object
) -> None:
    credential, invoice = _setup(session, project_id, httpx_mock, "invoice")
    before = len(httpx_mock.get_requests())
    with pytest.raises(ValidationError):
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential,
            action_ref="finance.stripe.invoices.update",
            input_json={"invoice_ref": invoice, field: value},
            idempotency_key="synthetic-invalid",
        )
    assert len(httpx_mock.get_requests()) == before


def test_invoice_provider_immutability_error_never_triggers_lifecycle_fallback(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    credential, invoice = _setup(session, project_id, httpx_mock, "invoice")
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/invoices/in_synthetic",
        status_code=400,
        json={
            "error": {
                "type": "invalid_request_error",
                "code": "invoice_not_editable",
                "message": "Invoice is not editable",
            }
        },
    )
    with pytest.raises(ConflictError):
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential,
            action_ref="finance.stripe.invoices.update",
            input_json={"invoice_ref": invoice, "description": ""},
            idempotency_key="synthetic-immutable",
        )
    assert len([r for r in httpx_mock.get_requests() if r.method == "POST"]) == 1
    assert all(
        "/send" not in str(r.url) and "/finalize" not in str(r.url) and "/void" not in str(r.url)
        for r in httpx_mock.get_requests()
    )


@pytest.mark.parametrize("wrong", ["customer", "value", "verification"])
def test_tax_id_unreconciled_post_stays_unknown_without_another_create(
    session: Session, project_id: int, httpx_mock: HTTPXMock, wrong: str
) -> None:
    credential, customer = _setup(session, project_id, httpx_mock)
    obj = _tax_id()
    if wrong == "customer":
        obj["customer"] = "cus_wrong_synthetic"
    elif wrong == "value":
        obj.pop("value")
    else:
        obj["verification"] = {"status": "invented_verified"}
    httpx_mock.add_response(
        method="POST", url=f"{STRIPE_ROOT}/customers/cus_synthetic/tax_ids", json=obj
    )
    with pytest.raises(ConflictError) as failure:
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential,
            action_ref="finance.stripe.customers.tax-ids.create",
            input_json={
                "customer_ref": customer,
                "type": "eu_vat",
                "value": _payload_secret_marker(session, project_id, "DE123456789"),
            },
            idempotency_key="synthetic-unknown-tax",
        )
    assert failure.value.data["provider_error"]["outcome_unknown"] is True
    assert failure.value.data["provider_error"]["retry_safe"] is False
    assert len([r for r in httpx_mock.get_requests() if r.method == "POST"]) == 1


def test_tax_ids_reject_other_account_customer_before_http(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    credential, customer = _setup(session, project_id, httpx_mock)
    row = session.exec(
        select(ProviderObjectReference).where(ProviderObjectReference.safe_ref == customer)
    ).one()
    row.provider_account_id = "acct_other_synthetic"
    session.add(row)
    session.commit()
    before = len(httpx_mock.get_requests())
    with pytest.raises(ConflictError):
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential,
            action_ref="finance.stripe.customers.tax-ids.list",
            input_json={"customer_ref": customer},
        )
    assert len(httpx_mock.get_requests()) == before


@pytest.mark.parametrize("status", ["unverified", "unavailable", None, "missing"])
def test_tax_id_read_preserves_observed_verification_without_inference(
    session: Session, project_id: int, httpx_mock: HTTPXMock, status: str | None
) -> None:
    credential, customer = _setup(session, project_id, httpx_mock)
    obj = _tax_id(verification=None if status is None else {"status": status})
    if status == "missing":
        obj.pop("verification")
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/customers/cus_synthetic/tax_ids?limit=25",
        json={"object": "list", "has_more": False, "data": [obj]},
    )
    data = _execute(
        session,
        project_id=project_id,
        credential_ref=credential,
        action_ref="finance.stripe.customers.tax-ids.list",
        input_json={"customer_ref": customer},
    ).output_json["data"]["items"][0]
    if status == "missing":
        assert "verification" not in data
    else:
        assert data["verification"] == (None if status is None else {"status": status})


@pytest.mark.parametrize("kind,key,length", [("invoice", "name", 41), ("customer", "value", 141)])
def test_custom_fields_validate_materialized_limits_before_http(
    session: Session, project_id: int, httpx_mock: HTTPXMock, kind: str, key: str, length: int
) -> None:
    credential, ref = _setup(session, project_id, httpx_mock, kind)
    fields = [
        {
            "name": _payload_secret_marker(session, project_id, "Name"),
            "value": _payload_secret_marker(session, project_id, "Value"),
        }
    ]
    fields[0][key] = _payload_secret_marker(session, project_id, "x" * length)
    payload = {
        f"{kind}_ref": ref,
        **(
            {"invoice_settings": {"custom_fields": fields}}
            if kind == "customer"
            else {"custom_fields": fields}
        ),
    }
    before = len(httpx_mock.get_requests())
    with pytest.raises((ValidationError, ConflictError)):
        _execute(
            session,
            project_id=project_id,
            credential_ref=credential,
            action_ref=f"finance.stripe.{kind}s.update",
            input_json=payload,
            idempotency_key="synthetic-too-long",
        )
    assert len(httpx_mock.get_requests()) == before


def test_tax_id_value_requires_secret_and_customer_defaults_forbid_payment_changes(
    session: Session, project_id: int, httpx_mock: HTTPXMock
) -> None:
    credential, customer = _setup(session, project_id, httpx_mock)
    before = len(httpx_mock.get_requests())
    for action, payload in (
        ("tax-ids.create", {"type": "eu_vat", "value": "DE123456789"}),
        (
            "update",
            {"invoice_settings": {"custom_fields": [], "default_payment_method": "pm_not_allowed"}},
        ),
    ):
        with pytest.raises(ValidationError):
            _execute(
                session,
                project_id=project_id,
                credential_ref=credential,
                action_ref=f"finance.stripe.customers.{action}",
                input_json={"customer_ref": customer, **payload},
                idempotency_key=f"synthetic-denied-{action}",
            )
    assert len(httpx_mock.get_requests()) == before
