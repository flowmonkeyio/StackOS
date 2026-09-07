"""Static contract tests for the transport-only finance workflow package."""

from __future__ import annotations

from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from stackos.workflows.template_schema import validate_workflow_template_obj

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "plugins" / "finance" / "workflows"
BACKEND_CONTRACT = ROOT / "plugins" / "finance" / "references" / "backend-contract.md"
APPROVAL_MATRIX = ROOT / "plugins" / "finance" / "references" / "approval-matrix.md"

EXPECTED_WORKFLOW_KEYS = {
    "finance.receipt-intake",
    "finance.bookkeeping-close",
    "finance.payment-request",
    "finance.payment-request-followups",
    "finance.cashflow-management",
    "finance.tax-estimates",
}
COMMON_REQUIRED_INPUTS = {
    "workspace_ref",
}


def _workflows() -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path
        loaded[payload["key"]] = payload
    return loaded


def _by_key(items: list[dict]) -> dict[str, dict]:
    return {item["key"]: item for item in items}


def test_finance_scope_remains_standalone_with_optional_agency_context() -> None:
    for workflow in _workflows().values():
        inputs = _by_key(workflow["inputs"])
        assert inputs["workspace_ref"]["required"] is True
        assert (
            "standalone business directory or agency root" in inputs["workspace_ref"]["description"]
        )
        assert not any(
            "agency" in item["key"] for item in workflow.get("capability_requirements", [])
        )
        assert not any(
            "agency" in item["key"] or "project" in item["key"]
            for item in workflow["inputs"]
            if item.get("required")
        )
        assert workflow.get("resource_contracts", []) == []
        assert {item["skill_preset_ref"] for item in workflow["skill_preset_requirements"]} == {
            "stackos.finance.department-orchestrator"
        }


def test_finance_yaml_has_no_silently_overridden_mapping_keys() -> None:
    def check(node: yaml.Node, path: Path) -> None:
        if isinstance(node, yaml.MappingNode):
            keys = [key.value for key, _ in node.value]
            assert len(keys) == len(set(keys)), (path, node.start_mark.line + 1, keys)
            for _, child in node.value:
                check(child, path)
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                check(child, path)

    for path in sorted((ROOT / "plugins" / "finance").rglob("*.yaml")):
        node = yaml.compose(path.read_text(encoding="utf-8"))
        assert node is not None, path
        check(node, path)


def _summary_schema(workflow: dict) -> dict:
    outputs = _by_key(workflow["outputs"])
    assert len(outputs) == 1
    return next(iter(outputs.values()))["schema"]


def _truthful_preflight_summaries() -> dict[str, dict]:
    return {
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
            "billing_request_ref": "billing-request:fixture",
            "billing_policy_ref": "billing-policy:fixture",
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
        "finance.tax-estimates": {
            "status": "scoped",
            "tax_year": 2026,
            "estimate_as_of": "2026-09-04",
            "tax_packet_scope_ref": "tax-scope:2026-q3",
            "official_source_packet_ref": "official-tax-sources:2026-q3",
            "projected_annual_liability_state": "not-prepared",
            "current_installment_state": "not-prepared",
            "exception_refs": [],
        },
    }


