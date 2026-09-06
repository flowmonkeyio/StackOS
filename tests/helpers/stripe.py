"""Explicit Stripe fixtures, never an automatic response-repair layer.

Source: Stripe public OpenAPI 2026-08-26.dahlia, commit
9ac29c7795ab21c7711b4bc25bb2dd739552a5fa/latest/openapi.spec3.json.
https://github.com/stripe/openapi/blob/9ac29c7795ab21c7711b4bc25bb2dd739552a5fa/latest/openapi.spec3.json

Each constructor represents a named valid provider object. Tests deliberately
override facts or remove fields afterwards when exercising malformed responses.
No mock transport or runtime automatically completes an arbitrary response.
"""

from typing import Any


def stripe_customer(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "cus_fixture",
        "object": "customer",
        "created": 1700000000,
        "livemode": False,
        **overrides,
    }


def stripe_invoice(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "in_fixture",
        "object": "invoice",
        "customer": "cus_fixture",
        "amount_due": 1000,
        "amount_paid": 0,
        "amount_remaining": 1000,
        "amount_overpaid": 0,
        "amount_paid_off_stripe": 0,
        "amount_shipping": 0,
        "subtotal": 1000,
        "total": 1000,
        "currency": "usd",
        "status": "draft",
        "collection_method": "send_invoice",
        "auto_advance": False,
        "created": 1700000000,
        "livemode": False,
        "attempt_count": 0,
        "attempted": False,
        "automatic_tax": {"enabled": False},
        "default_tax_rates": [],
        "discounts": [],
        "issuer": {"type": "self"},
        "lines": {
            "object": "list",
            "data": [],
            "has_more": False,
            "url": "/v1/invoices/in_fixture/lines",
        },
        "payment_settings": {},
        "period_end": 1700000000,
        "period_start": 1700000000,
        "post_payment_credit_notes_amount": 0,
        "pre_payment_credit_notes_amount": 0,
        "starting_balance": 0,
        "status_transitions": {},
        **overrides,
    }


def stripe_invoice_item(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "ii_fixture",
        "object": "invoiceitem",
        "customer": "cus_fixture",
        "amount": 1000,
        "currency": "usd",
        "date": 1700000000,
        "discountable": True,
        "livemode": False,
        "period": {"start": 1700000000, "end": 1700000000},
        "proration": False,
        "quantity": 1,
        "quantity_decimal": "1",
        **overrides,
    }


def stripe_charge(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "ch_fixture",
        "object": "charge",
        "amount": 1000,
        "amount_captured": 1000,
        "amount_refunded": 0,
        "currency": "usd",
        "paid": True,
        "refunded": False,
        "livemode": False,
        "status": "succeeded",
        "created": 1700000000,
        "billing_details": {},
        "captured": True,
        "disputed": False,
        "metadata": {},
        **overrides,
    }


def stripe_balance_transaction(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "txn_fixture",
        "object": "balance_transaction",
        "amount": 1000,
        "fee": 30,
        "net": 970,
        "currency": "usd",
        "type": "charge",
        "reporting_category": "charge",
        "status": "available",
        "balance_type": "payments",
        "available_on": 1700000000,
        "created": 1700000000,
        "fee_details": [],
        **overrides,
    }


def stripe_refund(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "re_fixture",
        "object": "refund",
        "amount": 1000,
        "currency": "usd",
        "created": 1700000000,
        **overrides,
    }


def stripe_dispute(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "dp_fixture",
        "object": "dispute",
        "amount": 1000,
        "currency": "usd",
        "status": "needs_response",
        "reason": "fraudulent",
        "created": 1700000000,
        "livemode": False,
        "charge": "ch_fixture",
        "balance_transactions": [],
        "enhanced_eligibility_types": [],
        "evidence": {"enhanced_evidence": {}},
        "evidence_details": {
            "enhanced_eligibility": {},
            "has_evidence": False,
            "past_due": False,
            "submission_count": 0,
        },
        "is_charge_refundable": True,
        "metadata": {},
        **overrides,
    }
