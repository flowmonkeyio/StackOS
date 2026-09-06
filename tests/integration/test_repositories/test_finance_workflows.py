"""Run-plan validation and grant tests for finance workflow templates."""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import Session

from stackos.db.models import ApprovalRequestStatus, RunPlanStepStatus
from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.repositories.base import ConflictError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader

FINANCE_WORKFLOW_KEYS = {
    "finance.receipt-intake",
    "finance.bookkeeping-close",
    "finance.payment-request",
    "finance.payment-request-followups",
    "finance.cashflow-management",
    "finance.tax-estimates",
}
COMMON_INPUTS = {
    "goal": "Prepare the selected finance workflow without a live provider side effect.",
    "backend_key": "local-json",
    "workspace_ref": "finance-workspace:local",
    "recording_mode": "prepared-unposted",
    "operator_policy_ref": "finance-policy:approved",
    "trigger_kind": "manual",
}


def _strict_inputs() -> dict[str, dict[str, Any]]:
    return {
        "finance.receipt-intake": {
            **COMMON_INPUTS,
            "intake_channel": "imap",
            "intake_scope_ref": "receipt-intake:september",
            "mailbox_ref": "imap-mailbox:receipts",
        },
        "finance.bookkeeping-close": {
            **COMMON_INPUTS,
            "period_ref": "finance-period:2026-09",
            "accounting_basis": "cash",
            "reconciliation_scope_ref": "reconciliation-scope:september",
        },
        "finance.payment-request": {
            **COMMON_INPUTS,
            "billing_request_ref": "billing-request:one",
            "billing_policy_ref": "billing-policy:standard",
        },
        "finance.payment-request-followups": {
            **COMMON_INPUTS,
            "review_window": {"start": "2026-09-01", "end": "2026-09-04"},
            "followup_policy_ref": "followup-policy:standard",
        },
        "finance.cashflow-management": {
            **COMMON_INPUTS,
            "forecast_scope_ref": "forecast-scope:q4",
            "forecast_as_of": "2026-09-04",
            "week_1_start": "2026-09-07",
            "forecast_horizon_weeks": 13,
            "cashflow_policy_ref": "cashflow-policy:standard",
        },
        "finance.tax-estimates": {
            **COMMON_INPUTS,
            "tax_year": 2026,
            "estimate_as_of": "2026-09-04",
            "tax_packet_scope_ref": "tax-scope:2026-q3",
            "official_source_packet_ref": "official-tax-sources:2026-q3",
            "cpa_ea_reviewer_ref": "advisor:qualified-reviewer",
        },
    }


def _manifest_action_refs() -> set[str]:
    return {
        f"{manifest.slug}.{action.key}"
        for manifest in BUILTIN_PLUGIN_MANIFESTS
        for action in manifest.actions
    }


def _step_grants(plan: Any, step_id: str) -> list[dict[str, Any]]:
    return [
        grant
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
        if grant["step_id"] == step_id
    ]


def _tax_estimate_result(status: str) -> dict[str, Any]:
    """Return a safe fixture result that satisfies the tax workflow output contract."""
    summary: dict[str, Any] = {
        "status": status,
        "tax_year": 2026,
        "estimate_as_of": "2026-09-04",
        "tax_packet_scope_ref": "tax-scope:2026-q3",
        "official_source_packet_ref": "official-tax-sources:2026-q3",
        "projected_annual_liability_state": "not-prepared",
        "current_installment_state": "not-prepared",
        "exception_refs": [],
    }
    if status in {"packet-prepared", "cpa-ea-reviewed", "owner-approved"}:
        summary.update(
            {
                "projected_annual_liability_state": "prepared",
                "current_installment_state": "prepared",
                "external_packet_ref": "tax-packet:fixture",
                "source_completeness_ref": "coverage:fixture",
                "obligation_matrix_ref": "tax-obligations:fixture",
                "external_write_proof_ref": "write:fixture",
            }
        )
    if status in {"cpa-ea-reviewed", "owner-approved"}:
        summary.update(
            {
                "projected_annual_liability_state": "reviewed",
                "current_installment_state": "reviewed",
                "cpa_ea_review_ref": "advisor-review:fixture",
                "control_review_ref": "control-review:fixture",
            }
        )
    if status == "owner-approved":
        summary.update(
            {
                "owner_approval_ref": "owner-approval:fixture",
                "cashflow_handoff_refs": ["cashflow-handoff:fixture"],
            }
        )
    return {"tax_estimate_summary": summary}