def _terminal_summaries() -> dict[str, tuple[dict, tuple[str, ...]]]:
    return {
        "finance.receipt-intake": (
            {
                "status": "cleaned-up",
                "receipt_record_ref": "receipt-record:fixture",
                "source_identity_ref": "receipt-source:fixture",
                "backend_write_proof_ref": "finance-write-proof:fixture",
                "acknowledgement_state": "acknowledged",
                "cleanup_state": "cleaned-up",
                "exception_refs": [],
            },
            ("receipt_record_ref", "source_identity_ref", "backend_write_proof_ref"),
        ),
        "finance.bookkeeping-close": (
            {
                "status": "handed-off",
                "period_ref": "finance-period:2026-09",
                "accounting_basis": "cash",
                "reconciliation_scope_ref": "reconciliation-scope:september",
                "recording_state": "prepared/unposted",
                "prepared_record_refs": ["prepared-close:fixture"],
                "reconciliation_state": "complete",
                "source_completeness_ref": "coverage:fixture",
                "source_coverage_state": "complete",
                "control_review_ref": "control-review:fixture",
                "external_write_proof_ref": "write:fixture",
                "exception_refs": [],
                "approval_refs": ["external-review:fixture"],
                "handoff_refs": ["cashflow-handoff:fixture"],
            },
            (
                "prepared_record_refs",
                "control_review_ref",
                "source_completeness_ref",
                "external_write_proof_ref",
                "handoff_refs",
            ),
        ),
        "finance.payment-request": (
            {
                "status": "recorded",
                "billing_request_ref": "billing-request:fixture",
                "billing_policy_ref": "billing-policy:fixture",
                "proposal_version_ref": "proposal:fixture-v1",
                "control_review_ref": "control-review:fixture",
                "delivery_state": "sent",
                "customer_ref": "stripe-customer:fixture",
                "invoice_ref": "stripe-invoice:fixture",
                "approval_refs": ["finalize-approval:fixture", "send-approval:fixture"],
                "action_call_refs": ["action-call:fixture"],
                "external_write_proof_ref": "finance-write-proof:fixture",
                "recovery_state": "not-needed",
                "handoff_refs": ["followup-handoff:fixture"],
                "exception_refs": [],
            },
            (
                "customer_ref",
                "invoice_ref",
                "approval_refs",
                "action_call_refs",
                "external_write_proof_ref",
            ),
        ),
        "finance.payment-request-followups": (
            {
                "status": "recorded",
                "occurrence_mode": "followup-only",
                "settlement_state": "not-requested",
                "resend_state": "suppressed",
                "suppressed_invoice_refs": ["stripe-invoice:suppressed"],
                "eligible_invoice_refs": [],
                "action_call_refs": ["action-call:fixture"],
                "followup_decision_ref": "followup-decision:fixture",
                "external_write_proof_ref": "finance-write-proof:fixture",
                "recovery_state": "not-needed",
                "exception_refs": [],
                "handoff_refs": ["bookkeeping-handoff:fixture"],
            },
            ("action_call_refs", "external_write_proof_ref"),
        ),
        "finance.cashflow-management": (
            {
                "status": "handed-off",
                "forecast_scope_ref": "forecast-scope:q4",
                "forecast_ref": "forecast:fixture",
                "forecast_as_of": "2026-09-04",
                "week_1_start": "2026-09-07",
                "forecast_horizon_weeks": 13,
                "base_scenario_ref": "forecast-base:fixture",
                "base_scenario_state": "reviewed",
                "downside_scenario_ref": "forecast-downside:fixture",
                "downside_scenario_state": "reviewed",
                "source_proof_refs": ["source-proof:fixture"],
                "source_completeness_ref": "coverage:fixture",
                "control_review_ref": "control-review:fixture",
                "external_write_proof_ref": "write:fixture",
                "uncertainty_refs": [],
                "exception_refs": [],
                "handoff_refs": ["tax-handoff:fixture"],
            },
            (
                "forecast_ref",
                "base_scenario_ref",
                "downside_scenario_ref",
                "source_proof_refs",
                "handoff_refs",
            ),
        ),
        "finance.tax-estimates": (
            {
                "status": "owner-approved",
                "tax_year": 2026,
                "estimate_as_of": "2026-09-04",
                "tax_packet_scope_ref": "tax-scope:2026-q3",
                "official_source_packet_ref": "official-tax-sources:2026-q3",
                "projected_annual_liability_state": "reviewed",
                "current_installment_state": "reviewed",
                "external_packet_ref": "tax-packet:fixture",
                "source_completeness_ref": "coverage:fixture",
                "obligation_matrix_ref": "tax-obligations:fixture",
                "control_review_ref": "control-review:fixture",
                "external_write_proof_ref": "write:fixture",
                "cpa_ea_review_ref": "advisor-review:fixture",
                "owner_approval_ref": "owner-approval:fixture",
                "exception_refs": [],
                "cashflow_handoff_refs": ["cashflow-handoff:fixture"],
            },
            (
                "external_packet_ref",
                "cpa_ea_review_ref",
                "owner_approval_ref",
                "cashflow_handoff_refs",
            ),
        ),
    }


