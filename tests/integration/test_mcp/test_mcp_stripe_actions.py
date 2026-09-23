"""MCP grant and no-secret audit proof for the Stripe finance actions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialAccount
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from tests.helpers.stripe import (
    stripe_charge,
    stripe_customer,
    stripe_dispute,
    stripe_invoice,
    stripe_invoice_item,
    stripe_price,
    stripe_product,
)

from .conftest import MCPClient

STRIPE_SECRET = "sk_test_mcp_stripe_sentinel"


@pytest.mark.parametrize("include_details", [None, False, True])
@pytest.mark.parametrize(
    ("action", "object_type", "object_id", "ref_field", "path", "provider_data", "details"),
    [
        (
            "customers.retrieve",
            "customer",
            "cus_fixture",
            "customer_ref",
            "/customers/cus_fixture",
            stripe_customer(
                name="Fixture Customer",
                email="fixture@example.test",
                description="Customer business context",
            ),
            {
                "name": "Fixture Customer",
                "email": "fixture@example.test",
                "description": "Customer business context",
            },
        ),
        (
            "invoices.retrieve",
            "invoice",
            "in_fixture",
            "invoice_ref",
            "/invoices/in_fixture?expand%5B%5D=customer",
            stripe_invoice(
                number="FIXTURE-0001",
                description="Completed services",
                customer_name="Fixture Customer",
                customer_email="fixture@example.test",
                hosted_invoice_url="https://invoice.stripe.com/i/acct_fixture/test_fixture",
                invoice_pdf="https://pay.stripe.com/invoice/acct_fixture/test_fixture/pdf",
            ),
            {
                "number": "FIXTURE-0001",
                "description": "Completed services",
                "customer_name": "Fixture Customer",
                "customer_email": "fixture@example.test",
                "hosted_invoice_url": "https://invoice.stripe.com/i/acct_fixture/test_fixture",
                "invoice_pdf": "https://pay.stripe.com/invoice/acct_fixture/test_fixture/pdf",
            },
        ),
        (
            "invoice-items.list",
            "invoice",
            "in_fixture",
            "invoice_ref",
            "/invoiceitems?limit=1&invoice=in_fixture",
            {
                "object": "list",
                "has_more": False,
                "data": [
                    stripe_invoice_item(invoice="in_fixture", description="Completed services")
                ],
            },
            {"description": "Completed services"},
        ),
        (
            "charges.retrieve",
            "charge",
            "ch_fixture",
            "charge_ref",
            "/charges/ch_fixture",
            stripe_charge(
                description="Completed services",
                receipt_url="https://pay.stripe.com/receipts/fixture",
            ),
            {
                "description": "Completed services",
                "receipt_url": "https://pay.stripe.com/receipts/fixture",
            },
        ),
    ],
)
def test_stripe_business_details_direct_file_and_audit_are_explicit_opt_in(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    include_details: bool | None,
    action: str,
    object_type: str,
    object_id: str,
    ref_field: str,
    path: str,
    provider_data: dict[str, Any],
    details: dict[str, str],
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    object_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type=f"stripe.{object_type}",
        provider_object_id=object_id,
    )
    payload: dict[str, Any] = {ref_field: object_ref}
    if action.endswith(".list"):
        payload["limit"] = 1
    if include_details is not None:
        payload["include_business_details"] = include_details
    httpx_mock.add_response(
        method="GET",
        url=f"https://api.stripe.com/v1{path}",
        json={
            **provider_data,
            "client_secret": "pi_fixture_secret_never_return",
            "api_key": STRIPE_SECRET,
            "metadata": {"private": "private-metadata-sentinel"},
        },
    )
    result = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": f"finance.stripe.{action}",
            "credential_ref": credential_ref,
            "input_json": payload,
        },
    )["data"]
    assert result["status"] == "success", result
    assert result["output"]["output_mode"] == "file"
    assert result["output"]["schema_ref"] == "stackos.action-output.v1"
    saved = json.loads(Path(result["output"]["path"]).read_text(encoding="utf-8"))
    data = saved["response"]["output_json"]["data"]
    record = data["items"][0] if action.endswith(".list") else data
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"action_key": f"stripe.{action}", "status": "success"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"][0]
    assert audit["response_json"]["file"]["path"] == result["output"]["path"]
    audit_file = json.loads(
        Path(audit["response_json"]["file"]["path"]).read_text(encoding="utf-8")
    )
    audit_data = audit_file["response"]["output_json"]["data"]
    audit_record = audit_data["items"][0] if action.endswith(".list") else audit_data
    if include_details:
        assert record["business_details"] == details
        assert audit_record["business_details"] == details
    else:
        assert "business_details" not in record
        assert "business_details" not in audit_record
        for value in details.values():
            assert value not in json.dumps({"file": saved, "audit": audit})
    serialized = json.dumps({"mcp": result, "file": saved, "audit": audit})
    for excluded in (STRIPE_SECRET, "pi_fixture_secret_never_return", "private-metadata-sentinel"):
        assert excluded not in serialized
    request = httpx_mock.get_requests()[0]
    assert "include_business_details" not in request.url.params
    assert request.content == b""


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
    assert len(listed["items"]) == 36
    assert {
        "finance.stripe.invoices.pdf.download",
        "finance.stripe.invoices.pdf.cleanup",
    } <= {item["action_ref"] for item in listed["items"]}
    assert action_ref not in {item["action_ref"] for item in listed["items"]}
    full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "finance",
            "include_unavailable_integrations": True,
        },
    )
    assert len(full["items"]) == 37
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


def _seed_stripe_credential(
    mcp: MCPClient, project_id: int, *, provider_account_id: str = "acct_mcp_fixture"
) -> str:
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    display_name = (
        "Stripe MCP fixture"
        if provider_account_id == "acct_mcp_fixture"
        else "Stripe MCP other-account fixture"
    )
    with Session(engine) as session:
        credential_ref = (
            AuthRepository(session)
            .store_credential(
                provider_key="stripe",
                auth_method_key="api_key",
                display_name=display_name,
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
                provider_account_id=provider_account_id,
                display_name=display_name,
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


@pytest.mark.parametrize("object_type", ["customer", "invoice"])
def test_stripe_update_direct_uses_secret_refs_and_independent_readback(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    object_type: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    provider_id = "cus_mcp_fixture" if object_type == "customer" else "in_mcp_fixture"
    ref_field = "customer_ref" if object_type == "customer" else "invoice_ref"
    object_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type=f"stripe.{object_type}",
        provider_object_id=provider_id,
    )

    def secret(value: str) -> dict[str, str]:
        saved = mcp_client.call_tool_structured(
            "secret.set", {"project_id": project_id, "value": value}
        )["data"]
        return {"$secret_ref": saved["secret_ref"]}

    if object_type == "customer":
        private = {"name": "Correct Billing Name", "line1": "101 Private Lane", "country": "US"}
        payload = {
            ref_field: object_ref,
            "name": secret(private["name"]),
            "address": {"line1": secret(private["line1"]), "country": secret(private["country"])},
        }
        provider_response = stripe_customer(
            id=provider_id,
            name=private["name"],
            address={"line1": private["line1"], "country": private["country"]},
        )
    else:
        private = {"footer": "Please pay by direct deposit to the approved account."}
        payload = {
            ref_field: object_ref,
            "footer": secret(private["footer"]),
            "payment_method_types": ["customer_balance"],
        }
        provider_response = stripe_invoice(
            id=provider_id,
            footer=private["footer"],
            payment_settings={"payment_method_types": ["customer_balance"]},
            hosted_invoice_url=None,
            invoice_pdf=None,
        )

    action_ref = f"finance.stripe.{object_type}s.update"
    args = {
        "project_id": project_id,
        "action_ref": action_ref,
        "credential_ref": credential_ref,
        "input_json": payload,
        "confirm_direct": True,
        "intent_id": f"mcp-{object_type}-update-fixture",
        "intent_summary": "Fixture owner requested an existing draft billing correction.",
    }
    bad_ref = mcp_client.call_tool_error(
        "action.run", {**args, "input_json": {**payload, ref_field: provider_id}}
    )
    assert bad_ref["message"] == "ValidationError"
    foreign_credential_ref = _seed_stripe_credential(
        mcp_client, project_id, provider_account_id="acct_other_fixture"
    )
    wrong_account = mcp_client.call_tool_error(
        "action.run",
        {
            **args,
            "credential_ref": foreign_credential_ref,
            "intent_id": f"mcp-{object_type}-wrong-account-fixture",
        },
    )
    assert wrong_account["message"] == "ConflictError"
    assert wrong_account["data"]["error"] == (
        "provider object reference is not valid for this connection and object type"
    )
    assert httpx_mock.get_requests() == []

    httpx_mock.add_response(
        method="POST",
        url=f"https://api.stripe.com/v1/{object_type}s/{provider_id}",
        json=provider_response,
    )
    written = mcp_client.call_tool_structured("action.run", args)["data"]
    assert written["status"] == "success"
    saved = json.loads(Path(written["output"]["path"]).read_text(encoding="utf-8"))
    write_data = saved["response"]["output_json"]["data"]
    assert write_data[ref_field] == object_ref

    read_url = f"https://api.stripe.com/v1/{object_type}s/{provider_id}"
    if object_type == "invoice":
        read_url += "?expand%5B%5D=customer"
    httpx_mock.add_response(method="GET", url=read_url, json=provider_response)
    read = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": f"finance.stripe.{object_type}s.retrieve",
            "credential_ref": credential_ref,
            "input_json": {ref_field: object_ref},
        },
    )["data"]
    read_data = json.loads(Path(read["output"]["path"]).read_text(encoding="utf-8"))["response"][
        "output_json"
    ]["data"]
    if object_type == "customer":
        expected_name_hash = hashlib.sha256(private["name"].encode()).hexdigest()
        expected_line_hash = hashlib.sha256(private["line1"].encode()).hexdigest()
        assert write_data["name_sha256"] == read_data["name_sha256"] == expected_name_hash
        assert (
            write_data["address_field_sha256"]["line1"]
            == read_data["address_field_sha256"]["line1"]
            == expected_line_hash
        )
        expected_form = {
            "name": [private["name"]],
            "address[line1]": [private["line1"]],
            "address[country]": [private["country"]],
        }
    else:
        expected_footer_hash = hashlib.sha256(private["footer"].encode()).hexdigest()
        assert write_data["footer_sha256"] == read_data["footer_sha256"] == expected_footer_hash
        assert (
            write_data["payment_settings"]
            == read_data["payment_settings"]
            == {"payment_method_types": ["customer_balance"]}
        )
        assert write_data["status"] == read_data["status"] == "draft"
        expected_form = {
            "footer": [private["footer"]],
            "payment_settings[payment_method_types][]": ["customer_balance"],
        }

    requests = httpx_mock.get_requests()
    assert [request.method for request in requests] == ["POST", "GET"]
    assert parse_qs(requests[0].content.decode()) == expected_form
    assert requests[0].headers["Idempotency-Key"]
    assert "Idempotency-Key" not in requests[1].headers
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"action_key": f"stripe.{object_type}s.update", "status": "success"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"]
    assert len(audit) == 1
    assert audit[0]["id"] == written["action_call_id"]
    exposed = json.dumps({"write": written, "saved": saved, "read": read, "audit": audit})
    assert STRIPE_SECRET not in exposed
    assert provider_id not in exposed
    assert all(json.dumps(value) not in exposed for value in private.values())


@pytest.mark.parametrize(
    ("object_type", "allowed_step"),
    [("customer", "resolve-customer"), ("invoice", "create-draft")],
)
def test_stripe_update_execute_uses_payment_request_step_grant_without_delivery(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    object_type: str,
    allowed_step: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    provider_id = "cus_mcp_fixture" if object_type == "customer" else "in_mcp_fixture"
    ref_field = "customer_ref" if object_type == "customer" else "invoice_ref"
    object_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project_id,
        credential_ref=credential_ref,
        object_type=f"stripe.{object_type}",
        provider_object_id=provider_id,
    )
    private_text = "Reviewed billing name" if object_type == "customer" else "Bank transfer only."
    secret_ref = mcp_client.call_tool_structured(
        "secret.set", {"project_id": project_id, "value": private_text}
    )["data"]["secret_ref"]
    payload = {
        ref_field: object_ref,
        ("name" if object_type == "customer" else "footer"): {"$secret_ref": secret_ref},
    }
    action_ref = f"finance.stripe.{object_type}s.update"
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "finance.payment-request",
            "plugin_slug": "finance",
            "inputs_json": _payment_request_inputs(),
        },
    )
    plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": plan_id}
    )["data"]
    run_token = started["run_token"]
    args = {
        "project_id": project_id,
        "action_ref": action_ref,
        "credential_ref": credential_ref,
        "input_json": payload,
        "run_token": run_token,
        "idempotency_key": f"mcp-granted-{object_type}-update",
        "output_policy_json": {"mode": "inline"},
        "response_mode": "raw",
    }
    predecessors = [("preflight", "scoped")]
    if object_type == "invoice":
        predecessors.append(("resolve-customer", "customer-resolved"))
    for step_id, status in predecessors:
        mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {"run_plan_id": plan_id, "step_id": step_id, "run_token": run_token},
        )
        denied = mcp_client.call_tool_error("action.execute", args)
        assert denied["message"] == "ToolNotGrantedError"
        assert httpx_mock.get_requests() == []
        mcp_client.call_tool_structured(
            "runPlan.recordStep",
            {
                "run_plan_id": plan_id,
                "step_id": step_id,
                "status": "success",
                "result_json": _payment_request_result(status, invoice_ref=object_ref),
                "run_token": run_token,
            },
        )
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": plan_id, "step_id": allowed_step, "run_token": run_token},
    )["data"]
    response = (
        stripe_customer(id=provider_id, name=private_text)
        if object_type == "customer"
        else stripe_invoice(id=provider_id, footer=private_text)
    )
    httpx_mock.add_response(
        method="POST", url=f"https://api.stripe.com/v1/{object_type}s/{provider_id}", json=response
    )
    executed = mcp_client.call_tool_structured("action.execute", args)["data"]
    assert executed["action_call"]["status"] == "success"
    assert executed["action_call"]["run_plan_step_id"] == claimed["id"]
    data = executed["output_json"]["data"]
    expected_hash = hashlib.sha256(private_text.encode()).hexdigest()
    assert data["name_sha256" if object_type == "customer" else "footer_sha256"] == expected_hash
    replay = mcp_client.call_tool_structured("action.execute", args)["data"]
    assert replay["action_call"]["id"] == executed["action_call"]["id"]
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/v1/{object_type}s/{provider_id}"
    assert requests[0].headers["Idempotency-Key"]
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"run_plan_id": plan_id, "action_key": f"stripe.{object_type}s.update"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"]
    assert len(audit) == 1
    assert audit[0]["run_plan_step_id"] == claimed["id"]
    exposed = json.dumps({"execute": executed, "replay": replay, "audit": audit})
    assert STRIPE_SECRET not in exposed
    assert private_text not in exposed
    assert provider_id not in exposed


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


def test_stripe_business_details_granted_read_preserves_links_and_denies_other_actions(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    invoice_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project_id,
        credential_ref=credential_ref,
    )
    action_ref = "finance.stripe.invoices.retrieve"
    plan = _stripe_balance_plan()
    plan["grants"]["mcp_tool_grants"][0]["action_refs"] = [action_ref]
    plan["steps"][0]["action_refs"] = [action_ref]
    plan_id = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan},
    )["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": plan_id},
    )["data"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": plan_id, "step_id": "read-balance", "run_token": started["run_token"]},
    )["data"]
    args = {
        "project_id": project_id,
        "credential_ref": credential_ref,
        "run_token": started["run_token"],
        "response_mode": "raw",
        "output_policy_json": {"mode": "inline"},
    }
    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            **args,
            "action_ref": "finance.stripe.charges.retrieve",
            "input_json": {
                "charge_ref": "provider-object:not-granted",
                "include_business_details": True,
            },
        },
    )
    assert denied["message"] == "ToolNotGrantedError"
    assert httpx_mock.get_requests() == []
    link = "https://invoice.stripe.com/i/acct_fixture/granted_fixture"
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoices/in_mcp_fixture?expand%5B%5D=customer",
        json=stripe_invoice(
            id="in_mcp_fixture",
            status="paid",
            amount_paid=1000,
            amount_remaining=0,
            hosted_invoice_url=link,
            client_secret="pi_fixture_secret_never_return",
        ),
    )
    executed = mcp_client.call_tool_structured(
        "action.execute",
        {
            **args,
            "action_ref": action_ref,
            "input_json": {"invoice_ref": invoice_ref, "include_business_details": True},
        },
    )["data"]
    assert executed["output_json"]["data"]["business_details"]["hosted_invoice_url"] == link
    assert executed["action_call"]["run_plan_step_id"] == claimed["id"]
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"run_plan_id": plan_id, "run_plan_step_id": claimed["id"], "status": "success"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"][0]
    assert audit["id"] == executed["action_call"]["id"]
    assert audit["response_json"]["data"]["business_details"]["hosted_invoice_url"] == link
    serialized = json.dumps({"mcp": executed, "audit": audit})
    assert STRIPE_SECRET not in serialized
    assert "pi_fixture_secret_never_return" not in serialized
    assert len(httpx_mock.get_requests()) == 1


def test_stripe_catalog_granted_read_is_file_backed_and_denies_ungranted_sibling(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    action_ref = "finance.stripe.products.list"
    plan = _stripe_balance_plan()
    plan["grants"]["mcp_tool_grants"][0]["action_refs"] = [action_ref]
    plan["steps"][0]["action_refs"] = [action_ref]
    plan_id = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan},
    )["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": plan_id},
    )["data"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": plan_id,
            "step_id": "read-balance",
            "run_token": started["run_token"],
        },
    )["data"]
    args = {
        "project_id": project_id,
        "credential_ref": credential_ref,
        "run_token": started["run_token"],
    }
    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            **args,
            "action_ref": "finance.stripe.prices.list",
            "input_json": {"limit": 1},
        },
    )
    assert denied["message"] == "ToolNotGrantedError"
    assert httpx_mock.get_requests() == []
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/products?limit=1",
        json={
            "object": "list",
            "has_more": False,
            "data": [stripe_product(name="Consulting package")],
        },
    )
    executed = mcp_client.call_tool_structured(
        "action.execute",
        {
            **args,
            "action_ref": action_ref,
            "input_json": {"limit": 1, "include_business_details": True},
        },
    )["data"]
    assert executed["status"] == "success"
    response_file = json.loads(Path(executed["output"]["path"]).read_text(encoding="utf-8"))
    output = response_file["response"]["output_json"]["data"]
    assert output["items"][0]["business_details"]["name"] == "Consulting package"
    assert output["items"][0]["product_ref"].startswith("provider-object:")
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"run_plan_id": plan_id, "run_plan_step_id": claimed["id"], "status": "success"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"]
    assert len(audit) == 1
    assert audit[0]["action_key"] == "stripe.products.list"
    assert audit[0]["run_plan_step_id"] == claimed["id"]
    serialized = json.dumps({"mcp": executed, "file": response_file, "audit": audit})
    assert STRIPE_SECRET not in serialized
    assert "prod_fixture" not in serialized
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "use_catalog_price", [False, True], ids=["manual-amount", "dated-catalog-price"]
)
def test_stripe_business_details_email_to_existing_payment_invoice_handoff(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    use_catalog_price: bool,
) -> None:
    """Mocked explicit owner-scoped actions, not a financial/workflow signoff.

    The fixture owner selects one full payment and its invoice text. It proves
    transport and agent-readable output, without send, charge, or live accounts.
    """
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_stripe_credential(mcp_client, project_id)
    email = "fixture@example.test"
    description = "Completed fixture consulting"
    email_secret, description_secret = [
        {
            "$secret_ref": mcp_client.call_tool_structured(
                "secret.set",
                {"project_id": project_id, "value": value},
            )["data"]["secret_ref"]
        }
        for value in (email, description)
    ]
    records: list[dict[str, Any]] = []

    def run(action: str, payload: dict[str, Any], *, write: bool = False) -> dict[str, Any]:
        result = mcp_client.call_tool_structured(
            "action.run",
            {
                "project_id": project_id,
                "credential_ref": credential_ref,
                "action_ref": f"finance.stripe.{action}",
                "input_json": payload,
                **(
                    {
                        "confirm_direct": True,
                        "intent_id": f"fixture-handoff-{action}",
                        "intent_summary": (
                            "Fixture owner approved one invoice for the selected existing payment; "
                            "no send or charge."
                        ),
                    }
                    if write
                    else {}
                ),
            },
        )["data"]
        assert result.get("status") == "success", result
        saved = json.loads(Path(result["output"]["path"]).read_text(encoding="utf-8"))
        records.append(saved)
        return saved["response"]["output_json"]["data"]

    customer = stripe_customer(email=email, name="Fixture Customer")
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/customers?limit=1&email=fixture%40example.test",
        json={"object": "list", "data": [customer], "has_more": False},
    )
    customers = run("customers.list", {"email": email_secret, "limit": 1})
    assert customers["has_more"] is False
    customer_ref = customers["items"][0]["customer_ref"]
    httpx_mock.add_response(
        method="GET", url="https://api.stripe.com/v1/customers/cus_fixture", json=customer
    )
    customer_read = run(
        "customers.retrieve", {"customer_ref": customer_ref, "include_business_details": True}
    )
    assert customer_read["business_details"]["email"] == email
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/charges?limit=1&customer=cus_fixture",
        json={
            "object": "list",
            "has_more": False,
            "data": [
                stripe_charge(
                    customer="cus_fixture",
                    payment_intent="pi_fixture",
                    invoice=None,
                )
            ],
        },
    )
    charges = run("charges.list", {"customer_ref": customer_ref, "limit": 1})
    assert charges["has_more"] is False
    payment_ref = charges["items"][0]["payment_intent_ref"]
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/payment_intents/pi_fixture",
        json={
            "id": "pi_fixture",
            "object": "payment_intent",
            "customer": "cus_fixture",
            "status": "succeeded",
            "amount": 1000,
            "amount_received": 1000,
            "currency": "usd",
            "livemode": False,
            "created": 1700000000,
            "client_secret": "pi_fixture_secret_never_return",
        },
    )
    payment = run("payment-intents.retrieve", {"payment_intent_ref": payment_ref})
    assert payment["status"] == "succeeded"
    assert payment["customer_ref"] == customer_ref
    assert payment["amount_received"] == 1000
    assert payment["currency"] == "usd"
    assert payment["livemode"] is False
    issue_timestamp = 1698796800
    price_ref = product_ref = None
    if use_catalog_price:
        product = stripe_product(name="Consulting package", description="Consulting services")
        price = stripe_price(
            unit_amount=500,
            unit_amount_decimal="500",
            nickname="Half-hour consulting",
            lookup_key="consulting-half-hour",
        )
        httpx_mock.add_response(
            method="GET",
            url="https://api.stripe.com/v1/products?limit=1&active=true",
            json={"object": "list", "data": [product], "has_more": False},
        )
        products = run(
            "products.list", {"limit": 1, "active": True, "include_business_details": True}
        )
        product_ref = products["items"][0]["product_ref"]
        assert products["items"][0]["business_details"]["name"] == product["name"]
        httpx_mock.add_response(
            method="GET", url="https://api.stripe.com/v1/products/prod_fixture", json=product
        )
        product_read = run(
            "products.retrieve", {"product_ref": product_ref, "include_business_details": True}
        )
        httpx_mock.add_response(
            method="GET",
            url="https://api.stripe.com/v1/prices?limit=1&product=prod_fixture&currency=usd&type=one_time",
            json={"object": "list", "data": [price], "has_more": False},
        )
        prices = run(
            "prices.list",
            {
                "product_ref": product_ref,
                "currency": "usd",
                "type": "one_time",
                "limit": 1,
                "include_business_details": True,
            },
        )
        price_ref = prices["items"][0]["price_ref"]
        assert price_ref == product_read["default_price_ref"]
        assert prices["items"][0]["business_details"]["nickname"] == price["nickname"]
        httpx_mock.add_response(
            method="GET",
            url="https://api.stripe.com/v1/prices/price_fixture?expand%5B%5D=tiers&expand%5B%5D=currency_options",
            json={
                **price,
                "currency_options": {"usd": {"unit_amount": 500, "unit_amount_decimal": "500"}},
            },
        )
        price_read = run(
            "prices.retrieve", {"price_ref": price_ref, "include_business_details": True}
        )
        assert price_read["product_ref"] == product_ref
        assert price_read["unit_amount"] == 500
        assert price_read["unit_amount_decimal"] == "500"
    draft = stripe_invoice(
        description=description,
        customer_email=email,
        **({"effective_at": issue_timestamp} if use_catalog_price else {}),
    )
    httpx_mock.add_response(method="POST", url="https://api.stripe.com/v1/invoices", json=draft)
    created = run(
        "invoices.create",
        {
            "customer_ref": customer_ref,
            "collection_method": "send_invoice",
            "currency": "usd",
            "days_until_due": 30,
            "description": description_secret,
            **({"effective_at": issue_timestamp} if use_catalog_price else {}),
        },
        write=True,
    )
    assert created["status"] == "draft"
    if use_catalog_price:
        assert created["effective_at"] == issue_timestamp
        assert created["created"] != issue_timestamp
    assert description not in json.dumps(records[-1])
    invoice_ref = created["invoice_ref"]
    item_description = "Consulting package" if use_catalog_price else description
    item = stripe_invoice_item(
        invoice="in_fixture",
        description=item_description,
        **(
            {
                "quantity": 2,
                "quantity_decimal": "2",
                "pricing": {
                    "type": "price_details",
                    "unit_amount_decimal": "500",
                    "price_details": {"price": "price_fixture", "product": "prod_fixture"},
                },
            }
            if use_catalog_price
            else {}
        ),
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.stripe.com/v1/invoiceitems",
        json=item,
    )
    line = run(
        "invoice-items.create",
        {
            "customer_ref": customer_ref,
            "invoice_ref": invoice_ref,
            **(
                {"price_ref": price_ref, "quantity": 2}
                if use_catalog_price
                else {
                    "amount": 1000,
                    "currency": "usd",
                    "description": description_secret,
                }
            ),
        },
        write=True,
    )
    assert line["invoice_ref"] == invoice_ref
    assert description not in json.dumps(records[-1])
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoiceitems?limit=1&invoice=in_fixture",
        json={"object": "list", "data": [item], "has_more": False},
    )
    independent_line = run(
        "invoice-items.list",
        {
            "invoice_ref": invoice_ref,
            "limit": 1,
            "include_business_details": True,
        },
    )["items"][0]
    assert independent_line["amount"] == 1000
    assert independent_line["currency"] == "usd"
    assert independent_line["business_details"]["description"] == item_description
    if use_catalog_price:
        assert independent_line["price_ref"] == price_ref
        assert independent_line["product_ref"] == product_ref
        assert independent_line["quantity"] == 2
        assert independent_line["quantity_decimal"] == "2"
        assert independent_line["unit_amount_decimal"] == "500"
    httpx_mock.add_response(
        method="POST",
        url="https://api.stripe.com/v1/invoices/in_fixture/finalize",
        json={**draft, "status": "open"},
    )
    finalized = run("invoices.finalize", {"invoice_ref": invoice_ref}, write=True)
    assert finalized["status"] == "open"
    link = "https://invoice.stripe.com/i/acct_fixture/paid_fixture"
    pdf_link = "https://pay.stripe.com/invoice/acct_fixture/paid_fixture/pdf"
    paid = {
        **draft,
        "status": "paid",
        "amount_paid": 1000,
        "amount_remaining": 0,
        "hosted_invoice_url": link,
        "invoice_pdf": pdf_link,
        "number": "FIXTURE-0002",
    }
    httpx_mock.add_response(
        method="POST",
        url="https://api.stripe.com/v1/invoices/in_fixture/attach_payment",
        json=paid,
    )
    attached = run(
        "invoices.attach-payment",
        {"invoice_ref": invoice_ref, "payment_intent_ref": payment_ref},
        write=True,
    )
    assert attached["status"] == "paid"
    assert "business_details" not in attached
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoices/in_fixture?expand%5B%5D=customer",
        json=paid,
    )
    invoice = run(
        "invoices.retrieve", {"invoice_ref": invoice_ref, "include_business_details": True}
    )
    assert invoice["invoice_ref"] == invoice_ref
    assert invoice["customer_ref"] == customer_ref
    assert invoice["status"] == "paid"
    assert invoice["amount_remaining"] == 0
    assert invoice["business_details"]["hosted_invoice_url"] == link
    assert invoice["business_details"]["invoice_pdf"] == pdf_link
    if use_catalog_price:
        assert invoice["effective_at"] == issue_timestamp
        assert invoice["created"] != issue_timestamp
    assert invoice["business_details"]["description"] == description
    assert invoice["business_details"]["customer_email"] == email
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoice_payments?limit=1&invoice=in_fixture",
        json={
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "inpay_fixture",
                    "object": "invoice_payment",
                    "invoice": "in_fixture",
                    "amount_requested": 1000,
                    "amount_paid": 1000,
                    "currency": "usd",
                    "created": 1700000000,
                    "is_default": False,
                    "livemode": False,
                    "status": "paid",
                    "status_transitions": {"paid_at": 1700000000},
                    "payment": {"type": "payment_intent", "payment_intent": "pi_fixture"},
                }
            ],
        },
    )
    allocations = run("invoice-payments.list", {"invoice_ref": invoice_ref, "limit": 1})
    assert allocations["has_more"] is False
    allocation = allocations["items"][0]
    assert allocation["invoice_ref"] == invoice_ref
    assert allocation["payment_intent_ref"] == payment_ref
    assert allocation["status"] == "paid"
    assert allocation["amount_paid"] == 1000
    posts = [request for request in httpx_mock.get_requests() if request.method == "POST"]
    assert [request.url.path for request in posts] == [
        "/v1/invoices",
        "/v1/invoiceitems",
        "/v1/invoices/in_fixture/finalize",
        "/v1/invoices/in_fixture/attach_payment",
    ]
    assert parse_qs(posts[0].content.decode())["auto_advance"] == ["false"]
    assert parse_qs(posts[0].content.decode())["collection_method"] == ["send_invoice"]
    if use_catalog_price:
        assert parse_qs(posts[0].content.decode())["effective_at"] == [str(issue_timestamp)]
        assert parse_qs(posts[1].content.decode()) == {
            "customer": ["cus_fixture"],
            "invoice": ["in_fixture"],
            "pricing[price]": ["price_fixture"],
            "quantity": ["2"],
        }
    assert parse_qs(posts[2].content.decode()) == {"auto_advance": ["false"]}
    assert parse_qs(posts[3].content.decode()) == {"payment_intent": ["pi_fixture"]}
    for request in httpx_mock.get_requests():
        assert "include_business_details" not in request.url.params
        assert b"include_business_details" not in request.content
        assert request.headers["Stripe-Version"] == "2026-08-26.dahlia"
        if request.method == "GET":
            assert "Idempotency-Key" not in request.headers
        else:
            assert request.headers["Idempotency-Key"]
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"plugin_slug": "finance", "status": "success"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"]
    assert len(audit) == len(records)
    serialized = json.dumps({"files": records, "audit": audit})
    for excluded in (
        STRIPE_SECRET,
        "pi_fixture_secret_never_return",
        "cus_fixture",
        "in_fixture",
        "prod_fixture",
        "price_fixture",
        'pi_fixture"',
    ):
        assert excluded not in serialized


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
