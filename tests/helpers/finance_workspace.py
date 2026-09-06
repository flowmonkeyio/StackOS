"""TEST-ONLY host rehearsal; not a StackOS backend or validator service.

These helpers make synthetic connector/workflow proofs use one external JSON
document. They exercise a bounded subset of the documented host checks; they do
not claim schema validation performs financial judgment or provider reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "plugins/finance/templates/finance-workspace"
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


def new_document() -> dict[str, Any]:
    return json.loads((TEMPLATE_ROOT / "finance.json").read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = [row for values in document.values() if isinstance(values, list) for row in values]
    by_id = {row["record_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate canonical record_id")
    return by_id


def validate(document: dict[str, Any], *, schema_path: Path | None = None) -> None:
    schema = json.loads(
        (schema_path or TEMPLATE_ROOT / "schemas/finance-v1.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
    by_id = records(document)
    for row in by_id.values():
        for field in ("created_at", "updated_at"):
            datetime.fromisoformat(row[field].replace("Z", "+00:00"))
    # Only internal record links are resolved here. Provider/account/action refs
    # and provenance refs can name external evidence and are not local records.
    local_keys = {
        "source_ref",
        "attachment_refs",
        "customer_mapping_ref",
        "recipient_settings_ref",
        "billing_version_ref",
        "tax_packet_ref",
        "forecast_ref",
        "duplicate_of_ref",
    }
    for row in by_id.values():
        for key in local_keys & row.keys():
            refs = row[key] if isinstance(row[key], list) else [row[key]]
            if any(ref not in by_id for ref in refs):
                raise ValueError(f"unresolved local reference: {key}")
    for row in document["reviews"]:
        target = by_id.get(row["target_ref"])
        if target is None:
            raise ValueError("review target does not resolve to a canonical record")
        if target and "version" in target and row.get("target_version") != target["version"]:
            raise ValueError("review must match exact versioned target")
        if target:
            collection = next(
                key
                for key, items in document.items()
                if isinstance(items, list) and target in items
            )
            if row["target_digest_sha256"] != material_record_digest(target, collection):
                raise ValueError("review must match exact material digest")
    for row in document["handoffs"]:
        target = by_id.get(row["source_packet_ref"])
        if target is None:
            raise ValueError("handoff source does not resolve to a canonical record")
        if target and "version" in target and row.get("source_version") != target["version"]:
            raise ValueError("handoff must match exact versioned target")
        if (
            target
            and "version" not in target
            and row.get("source_digest_sha256") != snapshot_digest(target)
        ):
            raise ValueError("handoff must match exact unversioned snapshot")
    sources = [
        row["source_ref"] for row in document["settlements"] if row["status"] != "superseded"
    ]
    if len(sources) != len(set(sources)):
        raise ValueError("duplicate current source allocation")
    pairs = [
        (
            row["tax_packet_ref"],
            row["tax_packet_version"],
            row["forecast_ref"],
            row["forecast_version"],
        )
        for row in document["reserve_applications"]
        if row["status"] != "superseded"
    ]
    if len(pairs) != len(set(pairs)):
        raise ValueError("duplicate tax/forecast reserve application")
    for billing in document["billing_versions"]:
        if material_billing_digest(billing) != billing["digest_sha256"]:
            raise ValueError("billing material digest mismatch")
        values = [line["amount"] for line in billing["lines"]]
        if any(
            value["currency"] != billing["currency"]
            for value in [*values, billing["subtotal"], billing["total"]]
        ):
            raise ValueError("billing currency mismatch")
        if sum(value["amount_minor"] for value in values) != billing["subtotal"]["amount_minor"]:
            raise ValueError("billing subtotal mismatch")
    for forecast in document["cashflow_forecasts"]:
        for rows in forecast["scenarios"].values():
            for ordinal, row in enumerate(rows):
                if row["week_number"] != ordinal + 1 or date.fromisoformat(
                    row["week_start"]
                ) != date.fromisoformat(forecast["week_1_start"]) + timedelta(weeks=ordinal):
                    raise ValueError("forecast weeks not sequential")
                fields = (
                    "opening_cash",
                    "operating_inflows",
                    "operating_outflows",
                    "actual_tax_payments",
                    "closing_cash",
                    "earmarked_reserve",
                    "free_cash",
                )
                if not all(field in row for field in fields):
                    if not row["gaps"]:
                        raise ValueError("unknown forecast cash needs an explicit gap")
                    continue
                values = [row[field] for field in fields]
                if len({value["currency"] for value in values}) != 1:
                    raise ValueError("forecast currency mismatch")
                opening, inflows, outflows, paid, closing, reserve, free = [
                    value["amount_minor"] for value in values
                ]
                if closing != opening + inflows - outflows - paid or free != closing - reserve:
                    raise ValueError("forecast cash arithmetic mismatch")
                if ordinal and row["opening_cash"] != rows[ordinal - 1].get("closing_cash"):
                    raise ValueError("forecast opening does not roll forward")


def read_document(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text())
    validate(document, schema_path=path.parent / "schemas/finance-v1.schema.json")
    return document


def atomic_write(path: Path, payload: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        assert temporary.read_bytes() == payload
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(finance: Path) -> Path:
    finance.mkdir(parents=True, exist_ok=True)
    path = finance / "finance.json"
    if path.exists() or (finance / "FINANCE.md").exists() or any(finance.iterdir()):
        raise ValueError("existing workspace requires readback, not empty bootstrap overwrite")
    document = new_document()
    schema_path = finance / "schemas/finance-v1.schema.json"
    schema_path.parent.mkdir()
    schema_path.write_bytes((TEMPLATE_ROOT / "schemas/finance-v1.schema.json").read_bytes())
    validate(document, schema_path=schema_path)
    atomic_write(path, json.dumps(document, sort_keys=True).encode())
    (finance / "FINANCE.md").write_bytes((TEMPLATE_ROOT / "FINANCE.md").read_bytes())
    return path


def write_document(path: Path, document: dict[str, Any], expected_hash: str) -> None:
    lock = path.with_name(".fixture-writer.lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if digest(path) != expected_hash:
            raise ValueError("stale host revision")
        prior = read_document(path)
        if document["revision"] != prior["revision"] + 1:
            raise ValueError("revision must advance exactly once")
        validate(document, schema_path=path.parent / "schemas/finance-v1.schema.json")
        current_records = records(document)
        for record_id, previous in records(prior).items():
            if record_id not in current_records:
                raise ValueError("canonical history cannot be silently deleted")
            current = current_records[record_id]
            for field in ("status_history", "provenance"):
                if current[field][: len(previous[field])] != previous[field]:
                    raise ValueError("canonical history must remain additive")
        approved = {
            row["record_id"]: row
            for row in prior["billing_versions"]
            if row["status"] in {"approved", "reviewed"}
        }
        for record_id, previous in approved.items():
            if material_billing_digest(current_records[record_id]) != material_billing_digest(
                previous
            ):
                raise ValueError("approved billing material is immutable")
        atomic_write(path, json.dumps(document, sort_keys=True).encode())
        assert read_document(path) == document
    finally:
        os.close(descriptor)
        lock.unlink()


def material_billing_digest(billing: dict[str, Any]) -> str:
    """Approved material only; unrelated workspace revisions cannot invalidate it."""
    keys = (
        "version",
        "customer_mapping_ref",
        "recipient_settings_ref",
        "currency",
        "terms",
        "lines",
        "subtotal",
        "total",
        "source_refs",
    )
    payload = {key: billing[key] for key in keys}
    if "supersedes_ref" in billing:
        payload["supersedes_ref"] = billing["supersedes_ref"]
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def snapshot_digest(value: dict[str, Any]) -> str:
    # Unversioned material excludes common lifecycle metadata and its own
    # result links, not provenance, gaps, identity or other business facts.
    excluded = {
        "created_at",
        "updated_at",
        "status",
        "status_history",
        "review_refs",
        "write_proof_refs",
        "handoff_refs",
    }
    value = {key: item for key, item in value.items() if key not in excluded}
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def material_record_digest(value: dict[str, Any], collection: str) -> str:
    if collection == "billing_versions":
        return material_billing_digest(value)
    projections = {
        "tax_packets": (
            "version",
            "tax_year",
            "as_of",
            "profile_refs",
            "annual_inputs",
            "source_refs",
            "obligations",
            "gaps",
            "supersedes_ref",
        ),
        "collection_decisions": (
            "version",
            "invoice_ref",
            "invoice_observation_ref",
            "eligibility",
            "decision_reason",
            "reminder_owner",
            "due_status",
            "dispute_state",
            "pause_state",
            "promise_state",
            "correction_state",
            "last_contact_at",
            "cooldown_until",
            "recipient_settings_ref",
            "settlement_refs",
            "gaps",
            "supersedes_ref",
        ),
        "settlements": (
            "version",
            "source_ref",
            "source_identity",
            "source_sha256",
            "payment_reference_sha256",
            "provider_ref",
            "account_ref",
            "customer_ref",
            "invoice_ref",
            "received_amount",
            "received_at",
            "selected_route",
            "allocation",
            "match_evidence_refs",
            "duplicate_of_ref",
            "gaps",
            "supersedes_ref",
        ),
    }
    if collection not in projections:
        return snapshot_digest(value)
    payload = {key: value[key] for key in projections[collection] if key in value}
    if collection == "settlements" and value["selected_route"] == "attach-payment":
        payload["payment_ref"] = value["payment_ref"]
    return snapshot_digest(payload)


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