def test_finance_package_defines_exactly_the_six_external_backend_workflows() -> None:
    workflows = _workflows()

    assert set(workflows) == EXPECTED_WORKFLOW_KEYS
    for key, workflow in workflows.items():
        validation = validate_workflow_template_obj(workflow)
        assert validation.valid is True, (key, validation.errors)
        assert workflow["metadata"]["artifact_grant_policy"] == "explicit"
        assert workflow["metadata"]["trigger_kinds"] == ["manual", "external"]
        assert workflow.get("resource_contracts", []) == []
        assert workflow.get("context_requirements", []) == []
        assert [item["skill_ref"] for item in workflow["skill_requirements"]] == ["stackos:stackos"]
        assert workflow["skill_requirements"][0]["requirement"] == "required"
        assert workflow["skill_preset_requirements"][0]["skill_preset_ref"] == (
            "stackos.finance.department-orchestrator"
        )
        inputs = _by_key(workflow["inputs"])
        assert {
            item["key"] for item in workflow["inputs"] if item["required"]
        } >= COMMON_REQUIRED_INPUTS
        assert inputs["backend_key"]["schema"]["const"] == "local-json"
        assert inputs["recording_mode"]["schema"]["const"] == "prepared-unposted"
        assert "entity_tax_classification" not in inputs
        assert inputs["business_profile_ref"]["required"] is False
        for setup_key in ("backend_key", "recording_mode", "operator_policy_ref", "trigger_kind"):
            assert inputs[setup_key]["required"] is False
        assert inputs["trigger_kind"]["schema"]["enum"] == ["manual", "external"]


def test_finance_workflows_route_every_local_packet_to_one_json_record() -> None:
    for key, workflow in _workflows().items():
        policy = _by_key(workflow["policies"])["local-json-single-source"]
        description = policy["description"].lower()
        for required in (
            "finance.json",
            "sole authoritative",
            "mutable setup",
            "record_id",
            "finance.md",
            "csv",
            "provider",
        ):
            assert required in description, (key, required)
        preflight = next(step for step in workflow["steps"] if step["id"] == "preflight")
        assert "local-json-single-source" in preflight["policy_refs"]
        instructions = " ".join(preflight["instructions"]).lower()
        for required in ("schema", "revision", "digest", "first-time setup", "readback"):
            assert required in instructions, (key, required)
        assert "local-markdown" not in str(workflow).lower()


def test_finance_workflows_keep_transport_and_finance_authority_separate() -> None:
    workflows = _workflows()
    action_refs = {
        action["action"]
        for workflow in workflows.values()
        for action in workflow.get("action_contracts", [])
    }

    assert {"communication.send", "communication.reply"}.isdisjoint(action_refs)
    assert not any("filesystem" in action for action in action_refs)
    allowed_refund_reads = {
        "finance.stripe.refunds.list",
        "finance.stripe.refunds.retrieve",
    }
    assert not any(
        "refund" in action and action not in allowed_refund_reads for action in action_refs
    )
    for workflow in workflows.values():
        assert workflow.get("resource_contracts", []) == []
        for action in workflow.get("action_contracts", []):
            assert action["action"] != "finance.stripe.refunds.create"
            assert "filesystem" not in action["action"]


def test_finance_contract_discloses_the_existing_non_authoritative_action_audit() -> None:
    text = " ".join(BACKEND_CONTRACT.read_text(encoding="utf-8").lower().split())
    payment = _workflows()["finance.payment-request"]
    payment_policies = " ".join(item["description"] for item in payment["policies"]).lower()

    assert "generic action executor" in text
    assert "sanitized request/response envelope" in text
    assert "bounded monetary" in text
    assert "non-authoritative transport evidence" in text
    assert "field-level audit projection" in text
    assert "generic action audit may retain" in payment_policies


