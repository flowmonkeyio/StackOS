"""MCP grant and no-secret audit proof for the Stripe finance actions."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialAccount
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from tests.helpers.stripe import stripe_dispute, stripe_invoice

from .conftest import MCPClient

STRIPE_SECRET = "sk_test_mcp_stripe_sentinel"


@pytest.mark.parametrize("response_mode", ["compact", "raw"])
def test_payment_record_list_is_deferred_before_auth_or_http(
    mcp_client: MCPClient, seeded_project: dict, httpx_mock: HTTPXMock, response_mode: str
) -> None:
    project_id = seeded_project["data"]["id"]
    action_ref = "finance.stripe.payment-records.list"
    args = {"project_id": project_id, "action_ref": action_ref, "response_mode": response_mode}
    described = mcp_client.call_tool_structured("action.describe", args)
    described = described.get("data", described)
    assert described["availability"]["status"] == "deferred"
    assert described["availability"]["executable"] is False
    if response_mode == "compact":
        # Compact discovery keeps status; raw description owns the full contract.
        described = mcp_client.call_tool_structured(
            "action.describe", {**args, "response_mode": "raw"}
        )
    reason = next(
        reason
        for reason in described["availability"]["reasons"]
        if "temporarily unavailable in StackOS" in reason
    )
    assert "temporarily unavailable in StackOS" in reason
    assert "Do not retry, change credentials, or guess a URL" in reason
    assert "known PaymentRecord ref" in reason
    assert "never create a replacement report" in reason
    validation = mcp_client.call_tool_structured("action.validate", {**args, "input_json": {}})
    validation = validation.get("data", validation)
    assert validation["valid"] is False
    assert any(
        issue["code"] == "execution_deferred" and issue["message"] == reason
        for issue in validation["issues"]
    )
    # No saved Account exists. A bogus opaque credential must not move this into auth.
    for credential_args in ({}, {"credential_ref": "credential:unresolved-fixture"}):
        failure = mcp_client.call_tool_error(
            "action.run", {**args, **credential_args, "input_json": {}}
        )
        assert failure["message"] == "ValidationError"
        assert any(
            issue["code"] == "execution_deferred" and issue["message"] == reason
            for issue in failure["data"]["issues"]
        )
    _seed_stripe_credential(mcp_client, project_id)
    listed = mcp_client.call_tool_structured(
        "action.list", {"project_id": project_id, "plugin_slug": "finance"}
    )
    assert len(listed["items"]) == 25
    assert action_ref not in {item["action_ref"] for item in listed["items"]}
    full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "finance",
            "include_unavailable_integrations": True,
        },
    )
    assert len(full["items"]) == 26
    deferred = next(item for item in full["items"] if item["action_ref"] == action_ref)
    assert deferred["availability_status"] == "deferred"
    assert deferred["executable"] is False
    assert deferred["exposure"]["hidden_reason"] == "action_deferred"
    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "finance.payment-request-followups",
            "response_mode": "raw",
        },
    )
    assert readiness["required_providers_ready"] is True
    optional_list = next(item for item in readiness["actions"] if item["action_ref"] == action_ref)
    assert optional_list["availability_status"] == "deferred"
    assert optional_list["missing"]
    assert {item["required_for"] for item in optional_list["missing"]} == {
        "optional_action_execution"
    }
    assert not any(
        item["required_for"] == "action_execution"
        for item in readiness["missing"]
        if item.get("action_ref") == action_ref
    )
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls", headers=mcp_client._headers()
    )
    assert audit.json()["items"] == []
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("target_step", ["prepare-settlement", "apply-settlement"])
def test_payment_record_list_remains_deferred_when_step_granted(
    mcp_client: MCPClient, seeded_project: dict, httpx_mock: HTTPXMock, target_step: str
) -> None:
    """Actual optional template grants and synthetic predecessors; no provider call."""
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "finance.payment-request-followups",
            "plugin_slug": "finance",
            "inputs_json": {
                "workspace_ref": "finance-workspace:list-recovery-fixture",
                "review_window": {"start": "2026-09-01", "end": "2026-09-05"},
                "occurrence_mode": "settlement-only",
                "settlement_scope_ref": "settlement:list-fixture",
            },
        },
    )
    plan_id = created["data"]["id"]
    assert {row["status"] for row in created["data"]["approval_requests"]} == {"pending"}
    run_token = mcp_client.call_tool_structured(
        "runPlan.start",
        {
            "project_id": project_id,
            "run_plan_id": plan_id,
        },
    )["data"]["run_token"]
    args = {
        "project_id": project_id,
        "action_ref": "finance.stripe.payment-records.list",
        "credential_ref": credential_ref,
        "input_json": {"limit": 1},
        "run_token": run_token,
        "output_policy_json": {"mode": "inline"},
        "response_mode": "raw",
    }
    for step_id in (
        "preflight",
        "read-invoice-lifecycle",
        "prepare-settlement",
        "review-settlement",
        "apply-settlement",
    ):
        mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {
                "run_plan_id": plan_id,
                "step_id": step_id,
                "run_token": run_token,
            },
        )
        if step_id == target_step:
            break
        if step_id == "preflight":
            denied = mcp_client.call_tool_error("action.execute", args)
            assert denied["message"] == "ToolNotGrantedError"
            assert httpx_mock.get_requests() == []
        recorded = mcp_client.call_tool_structured(
            "runPlan.recordStep",
            {
                "run_plan_id": plan_id,
                "step_id": step_id,
                "run_token": run_token,
                "status": "success",
                "result_json": {
                    "followup_summary": {
                        "status": "scoped",
                        "occurrence_mode": "settlement-only",
                        "settlement_state": "not-requested",
                        "resend_state": "not-requested",
                        "recovery_state": "not-needed",
                        "exception_refs": [],
                    }
                },
            },
        )
        assert "data" in recorded
    failure = mcp_client.call_tool_error("action.execute", args)
    assert failure["message"] == "ValidationError"
    issue = next(item for item in failure["data"]["issues"] if item["code"] == "execution_deferred")
    assert "temporarily unavailable in StackOS" in issue["message"]
    assert "known PaymentRecord ref" in issue["message"]
    assert "never create a replacement report" in issue["message"]
    assert httpx_mock.get_requests() == []
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"run_plan_id": plan_id, "action_key": "stripe.payment-records.list"},
        headers=mcp_client._headers(),
    )
    assert audit.status_code == 200
    assert audit.json()["items"] == []
    assert STRIPE_SECRET not in json.dumps({"failure": failure, "audit": audit.json()})


@pytest.mark.parametrize("response_mode", ["compact", "raw"])
@pytest.mark.parametrize("response_version", [None, "2025-10-29.clover"])
def test_invoice_list_endpoint_404_preserves_safe_repair_and_failed_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    response_mode: str,
    response_version: str | None,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    message = "Unrecognized request URL (GET: /v1/invoices)."
    request_log_url = (
        "https://dashboard.stripe.com/acct_Fixture/test/workbench/logs?object=req_route_fixture"
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoices?limit=1",
        status_code=404,
        headers={
            "Request-Id": "req_route_fixture",
            **({"Stripe-Version": response_version} if response_version else {}),
        },
        json={
            "error": {
                "type": "invalid_request_error",
                "message": (
                    message + " Please see https://stripe.com/docs or we can help at "
                    "https://support.stripe.com/."
                ),
                "request_log_url": request_log_url,
            }
        },
    )

    failure = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "finance.stripe.invoices.list",
            "credential_ref": credential_ref,
            "input_json": {"limit": 1},
            "response_mode": response_mode,
        },
    )

    assert failure["message"] == "ConflictError"
    assert failure["data"]["provider_status_code"] == 404
    provider_error = failure["data"]["provider_error"]
    assert provider_error["reason_code"] == "endpoint_not_recognized"
    assert provider_error["message"] == message
    assert provider_error["request_log_url"] == request_log_url
    assert provider_error["request_id"] == "req_route_fixture"
    assert provider_error["request_method"] == "GET"
    assert provider_error["request_path"] == "/v1/invoices"
    assert provider_error["request_api_version"] == "2026-08-26.dahlia"
    assert provider_error["response_api_version"] == response_version
    assert provider_error["outcome_unknown"] is False
    assert provider_error["retry_safe"] is True

    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"action_key": "stripe.invoices.list"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()
    assert len(audit["items"]) == 1
    call = audit["items"][0]
    assert call["status"] == "failed"
    assert call["run_plan_id"] is None
    assert call["response_json"]["provider_status_code"] == 404
    assert call["response_json"]["provider_error"] == provider_error
    assert call["response_json"]["outcome_unknown"] is False

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert "Idempotency-Key" not in requests[0].headers
    assert STRIPE_SECRET not in json.dumps({"failure": failure, "audit": audit})


@pytest.mark.parametrize("response_mode", ["compact", "raw"])
@pytest.mark.parametrize(
    "status_code,error_type,error_code",
    [
        (409, "invalid_request_error", "idempotency_key_in_use"),
        (400, "idempotency_error", None),
    ],
)
def test_stripe_idempotency_conflict_retains_original_operation_uncertainty_in_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    response_mode: str,
    status_code: int,
    error_type: str,
    error_code: str | None,
) -> None:
    project = seeded_project["data"]["id"]
    credential = _seed_stripe_credential(mcp_client, project)
    secret = mcp_client.call_tool_structured(
        "secret.set",
        {
            "project_id": project,
            "value": "private-customer@example.test",
        },
    )["data"]["secret_ref"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.stripe.com/v1/customers",
        status_code=status_code,
        headers={"Request-Id": "req_conflict"},
        json={
            "error": {
                "type": error_type,
                "code": error_code,
                "message": "PRIVATE ORIGINAL OPERATION",
            }
        },
    )
    failure = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project,
            "action_ref": "finance.stripe.customers.create",
            "credential_ref": credential,
            "input_json": {"email": {"$secret_ref": secret}},
            "idempotency_key": "original-operation-fixture",
            "confirm_direct": True,
            "intent_summary": "Isolated provider-mocked idempotency conflict proof",
            "response_mode": response_mode,
        },
    )
    assert failure["data"]["provider_status_code"] == status_code, failure
    error = failure["data"]["provider_error"]
    assert error["outcome_unknown"] is True
    assert error["retry_safe"] is False
    assert "reconcil" in error["recovery"].lower()
    assert "original" in error["recovery"].lower()
    assert "fresh idempotency key" not in error["recovery"].lower()
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        params={"action_key": "stripe.customers.create"},
        headers=mcp_client._headers(),
    ).json()
    assert len(audit["items"]) == 1 and audit["items"][0]["status"] == "failed"
    assert audit["items"][0]["response_json"]["provider_error"] == error
    assert len(httpx_mock.get_requests()) == 1
    assert httpx_mock.get_requests()[0].headers["Idempotency-Key"] == "original-operation-fixture"
    serialized = json.dumps({"failure": failure, "audit": audit})
    for private in (STRIPE_SECRET, "private-customer@example.test", "PRIVATE ORIGINAL OPERATION"):
        assert private not in serialized


@pytest.mark.parametrize("missing_kind", ["missing", "null"])
def test_invoice_payment_unavailable_linkage_preserves_page_and_audit_without_a_match(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    missing_kind: str,
) -> None:
    project = seeded_project["data"]["id"]
    credential = _seed_stripe_credential(mcp_client, project)
    invoice_ref = _seed_stripe_object_ref(mcp_client, project_id=project, credential_ref=credential)
    unavailable: dict[str, Any] = {"type": "payment_intent"}
    if missing_kind == "null":
        unavailable["payment_intent"] = None
    base = {
        "object": "invoice_payment",
        "invoice": "in_mcp_fixture",
        "amount_requested": 1000,
        "amount_paid": None,
        "created": 1700000000,
        "currency": "usd",
        "is_default": True,
        "livemode": False,
        "status": "open",
        "status_transitions": {},
    }
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoice_payments?limit=2&invoice=in_mcp_fixture",
        json={
            "object": "list",
            "has_more": True,
            "data": [
                {**base, "id": "inpay_missing", "payment": unavailable},
                {
                    **base,
                    "id": "inpay_known",
                    "payment": {"type": "payment_intent", "payment_intent": "pi_known"},
                },
            ],
        },
    )
    args = {
        "project_id": project,
        "credential_ref": credential,
        "action_ref": "finance.stripe.invoice-payments.list",
        "response_mode": "raw",
        "output_policy_json": {"mode": "inline"},
        "input_json": {"invoice_ref": invoice_ref, "limit": 2},
    }
    result = mcp_client.call_tool_structured("action.run", args)["data"]
    assert "output_json" in result, result
    page = result["output_json"]["data"]
    unknown, known = page["items"]
    assert unknown["payment_ref_state"] == missing_kind
    assert unknown.get("payment_intent_ref") is None
    assert known["payment_ref_state"] == "available"
    assert known["payment_intent_ref"].startswith("provider-object:")
    assert page["has_more"] is True
    assert page["next_page_cursor"] == known["invoice_payment_ref"]
    assert unknown["amount_requested"] == 1000 and unknown["status"] == "open"
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoice_payments?limit=2&invoice=in_mcp_fixture&starting_after=inpay_known",
        json={"object": "list", "data": [], "has_more": False},
    )
    args["input_json"]["page_cursor"] = page["next_page_cursor"]
    final = mcp_client.call_tool_structured("action.run", args)["data"]
    assert final["output_json"]["data"]["has_more"] is False
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        params={"action_key": "stripe.invoice-payments.list"},
        headers=mcp_client._headers(),
    ).json()
    assert len(audit["items"]) == 2 and all(row["status"] == "success" for row in audit["items"])
    assert len(httpx_mock.get_requests()) == 2
    assert all(request.method == "GET" for request in httpx_mock.get_requests())
    assert "pi_known" not in json.dumps({"result": result, "audit": audit})


def _seed_stripe_credential(mcp: MCPClient, project_id: int) -> str:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential_ref = (
            AuthRepository(session)
            .store_credential(
                provider_key="stripe",
                auth_method_key="api_key",
                display_name="Stripe MCP fixture",
                fields={"api_key": STRIPE_SECRET},
                attach_project_id=project_id,
            )
            .data.credential_ref
        )
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        assert credential.id is not None
        session.add(
            CredentialAccount(
                credential_id=credential.id,
                provider_account_id="acct_mcp_fixture",
                display_name="Stripe MCP fixture",
            )
        )
        session.commit()
        return credential_ref


def _seed_stripe_object_ref(
    mcp: MCPClient,
    *,
    project_id: int,
    credential_ref: str,
    object_type: str = "stripe.invoice",
    provider_object_id: str = "in_mcp_fixture",
) -> str:
    """Seed one account-bound safe ref for a fully mocked Stripe object."""
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        invoice_ref = ProviderObjectReferenceRepository(session, project_id=project_id).upsert(
            credential=credential,
            object_type=object_type,
            provider_object_id=provider_object_id,
        )
        session.commit()
        return invoice_ref


def _payment_request_inputs() -> dict[str, Any]:
    return {
        "goal": "Fixture-only proof of a reviewed invoice finalization gate.",
        "backend_key": "local-json",
        "workspace_ref": "finance-workspace:fixture",
        "recording_mode": "prepared-unposted",
        "operator_policy_ref": "finance-policy:fixture",
        "trigger_kind": "manual",
        "billing_request_ref": "billing-request:fixture",
        "billing_policy_ref": "billing-policy:fixture",
    }


def _payment_request_result(status: str, *, invoice_ref: str) -> dict[str, Any]:
    """Return a safe fixture result that satisfies the actual template contract."""
    summary: dict[str, Any] = {
        "status": status,
        "billing_request_ref": "billing-request:fixture",
        "billing_policy_ref": "billing-policy:fixture",
        "recovery_state": "not-needed",
        "exception_refs": [],
    }
    if status in {
        "customer-resolved",
        "draft-prepared",
        "review-ready",
        "finalized",
        "sent",
        "recorded",
    }:
        summary.update(
            {
                "customer_ref": "provider-object:customer-fixture",
                "action_call_refs": ["action-call:fixture"],
            }
        )
    if status in {"draft-prepared", "review-ready", "finalized", "sent", "recorded"}:
        summary["invoice_ref"] = invoice_ref
    if status in {"review-ready", "finalized", "sent", "recorded"}:
        summary["proposal_version_ref"] = "proposal-version:fixture"
        summary["control_review_ref"] = "control-review:fixture"
    if status in {"finalized", "sent", "recorded"}:
        summary["approval_refs"] = ["finalize-approval:fixture"]
    if status in {"sent", "recorded"}:
        summary["approval_refs"] = ["finalize-approval:fixture", "send-approval:fixture"]
    if status == "recorded":
        summary.update(
            {
                "external_write_proof_ref": "finance-write-proof:fixture",
                "handoff_refs": ["followup-handoff:fixture"],
            }
        )
    return {"payment_request_summary": summary}


def _complete_payment_request_predecessors(
    mcp: MCPClient,
    *,
    run_plan_id: int,
    run_token: str,
    invoice_ref: str,
) -> None:
    for step_id, status in (
        ("preflight", "scoped"),
        ("resolve-customer", "customer-resolved"),
        ("create-draft", "draft-prepared"),
        ("review-draft", "review-ready"),
    ):
        result = _payment_request_result(status, invoice_ref=invoice_ref)
        summary = result["payment_request_summary"]
        assert "approval_refs" not in summary
        assert "external_write_proof_ref" not in summary
        assert "handoff_refs" not in summary
        claimed = mcp.call_tool_structured(
            "runPlan.claimStep",
            {
                "run_plan_id": run_plan_id,
                "step_id": step_id,
                "run_token": run_token,
            },
        )
        assert claimed["data"]["step_id"] == step_id
        mcp.call_tool_structured(
            "runPlan.recordStep",
            {
                "run_plan_id": run_plan_id,
                "step_id": step_id,
                "status": "success",
                "result_json": result,
                "run_token": run_token,
            },
        )


@pytest.mark.parametrize(
    ("action_key", "gate"),
    [
        ("invoices.mark-paid-out-of-band", "owner-external-settlement"),
        ("invoices.attach-payment", "owner-payment-attachment"),
        ("payment-records.report", "owner-payment-record"),
    ],
)
def test_settlement_actions_require_separate_owner_gates_and_safe_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    action_key: str,
    gate: str,
) -> None:
    """Exercise actual template grants; predecessor packets are synthetic scaffolding.

    This proves technical gates/transport/audit, not a bank match or real approval.
    A fresh isolated occurrence is used for each mutually exclusive write route.
    """
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    invoice_ref = _seed_stripe_object_ref(
        mcp_client, project_id=project_id, credential_ref=credential_ref
    )
    customer_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.customer",
        provider_object_id="cus_mcp_settlement",
    )
    payment_record_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type="stripe.payment-record",
        provider_object_id="pr_mcp_settlement",
    )
    bank_reference = "private-bank-transfer-reference-fixture"
    secret = mcp_client.call_tool_structured(
        "secret.set", {"project_id": project_id, "value": bank_reference}
    )["data"]["secret_ref"]
    inputs = {
        "workspace_ref": "finance-workspace:settlement-fixture",
        "review_window": {"start": "2026-09-01", "end": "2026-09-04"},
        "occurrence_mode": "settlement-only",
        "settlement_scope_ref": "settlement-scope:fixture-v1",
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "finance.payment-request-followups",
            "plugin_slug": "finance",
            "inputs_json": inputs,
        },
    )
    assert "id" in created["data"], created
    plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": plan_id}
    )
    run_token = started["data"]["run_token"]
    action_ref = f"finance.stripe.{action_key}"
    payload: dict[str, Any] = {"invoice_ref": invoice_ref}
    path = "/v1/invoices/in_mcp_fixture/pay"
    response: dict[str, Any] = stripe_invoice(
        **{
            "id": "in_mcp_fixture",
            "object": "invoice",
            "customer": "cus_mcp_settlement",
            "status": "paid",
            "collection_method": "send_invoice",
            "amount_due": 10000,
            "amount_paid": 10000,
            "amount_remaining": 0,
            "amount_overpaid": 0,
            "currency": "usd",
            "livemode": False,
            "total": 10000,
            "subtotal": 10000,
        }
    )
    if action_key == "invoices.attach-payment":
        payload["payment_record_ref"] = payment_record_ref
        path = "/v1/invoices/in_mcp_fixture/attach_payment"
    elif action_key == "payment-records.report":
        payload = {
            "customer_ref": customer_ref,
            "amount": 10000,
            "currency": "usd",
            "initiated_at": 1788476400,
            "guaranteed_at": 1788476460,
            "payment_reference": {"$secret_ref": secret},
        }
        path = "/v1/payment_records/report_payment"
        response = {
            "id": "pr_mcp_settlement",
            "object": "payment_record",
            "customer_details": {"customer": "cus_mcp_settlement"},
            "created": 1788476460,
            "livemode": False,
            "reported_by": "self",
            "amount": {"value": 10000, "currency": "usd"},
            "amount_authorized": {"value": 10000, "currency": "usd"},
            "amount_requested": {"value": 10000, "currency": "usd"},
            "amount_guaranteed": {"value": 10000, "currency": "usd"},
            "amount_refunded": {"value": 0, "currency": "usd"},
            "amount_failed": {"value": 0, "currency": "usd"},
            "amount_canceled": {"value": 0, "currency": "usd"},
            "processor_details": {
                "type": "custom",
                "custom": {"payment_reference": bank_reference},
            },
            "payment_method_details": {
                "type": "custom",
                "custom": {"display_name": "Bank transfer"},
            },
        }
    args = {
        "project_id": project_id,
        "action_ref": action_ref,
        "input_json": payload,
        "credential_ref": credential_ref,
        "run_token": run_token,
        "idempotency_key": f"settlement-fixture-{action_key}",
        "output_policy_json": {"mode": "inline"},
        "response_mode": "raw",
    }
    for step_id in (
        "preflight",
        "read-invoice-lifecycle",
        "prepare-settlement",
        "review-settlement",
    ):
        mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {"run_plan_id": plan_id, "step_id": step_id, "run_token": run_token},
        )
        if step_id == "preflight":
            denied_grant = mcp_client.call_tool_error("action.execute", args)
            assert denied_grant["message"] == "ToolNotGrantedError"
            assert httpx_mock.get_requests() == []
        mcp_client.call_tool_structured(
            "runPlan.recordStep",
            {
                "run_plan_id": plan_id,
                "step_id": step_id,
                "status": "success",
                "run_token": run_token,
                "result_json": {
                    "followup_summary": {
                        "status": "scoped",
                        "occurrence_mode": "settlement-only",
                        "settlement_state": "not-requested",
                        "resend_state": "not-requested",
                        "recovery_state": "not-needed",
                        "exception_refs": [],
                    }
                },
            },
        )
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": plan_id, "step_id": "apply-settlement", "run_token": run_token},
    )
    denied = mcp_client.call_tool_error("action.execute", args)
    assert denied["message"] == "ConflictError"
    assert denied["data"]["approval_ref"] == gate
    assert denied["data"]["approval_status"] == "pending"
    assert httpx_mock.get_requests() == []
    approved = mcp_client.test_client.post(
        "/api/v1/operations/runPlan.update/call",
        json={
            "arguments": {
                "run_plan_id": plan_id,
                "approval_key": gate,
                "approval_status": "approved",
                "decided_by": "owner-fixture",
                "decision_json": {"approval_ref": "external-approval:settlement-v1"},
                "response_mode": "raw",
            }
        },
        headers=mcp_client._headers(),
    )
    assert approved.status_code == 200, approved.text
    approvals = {
        row["approval_key"]: row["status"] for row in approved.json()["data"]["approval_requests"]
    }
    assert approvals.pop(gate) == "approved"
    assert set(approvals.values()) == {"pending"}
    httpx_mock.add_response(method="POST", url=f"https://api.stripe.com{path}", json=response)
    executed = mcp_client.call_tool_structured("action.execute", args)
    assert "action_call" in executed["data"], json.dumps(executed)
    call = executed["data"]["action_call"]
    assert call["run_plan_step_id"] == claimed["data"]["id"]
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].headers["Stripe-Version"] == "2026-08-26.dahlia"
    form = parse_qs(requests[0].content.decode())
    if action_key == "invoices.mark-paid-out-of-band":
        assert form == {"paid_out_of_band": ["true"]}
    elif action_key == "invoices.attach-payment":
        assert form == {"payment_record": ["pr_mcp_settlement"]}
    else:
        assert form["outcome"] == ["guaranteed"]
        assert form["processor_details[custom][payment_reference]"] == [bank_reference]
    replay = mcp_client.call_tool_structured("action.execute", args)
    assert replay["data"]["action_call"]["id"] == call["id"]
    assert len(httpx_mock.get_requests()) == 1
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"run_plan_id": plan_id, "action_key": f"stripe.{action_key}", "status": "success"},
        headers=mcp_client._headers(),
    )
    assert audit.status_code == 200
    assert audit.json()["items"][0]["id"] == call["id"]
    serialized = json.dumps({"executed": executed, "audit": audit.json()})
    for private in (STRIPE_SECRET, bank_reference, "pr_mcp_settlement", "cus_mcp_settlement"):
        assert private not in serialized
    revised = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "finance.payment-request-followups",
            "plugin_slug": "finance",
            "inputs_json": {**inputs, "settlement_scope_ref": "settlement-scope:fixture-v2"},
        },
    )
    assert revised["data"]["id"] != plan_id
    assert {row["status"] for row in revised["data"]["approval_requests"]} == {"pending"}


def _stripe_balance_plan() -> dict[str, Any]:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "stripe.balance-proof.run",
        "title": "Stripe balance proof",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "read-balance",
                    "tool": "action.execute",
                    "action_refs": ["finance.stripe.balance.retrieve"],
                }
            ]
        },
        "steps": [
            {
                "id": "read-balance",
                "title": "Read Stripe balance",
                "action_refs": ["finance.stripe.balance.retrieve"],
            }
        ],
    }


def test_stripe_action_execute_respects_grants_and_redacts_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _stripe_balance_plan()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "read-balance",
            "run_token": started["data"]["run_token"],
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/balance",
        json={
            "object": "balance",
            "livemode": False,
            "available": [{"amount": 9680, "currency": "usd"}],
            "pending": [],
        },
    )

    executed = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "finance.stripe.balance.retrieve",
            "input_json": {},
            "credential_ref": credential_ref,
            "run_token": started["data"]["run_token"],
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )
    assert executed["data"]["output_json"]["data"]["available"] == [
        {"amount": 9680, "currency": "usd"}
    ]
    assert executed["data"]["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": created["data"]["id"],
            "run_plan_step_id": claimed["data"]["id"],
            "plugin_slug": "finance",
            "action_key": "stripe.balance.retrieve",
            "status": "success",
        },
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"][0]
    assert audit["id"] == executed["data"]["action_call"]["id"]
    assert STRIPE_SECRET not in json.dumps({"executed": executed, "audit": audit})

    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "finance.stripe.charges.list",
            "input_json": {"limit": 1},
            "credential_ref": credential_ref,
            "run_token": started["data"]["run_token"],
            "response_mode": "raw",
        },
    )
    assert denied["message"] == "ToolNotGrantedError"
    assert denied["data"]["tool"] == "action.execute"
    assert len(httpx_mock.get_requests()) == 1


def test_payment_request_finalization_requires_exact_owner_gate_then_runs_fixture_stripe(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    """Prove the real finance template enforces its action-level owner gate.

    Every provider response is supplied by ``pytest-httpx``.  The test never
    reaches a live Stripe account, sends an invoice, or changes a customer.
    """
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    invoice_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project_id,
        credential_ref=credential_ref,
    )
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "finance.payment-request",
            "plugin_slug": "finance",
            "inputs_json": _payment_request_inputs(),
        },
    )
    run_plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )
    run_token = started["data"]["run_token"]
    _complete_payment_request_predecessors(
        mcp_client,
        run_plan_id=run_plan_id,
        run_token=run_token,
        invoice_ref=invoice_ref,
    )
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": run_plan_id,
            "step_id": "finalize-invoice",
            "run_token": run_token,
        },
    )
    assert claimed["data"]["step_id"] == "finalize-invoice"

    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "finance.stripe.invoices.finalize",
            "input_json": {"invoice_ref": invoice_ref},
            "credential_ref": credential_ref,
            "run_token": run_token,
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )
    assert denied["message"] == "ConflictError"
    assert denied["data"] == {
        "run_plan_id": run_plan_id,
        "action_ref": "finance.stripe.invoices.finalize",
        "approval_ref": "owner-invoice-finalization",
        "approval_status": "pending",
        "detail": "action execution requires approval",
        "retryable": False,
    }
    assert httpx_mock.get_requests() == []

    approved = mcp_client.test_client.post(
        "/api/v1/operations/runPlan.update/call",
        json={
            "arguments": {
                "run_plan_id": run_plan_id,
                "approval_key": "owner-invoice-finalization",
                "approval_status": "approved",
                "decided_by": "owner-fixture",
                "decision_json": {"approval_ref": "owner-approval:fixture"},
                "response_mode": "raw",
            }
        },
        headers=mcp_client._headers(),
    )
    assert approved.status_code == 200, approved.text
    approvals = {
        item["approval_key"]: item["status"]
        for item in approved.json()["data"]["approval_requests"]
    }
    assert approvals == {
        "owner-invoice-finalization": "approved",
        "owner-invoice-send": "pending",
    }

    httpx_mock.add_response(
        method="POST",
        url="https://api.stripe.com/v1/invoices/in_mcp_fixture/finalize",
        json=stripe_invoice(
            **{
                "id": "in_mcp_fixture",
                "object": "invoice",
                "customer": "cus_mcp_fixture",
                "status": "open",
                "collection_method": "send_invoice",
                "amount_due": 125000,
                "amount_paid": 0,
                "amount_remaining": 125000,
                "currency": "usd",
                "livemode": False,
                "subtotal": 125000,
                "total": 125000,
            }
        ),
    )
    executed = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "finance.stripe.invoices.finalize",
            "input_json": {"invoice_ref": invoice_ref},
            "credential_ref": credential_ref,
            "run_token": run_token,
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )
    assert executed["data"]["output_json"]["data"]["status"] == "open"
    assert executed["data"]["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.stripe.com/v1/invoices/in_mcp_fixture/finalize"
    mcp_client.call_tool_structured(
        "runPlan.recordStep",
        {
            "run_plan_id": run_plan_id,
            "step_id": "finalize-invoice",
            "status": "success",
            "result_json": _payment_request_result("finalized", invoice_ref=invoice_ref),
            "run_token": run_token,
        },
    )
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": run_plan_id, "step_id": "send-invoice", "run_token": run_token},
    )
    send_args = {
        "project_id": project_id,
        "action_ref": "finance.stripe.invoices.send",
        "input_json": {"invoice_ref": invoice_ref},
        "credential_ref": credential_ref,
        "run_token": run_token,
        "output_policy_json": {"mode": "inline"},
        "response_mode": "raw",
    }
    denied_send = mcp_client.call_tool_error("action.execute", send_args)
    assert denied_send["data"]["approval_ref"] == "owner-invoice-send"
    assert len(httpx_mock.get_requests()) == 1
    send_approval = mcp_client.test_client.post(
        "/api/v1/operations/runPlan.update/call",
        json={
            "arguments": {
                "run_plan_id": run_plan_id,
                "approval_key": "owner-invoice-send",
                "approval_status": "approved",
                "decided_by": "owner-fixture",
                # One external owner decision authorizes both exact operations, but
                # both existing technical gates must be updated separately.
                "decision_json": {"approval_ref": "owner-approval:fixture"},
                "response_mode": "raw",
            }
        },
        headers=mcp_client._headers(),
    )
    assert send_approval.status_code == 200, send_approval.text
    approved_rows = send_approval.json()["data"]["approval_requests"]
    assert {row["status"] for row in approved_rows} == {"approved"}
    assert {row["decision_json"]["approval_ref"] for row in approved_rows} == {
        "owner-approval:fixture"
    }
    httpx_mock.add_response(
        method="POST",
        url="https://api.stripe.com/v1/invoices/in_mcp_fixture/send",
        json=stripe_invoice(
            id="in_mcp_fixture",
            customer="cus_mcp_fixture",
            status="open",
            amount_due=125000,
            amount_remaining=125000,
            subtotal=125000,
            total=125000,
        ),
    )
    sent = mcp_client.call_tool_structured("action.execute", send_args)
    assert sent["data"]["action_call"]["action_key"] == "stripe.invoices.send"
    assert len(httpx_mock.get_requests()) == 2
    assert sent["data"]["action_call"]["id"] != executed["data"]["action_call"]["id"]
    revised = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "finance.payment-request",
            "plugin_slug": "finance",
            "inputs_json": {
                **_payment_request_inputs(),
                "billing_request_ref": "billing-request:revised-fixture",
            },
        },
    )
    assert revised["data"]["id"] != run_plan_id
    assert {row["status"] for row in revised["data"]["approval_requests"]} == {"pending"}


def test_stripe_dispute_read_executes_only_through_its_step_grant(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    engine = mcp_client.test_client.app.state.engine
    with Session(engine) as session:
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        charge_ref = ProviderObjectReferenceRepository(session, project_id=project_id).upsert(
            credential=credential,
            object_type="stripe.charge",
            provider_object_id="ch_dispute_mcp",
        )
        session.commit()
    plan = {
        "schema_version": "stackos.run-plan.v1",
        "key": "stripe.dispute-proof.run",
        "title": "Dispute proof",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "read-dispute",
                    "tool": "action.execute",
                    "action_refs": ["finance.stripe.disputes.list"],
                }
            ]
        },
        "steps": [
            {
                "id": "read-dispute",
                "title": "Read dispute",
                "action_refs": ["finance.stripe.disputes.list"],
            }
        ],
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create", {"project_id": project_id, "run_plan_json": plan}
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": created["data"]["id"]}
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "read-dispute",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/disputes?limit=25&charge=ch_dispute_mcp",
        json={
            "object": "list",
            "has_more": False,
            "data": [
                stripe_dispute(
                    **{
                        "id": "du_mcp",
                        "object": "dispute",
                        "status": "needs_response",
                        "charge": "ch_dispute_mcp",
                        "evidence": {"customer_email_address": "private@example.test"},
                    }
                ),
            ],
        },
    )
    executed = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "finance.stripe.disputes.list",
            "input_json": {"charge_ref": charge_ref},
            "credential_ref": credential_ref,
            "run_token": run_token,
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )
    assert executed["data"]["output_json"]["data"]["items"][0]["status"] == "needs_response"
    assert executed["data"]["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert "private@example.test" not in json.dumps(executed)
