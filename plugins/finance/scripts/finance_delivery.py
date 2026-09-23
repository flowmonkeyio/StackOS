"""Read-only external invoice evidence checks; never a Stripe send operation.

The host supplies the current invoice snapshot digest from an independent read.
These checks verify bindings and retained evidence custody, not the truth of an
image or email. A qualified independent verifier supplies that observation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__:
    from .finance_records import CurrentRecords
    from .finance_workspace import (
        ValidationError,
        evidence_path,
        material_record_digest,
        read_document,
        read_regular,
        validate,
    )
else:
    from finance_records import CurrentRecords
    from finance_workspace import (
        ValidationError,
        evidence_path,
        material_record_digest,
        read_document,
        read_regular,
        validate,
    )

API_ROUTE = "stripe-api-send"
MANUAL_ROUTE = "stripe-dashboard-email-without-link"


def invoice_snapshot_digest(invoice: dict, items: list[dict], *, complete: bool) -> str:
    """Hash the material projection of independent safe invoice/item readbacks.

    The caller must exhaust invoice-items pagination. Status, payment outcomes,
    signed URLs and request/audit metadata are deliberately not version material.
    Preserve missing versus null fields and exact nested values; sort lines by
    stable invoice_item_ref, rejecting duplicates and cross-invoice/customer data.
    """
    required = (
        "invoice_ref",
        "customer_ref",
        "currency",
        "subtotal",
        "total",
        "collection_method",
        "auto_advance",
        "livemode",
        "invoice_customer_email_sha256",
        "footer_sha256",
        "payment_settings",
    )
    if complete is not True or any(key not in invoice for key in required):
        raise ValueError("complete independent invoice and item readbacks are required")
    invoice_keys = (
        *required,
        "amount_due",
        "due_date",
        "effective_at",
        "created",
        "invoice_customer_name_sha256",
        "invoice_customer_phone_sha256",
        "invoice_customer_address_sha256",
        "invoice_customer_address_field_sha256",
        "description_sha256",
        "custom_fields",
        "customer_tax_ids",
    )
    item_keys = (
        "invoice_item_ref",
        "invoice_ref",
        "customer_ref",
        "amount",
        "currency",
        "date",
        "quantity",
        "quantity_decimal",
        "pricing_type",
        "unit_amount_decimal",
        "price_ref",
        "product_ref",
        "proration",
        "livemode",
        "description_sha256",
    )
    lines = []
    seen = set()
    for item in items:
        if (
            not item.get("invoice_item_ref")
            or item["invoice_item_ref"] in seen
            or item.get("invoice_ref") != invoice["invoice_ref"]
            or item.get("customer_ref") != invoice["customer_ref"]
            or any(key not in item for key in ("amount", "currency", "description_sha256"))
        ):
            raise ValueError("incomplete, duplicate or mismatched invoice item readback")
        seen.add(item["invoice_item_ref"])
        lines.append({key: item[key] for key in item_keys if key in item})
    payload = {
        "invoice": {key: invoice[key] for key in invoice_keys if key in invoice},
        "items": sorted(lines, key=lambda row: row["invoice_item_ref"]),
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("evidence time requires a timezone")
    return result


def _record(resolver: CurrentRecords, ref: str, collection: str, *, current: bool) -> dict:
    if resolver.collections.get(ref) != collection:
        raise ValueError(f"unresolved {collection} evidence reference")
    row = resolver.records[ref]
    if current and (
        resolver.resolve(ref) != ref
        or row["status"]
        in {
            "superseded",
            "revoked",
            "rejected",
            "disposed",
            "quarantined",
            "invalidated",
        }
    ):
        raise ValueError("evidence or authorization is no longer current")
    return row


def _binding(resolver: CurrentRecords, proof: dict, *, current: bool) -> tuple[dict, dict, dict]:
    billing = _record(resolver, proof["billing_version_ref"], "billing_versions", current=current)
    if (
        proof["billing_version"] != billing["version"]
        or proof["billing_digest_sha256"] != billing["digest_sha256"]
    ):
        raise ValueError("evidence billing version/digest mismatch")
    recipients = _record(
        resolver, proof["recipient_settings_ref"], "recipient_settings", current=current
    )
    if billing["recipient_settings_ref"] != recipients["record_id"] or proof[
        "recipient_digest_sha256"
    ] != material_record_digest(recipients, "recipient_settings"):
        raise ValueError("evidence recipient version/digest mismatch")
    customer = _record(
        resolver, billing["customer_mapping_ref"], "customer_mappings", current=current
    )
    return billing, recipients, customer


def _observation(
    resolver: CurrentRecords, ref: str, kind: str, *, current: bool
) -> tuple[dict, dict]:
    row = _record(resolver, ref, "provider_observations", current=current)
    proof = row.get(kind)
    if not proof:
        raise ValueError(f"unresolved {kind} contract")
    _billing, recipients, customer = _binding(resolver, proof, current=current)
    if (
        row["object_type"] != "invoice"
        or row["account_ref"] != customer["account_ref"]
        or row["provider_ref"] != customer["provider_ref"]
        or row.get("customer_ref") != customer["customer_ref"]
        or recipients["account_ref"] != row["account_ref"]
        or recipients["provider_ref"] != row["provider_ref"]
        or recipients["customer_ref"] != customer["customer_ref"]
        or recipients.get("invoice_ref") != row["object_ref"]
    ):
        raise ValueError("evidence invoice/account/customer mismatch")
    if _time(proof["verified_at"]) < _time(row["observed_at"]):
        raise ValueError("evidence verification precedes observation")
    if "expires_at" in proof and _time(proof["expires_at"]) <= _time(proof["verified_at"]):
        raise ValueError("evidence expiry must follow verification")
    for attachment_ref in proof["attachment_refs"]:
        attachment = _record(resolver, attachment_ref, "attachments", current=current)
        if attachment["source_ref"] not in row["source_refs"]:
            raise ValueError("evidence attachment lacks observation source binding")
    return row, proof


def validate_delivery_records(document: dict[str, Any]) -> None:
    """Resolve typed proof without expiring historical finance records at read time."""
    resolver = CurrentRecords(document)
    for billing in document["billing_versions"]:
        presentation = billing.get("payment_presentation", {})
        if "review_evidence_ref" in presentation:
            _row, proof = _observation(
                resolver,
                presentation["review_evidence_ref"],
                "payment_presentation_evidence",
                current=False,
            )
            if (
                proof["billing_version_ref"] != billing["record_id"]
                or proof["online_payment_link_state"] != presentation["online_payment_link_state"]
            ):
                raise ValueError("payment presentation evidence mismatch")
    for row in document["provider_observations"]:
        for kind in ("payment_presentation_evidence", "invoice_delivery_evidence"):
            if kind in row:
                _observation(resolver, row["record_id"], kind, current=False)
    for row in document["reviews"]:
        if "delivery_authorization" in row:
            binding = row["delivery_authorization"]
            billing, _recipients, _customer = _binding(resolver, binding, current=False)
            if billing["record_id"] != row["target_ref"]:
                raise ValueError("delivery authorization target mismatch")
    for row in document["reconciliations"]:
        if "manual_invoice_delivery" in row:
            binding = row["manual_invoice_delivery"]
            _binding(resolver, binding, current=False)
            for field, collection in (
                ("owner_authorization_ref", "reviews"),
                ("delivery_evidence_ref", "provider_observations"),
            ):
                if field in binding:
                    _record(resolver, binding[field], collection, current=False)


def _custody(resolver: CurrentRecords, proof: dict, finance: Path) -> None:
    for ref in proof["attachment_refs"]:
        row = _record(resolver, ref, "attachments", current=True)
        if "quarantine_ref" in row or "relative_path" not in row:
            raise ValueError("evidence original is unavailable")
        raw = read_regular(evidence_path(finance, row["relative_path"]))
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("evidence original hash/size mismatch")


def _recipients(recipients: dict, checked_at: str) -> None:
    if (
        not recipients.get("primary_email")
        or not recipients.get("verifier_ref")
        or not recipients.get("evidence_refs")
        or not recipients.get("verified_at")
        or recipients["additional_recipients"]["state"] == "unknown"
    ):
        raise ValueError("all primary/To/CC recipients require verification")
    if (
        hashlib.sha256(recipients["primary_email"].encode()).hexdigest()
        != recipients["primary_email_sha256"]
    ):
        raise ValueError("primary recipient digest mismatch")
    expiry = recipients.get("validity", {}).get("expires_at")
    if not expiry or not (_time(recipients["verified_at"]) <= _time(checked_at) < _time(expiry)):
        raise ValueError("recipient evidence is stale or not yet verified")


def validate_payment_presentation(
    document: dict[str, Any],
    *,
    billing_version_ref: str,
    invoice_ref: str,
    account_ref: str,
    invoice_snapshot_sha256: str,
    delivery_route: str,
    checked_at: str,
    finance: Path,
    schema_path: Path | None = None,
) -> dict[str, str]:
    """Fail closed before delivery; explicit unknown remains valid in a saved draft."""
    validate(document, schema_path=schema_path or finance / "schemas/finance-v1.schema.json")
    resolver = CurrentRecords(document)
    billing = _record(resolver, billing_version_ref, "billing_versions", current=True)
    presentation = billing.get("payment_presentation", {})
    if presentation.get("online_payment_link_state") != "absent":
        raise ValueError("payment presentation remains visible or unknown")
    evidence_ref = presentation.get("review_evidence_ref")
    if not evidence_ref:
        raise ValueError("missing payment presentation evidence")
    row, proof = _observation(resolver, evidence_ref, "payment_presentation_evidence", current=True)
    if (
        row["object_ref"] != invoice_ref
        or row["account_ref"] != account_ref
        or proof["invoice_snapshot_sha256"] != invoice_snapshot_sha256
        or proof["delivery_route"] != delivery_route
    ):
        raise ValueError("payment presentation invoice/account/snapshot/route mismatch")
    if (
        row["status"] != "verified"
        or row["gaps"]
        or proof["online_payment_link_state"] != "absent"
        or proof["verification_method"] != "recipient-visible-route"
    ):
        raise ValueError("payment presentation lacks verified recipient-visible proof")
    if not (_time(proof["verified_at"]) <= _time(checked_at) < _time(proof["expires_at"])):
        raise ValueError("payment presentation evidence is stale or not yet verified")
    _billing, recipients, _customer = _binding(resolver, proof, current=True)
    _recipients(recipients, checked_at)
    _custody(resolver, proof, finance)
    return {
        "status": "verified",
        "review_evidence_ref": evidence_ref,
        "billing_version_ref": billing_version_ref,
        "invoice_ref": invoice_ref,
        "account_ref": account_ref,
        "delivery_route": delivery_route,
    }


def reconcile_manual_invoice_delivery(
    document: dict[str, Any],
    *,
    reconciliation_ref: str,
    checked_at: str,
    finance: Path,
    schema_path: Path | None = None,
) -> dict[str, str]:
    """Return completion proof for an already sent invoice, without performing a send."""
    validate(document, schema_path=schema_path or finance / "schemas/finance-v1.schema.json")
    resolver = CurrentRecords(document)
    reconciliation = _record(resolver, reconciliation_ref, "reconciliations", current=True)
    entry = reconciliation.get("manual_invoice_delivery")
    if not entry or entry["outcome"] != "verified-sent":
        raise ValueError("manual delivery outcome remains unresolved")
    if (
        reconciliation["status"] != "verified"
        or reconciliation["unmatched_refs"]
        or reconciliation["differences"]
        or reconciliation["gaps"]
        or reconciliation["account_refs"] != [entry["account_ref"]]
        or reconciliation["matched_refs"] != [entry["invoice_ref"]]
    ):
        raise ValueError("manual delivery reconciliation remains unresolved or mismatched")
    row, proof = _observation(
        resolver, entry["delivery_evidence_ref"], "invoice_delivery_evidence", current=True
    )
    if (
        row["status"] != "verified"
        or row["gaps"]
        or reconciliation["gaps"]
        or row.get("livemode") is not True
        or row["observed_status"] not in {"open", "paid", "uncollectible"}
        or proof["outcome"] != "sent"
        or proof["verifier_ref"] == proof["sent_by_ref"]
    ):
        raise ValueError("manual delivery requires independent live delivery evidence")
    if not set(row["source_refs"]).issubset(reconciliation["source_refs"]):
        raise ValueError("manual reconciliation is missing the independent delivery source")
    for key in (
        "billing_version_ref",
        "billing_version",
        "billing_digest_sha256",
        "recipient_settings_ref",
        "recipient_digest_sha256",
        "invoice_snapshot_sha256",
    ):
        if entry[key] != proof[key]:
            raise ValueError("manual delivery evidence version/recipient/snapshot mismatch")
    if row["object_ref"] != entry["invoice_ref"] or row["account_ref"] != entry["account_ref"]:
        raise ValueError("manual delivery invoice/account mismatch")
    if not (
        _time(proof["sent_at"])
        <= _time(row["observed_at"])
        <= _time(proof["verified_at"])
        <= _time(checked_at)
    ):
        raise ValueError("manual delivery evidence time mismatch")
    authorization = _record(resolver, entry["owner_authorization_ref"], "reviews", current=True)
    approved = authorization.get("delivery_authorization", {})
    if (
        authorization["authority"] != "owner"
        or authorization["verdict"] != "approved"
        or authorization["status"] != "approved"
        or "owner-invoice-send" not in authorization["scope"]
    ):
        raise ValueError("manual delivery requires owner send authorization")
    for key in (
        "billing_version_ref",
        "billing_version",
        "billing_digest_sha256",
        "recipient_settings_ref",
        "recipient_digest_sha256",
        "invoice_snapshot_sha256",
        "invoice_ref",
        "account_ref",
    ):
        if approved.get(key) != entry[key]:
            raise ValueError("owner send authorization invoice/version/recipient mismatch")
    if approved.get("delivery_route") != MANUAL_ROUTE or not (
        _time(authorization["decided_at"])
        <= _time(proof["sent_at"])
        < _time(approved["expires_at"])
    ):
        raise ValueError("owner send authorization is stale or route mismatched")
    validate_payment_presentation(
        document,
        billing_version_ref=entry["billing_version_ref"],
        invoice_ref=entry["invoice_ref"],
        account_ref=entry["account_ref"],
        invoice_snapshot_sha256=entry["invoice_snapshot_sha256"],
        delivery_route=MANUAL_ROUTE,
        checked_at=proof["sent_at"],
        finance=finance,
        schema_path=schema_path,
    )
    _custody(resolver, proof, finance)
    return {
        "status": "manual-sent",
        "delivery_state": "manual-sent",
        "delivery_route": MANUAL_ROUTE,
        "recovery_state": "reconciled",
        "billing_request_ref": entry["billing_version_ref"],
        "invoice_ref": entry["invoice_ref"],
        "manual_delivery_reconciliation_ref": reconciliation_ref,
        "delivery_evidence_ref": entry["delivery_evidence_ref"],
        "owner_send_approval_ref": entry["owner_authorization_ref"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-presentation", "reconcile-manual-delivery"))
    parser.add_argument("--finance-dir", type=Path, required=True)
    parser.add_argument("--checked-at", required=True)
    parser.add_argument("--billing-version-ref")
    parser.add_argument("--invoice-ref")
    parser.add_argument("--account-ref")
    parser.add_argument("--invoice-snapshot-sha256")
    parser.add_argument("--delivery-route", choices=(API_ROUTE, MANUAL_ROUTE))
    parser.add_argument("--reconciliation-ref")
    args = parser.parse_args()
    try:
        document = read_document(args.finance_dir / "finance.json")
        if args.command == "check-presentation":
            names = (
                "billing_version_ref",
                "invoice_ref",
                "account_ref",
                "invoice_snapshot_sha256",
                "delivery_route",
            )
            if any(getattr(args, name) is None for name in names):
                parser.error(
                    "check-presentation requires exact billing, invoice, account, "
                    "snapshot and route"
                )
            result = validate_payment_presentation(
                document,
                **{name: getattr(args, name) for name in names},
                checked_at=args.checked_at,
                finance=args.finance_dir,
            )
        else:
            if not args.reconciliation_ref:
                parser.error("reconcile-manual-delivery requires --reconciliation-ref")
            result = reconcile_manual_invoice_delivery(
                document,
                reconciliation_ref=args.reconciliation_ref,
                checked_at=args.checked_at,
                finance=args.finance_dir,
            )
        print(json.dumps(result))
        return 0
    except ValidationError as error:
        parser.exit(
            1, f"ValidationError: schema invalid at {'.'.join(map(str, error.absolute_path))}\n"
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