def test_receipt_contract_uses_the_common_external_intake_and_skips_imap_off_route() -> None:
    workflow = _workflows()["finance.receipt-intake"]
    inputs = _by_key(workflow["inputs"])
    actions = _by_key(workflow["action_contracts"])
    steps = {item["id"]: item for item in workflow["steps"]}

    assert inputs["intake_channel"]["schema"]["enum"] == ["imap", "chat-upload", "manual-upload"]
    assert inputs["intake_scope_ref"]["required"] is False
    assert inputs["mailbox_ref"]["required"] is False
    assert {item["action"] for item in actions.values()} == {
        "communications.imap.messages.search",
        "communications.imap.message.export",
        "communications.imap.message.mark_seen",
        "communications.imap.message.export.cleanup",
    }
    acknowledge_instructions = " ".join(steps["acknowledge-imap"]["instructions"]).lower()
    assert "expected uidvalidity" in acknowledge_instructions
    for step_id in (
        "discover-or-receive",
        "stage-imap-evidence",
        "acknowledge-imap",
        "cleanup-staging",
    ):
        assert "not applicable" in " ".join(steps[step_id]["instructions"]).lower()
    assert "receipt-exception-review" not in steps["validate-and-write-external"].get(
        "approval_refs", []
    )


def test_approval_bindings_and_finance_state_boundaries_are_exact() -> None:
    workflows = _workflows()
    payment_actions = _by_key(workflows["finance.payment-request"]["action_contracts"])
    followup_actions = _by_key(workflows["finance.payment-request-followups"]["action_contracts"])
    tax_steps = {item["id"]: item for item in workflows["finance.tax-estimates"]["steps"]}

    assert payment_actions["stripe_invoices_finalize"]["approval_ref"] == (
        "owner-invoice-finalization"
    )
    assert payment_actions["stripe_invoices_send"]["approval_ref"] == "owner-invoice-send"
    assert followup_actions["stripe_invoices_send"]["approval_ref"] == "owner-followup-resend"
    assert followup_actions["stripe_payment_records_report"] == {
        "key": "stripe_payment_records_report",
        "action": "finance.stripe.payment-records.report",
        "capability": "stripe-reconciliation",
        "provider": "stripe",
        "auth_ref": "stripe",
        "approval_ref": "owner-payment-record",
        "risk_level": "write",
    }
    assert followup_actions["stripe_invoices_attach_payment"]["approval_ref"] == (
        "owner-payment-attachment"
    )
    assert followup_actions["stripe_invoices_mark_paid_out_of_band"]["approval_ref"] == (
        "owner-external-settlement"
    )
    assert tax_steps["cpa-ea-review"]["approval_refs"] == ["cpa-ea-tax-review"]
    assert tax_steps["owner-review-and-handoff"]["approval_refs"] == ["owner-tax-packet-approval"]

    bookkeeping_output = _by_key(workflows["finance.bookkeeping-close"]["outputs"])[
        "bookkeeping_close_summary"
    ]["schema"]
    assert bookkeeping_output["properties"]["recording_state"]["const"] == "prepared/unposted"

    cashflow_output = _by_key(workflows["finance.cashflow-management"]["outputs"])[
        "cashflow_summary"
    ]["schema"]
    assert cashflow_output["properties"]["forecast_horizon_weeks"]["const"] == 13
    assert {"base_scenario_ref", "downside_scenario_ref"} <= set(cashflow_output["properties"])

    tax_workflow = workflows["finance.tax-estimates"]
    assert tax_workflow.get("action_contracts", []) == []
    tax_output = _by_key(tax_workflow["outputs"])["tax_estimate_summary"]["schema"]
    assert {"projected_annual_liability_state", "current_installment_state"} <= set(
        tax_output["properties"]
    )

    followup_output = _by_key(workflows["finance.payment-request-followups"]["outputs"])[
        "followup_summary"
    ]["schema"]
    assert "followup_scope_ref" in followup_output["properties"]
    assert "followup_scope_ref" not in followup_output["required"]
    assert followup_output["properties"]["occurrence_mode"]["enum"] == [
        "followup-only",
        "settlement-only",
    ]
    assert followup_output["properties"]["resend_state"]["enum"] == [
        "not-requested",
        "suppressed",
        "sent",
        "test-accepted",
        "unknown",
    ]


