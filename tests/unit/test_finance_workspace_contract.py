from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.helpers.finance_workspace import (
    AT,
    add_billing_fixture,
    add_settlement_fixture,
    digest,
    initialize,
    material_billing_digest,
    material_record_digest,
    money,
    read_document,
    record,
    snapshot_digest,
    validate,
    write_document,
)

FINANCE_ROOT = Path("plugins/finance")
BACKEND_CONTRACT = FINANCE_ROOT / "references" / "backend-contract.md"
LOCAL_WORKSPACE_CONTRACT = FINANCE_ROOT / "references" / "local-workspace-contract.md"
WORKSPACE_TEMPLATE = FINANCE_ROOT / "templates" / "finance-workspace" / "FINANCE.md"


def test_canonical_financial_document_is_structured_and_markdown_is_guidance() -> None:
    workspace = WORKSPACE_TEMPLATE.parent
    data = json.loads((workspace / "finance.json").read_text())
    assert data["schema_version"] == "local-json-v1"
    assert data["revision"] == 0
    guide = WORKSPACE_TEMPLATE.read_text()
    assert "finance.json" in guide and "single" in guide.lower()
    assert "Document revision:" not in guide
    assert "| Receipt ID |" not in guide
    assert (workspace / "schemas/finance-v1.schema.json").is_file()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backend_contract_keeps_finance_records_outside_stackos() -> None:
    contract = _read(BACKEND_CONTRACT)

    for required in (
        "`local-json`",
        "host-agent-managed",
        "must not own finance-domain",
        "finance-domain tables",
        "repositories",
        "resources",
        "private evidence storage",
        "filesystem connector",
        "QuickBooks",
        "Google Sheets",
        "`external_project_ref`",
        "not a StackOS project ID",
        "not an authority grant",
        "no automatic cross-project access",
        "no project registry",
    ):
        assert required in contract


def test_backend_contract_names_the_shared_record_responsibilities() -> None:
    contract = _read(BACKEND_CONTRACT)

    for required in (
        "stable `record_id`",
        "source/provenance block",
        "`content_sha256`",
        "SHA-256",
        "status history",
        "Duplicate handling and retries",
        "Corrections and reconciliation",
        "Approvals and cross-workflow handoffs",
        "Received-payment settlement",
        "one allocation",
        "report and attach",
        "paid-out-of-band",
        "24 hours",
        "`handoff_id`",
        "`prepared/unposted`",
    ):
        assert required in contract


def test_local_workspace_contract_requires_safe_common_intake_ordering() -> None:
    contract = " ".join(_read(LOCAL_WORKSPACE_CONTRACT).split())

    for required in (
        "finance/finance.json",
        "attachments/YYYY/MM/",
        "complete original email as `.eml`",
        "chat/manual upload",
        "receipt record",
        "atomically rename",
        "source identity plus hash",
        "unacknowledged",
        "source as seen or advance its cursor",
        "one allocation",
        "report+mark",
        "supported partial report-and-attach allocation",
        "Unsupported or unsynchronized partials",
        "split allocations, overpayments, mismatches and unknown outcomes",
        "24 hours",
        "project is unknown or multi-project",
        "Never infer attribution from a customer mapping",
        "backup, restore, retention periods, access permissions",
    ):
        assert required in contract


def test_finance_markdown_template_routes_to_canonical_collections_and_controls() -> None:
    template = _read(WORKSPACE_TEMPLATE)

    for required in (
        "`local-json`",
        "`local-json-v1`",
        "## Start and resume",
        "## Where records live",
        "## One writer and verified custody",
        "## Decisions and workflow completion",
        "## Reports, CSV and corrections",
        "`business_profiles`, `operating_settings`",
        "`sources`, `attachments`, `receipts`",
        "`bookkeeping`, `reconciliations`",
        "`customer_mappings`, `recipient_settings`, `billing_versions`",
        "`provider_observations`, `mutation_attempts`, `settlements`, `collection_decisions`",
        "`cashflow_forecasts`, `reserve_applications`, `tax_packets`",
        "`reviews`, `corrections`, `exceptions`, `handoffs`, `write_proofs`, `custody_events`",
        "prepared/unposted",
        "one verified source/allocation",
        "Unknown outcomes remain",
        "PaymentRecord listing is temporarily",
        "single authoritative local financial",
    ):
        assert required in template


