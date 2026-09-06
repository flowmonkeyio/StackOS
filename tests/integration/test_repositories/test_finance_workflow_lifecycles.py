"""Complete real finance-template lifecycles with explicitly simulated packets.

These tests exercise repository lifecycle/output contracts, not provider calls
or autonomous finance reasoning. Provider/owner outcomes are test fixtures;
the separate MCP connector tests prove actual gated fake-provider dispatch.
All financial packet contents stay in a disposable host workspace, not in run
results. This is test-local rehearsal code, not a new finance backend.
"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from stackos.db.models import ApprovalRequestStatus, RunPlanStepStatus
from stackos.repositories.base import ConflictError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from tests.helpers.finance_workspace import (
    AT,
    add_billing_fixture,
    add_settlement_fixture,
    digest,
    initialize,
    material_record_digest,
    money,
    read_document,
    records,
    snapshot_digest,
    validate,
    write_document,
)
from tests.helpers.finance_workspace import (
    record as finance_record,
)

from .test_finance_workflows import _preflight_result, _strict_inputs

CASES = [
    ("finance.bookkeeping-close", "complete"),
    ("finance.bookkeeping-close", "missing-statement"),
    ("finance.bookkeeping-close", "no-activity"),
    ("finance.cashflow-management", "complete"),
    ("finance.cashflow-management", "missing-opening-cash"),
    ("finance.payment-request", "sent"),
    ("finance.payment-request", "suppressed"),
    ("finance.payment-request-followups", "paid-suppressed"),
    ("finance.payment-request-followups", "resent"),
    ("finance.payment-request-followups", "report-and-attach"),
    ("finance.payment-request-followups", "partial-applied"),
    ("finance.payment-request-followups", "mark-paid-out-of-band"),
]


def _step_result(workflow: str, case: str, step: str, refs: dict[str, str]) -> dict[str, Any]:
    result = _preflight_result(workflow)
    summary = next(iter(result.values()))
    settlement = case in {"report-and-attach", "partial-applied", "mark-paid-out-of-band"}
    if settlement:
        summary.update(occurrence_mode="settlement-only", settlement_scope_ref=refs["source"])
    if step == "preflight":
        return result

    if workflow == "finance.bookkeeping-close":
        summary.update(
            status="evidence-collected",
            source_completeness_ref=refs["coverage"],
            source_coverage_state="partial" if case == "missing-statement" else "complete",
        )
        if step != "collect-evidence":
            summary.update(
                status="prepared-unposted",
                external_write_proof_ref=refs["write"],
                prepared_record_refs=[] if case == "no-activity" else [refs["packet"]],
                reconciliation_state="exception" if case == "missing-statement" else "complete",
            )
            if case == "missing-statement":
                summary.update(
                    status="completed-with-open-exceptions", exception_refs=[refs["exception"]]
                )
            elif step in {"review-prepared-close", "handoff"}:
                summary.update(status="reviewed", control_review_ref=refs["review"])
                if step == "handoff":
                    summary.update(status="handed-off", handoff_refs=[refs["handoff"]])
        return result

    if workflow == "finance.cashflow-management":
        summary.update(
            status="evidence-collected", source_proof_refs=[refs["source"]], uncertainty_refs=[]
        )
        if step != "collect-evidence":
            summary.update(
                forecast_ref=refs["packet"],
                source_completeness_ref=refs["coverage"],
                external_write_proof_ref=refs["write"],
            )
            if case == "missing-opening-cash":
                summary.update(status="forecast-incomplete", exception_refs=[refs["exception"]])
            else:
                summary.update(
                    status="forecast-prepared",
                    base_scenario_ref=refs["base"],
                    downside_scenario_ref=refs["downside"],
                    base_scenario_state="prepared",
                    downside_scenario_state="prepared",
                    tax_reserve_application_ref=refs["reserve"],
                )
                if step == "review-and-handoff":
                    summary.update(
                        status="review-ready",
                        control_review_ref=refs["review"],
                        base_scenario_state="reviewed",
                        downside_scenario_state="reviewed",
                    )
        return result

    if workflow == "finance.payment-request":
        statuses = {
            "resolve-customer": "customer-resolved",
            "create-draft": "draft-prepared",
            "review-draft": "review-ready",
            "finalize-invoice": "finalized",
            "send-invoice": "sent" if case == "sent" else "suppressed",
            "record-and-handoff": "recorded",
        }
        summary.update(
            status=statuses[step],
            customer_ref="simulated-provider:customer",
            action_call_refs=["simulated-action-call:invoice-fixture"],
        )
        if step != "resolve-customer":
            summary["invoice_ref"] = "simulated-provider:invoice"
        if step not in {"resolve-customer", "create-draft"}:
            summary.update(proposal_version_ref=refs["proposal"], control_review_ref=refs["review"])
        if step in {"finalize-invoice", "send-invoice", "record-and-handoff"}:
            summary["approval_refs"] = [refs["approval-finalize"]]
        if step in {"send-invoice", "record-and-handoff"}:
            summary["delivery_state"] = case
            if case == "sent":
                summary["approval_refs"].append(refs["approval-send"])
            else:
                summary["suppression_ref"] = refs["decision"]
        if step == "record-and-handoff":
            summary.update(
                external_write_proof_ref=refs["write"],
                handoff_refs=[refs["handoff"]] if case == "sent" else [],
            )
        return result

    assert workflow == "finance.payment-request-followups"
    summary.update(
        status="lifecycle-read", action_call_refs=["simulated-action-call:followup-fixture"]
    )
    if settlement:
        if step == "read-invoice-lifecycle":
            return result
        route = "report-and-attach" if case == "partial-applied" else case
        summary.update(
            status="settlement-prepared",
            settlement_state="prepared",
            settlement_route=route,
            payment_source_ref=refs["source"],
            payment_allocation_ref=refs["allocation"],
        )
        if step == "prepare-settlement":
            return result
        summary.update(
            status="settlement-review-ready",
            settlement_state="review-ready",
            settlement_decision_ref=refs["decision"],
            control_review_ref=refs["review"],
        )
        if step == "review-settlement":
            return result
        state = {
            "report-and-attach": "payment-attached",
            "partial-applied": "partial-applied",
            "mark-paid-out-of-band": "marked-paid-out-of-band",
        }[case]
        summary.update(
            status="settlement-recorded",
            settlement_state=state,
            approval_refs=(
                [refs["approval-mark"]]
                if case == "mark-paid-out-of-band"
                else [refs["approval-record"], refs["approval-attach"]]
            ),
            payment_ref="simulated-provider:received-payment",
            external_write_proof_ref=refs["write"],
            handoff_refs=[refs["handoff"]],
        )
        return result

    if step in {
        "read-invoice-lifecycle",
        "prepare-settlement",
        "review-settlement",
        "apply-settlement",
    }:
        return result
    suppressed = case == "paid-suppressed"
    summary.update(
        status="suppressed" if suppressed else "review-ready",
        followup_decision_ref=refs["decision"],
        suppressed_invoice_refs=["simulated-provider:invoice"] if suppressed else [],
        eligible_invoice_refs=[] if suppressed else ["simulated-provider:invoice"],
        resend_state="suppressed" if suppressed else "not-requested",
    )
    if step != "suppress-ineligible":
        summary["control_review_ref"] = refs["review"]
    if step in {"resend-approved", "record-outcome"} and not suppressed:
        summary.update(
            status="resent", resend_state="sent", approval_refs=[refs["approval-resend"]]
        )
    if step == "record-outcome":
        summary.update(status="recorded", external_write_proof_ref=refs["write"], handoff_refs=[])
    return result


def _external_fixture(finance: Path, workflow: str, case: str) -> dict[str, str]:
    """All fixture facts live once in typed records in the canonical JSON file."""
    index = initialize(finance)
    prior_hash = digest(index)
    document = read_document(index)
    incomplete = case in {"missing-statement", "missing-opening-cash", "packet-incomplete"}
    source = finance_record(
        "source:fixture",
        "observed",
        source_kind="synthetic-packet",
        source_identity=f"fixture:{workflow}:{case}",
        attachment_refs=[],
        coverage_state="partial" if incomplete else "complete",
        gaps=[{"field": "coverage", "reason": case, "source_refs": []}] if incomplete else [],
    )
    document["sources"].append(source)
    refs = {"source": source["record_id"], "coverage": source["record_id"]}
    packet: dict[str, Any]
    if workflow == "finance.bookkeeping-close":
        reconciliation = finance_record(
            "reconciliation:fixture",
            "exception" if incomplete else "complete",
            period={"start": "2026-09-01", "end": "2026-09-30"},
            account_refs=["fixture:bank"],
            source_refs=[source["record_id"]],
            matched_refs=[] if case == "no-activity" else ["bookkeeping:fixture"],
            unmatched_refs=[source["record_id"]] if incomplete else [],
            differences=[],
        )
        document["reconciliations"].append(reconciliation)
        packet = reconciliation
        if case != "no-activity":
            packet = finance_record(
                "bookkeeping:fixture",
                "prepared/unposted",
                date="2026-09-05",
                amount=money(1000),
                source_refs=[source["record_id"]],
                category={"state": "unresolved"},
                reconciliation_refs=[reconciliation["record_id"]],
            )
            document["bookkeeping"].append(packet)
    elif workflow == "finance.payment-request":
        packet = add_billing_fixture(document)
        refs["proposal"] = packet["record_id"]
    elif workflow == "finance.payment-request-followups":
        if case in {"report-and-attach", "partial-applied", "mark-paid-out-of-band"}:
            route = "paid-out-of-band" if case == "mark-paid-out-of-band" else "report-and-attach"
            packet = add_settlement_fixture(
                document, amount=400 if case == "partial-applied" else 1000, route=route
            )
            refs.update(source=packet["source_ref"], allocation=packet["record_id"])
        else:
            packet = finance_record(
                "collections:fixture",
                "recorded",
                version=1,
                invoice_ref="fixture:invoice",
                eligibility="suppressed" if case == "paid-suppressed" else "eligible",
                decision_reason=f"Synthetic lifecycle fixture: {case}",
                reminder_owner="agent",
                send_outcome="not-attempted" if case == "paid-suppressed" else "accepted",
            )
            document["collection_decisions"].append(packet)
    else:
        packet = finance_record(
            "tax:fixture:v1",
            "incomplete" if incomplete else "awaiting-advisor",
            version=1,
            tax_year=2026,
            as_of="2026-09-06",
            profile_refs=[],
            annual_inputs={},
            source_refs=[source["record_id"]],
            obligations=[],
            review_state={
                "control": "not-reviewed",
                "advisor": "not-reviewed",
                "owner": "not-adopted",
            },
            gaps=[
                {
                    "field": "tax_treatment",
                    "reason": "Synthetic incomplete annual inputs.",
                    "source_refs": [source["record_id"]],
                }
            ],
        )
        document["tax_packets"].append(packet)
    if workflow == "finance.cashflow-management":
        tax_packet = packet
        scenarios: dict[str, list[dict[str, Any]]] = {}
        for scenario, receipts in (("base", 10000), ("downside", 5000)):
            opening = 100000
            scenarios[scenario] = []
            for week in range(13):
                row = {
                    "week_number": week + 1,
                    "week_start": (date(2026, 9, 7) + timedelta(weeks=week)).isoformat(),
                    "source_refs": [source["record_id"]],
                    "gaps": [],
                }
                if incomplete:
                    row["gaps"] = [
                        {
                            "field": "opening_cash",
                            "reason": case,
                            "source_refs": [source["record_id"]],
                        }
                    ]
                else:
                    closing = opening + receipts - 4000
                    row.update(
                        opening_cash=money(opening),
                        operating_inflows=money(receipts),
                        operating_outflows=money(4000),
                        actual_tax_payments=money(0),
                        closing_cash=money(closing),
                        earmarked_reserve=money(10000),
                        free_cash=money(closing - 10000),
                    )
                    opening = closing
                scenarios[scenario].append(row)
        packet = finance_record(
            "forecast:fixture:v1",
            "forecast-incomplete" if incomplete else "prepared",
            version=1,
            as_of="2026-09-06",
            week_1_start="2026-09-07",
            source_refs=[source["record_id"]],
            assumptions=[],
            scenarios=scenarios,
        )
        document["cashflow_forecasts"].append(packet)
        refs.update(
            base=f"{packet['record_id']}#scenarios/base",
            downside=f"{packet['record_id']}#scenarios/downside",
        )
        if not incomplete:
            # Explicitly simulated reviewed reserve evidence, never actual advisor signoff.
            tax_packet["review_state"] = {
                "control": "approved",
                "advisor": "approved",
                "owner": "adopted",
            }
            tax_packet["gaps"] = []
            document["reserve_applications"].append(
                finance_record(
                    "reserve:fixture",
                    "applied",
                    tax_packet_ref=tax_packet["record_id"],
                    tax_packet_version=1,
                    forecast_ref=packet["record_id"],
                    forecast_version=1,
                    amount=money(10000),
                    applied_at=AT,
                )
            )
            refs["reserve"] = "reserve:fixture"
    refs["packet"] = packet["record_id"]
    packet_collection = next(
        key for key, rows in document.items() if isinstance(rows, list) and packet in rows
    )
    for name in (
        "review",
        "decision",
        "approval-finalize",
        "approval-send",
        "approval-resend",
        "approval-record",
        "approval-attach",
        "approval-mark",
    ):
        row = finance_record(
            f"review:{name}",
            "simulated",
            target_ref=packet["record_id"],
            **({"target_version": packet["version"]} if "version" in packet else {}),
            target_digest_sha256=material_record_digest(packet, packet_collection),
            scope=[name],
            authority="control-reviewer" if name == "review" else "owner",
            verdict="approved",
            actor="fixture:simulated-reviewer",
            decided_at=AT,
        )
        document["reviews"].append(row)
        refs[name] = row["record_id"]
    document["exceptions"].append(
        finance_record(
            "exception:fixture",
            "open" if incomplete else "not-applicable",
            kind="synthetic-coverage",
            reason=case,
            affected_refs=[source["record_id"]] if incomplete else [],
        )
    )
    refs["exception"] = "exception:fixture"
    document["handoffs"].append(
        finance_record(
            "handoff:fixture",
            "proposed",
            handoff_id="fixture-handoff-v1",
            from_workflow=workflow,
            to_workflow="finance.bookkeeping-close",
            source_packet_ref=packet["record_id"],
            **(
                {"source_version": packet["version"]}
                if "version" in packet
                else {"source_digest_sha256": snapshot_digest(packet)}
            ),
            conditions=["Synthetic rehearsal only; no next run authorized."],
            owner_ref="fixture:owner",
        )
    )
    refs["handoff"] = "handoff:fixture"
    document["write_proofs"].append(
        finance_record(
            "write:fixture",
            "prepared",
            prior_revision=0,
            prior_sha256=prior_hash,
            new_revision=1,
            changed_refs=list(records(document)),
            writer_ref="fixture:host",
            reread_result="pending",
        )
    )
    refs["write"] = "write:fixture"
    document["revision"] = 1
    write_document(index, document, prior_hash)
    assert not (finance / "packets").exists()
    return refs


def _fixture_approvals(workflow: str, case: str, step: str) -> list[str]:
    if workflow == "finance.payment-request":
        if step == "finalize-invoice":
            return ["owner-invoice-finalization"]
        if step == "send-invoice" and case == "sent":
            return ["owner-invoice-send"]
    if workflow == "finance.payment-request-followups":
        if step == "resend-approved" and case == "resent":
            return ["owner-followup-resend"]
        if step == "apply-settlement":
            if case in {"report-and-attach", "partial-applied"}:
                return ["owner-payment-record", "owner-payment-attachment"]
            if case == "mark-paid-out-of-band":
                return ["owner-external-settlement"]
    return []


@pytest.mark.parametrize(("workflow", "case"), CASES)
def test_finance_complete_template_lifecycle_keeps_packet_contents_external(
    session: Session,
    project_id: int,
    tmp_path: Path,
    workflow: str,
    case: str,
) -> None:
    repo = RunPlanRepository(session)
    inputs = _strict_inputs()[workflow]
    if case in {"report-and-attach", "partial-applied", "mark-paid-out-of-band"}:
        inputs.update(
            occurrence_mode="settlement-only", settlement_scope_ref="external-fixture:source"
        )
    finance = tmp_path / "finance"
    refs = _external_fixture(finance, workflow, case)
    plan = repo.create(project_id=project_id, template_key=workflow, inputs_json=inputs).data
    started = repo.start(plan.id, project_id=project_id).data
    outcomes: list[dict[str, Any]] = []

    for step in plan.steps:
        for gate in _fixture_approvals(workflow, case, step.step_id):
            # Local isolated repository fixture only, never agent self-approval
            # against a native or production daemon.
            repo.update(
                run_plan_id=plan.id,
                project_id=project_id,
                approval_key=gate,
                approval_status=ApprovalRequestStatus.APPROVED,
                decided_by="simulated-owner-in-isolated-test",
                decision_json={"approval_ref": refs["decision"], "simulation": True},
            )
        repo.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step.step_id,
            claimed_by="isolated-fixture-agent",
        )
        result = _step_result(workflow, case, step.step_id, refs)
        outcomes.append(result)
        recorded = repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step.step_id,
            status=RunPlanStepStatus.SUCCESS,
            result_json=result,
        ).data
        persisted = repo.get_step(plan.id, step.step_id, project_id=project_id)
        assert persisted.result_json == result

    assert recorded.status == "completed"
    assert all(step.status == RunPlanStepStatus.SUCCESS for step in recorded.steps)
    tracker = TrackerRepository(session).get(project_id=project_id, task_key=f"workflow-{plan.id}")
    assert tracker.tasks[0].status == "complete"
    assert all(ticket.status == "complete" for ticket in tracker.tickets)
    encoded = json.dumps(outcomes)
    for private_key in (
        "amount",
        "currency",
        "opening_cash",
        "transactions",
        "weeks",
        "reserve_applications",
    ):
        assert f'"{private_key}"' not in encoded
    document = read_document(finance / "finance.json")
    by_id = records(document)
    for ref in refs.values():
        assert ref.split("#", 1)[0] in by_id
    packet = by_id[refs["packet"]]
    assert packet["provenance"][0]["kind"] == "fixture"
    assert list(finance.glob("*.json")) == [finance / "finance.json"]
    if case == "no-activity":
        assert document["bookkeeping"] == []
        assert outcomes[-1]["bookkeeping_close_summary"]["prepared_record_refs"] == []
    if workflow == "finance.cashflow-management" and case == "complete":
        for scenario in ("base", "downside"):
            rows = packet["scenarios"][scenario]
            assert len(rows) == 13
            for ordinal, row in enumerate(rows):
                assert date.fromisoformat(row["week_start"]) == date(2026, 9, 7) + timedelta(
                    weeks=ordinal
                )
                assert (
                    row["closing_cash"]["amount_minor"]
                    == row["opening_cash"]["amount_minor"]
                    + row["operating_inflows"]["amount_minor"]
                    - row["operating_outflows"]["amount_minor"]
                    - row["actual_tax_payments"]["amount_minor"]
                )
                assert (
                    row["free_cash"]["amount_minor"]
                    == row["closing_cash"]["amount_minor"]
                    - row["earmarked_reserve"]["amount_minor"]
                )
                if ordinal:
                    assert row["opening_cash"] == rows[ordinal - 1]["closing_cash"]
        broken = copy.deepcopy(document)
        duplicate_reserve = copy.deepcopy(broken["reserve_applications"][0])
        duplicate_reserve["record_id"] = "reserve:duplicate"
        broken["reserve_applications"].append(duplicate_reserve)
        with pytest.raises(ValueError, match="duplicate tax/forecast reserve application"):
            validate(broken)
        broken = copy.deepcopy(document)
        broken["reviews"] = []  # Isolate arithmetic from the independent scope-digest check.
        broken["cashflow_forecasts"][0]["scenarios"]["base"][0]["closing_cash"]["amount_minor"] += 1
        with pytest.raises(ValueError, match="forecast cash arithmetic mismatch"):
            validate(broken)
    if inputs.get("occurrence_mode") == "settlement-only":
        assert all(
            result["followup_summary"]["resend_state"] == "not-requested" for result in outcomes
        )
        approvals = {
            approval.approval_key: approval.status for approval in recorded.approval_requests
        }
        assert approvals["owner-followup-resend"] == "pending"


@pytest.mark.parametrize("packet_status", ["packet-incomplete", "awaiting-advisor"])
def test_tax_preparation_reaches_real_review_gate_without_fabricating_advisor(
    session: Session, project_id: int, tmp_path: Path, packet_status: str
) -> None:
    repo = RunPlanRepository(session)
    inputs = {
        "workspace_ref": "finance-workspace:fixture",
        "tax_year": 2026,
        "estimate_as_of": "2026-09-05",
    }
    refs = _external_fixture(tmp_path / "finance", "finance.tax-estimates", packet_status)
    plan = repo.create(
        project_id=project_id, template_key="finance.tax-estimates", inputs_json=inputs
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    for step, status in (
        ("preflight", "scoped"),
        ("validate-current-sources", "current-sources-validated"),
        ("prepare-external-packet", packet_status),
    ):
        result = _preflight_result("finance.tax-estimates")
        summary = result["tax_estimate_summary"]
        summary.update(status=status, estimate_as_of="2026-09-05")
        if step != "preflight":
            summary["official_source_packet_ref"] = refs["source"]
        if step == "prepare-external-packet":
            summary.update(
                external_packet_ref=refs["packet"],
                source_completeness_ref=refs["coverage"],
                obligation_matrix_ref=refs["packet"],
                external_write_proof_ref=refs["write"],
            )
            if packet_status == "awaiting-advisor":
                summary.update(
                    projected_annual_liability_state="prepared",
                    current_installment_state="prepared",
                )
            else:
                summary["exception_refs"] = [refs["exception"]]
        repo.claim_step(
            run_plan_id=plan.id, run_id=started.run_id, step_id=step, claimed_by="fixture"
        )
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step,
            status=RunPlanStepStatus.SUCCESS,
            result_json=result,
        )
    with pytest.raises(ConflictError) as blocked:
        repo.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="cpa-ea-review",
            claimed_by="fixture",
        )
    assert blocked.value.data["approval_keys"] == ["cpa-ea-tax-review"]
    current = repo.get(plan.id, project_id=project_id)
    assert current.status == "started"
    assert {approval.status for approval in current.approval_requests} == {"pending"}
    assert "cpa_ea_review_ref" not in summary and "owner_approval_ref" not in summary
    assert "cashflow_handoff_refs" not in summary