def test_followup_settlement_branch_is_optional_safe_and_has_no_money_movement() -> None:
    followups = _workflows()["finance.payment-request-followups"]
    inputs = _by_key(followups["inputs"])
    actions = _by_key(followups["action_contracts"])
    steps = {item["id"]: item for item in followups["steps"]}
    policies = " ".join(item["description"] for item in followups["policies"]).lower()
    instructions = " ".join(
        instruction for step in followups["steps"] for instruction in step.get("instructions", [])
    ).lower()

    assert inputs["occurrence_mode"]["schema"] == {
        "type": "string",
        "enum": ["followup-only", "settlement-only"],
    }
    assert inputs["occurrence_mode"]["default"] == "followup-only"
    assert inputs["settlement_scope_ref"]["required"] is False
    assert actions["stripe_payment_records_list"]["optional"] is True
    assert set(steps) >= {
        "prepare-settlement",
        "review-settlement",
        "apply-settlement",
        "suppress-ineligible",
        "review-eligible",
        "resend-approved",
        "record-outcome",
    }
    assert steps["apply-settlement"]["action_refs"] == [
        "stripe_payment_records_report",
        "stripe_invoices_attach_payment",
        "stripe_invoices_mark_paid_out_of_band",
        "stripe_invoices_retrieve",
        "stripe_invoice_payments_list",
        "stripe_payment_records_retrieve",
        "stripe_payment_records_list",
        "stripe_payment_intents_retrieve",
    ]
    assert {
        "finance.stripe.payment-intents.retrieve",
        "finance.stripe.payment-records.retrieve",
        "finance.stripe.payment-records.report",
        "finance.stripe.invoices.attach-payment",
        "finance.stripe.invoices.mark-paid-out-of-band",
    } <= {item["action"] for item in actions.values()}
    assert "settlement-only" in instructions
    assert "do not call finance.stripe.invoices.send" in instructions
    assert "partial" in instructions
    assert "split" in instructions
    assert "overpayment" in instructions
    assert "mismatch" in instructions
    assert "payment_reference_sha256" in instructions
    assert "independently retrieve" in instructions
    assert "stable operation key" in instructions
    assert "report+mark" in policies
    assert "charge, transfer, pay, or move money" in policies


def test_settlement_guidance_does_not_require_reminders_or_reject_supported_partial() -> None:
    """Alignment regression; independent agent rehearsal owns decision proof."""
    workflow = _workflows()["finance.payment-request-followups"]
    preflight = next(step for step in workflow["steps"] if step["id"] == "preflight")
    instructions = " ".join(preflight.get("instructions", [])).lower()
    assert "for followup-only" in instructions
    assert "for settlement-only" in instructions
    assert "no reminder" in instructions
    source = (ROOT / "plugins/finance/agent-presets/finance.yaml").read_text()
    local = (ROOT / ".codex/agents/finance-billing-collections-operator.toml").read_text()
    assert "stop partial/split" not in source
    assert "Partial/split/overpayment/mismatch/unknown cases stop" not in local
    for text in (source, local):
        assert "exact supported partial" in text
        assert "unsynchronized" in text


def test_settlement_only_summary_cannot_claim_a_resend_or_financial_contents() -> None:
    schema = _summary_schema(_workflows()["finance.payment-request-followups"])
    settlement = {
        "status": "settlement-recorded",
        "occurrence_mode": "settlement-only",
        "settlement_state": "payment-attached",
        "resend_state": "not-requested",
        "settlement_decision_ref": "settlement-decision:fixture",
        "payment_source_ref": "payment-source:fixture",
        "payment_allocation_ref": "payment-allocation:fixture",
        "payment_ref": "stripe-payment-record:fixture",
        "approval_refs": ["payment-record:fixture", "payment-attachment:fixture"],
        "action_call_refs": ["action-call:fixture"],
        "external_write_proof_ref": "finance-write-proof:fixture",
        "recovery_state": "not-needed",
        "exception_refs": [],
        "handoff_refs": ["bookkeeping-handoff:fixture"],
    }
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(settlement)) == []
    assert {"amount", "currency", "customer", "payment_reference"}.isdisjoint(schema["properties"])

    settlement["resend_state"] = "sent"
    assert list(validator.iter_errors(settlement))