def _add_receipt(document: dict, *, record_id: str = "receipt:fixture") -> None:
    document["sources"].append(
        record(
            "source:receipt",
            "retained",
            source_kind="synthetic-manual",
            source_identity="fixture-manual-1",
            attachment_refs=["attachment:receipt"],
            coverage_state="complete",
        )
    )
    document["attachments"].append(
        record(
            "attachment:receipt",
            "retained",
            source_ref="source:receipt",
            role="original",
            relative_path="attachments/2026/09/fixture-original.txt",
            sha256="a" * 64,
            bytes=1,
            media_type="text/plain",
            original_filename="synthetic.txt",
        )
    )
    document["receipts"].append(
        record(
            record_id,
            "prepared",
            source_ref="source:receipt",
            attachment_refs=["attachment:receipt"],
            amount=money(1200),
        )
    )


def test_first_run_and_resume_never_replace_existing_canonical_records(tmp_path: Path) -> None:
    path = initialize(tmp_path / "finance")
    document = read_document(path)
    assert document["business_profiles"] == document["tax_packets"] == []
    assert (path.parent / "schemas/finance-v1.schema.json").read_bytes() == (
        WORKSPACE_TEMPLATE.parent / "schemas/finance-v1.schema.json"
    ).read_bytes()
    _add_receipt(document)
    document["revision"] = 1
    write_document(path, document, digest(path))
    prior = path.read_bytes()
    with pytest.raises(ValueError, match="not empty bootstrap overwrite"):
        initialize(path.parent)
    assert path.read_bytes() == prior
    assert read_document(path)["receipts"][0]["amount"] == money(1200)


def test_host_rejects_duplicate_ids_dangling_refs_and_silent_history_changes(
    tmp_path: Path,
) -> None:
    path = initialize(tmp_path / "finance")
    document = read_document(path)
    _add_receipt(document)
    document["revision"] = 1
    write_document(path, document, digest(path))
    duplicate = copy.deepcopy(document)
    duplicate["receipts"].append(copy.deepcopy(duplicate["receipts"][0]))
    with pytest.raises(ValueError, match="duplicate canonical record_id"):
        validate(duplicate)
    broken = copy.deepcopy(document)
    broken["receipts"][0]["source_ref"] = "source:missing"
    with pytest.raises(ValueError, match="unresolved local reference"):
        validate(broken)
    broken = copy.deepcopy(document)
    broken["revision"] = 2
    broken["receipts"][0]["status_history"][0]["reason"] = "Silently rewritten"
    with pytest.raises(ValueError, match="history must remain additive"):
        write_document(path, broken, digest(path))
    assert read_document(path) == document


def test_stale_or_competing_host_writer_cannot_lose_a_canonical_update(tmp_path: Path) -> None:
    path = initialize(tmp_path / "finance")
    expected = digest(path)
    stale = read_document(path)
    current = read_document(path)
    _add_receipt(current)
    current["revision"] = 1
    write_document(path, current, expected)
    stale["revision"] = 1
    with pytest.raises(ValueError, match="stale host revision"):
        write_document(path, stale, expected)
    lock = path.with_name(".fixture-writer.lock")
    lock.write_text("another isolated host writer owns this lock")
    with pytest.raises(FileExistsError):
        write_document(path, stale, digest(path))
    assert lock.exists() and read_document(path) == current


def test_markdown_and_csv_are_non_authoritative_views(tmp_path: Path) -> None:
    path = initialize(tmp_path / "finance")
    document = read_document(path)
    _add_receipt(document)
    document["revision"] = 1
    write_document(path, document, digest(path))
    before = digest(path)
    (path.parent / "report.md").write_text(
        "Derived from finance.json revision 1, receipt:fixture: 12.00 USD"
    )
    (path.parent / "export.csv").write_text(
        "record_id,source_revision,amount_minor,currency\nreceipt:fixture,1,1200,USD\n"
    )
    (path.parent / "FINANCE.md").write_text("Untrusted manual edit: amount 999999 USD")
    (path.parent / "export.csv").write_text(
        "record_id,source_revision,amount_minor,currency\nreceipt:fixture,1,999999,USD\n"
    )
    assert digest(path) == before
    assert read_document(path)["receipts"][0]["amount"] == money(1200)


def test_unrelated_receipt_does_not_invalidate_exact_billing_approval(tmp_path: Path) -> None:
    path = initialize(tmp_path / "finance")
    document = read_document(path)
    billing = add_billing_fixture(document)
    approved_digest = billing["digest_sha256"]
    document["revision"] = 1
    write_document(path, document, digest(path))
    _add_receipt(document)
    document["revision"] = 2
    write_document(path, document, digest(path))
    current = read_document(path)
    assert material_billing_digest(current["billing_versions"][0]) == approved_digest
    assert current["revision"] == 2
    changed = copy.deepcopy(current)
    changed["revision"] = 3
    invoice = changed["billing_versions"][0]
    invoice["terms"]["days_until_due"] = 45
    invoice["digest_sha256"] = material_billing_digest(invoice)
    assert invoice["digest_sha256"] != approved_digest
    with pytest.raises(ValueError, match="approved billing material is immutable"):
        write_document(path, changed, digest(path))
    assert read_document(path) == current