def _preflight_result(workflow_key: str) -> dict[str, Any]:
    summaries: dict[str, dict[str, Any]] = {
        "finance.receipt-intake": {
            "status": "scoped",
            "acknowledgement_state": "pending",
            "cleanup_state": "pending",
            "exception_refs": [],
        },
        "finance.bookkeeping-close": {
            "status": "scoped",
            "period_ref": "finance-period:2026-09",
            "accounting_basis": "cash",
            "reconciliation_scope_ref": "reconciliation-scope:september",
            "recording_state": "prepared/unposted",
            "reconciliation_state": "not-started",
            "exception_refs": [],
        },
        "finance.payment-request": {
            "status": "scoped",
            "billing_request_ref": "billing-request:one",
            "billing_policy_ref": "billing-policy:standard",
            "recovery_state": "not-needed",
            "exception_refs": [],
        },
        "finance.payment-request-followups": {
            "status": "scoped",
            "occurrence_mode": "followup-only",
            "settlement_state": "not-requested",
            "resend_state": "not-requested",
            "recovery_state": "not-needed",
            "exception_refs": [],
        },
        "finance.cashflow-management": {
            "status": "scoped",
            "forecast_scope_ref": "forecast-scope:q4",
            "forecast_as_of": "2026-09-04",
            "week_1_start": "2026-09-07",
            "forecast_horizon_weeks": 13,
            "exception_refs": [],
        },
        "finance.tax-estimates": _tax_estimate_result("scoped")["tax_estimate_summary"],
    }
    output_keys = {
        "finance.receipt-intake": "receipt_intake_summary",
        "finance.bookkeeping-close": "bookkeeping_close_summary",
        "finance.payment-request": "payment_request_summary",
        "finance.payment-request-followups": "followup_summary",
        "finance.cashflow-management": "cashflow_summary",
        "finance.tax-estimates": "tax_estimate_summary",
    }
    return {output_keys[workflow_key]: summaries[workflow_key]}


def test_finance_workflows_pass_structural_and_strict_validation_with_safe_inputs(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    inputs_by_workflow = _strict_inputs()
    loader = WorkflowTemplateLoader(session)

    listed = {item.key for item in loader.list_templates(plugin_slug="finance").templates}
    assert listed >= FINANCE_WORKFLOW_KEYS

    for workflow_key, inputs in inputs_by_workflow.items():
        structural = repo.validate_plan(
            project_id=project_id,
            template_key=workflow_key,
            plugin_slug="finance",
            enforce_required_inputs=False,
        )
        strict = repo.validate_plan(
            project_id=project_id,
            template_key=workflow_key,
            plugin_slug="finance",
            inputs_json=inputs,
            enforce_required_inputs=True,
        )

        assert structural.valid is True, (workflow_key, structural.errors)
        assert structural.warnings == [], (workflow_key, structural.warnings)
        assert strict.valid is True, (workflow_key, strict.errors)
        assert strict.warnings == [], (workflow_key, strict.warnings)
        assert strict.plan is not None
        assert strict.plan.grant_snapshot_json["artifact_grant_policy"] == "explicit"


@pytest.mark.parametrize("workflow_key", sorted(FINANCE_WORKFLOW_KEYS))
def test_finance_preflight_records_truthful_scoped_results_without_future_refs(
    session: Session,
    project_id: int,
    workflow_key: str,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        template_key=workflow_key,
        plugin_slug="finance",
        inputs_json=_strict_inputs()[workflow_key],
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="preflight",
        claimed_by="fixture-agent",
    )

    result = _preflight_result(workflow_key)
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="preflight",
        status=RunPlanStepStatus.SUCCESS,
        result_json=result,
    )

    recorded = repo.get_step(plan.id, "preflight", project_id=project_id)
    assert recorded.status == RunPlanStepStatus.SUCCESS
    assert recorded.result_json == result


