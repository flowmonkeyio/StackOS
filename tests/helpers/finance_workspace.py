"""Synthetic fixture builders importing the shipped optional host mechanics."""

from __future__ import annotations

import hashlib
from typing import Any

from plugins.finance.scripts.finance_workspace import (
    TEMPLATE_ROOT as TEMPLATE_ROOT,
)
from plugins.finance.scripts.finance_workspace import (
    atomic_write as atomic_write,
)
from plugins.finance.scripts.finance_workspace import (
    digest as digest,
)
from plugins.finance.scripts.finance_workspace import (
    initialize as initialize,
)
from plugins.finance.scripts.finance_workspace import (
    material_billing_digest as material_billing_digest,
)
from plugins.finance.scripts.finance_workspace import (
    material_record_digest as material_record_digest,
)
from plugins.finance.scripts.finance_workspace import (
    new_document as new_document,
)
from plugins.finance.scripts.finance_workspace import (
    read_document as read_document,
)
from plugins.finance.scripts.finance_workspace import (
    records as records,
)
from plugins.finance.scripts.finance_workspace import (
    snapshot_digest as snapshot_digest,
)
from plugins.finance.scripts.finance_workspace import (
    validate as validate,
)
from plugins.finance.scripts.finance_workspace import (
    write_document as write_document,
)

AT = "2026-09-06T12:00:00Z"


def record(record_id: str, status: str = "prepared", **fields: Any) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "created_at": AT,
        "updated_at": AT,
        "status": status,
        "status_history": [
            {
                "at": AT,
                "actor": "fixture:host",
                "to_status": status,
                "reason": "Synthetic isolated rehearsal; no real provider or owner decision.",
                "evidence_refs": ["fixture:source"],
            }
        ],
        "provenance": [{"kind": "fixture", "ref": "fixture:source", "observed_at": AT}],
        "gaps": [],
        **fields,
    }


def money(amount: int, currency: str = "USD") -> dict[str, Any]:
    return {"amount_minor": amount, "currency": currency.upper()}


def add_billing_fixture(
    document: dict[str, Any],
    *,
    currency: str = "USD",
    lines: tuple[tuple[int, str], ...] = ((1000, "Synthetic work"),),
    email: str = "synthetic@example.test",
) -> dict[str, Any]:
    """Populate normalized, synthetic source/customer/recipients/billing once."""
    source_id = "source:billing"
    document["sources"].append(
        record(
            source_id,
            source_kind="synthetic-request",
            source_identity="fixture-billing-v1",
            attachment_refs=[],
            coverage_state="complete",
        )
    )
    document["customer_mappings"].append(
        record(
            "customer:fixture:v1",
            "verified",
            version=1,
            external_customer_ref="fixture:customer",
            provider_ref="fixture:provider",
            account_ref="fixture:account",
            customer_ref="fixture:provider-customer",
            match_evidence_refs=[source_id],
            verified_at=AT,
        )
    )
    document["recipient_settings"].append(
        record(
            "recipients:fixture:v1",
            "verified",
            version=1,
            provider_ref="fixture:provider",
            account_ref="fixture:account",
            customer_ref="fixture:provider-customer",
            primary_email=email,
            primary_email_sha256=hashlib.sha256(email.encode()).hexdigest(),
            additional_recipients={"state": "verified-none", "to": [], "cc": []},
            verifier_ref="fixture:owner",
            verified_at=AT,
            evidence_refs=[source_id],
            validity={"recheck_condition": "Immediately before the approved operation."},
        )
    )
    billing = record(
        "billing:fixture:v1",
        "approved",
        version=1,
        customer_mapping_ref="customer:fixture:v1",
        recipient_settings_ref="recipients:fixture:v1",
        currency=currency.upper(),
        terms={"days_until_due": 30},
        lines=[
            {
                "line_id": f"line:fixture:{ordinal}",
                "description": description,
                "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
                "amount": money(amount, currency),
                "source_refs": [source_id],
            }
            for ordinal, (amount, description) in enumerate(lines, start=1)
        ],
        subtotal=money(sum(amount for amount, _ in lines), currency),
        total=money(sum(amount for amount, _ in lines), currency),
        source_refs=[source_id],
    )
    billing["digest_sha256"] = material_billing_digest(billing)
    document["billing_versions"].append(billing)
    return billing


def add_settlement_fixture(
    document: dict[str, Any],
    *,
    amount: int = 1000,
    invoice_ref: str = "fixture:invoice",
    customer_ref: str = "fixture:customer",
    route: str = "report-and-attach",
    reference_hash: str = "a" * 64,
) -> dict[str, Any]:
    document["sources"].append(
        record(
            "source:settlement",
            "verified-synthetic",
            source_kind="synthetic-bank",
            source_identity="synthetic-bank-source-v1",
            attachment_refs=[],
            coverage_state="complete",
        )
    )
    settlement = record(
        "settlement:fixture:v1",
        "prepared",
        version=1,
        source_ref="source:settlement",
        source_identity="synthetic-bank-source-v1",
        source_sha256="b" * 64,
        payment_reference_sha256=reference_hash,
        provider_ref="fixture:provider",
        account_ref="fixture:account",
        customer_ref=customer_ref,
        invoice_ref=invoice_ref,
        received_amount=money(amount),
        received_at=AT,
        selected_route=route,
        allocation={
            "allocation_id": "allocation:fixture:v1",
            "invoice_ref": invoice_ref,
            "amount": money(amount),
            "match_evidence_refs": ["source:settlement"],
        },
        recovery={"state": "not-needed"},
    )
    document["settlements"].append(settlement)
    return settlement