def test_finance_output_schemas_accept_truthful_preflight_without_future_refs() -> None:
    workflows = _workflows()
    future_ref_fields = {
        "finance.receipt-intake": {
            "receipt_record_ref",
            "source_identity_ref",
            "backend_write_proof_ref",
        },
        "finance.bookkeeping-close": {
            "prepared_record_refs",
            "approval_refs",
            "handoff_refs",
        },
        "finance.payment-request": {
            "customer_ref",
            "invoice_ref",
            "approval_refs",
            "action_call_refs",
            "external_write_proof_ref",
            "handoff_refs",
        },
        "finance.payment-request-followups": {
            "suppressed_invoice_refs",
            "eligible_invoice_refs",
            "approval_refs",
            "action_call_refs",
            "external_write_proof_ref",
            "handoff_refs",
        },
        "finance.cashflow-management": {
            "forecast_ref",
            "base_scenario_ref",
            "base_scenario_state",
            "downside_scenario_ref",
            "downside_scenario_state",
            "source_proof_refs",
            "uncertainty_refs",
            "handoff_refs",
        },
        "finance.tax-estimates": {
            "external_packet_ref",
            "cpa_ea_review_ref",
            "owner_approval_ref",
            "cashflow_handoff_refs",
        },
    }

    for workflow_key, summary in _truthful_preflight_summaries().items():
        schema = _summary_schema(workflows[workflow_key])
        Draft202012Validator.check_schema(schema)
        assert list(Draft202012Validator(schema).iter_errors(summary)) == []
        assert future_ref_fields[workflow_key].isdisjoint(summary)


def test_partial_preparation_has_truthful_states_without_future_approvals() -> None:
    workflows = _workflows()
    for workflow_key, status in (
        ("finance.cashflow-management", "forecast-incomplete"),
        ("finance.tax-estimates", "packet-incomplete"),
    ):
        summary = dict(_truthful_preflight_summaries()[workflow_key])
        summary.update(status=status, exception_refs=["source-gap:fixture"])
        summary.pop("official_source_packet_ref", None)
        summary.pop("tax_packet_scope_ref", None)
        validator = Draft202012Validator(_summary_schema(workflows[workflow_key]))
        assert list(validator.iter_errors(summary)) == []
        summary["exception_refs"] = []
        assert list(validator.iter_errors(summary))

    bookkeeping = dict(_terminal_summaries()["finance.bookkeeping-close"][0])
    validator = Draft202012Validator(_summary_schema(workflows["finance.bookkeeping-close"]))
    bookkeeping["exception_refs"] = ["missing-statement:fixture"]
    assert list(validator.iter_errors(bookkeeping))
    bookkeeping.update(
        status="completed-with-open-exceptions",
        source_coverage_state="partial",
        reconciliation_state="exception",
    )
    assert list(validator.iter_errors(bookkeeping)) == []


def test_sources_reviews_and_proposals_are_step_outputs_not_startup_requirements() -> None:
    workflows = _workflows()
    for workflow in workflows.values():
        required = {item["key"] for item in workflow["inputs"] if item["required"]}
        assert required.isdisjoint(
            {
                "official_source_packet_ref",
                "cpa_ea_reviewer_ref",
                "control_review_ref",
                "source_completeness_ref",
                "proposal_version_ref",
                "external_write_proof_ref",
            }
        )
    assert _by_key(workflows["finance.payment-request"]["inputs"])["billing_request_ref"][
        "required"
    ]


def test_finance_terminal_states_require_their_evidence_and_handoff_fields() -> None:
    workflows = _workflows()

    for workflow_key, (summary, required_transition_fields) in _terminal_summaries().items():
        schema = _summary_schema(workflows[workflow_key])
        validator = Draft202012Validator(schema)
        assert list(validator.iter_errors(summary)) == []

        for required_field in required_transition_fields:
            incomplete = dict(summary)
            incomplete.pop(required_field)
            errors = list(validator.iter_errors(incomplete))
            assert errors, (workflow_key, required_field)
            assert any(required_field in error.message for error in errors)

            original = summary[required_field]
            invalid_values = (
                ([""],)
                if required_field == "prepared_record_refs"
                else ([], [""])
                if isinstance(original, list)
                else ("",)
            )
            for invalid_value in invalid_values:
                invalid = dict(summary)
                invalid[required_field] = invalid_value
                invalid_errors = list(validator.iter_errors(invalid))
                assert invalid_errors, (workflow_key, required_field, invalid_value)
                assert any(
                    required_field in error.absolute_path or required_field in error.message
                    for error in invalid_errors
                )

        status_values = schema["properties"]["status"]["enum"]
        assert status_values == workflows[workflow_key]["metadata"]["state_model"]