def test_finance_required_inputs_fail_when_each_required_key_is_missing(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    loader = WorkflowTemplateLoader(session)

    for workflow_key, inputs in _strict_inputs().items():
        described = loader.describe_template(
            project_id=project_id,
            key=workflow_key,
            plugin_slug="finance",
        )
        for item in described.spec.inputs:
            if not item.required:
                continue
            missing_inputs = dict(inputs)
            missing_inputs.pop(item.key)
            result = repo.validate_plan(
                project_id=project_id,
                template_key=workflow_key,
                plugin_slug="finance",
                inputs_json=missing_inputs,
                enforce_required_inputs=True,
            )
            assert result.valid is False, (workflow_key, item.key)
            assert item.key in result.errors[0].message


@pytest.mark.parametrize("workflow_key", sorted(FINANCE_WORKFLOW_KEYS))
def test_finance_minimal_run_choices_validate_without_future_artifact_inputs(
    session: Session,
    project_id: int,
    workflow_key: str,
) -> None:
    loader = WorkflowTemplateLoader(session)
    spec = loader.describe_template(
        project_id=project_id, key=workflow_key, plugin_slug="finance"
    ).spec
    required = {item.key for item in spec.inputs if item.required}
    inputs = {
        key: value for key, value in _strict_inputs()[workflow_key].items() if key in required
    }
    result = RunPlanRepository(session).validate_plan(
        project_id=project_id,
        template_key=workflow_key,
        plugin_slug="finance",
        inputs_json=inputs,
        enforce_required_inputs=True,
    )
    assert result.valid, result.errors
    assert "entity_tax_classification" not in required
    assert "official_source_packet_ref" not in required
    assert "cpa_ea_reviewer_ref" not in required


def test_finance_action_refs_resolve_and_grants_exclude_finance_state_owners(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    loader = WorkflowTemplateLoader(session)
    known_actions = _manifest_action_refs()

    for workflow_key, inputs in _strict_inputs().items():
        described = loader.describe_template(
            project_id=project_id,
            key=workflow_key,
            plugin_slug="finance",
        )
        validation = repo.validate_plan(
            project_id=project_id,
            template_key=workflow_key,
            plugin_slug="finance",
            inputs_json=inputs,
            enforce_required_inputs=True,
        )
        assert validation.valid is True, (workflow_key, validation.errors)
        assert validation.plan is not None
        plan = validation.plan
        action_refs = {
            action.action for action in described.spec.action_contracts if action.action is not None
        }
        assert action_refs <= known_actions

        resolved_actions = {
            item["action_ref"] for item in plan.grant_snapshot_json["resolved_action_contracts"]
        }
        assert resolved_actions == action_refs

        grants = plan.grant_snapshot_json["mcp_tool_grants"]
        assert all(
            grant.get("tool")
            not in {"resource.upsert", "communication.send", "communication.reply"}
            for grant in grants
        )
        assert all("artifact.create" not in grant.get("tools", []) for grant in grants)
        assert all("artifact.update" not in grant.get("tools", []) for grant in grants)
        assert all("artifact.archive" not in grant.get("tools", []) for grant in grants)
        assert all("artifact.supersede" not in grant.get("tools", []) for grant in grants)

        for step in plan.steps:
            step_grants = _step_grants(plan, step.id)
            granted_actions = {
                action_ref
                for grant in step_grants
                if grant.get("tool") == "action.execute"
                for action_ref in grant.get("action_refs", [])
            }
            assert set(step.action_refs).issubset(granted_actions)


def test_finance_payment_workflows_keep_distinct_action_level_owner_gates(
    session: Session,
    project_id: int,
) -> None:
    """Lock the static gate names alongside the dynamic MCP proof.

    The finalize proof lives at the MCP boundary because approval enforcement
    happens during ``action.execute``.  This test also protects the separate
    customer-send and follow-up resend contracts from being collapsed into a
    generic approval.
    """
    loader = WorkflowTemplateLoader(session)
    payment = loader.describe_template(
        project_id=project_id,
        key="finance.payment-request",
        plugin_slug="finance",
    ).spec
    followups = loader.describe_template(
        project_id=project_id,
        key="finance.payment-request-followups",
        plugin_slug="finance",
    ).spec

    payment_actions = {item.key: item for item in payment.action_contracts}
    followup_actions = {item.key: item for item in followups.action_contracts}
    payment_steps = {item.id: item for item in payment.steps}
    followup_steps = {item.id: item for item in followups.steps}

    assert payment_actions["stripe_invoices_finalize"].approval_ref == (
        "owner-invoice-finalization"
    )
    assert payment_actions["stripe_invoices_send"].approval_ref == "owner-invoice-send"
    assert followup_actions["stripe_invoices_send"].approval_ref == "owner-followup-resend"
    assert followup_actions["stripe_payment_records_report"].approval_ref == "owner-payment-record"
    assert followup_actions["stripe_invoices_attach_payment"].approval_ref == (
        "owner-payment-attachment"
    )
    assert followup_actions["stripe_invoices_mark_paid_out_of_band"].approval_ref == (
        "owner-external-settlement"
    )
    assert payment_steps["finalize-invoice"].action_refs == [
        "stripe_invoices_finalize",
        "stripe_invoices_retrieve",
        "stripe_invoice_items_list",
    ]
    assert payment_steps["send-invoice"].action_refs == [
        "stripe_invoices_send",
        "stripe_invoices_retrieve",
        "stripe_invoice_items_list",
    ]
    assert followup_steps["resend-approved"].action_refs == [
        "stripe_invoices_send",
        "stripe_invoices_retrieve",
        "stripe_invoice_payments_list",
        "stripe_disputes_list",
        "stripe_disputes_retrieve",
    ]
    assert followup_steps["apply-settlement"].action_refs == [
        "stripe_payment_records_report",
        "stripe_invoices_attach_payment",
        "stripe_invoices_mark_paid_out_of_band",
        "stripe_invoices_retrieve",
        "stripe_invoice_payments_list",
        "stripe_payment_records_retrieve",
        "stripe_payment_records_list",
        "stripe_payment_intents_retrieve",
    ]


def test_finance_settlement_only_plan_has_action_gates_without_a_new_workflow(
    session: Session,
    project_id: int,
) -> None:
    """Keep reconciliation within follow-ups, with no core finance state grant."""
    inputs = {
        **_strict_inputs()["finance.payment-request-followups"],
        "occurrence_mode": "settlement-only",
        "settlement_scope_ref": "received-payment:bank-transfer-one",
    }
    repo = RunPlanRepository(session)
    validation = repo.validate_plan(
        project_id=project_id,
        template_key="finance.payment-request-followups",
        plugin_slug="finance",
        inputs_json=inputs,
        enforce_required_inputs=True,
    )

    assert validation.valid is True, validation.errors
    assert validation.plan is not None
    plan = validation.plan
    assert {step.id for step in plan.steps} >= {
        "prepare-settlement",
        "review-settlement",
        "apply-settlement",
        "suppress-ineligible",
        "review-eligible",
        "resend-approved",
        "record-outcome",
    }
    apply_grants = _step_grants(plan, "apply-settlement")
    granted_actions = {
        action_ref
        for grant in apply_grants
        if grant.get("tool") == "action.execute"
        for action_ref in grant.get("action_refs", [])
    }
    assert {
        "finance.stripe.payment-records.report",
        "finance.stripe.invoices.attach-payment",
        "finance.stripe.invoices.mark-paid-out-of-band",
    } <= granted_actions
    assert all(
        grant.get("tool") not in {"resource.upsert", "communication.send", "communication.reply"}
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
    )


def test_finance_tax_estimates_requires_cpa_ea_then_owner_at_their_actual_steps(
    session: Session,
    project_id: int,
) -> None:
    """Exercise the real tax template without a provider or tax side effect."""
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        template_key="finance.tax-estimates",
        plugin_slug="finance",
        inputs_json=_strict_inputs()["finance.tax-estimates"],
    ).data
    started = repo.start(plan.id, project_id=project_id).data

    for step_id, status in (
        ("preflight", "scoped"),
        ("validate-current-sources", "current-sources-validated"),
        ("prepare-external-packet", "packet-prepared"),
    ):
        result = _tax_estimate_result(status)
        summary = result["tax_estimate_summary"]
        assert "cpa_ea_review_ref" not in summary
        assert "owner_approval_ref" not in summary
        assert "cashflow_handoff_refs" not in summary
        repo.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step_id,
            claimed_by="fixture-agent",
        )
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step_id,
            status=RunPlanStepStatus.SUCCESS,
            result_json=result,
        )

    with pytest.raises(ConflictError) as cpa_blocked:
        repo.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="cpa-ea-review",
            claimed_by="fixture-agent",
        )
    assert cpa_blocked.value.data["approval_keys"] == ["cpa-ea-tax-review"]

    repo.update(
        run_plan_id=plan.id,
        approval_key="cpa-ea-tax-review",
        approval_status=ApprovalRequestStatus.APPROVED,
        decided_by="cpa-ea-fixture",
        decision_json={"review_ref": "advisor-review:fixture"},
        project_id=project_id,
    )
    cpa_step = repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="cpa-ea-review",
        claimed_by="fixture-agent",
    ).data
    assert cpa_step.step_id == "cpa-ea-review"
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="cpa-ea-review",
        status=RunPlanStepStatus.SUCCESS,
        result_json=_tax_estimate_result("cpa-ea-reviewed"),
    )
    cpa_result = _tax_estimate_result("cpa-ea-reviewed")["tax_estimate_summary"]
    assert cpa_result["cpa_ea_review_ref"] == "advisor-review:fixture"
    assert "owner_approval_ref" not in cpa_result
    assert "cashflow_handoff_refs" not in cpa_result

    with pytest.raises(ConflictError) as owner_blocked:
        repo.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="owner-review-and-handoff",
            claimed_by="fixture-agent",
        )
    assert owner_blocked.value.data["approval_keys"] == ["owner-tax-packet-approval"]

    repo.update(
        run_plan_id=plan.id,
        approval_key="owner-tax-packet-approval",
        approval_status=ApprovalRequestStatus.APPROVED,
        decided_by="owner-fixture",
        decision_json={"approval_ref": "owner-approval:fixture"},
        project_id=project_id,
    )
    owner_step = repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="owner-review-and-handoff",
        claimed_by="fixture-agent",
    ).data
    assert owner_step.step_id == "owner-review-and-handoff"
    completed = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="owner-review-and-handoff",
        status=RunPlanStepStatus.SUCCESS,
        result_json=_tax_estimate_result("owner-approved"),
    ).data
    assert completed.status == "completed"