def test_project_attribution_is_billing_material_without_a_second_amount_or_record(
    tmp_path: Path,
) -> None:
    path = initialize(tmp_path / "finance")
    document = read_document(path)
    billing = add_billing_fixture(document)
    unassigned_digest = billing["digest_sha256"]
    billing["external_project_ref"] = "agency-project:fixture-client-a-website"
    billing["digest_sha256"] = material_billing_digest(billing)
    assert billing["digest_sha256"] != unassigned_digest
    document["revision"] = 1
    write_document(path, document, digest(path))

    current = read_document(path)
    current_billing = current["billing_versions"][0]
    assert len(current["billing_versions"]) == 1
    assert current_billing["total"] == money(1000)
    assert sum(line["amount"]["amount_minor"] for line in current_billing["lines"]) == 1000
    assert "allocation" not in current_billing
    assert all("external_project_ref" not in line for line in current_billing["lines"])

    changed = copy.deepcopy(current)
    changed["revision"] = 2
    changed_billing = changed["billing_versions"][0]
    changed_billing["external_project_ref"] = "agency-project:fixture-client-b-website"
    changed_billing["digest_sha256"] = material_billing_digest(changed_billing)
    assert changed_billing["digest_sha256"] != current_billing["digest_sha256"]
    with pytest.raises(ValueError, match="approved billing material is immutable"):
        write_document(path, changed, digest(path))
    assert read_document(path) == current


def test_one_received_payment_cannot_gain_two_current_allocations(tmp_path: Path) -> None:
    path = initialize(tmp_path / "finance")
    document = read_document(path)
    original = add_settlement_fixture(document)
    validate(document)
    duplicate = copy.deepcopy(original)
    duplicate["record_id"] = "settlement:second"
    duplicate["allocation"]["allocation_id"] = "allocation:second"
    document["settlements"].append(duplicate)
    with pytest.raises(ValueError, match="duplicate current source allocation"):
        validate(document)


def test_unversioned_receipt_handoff_and_versioned_settlement_review_bind_real_scope(
    tmp_path: Path,
) -> None:
    path = initialize(tmp_path / "finance")
    document = read_document(path)
    _add_receipt(document)
    receipt = document["receipts"][0]
    handoff = record(
        "handoff:receipt",
        "proposed",
        handoff_id="handoff:receipt:once",
        from_workflow="finance.receipt-intake",
        to_workflow="finance.bookkeeping-close",
        source_packet_ref=receipt["record_id"],
        source_digest_sha256=snapshot_digest(receipt),
        conditions=["Verified original custody; source preparation only."],
        owner_ref="fixture:host",
    )
    document["handoffs"].append(handoff)
    assert "source_version" not in handoff
    settlement = add_settlement_fixture(document)
    review = record(
        "review:settlement",
        "reviewed",
        target_ref=settlement["record_id"],
        target_version=settlement["version"],
        target_digest_sha256=material_record_digest(settlement, "settlements"),
        scope=["report-and-attach"],
        authority="owner",
        verdict="approved",
        actor="fixture:owner",
        decided_at=AT,
    )
    document["reviews"].append(review)
    validate(document)
    review["target_ref"] = "settlement:missing"
    with pytest.raises(ValueError, match="review target does not resolve"):
        validate(document)
    review["target_ref"] = settlement["record_id"]
    handoff["source_packet_ref"] = "receipt:missing"
    with pytest.raises(ValueError, match="handoff source does not resolve"):
        validate(document)
    handoff["source_packet_ref"] = receipt["record_id"]
    # Reporting returns a new provider ref and recovery facts. They are results,
    # not a change to the owner-approved source/allocation on report-and-attach.
    approved = review["target_digest_sha256"]
    settlement["payment_ref"] = "provider-object:reported-payment"
    settlement["recovery"] = {
        "state": "resolved",
        "verified_payment_ref": settlement["payment_ref"],
    }
    assert material_record_digest(settlement, "settlements") == approved
    validate(document)
    settlement["allocation"]["amount"] = money(999)
    with pytest.raises(ValueError, match="exact material digest"):
        validate(document)
    settlement["allocation"]["amount"] = settlement["received_amount"]
    handoff["source_digest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="unversioned snapshot"):
        validate(document)
    handoff["source_digest_sha256"] = snapshot_digest(receipt)
    review["target_version"] = 2
    with pytest.raises(ValueError, match="exact versioned target"):
        validate(document)
    del review["target_version"]
    with pytest.raises(ValueError, match="exact versioned target"):
        validate(document)