def test_finance_output_schemas_keep_legitimately_empty_collections_valid() -> None:
    workflows = _workflows()
    terminal_summaries = _terminal_summaries()

    cashflow = dict(terminal_summaries["finance.cashflow-management"][0])
    cashflow["uncertainty_refs"] = []
    cashflow["exception_refs"] = []
    assert (
        list(
            Draft202012Validator(
                _summary_schema(workflows["finance.cashflow-management"])
            ).iter_errors(cashflow)
        )
        == []
    )

    followup = dict(terminal_summaries["finance.payment-request-followups"][0])
    followup["suppressed_invoice_refs"] = []
    followup["eligible_invoice_refs"] = []
    followup["exception_refs"] = []
    followup["handoff_refs"] = []
    assert (
        list(
            Draft202012Validator(
                _summary_schema(workflows["finance.payment-request-followups"])
            ).iter_errors(followup)
        )
        == []
    )


def test_finance_transition_approvals_cannot_be_empty_or_blank() -> None:
    workflows = _workflows()
    payment_schema = _summary_schema(workflows["finance.payment-request"])
    payment = dict(_terminal_summaries()["finance.payment-request"][0])
    payment["status"] = "finalized"
    payment.pop("external_write_proof_ref")
    payment.pop("handoff_refs")

    followup_schema = _summary_schema(workflows["finance.payment-request-followups"])
    followup = dict(_terminal_summaries()["finance.payment-request-followups"][0])
    followup["status"] = "resent"
    followup.pop("external_write_proof_ref")
    followup.pop("handoff_refs")
    followup["approval_refs"] = ["owner-followup-approval:fixture"]
    followup["control_review_ref"] = "control-review:fixture"

    for schema, summary in ((payment_schema, payment), (followup_schema, followup)):
        validator = Draft202012Validator(schema)
        assert list(validator.iter_errors(summary)) == []
        for invalid_approvals in ([], [""]):
            invalid = dict(summary)
            invalid["approval_refs"] = invalid_approvals
            assert list(validator.iter_errors(invalid))


def test_recorded_no_send_and_no_activity_month_do_not_require_invented_work() -> None:
    workflows = _workflows()
    payment = dict(_terminal_summaries()["finance.payment-request"][0])
    payment.update(
        delivery_state="suppressed", suppression_ref="paid-before-send:fixture", handoff_refs=[]
    )
    payment.pop("approval_refs")
    payment.pop("proposal_version_ref")
    payment.pop("control_review_ref")
    validator = Draft202012Validator(_summary_schema(workflows["finance.payment-request"]))
    assert list(validator.iter_errors(payment)) == []
    payment["delivery_state"] = "sent"
    assert list(validator.iter_errors(payment))

    bookkeeping = dict(_terminal_summaries()["finance.bookkeeping-close"][0])
    bookkeeping["prepared_record_refs"] = []
    validator = Draft202012Validator(_summary_schema(workflows["finance.bookkeeping-close"]))
    assert list(validator.iter_errors(bookkeeping)) == []
    bookkeeping["source_coverage_state"] = "partial"
    assert list(validator.iter_errors(bookkeeping))


def test_stripe_invoice_creation_requires_explicit_approved_currency() -> None:
    plugin = yaml.safe_load((ROOT / "plugins/finance/plugin.yaml").read_text())
    action = _by_key(plugin["actions"])["stripe.invoices.create"]
    validator = Draft202012Validator(action["input_schema"])
    request = {
        "customer_ref": "provider-object:customer-fixture",
        "collection_method": "send_invoice",
        "days_until_due": 14,
        "currency": "eur",
    }
    assert list(validator.iter_errors(request)) == []
    for invalid in (
        {k: v for k, v in request.items() if k != "currency"},
        {**request, "currency": "EUR"},
    ):
        assert list(validator.iter_errors(invalid))


def test_finance_test_send_acceptance_is_not_customer_delivery() -> None:
    workflows = _workflows()
    payment = dict(_terminal_summaries()["finance.payment-request"][0])
    payment.update(status="test-accepted", delivery_state="test-accepted", handoff_refs=[])
    validator = Draft202012Validator(_summary_schema(workflows["finance.payment-request"]))
    assert list(validator.iter_errors(payment)) == []
    assert list(validator.iter_errors({**payment, "delivery_state": "sent"}))
    assert list(validator.iter_errors({**payment, "approval_refs": []}))
    assert list(validator.iter_errors({**payment, "status": "recorded"})) == []

    followup = dict(_terminal_summaries()["finance.payment-request-followups"][0])
    followup.update(
        status="test-accepted",
        resend_state="test-accepted",
        approval_refs=["owner-approval:fixture"],
        control_review_ref="review:fixture",
    )
    validator = Draft202012Validator(
        _summary_schema(workflows["finance.payment-request-followups"])
    )
    assert list(validator.iter_errors(followup)) == []
    assert list(validator.iter_errors({**followup, "resend_state": "sent"}))
    assert list(validator.iter_errors({**followup, "approval_refs": []}))
    assert list(validator.iter_errors({**followup, "occurrence_mode": "settlement-only"}))


def test_finance_guidance_keeps_provider_availability_and_missing_links_explicit() -> None:
    workflows = _workflows()
    followup = workflows["finance.payment-request-followups"]
    preparation = next(step for step in followup["steps"] if step["id"] == "prepare-settlement")
    guidance = " ".join(preparation["instructions"])
    assert "PaymentRecord listing is temporarily unavailable in StackOS" in guidance
    assert "do not retry listing" in guidance
    assert "replacement report" in guidance
    assert "owner/provider resolution" in guidance
    assert "known ref" in guidance
    assert "missing payment linkage" in guidance
    backend = BACKEND_CONTRACT.read_text()
    assert "temporarily unavailable in StackOS" in backend
    assert "unrecognized-route 404 despite the published" in backend
    assert "hold lost-reference recovery for owner/provider resolution" in backend
    assert "idempotency_key_in_use" in backend
    assert "different parameters" in backend
    assert "source_ref" in backend
    for path in (
        "plugins/finance/agent-presets/finance.yaml",
        "plugins/finance/skill-presets/finance.yaml",
    ):
        text = (ROOT / path).read_text()
        assert "test-accepted" in text
        assert "PaymentRecord listing is temporarily unavailable in StackOS" in text
        assert "owner/provider resolution" in text
        assert "missing payment linkage" in text


def test_finance_capabilities_and_technical_approvals_are_fully_bound() -> None:
    workflows = _workflows()
    workflows_without_technical_gates = {
        "finance.receipt-intake",
        "finance.bookkeeping-close",
        "finance.cashflow-management",
    }

    for workflow_key, workflow in workflows.items():
        declared_capabilities = {
            requirement["key"] for requirement in workflow.get("capability_requirements", [])
        }
        action_capabilities = {
            action["capability"] for action in workflow.get("action_contracts", [])
        }
        assert action_capabilities <= declared_capabilities, workflow_key

        declared_approvals = {approval["key"] for approval in workflow.get("approval_gates", [])}
        referenced_approvals = {
            action["approval_ref"]
            for action in workflow.get("action_contracts", [])
            if action.get("approval_ref")
        }
        referenced_approvals.update(
            approval_ref
            for step in workflow["steps"]
            for approval_ref in step.get("approval_refs", [])
        )
        assert declared_approvals == referenced_approvals, workflow_key

        if workflow_key in workflows_without_technical_gates:
            assert workflow.get("approval_gates", []) == []

    approval_guidance = " ".join(APPROVAL_MATRIX.read_text(encoding="utf-8").lower().split())
    assert "agents never self-approve" in approval_guidance
    assert "fresh decision/new occurrence" in approval_guidance
    assert "never reuse an approved run for another invoice" in approval_guidance
    assert "receipt-exception-review" not in approval_guidance
    assert "owner-exception-recovery-review" not in approval_guidance
    assert "owner-cashflow-exception-recovery" not in approval_guidance
