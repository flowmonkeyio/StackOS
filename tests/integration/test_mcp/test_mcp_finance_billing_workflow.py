"""Actual billing templates with isolated MCP, mocked HTTP and external fixtures.

The Python test plays the agent and owner. It does not prove LLM judgment, real
email delivery, or production custody. No finance implementation is introduced:
the small Markdown fixture is test-only, outside the StackOS database.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from pytest_httpx import HTTPXMock

from tests.helpers.finance_workspace import (
    AT,
    add_billing_fixture,
    add_settlement_fixture,
    digest,
    initialize,
    money,
    read_document,
    write_document,
)
from tests.helpers.finance_workspace import (
    record as finance_record,
)
from tests.helpers.stripe import stripe_customer, stripe_invoice, stripe_invoice_item

from .conftest import MCPClient
from .test_mcp_stripe_actions import (
    STRIPE_SECRET,
    _seed_stripe_credential,
    _seed_stripe_object_ref,
)

EMAIL = "billing-fixture@example.test"
LINES = [(7000, "Synthetic design work"), (3000, "Synthetic review work")]


class BillingOccurrence:
    """Test driver for real operations, not a shipping orchestration engine."""

    def __init__(
        self, mcp: MCPClient, project: int, credential: str, *, settlement: bool = False
    ) -> None:
        self.mcp, self.project, self.credential = mcp, project, credential
        inputs = {
            "workspace_ref": "finance-workspace:billing-fixture",
            "billing_request_ref": "billing-request:fixture-v1",
        }
        workflow = "finance.payment-request"
        self.summary_key = "payment_request_summary"
        if settlement:
            workflow = "finance.payment-request-followups"
            self.summary_key = "followup_summary"
            inputs = {
                "workspace_ref": "finance-workspace:billing-fixture",
                "occurrence_mode": "settlement-only",
                "settlement_scope_ref": "settlement-scope:fixture-v1",
                "review_window": {"start": "2026-09-01", "end": "2026-09-05"},
            }
        validated = self.call(
            "runPlan.validate",
            workflow_key=workflow,
            inputs_json=inputs,
            enforce_required_inputs=True,
        )
        assert validated["valid"] is True, validated
        created = self.call(
            "runPlan.create",
            workflow_key=workflow,
            inputs_json=inputs,
        )
        self.plan = created["id"]
        self.token = self.call("runPlan.start", run_plan_id=self.plan)["run_token"]
        self.audit_refs: list[str] = []
        self.summary: dict[str, Any] = {
            "status": "scoped",
            "recovery_state": "not-needed",
            "exception_refs": [],
        }
        if settlement:
            self.summary.update(
                occurrence_mode="settlement-only",
                settlement_state="not-requested",
                resend_state="not-requested",
                settlement_scope_ref=inputs["settlement_scope_ref"],
            )
        else:
            self.summary["billing_request_ref"] = inputs["billing_request_ref"]
        self.step = ""

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        result = self.mcp.call_tool_structured(
            name,
            {"project_id": self.project, "response_mode": "raw", **arguments},
        )
        return result.get("data", result)

    def claim(self, step: str) -> None:
        self.step = step
        result = self.call(
            "runPlan.claimStep",
            run_plan_id=self.plan,
            step_id=step,
            run_token=self.token,
        )
        assert result["status"] == "running"

    def record(self, status: str, *, blocked: bool = False) -> dict[str, Any]:
        self.summary["status"] = status
        return self.call(
            "runPlan.recordStep",
            run_plan_id=self.plan,
            step_id=self.step,
            run_token=self.token,
            status="blocked" if blocked else "success",
            result_json={self.summary_key: dict(self.summary)},
            **({"error": "Synthetic provider outcome requires reconciliation"} if blocked else {}),
        )

    def args(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": self.project,
            "run_token": self.token,
            "credential_ref": self.credential,
            "action_ref": "finance.stripe." + action,
            "input_json": payload,
            "response_mode": "raw",
            "output_policy_json": {"mode": "inline"},
        }

    def execute(
        self, action: str, payload: dict[str, Any], *, key: str | None = None
    ) -> dict[str, Any]:
        args = self.args(action, payload)
        if key:
            args["idempotency_key"] = key
        result = self.mcp.call_tool_structured("action.execute", args)["data"]
        assert "action_call" in result, result
        self.audit_refs.append(f"action-call:{result['action_call']['id']}")
        self.summary["action_call_refs"] = list(self.audit_refs)
        assert result["action_call"]["run_plan_id"] == self.plan
        serialized = json.dumps(result)
        for private in (STRIPE_SECRET, EMAIL, *(line[1] for line in LINES)):
            assert private not in serialized
        return result["output_json"]["data"]

    def approve(self, gate: str) -> None:
        # Explicitly simulated owner decision through the existing admin surface
        # of the isolated app, never the installed localhost service.
        response = self.mcp.test_client.post(
            "/api/v1/operations/runPlan.update/call",
            json={
                "arguments": {
                    "run_plan_id": self.plan,
                    "approval_key": gate,
                    "approval_status": "approved",
                    "decided_by": "synthetic-owner",
                    "decision_json": {"approval_ref": "external-approval:proposal-v1"},
                    "response_mode": "raw",
                }
            },
            headers=self.mcp._headers(),
        )
        assert response.status_code == 200, response.text

    def secret(self, text: str) -> dict[str, str]:
        result = self.call("secret.set", value=text)
        return {"$secret_ref": result["secret_ref"]}


def _stripe_billing_fixture(httpx_mock: HTTPXMock, *, currency: str = "usd") -> dict[str, Any]:
    state: dict[str, Any] = {"status": "draft", "items": [], "send_timeout": False}

    def invoice() -> dict[str, Any]:
        total = sum(row["amount"] for row in state["items"])
        paid = total if state["status"] == "paid" else 0
        return stripe_invoice(
            **{
                "id": "in_billing_fixture",
                "object": "invoice",
                "status": state["status"],
                "customer": stripe_customer(id="cus_billing_fixture", email=EMAIL, currency="usd"),
                "customer_email": EMAIL,
                "currency": currency,
                "collection_method": "send_invoice",
                "auto_advance": False,
                "livemode": False,
                "subtotal": total,
                "total": total,
                "amount_due": total,
                "amount_paid": paid,
                "amount_remaining": total - paid,
            }
        )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Stripe-Version"] == "2026-08-26.dahlia"
        assert request.headers["Authorization"] == "Bearer " + STRIPE_SECRET
        path, method = request.url.path, request.method
        if method == "GET":
            assert "Idempotency-Key" not in request.headers
        else:
            assert request.headers["Idempotency-Key"]
        form = parse_qs(request.content.decode())
        if path == "/v1/customers" and method == "GET":
            assert request.url.params["email"] == EMAIL
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "has_more": False,
                    "data": [
                        stripe_customer(id="cus_billing_fixture", email=EMAIL, currency="usd"),
                    ],
                },
            )
        if path == "/v1/invoices" and method == "POST":
            assert form["customer"] == ["cus_billing_fixture"]
            assert form["auto_advance"] == ["false"]
            assert form["collection_method"] == ["send_invoice"]
            assert form["days_until_due"] == ["30"]
            assert form["currency"] == [currency]
            return httpx.Response(200, json=invoice())
        if path == "/v1/invoiceitems" and method == "POST":
            amount, description = LINES[len(state["items"])]
            assert form["amount"] == [str(amount)]
            assert form["description"] == [description]
            assert form["invoice"] == ["in_billing_fixture"]
            assert form["customer"] == ["cus_billing_fixture"]
            assert form["currency"] == [currency]
            item = stripe_invoice_item(
                **{
                    "id": f"ii_billing_{len(state['items'])}",
                    "object": "invoiceitem",
                    "amount": amount,
                    "description": description,
                    "currency": currency,
                    "invoice": "in_billing_fixture",
                    "customer": "cus_billing_fixture",
                }
            )
            state["items"].append(item)
            return httpx.Response(200, json=item)
        if path == "/v1/invoiceitems" and method == "GET":
            assert request.url.params["invoice"] == "in_billing_fixture"
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "has_more": False,
                    "data": state["items"],
                },
            )
        if path == "/v1/invoices/in_billing_fixture" and method == "GET":
            return httpx.Response(200, json=invoice())
        if path == "/v1/invoices/in_billing_fixture/finalize" and method == "POST":
            assert state["status"] == "draft"
            assert form == {"auto_advance": ["false"]}
            state["status"] = "open"
            return httpx.Response(200, json=invoice())
        if path == "/v1/invoices/in_billing_fixture/send" and method == "POST":
            assert state["status"] == "open"
            assert form == {}
            if state["send_timeout"]:
                raise httpx.ReadTimeout("Synthetic uncertain send", request=request)
            return httpx.Response(200, json=invoice())
        raise AssertionError(f"Unexpected fixture HTTP: {method} {path}")

    httpx_mock.add_callback(respond, is_reusable=True)
    return state


@pytest.mark.parametrize("outcome", ["accepted", "paid-before-send", "unknown-send"])
@pytest.mark.parametrize("currency", ["usd", "cad"])
def test_actual_billing_template_two_lines_and_send_recovery(
    mcp_client: MCPClient,
    seeded_project: dict[str, Any],
    httpx_mock: HTTPXMock,
    tmp_path: Path,
    outcome: str,
    currency: str,
) -> None:
    # CAD is deliberately different from this customer's USD default. Approval
    # must reach invoice creation before its first line is added.
    state = _stripe_billing_fixture(httpx_mock, currency=currency)
    project = seeded_project["data"]["id"]
    run = BillingOccurrence(mcp_client, project, _seed_stripe_credential(mcp_client, project))
    finance = tmp_path / "external" / "finance"
    index = initialize(finance)
    # This fixture models an immutable externally approved request. Synthetic
    # reviewer/owner decisions below are not assertions of automated judgment.
    document = read_document(index)
    request_record = add_billing_fixture(document, currency=currency, lines=LINES, email=EMAIL)
    document["revision"] += 1
    write_document(index, document, digest(index))
    assert [
        (row["amount"]["amount_minor"], row["description"])
        for row in read_document(index)["billing_versions"][0]["lines"]
    ] == list(LINES)

    run.claim("preflight")
    run.record("scoped")
    run.claim("resolve-customer")
    customers = run.execute("customers.list", {"email": run.secret(EMAIL)})
    assert customers["has_more"] is False and len(customers["items"]) == 1
    customer_ref = customers["items"][0]["customer_ref"]
    run.summary["customer_ref"] = customer_ref
    run.record("customer-resolved")
    run.claim("create-draft")
    draft = run.execute(
        "invoices.create",
        {
            "customer_ref": customer_ref,
            "collection_method": "send_invoice",
            "days_until_due": 30,
            "currency": request_record["currency"].lower(),
            "correlation_key": "a" * 32,
        },
        key="billing-fixture-v1-draft",
    )
    invoice_ref = draft["invoice_ref"]
    assert draft["currency"] == request_record["currency"].lower()
    run.summary["invoice_ref"] = invoice_ref
    for number, (amount, description) in enumerate(LINES):
        run.execute(
            "invoice-items.create",
            {
                "customer_ref": customer_ref,
                "invoice_ref": invoice_ref,
                "amount": amount,
                "currency": currency,
                "description": run.secret(description),
            },
            key=f"billing-fixture-v1-line-{number}",
        )
    run.record("draft-prepared")

    def read_and_compare() -> dict[str, Any]:
        observed = run.execute("invoices.retrieve", {"invoice_ref": invoice_ref})
        items = run.execute("invoice-items.list", {"invoice_ref": invoice_ref})
        assert items["has_more"] is False and len(items["items"]) == 2
        assert sorted(
            (row["amount"], row["description_sha256"]) for row in items["items"]
        ) == sorted((amount, hashlib.sha256(text.encode()).hexdigest()) for amount, text in LINES)
        assert observed["customer_ref"] == customer_ref
        assert observed["subtotal"] == observed["total"] == 10000
        assert observed["currency"] == currency and observed["livemode"] is False
        digest = hashlib.sha256(EMAIL.encode()).hexdigest()
        assert observed["current_customer_email_sha256"] == digest
        assert observed["invoice_customer_email_sha256"] == digest
        # The API deliberately cannot prove Dashboard-only extra recipients.
        assert observed["recipient_scope"] == "primary-email-fields-only"
        saved = read_document(index)
        assert saved["recipient_settings"][0]["additional_recipients"]["state"] == "verified-none"
        assert saved["billing_versions"][0] == request_record
        return observed

    run.claim("review-draft")
    assert read_and_compare()["status"] == "draft"
    run.summary.update(proposal_version_ref="proposal:v1", control_review_ref="review:synthetic-v1")
    run.record("review-ready")
    run.claim("finalize-invoice")
    before = len(httpx_mock.get_requests())
    denied = mcp_client.call_tool_error(
        "action.execute", run.args("invoices.finalize", {"invoice_ref": invoice_ref})
    )
    assert denied["data"]["approval_ref"] == "owner-invoice-finalization"
    assert len(httpx_mock.get_requests()) == before
    run.approve("owner-invoice-finalization")
    read_and_compare()
    assert (
        run.execute("invoices.finalize", {"invoice_ref": invoice_ref}, key="billing-v1-finalize")[
            "status"
        ]
        == "open"
    )
    run.summary["approval_refs"] = ["approval:finalize-v1"]
    run.record("finalized")

    run.claim("send-invoice")
    before = len(httpx_mock.get_requests())
    denied = mcp_client.call_tool_error(
        "action.execute", run.args("invoices.send", {"invoice_ref": invoice_ref})
    )
    assert denied["data"]["approval_ref"] == "owner-invoice-send"
    assert len(httpx_mock.get_requests()) == before
    run.approve("owner-invoice-send")
    run.summary["approval_refs"].append("approval:send-v1")
    if outcome == "paid-before-send":
        state["status"] = "paid"
    observed = read_and_compare()
    if outcome == "paid-before-send":
        assert observed["amount_remaining"] == 0
        run.summary.update(delivery_state="suppressed", suppression_ref="suppression:paid-v1")
        run.record("suppressed")
    elif outcome == "unknown-send":
        state["send_timeout"] = True
        args = {
            **run.args("invoices.send", {"invoice_ref": invoice_ref}),
            "idempotency_key": "billing-v1-send",
        }
        error = mcp_client.call_tool_error("action.execute", args)
        assert "unknown" in json.dumps(error).lower() or "reconcile" in json.dumps(error).lower()
        run.summary.update(delivery_state="unknown", recovery_state="same-key-reconcile-required")
        run.record("reconcile-required", blocked=True)
        # Resume with the same plan and inspect. Open status is not proof of
        # send success, so neither a second send nor final closeout is allowed.
        run.claim("send-invoice")
        assert read_and_compare()["status"] == "open"
        run.record("reconcile-required", blocked=True)
    else:
        run.execute("invoices.send", {"invoice_ref": invoice_ref}, key="billing-v1-send")
        # Stripe test mode accepts send but emits no email. This fixture cannot
        # establish delivery and must not close out with a real-send claim.
        run.summary["delivery_state"] = "test-accepted"
        run.record("test-accepted")

    document = read_document(index)
    document["provider_observations"].append(
        finance_record(
            "observation:billing-outcome",
            "observed",
            provider_ref="fixture:provider",
            account_ref="fixture:account",
            object_type="invoice",
            object_ref=invoice_ref,
            observed_at=AT,
            observed_status=observed["status"],
            livemode=False,
            amount_remaining=money(observed["amount_remaining"], currency),
            source_refs=run.audit_refs,
        )
    )
    document["collection_decisions"].append(
        finance_record(
            "decision:billing-outcome",
            "recorded",
            version=1,
            invoice_ref=invoice_ref,
            eligibility="suppressed" if outcome == "paid-before-send" else "unknown",
            decision_reason=f"Synthetic provider outcome: {outcome}",
            reminder_owner="agent",
            send_outcome={
                "accepted": "test-accepted",
                "unknown-send": "unknown",
                "paid-before-send": "not-attempted",
            }[outcome],
        )
    )
    document["revision"] += 1
    write_document(index, document, digest(index))
    assert read_document(index)["billing_versions"][0] == request_record
    assert list(finance.glob("*.json")) == [index]
    if outcome != "unknown-send":
        run.claim("record-and-handoff")
        # Neither a test-only acceptance nor a paid-before-send suppression
        # creates a real customer contact or live follow-up handoff.
        run.summary.update(external_write_proof_ref="write-proof:billing-v1", handoff_refs=[])
        completed = run.record("recorded")
        assert completed["status"] == "completed"
        if outcome == "accepted":
            final_step = run.call(
                "runPlan.getStep", run_plan_id=run.plan, step_id="record-and-handoff"
            )
            final_summary = final_step["result_json"]["payment_request_summary"]
            assert final_summary["delivery_state"] == "test-accepted"
            assert final_summary["handoff_refs"] == []
    sends = [r for r in httpx_mock.get_requests() if r.url.path.endswith("/send")]
    assert len(sends) == (0 if outcome == "paid-before-send" else 1)
    assert (
        len(
            [
                r
                for r in httpx_mock.get_requests()
                if r.method == "POST" and r.url.path == "/v1/invoices"
            ]
        )
        == 1
    )
    assert len(state["items"]) == 2
    final = run.call("runPlan.get", run_plan_id=run.plan)
    assert final["status"] == ("started" if outcome == "unknown-send" else "completed")
    for private in (EMAIL, *(line[1] for line in LINES)):
        assert private not in json.dumps(final)


@pytest.mark.parametrize(
    ("amount", "attach_timeout", "missing_recovery_ref"),
    [
        pytest.param(4000, False, False, id="partial-attach-known"),
        pytest.param(10000, False, False, id="full-attach-known"),
        pytest.param(4000, True, False, id="partial-attach-unknown"),
        pytest.param(10000, True, False, id="full-attach-unknown"),
        pytest.param(10000, False, True, id="missing-ref-deferred-list-hold"),
    ],
)
def test_actual_settlement_template_recovers_lost_report_without_reporting_twice(
    mcp_client: MCPClient,
    seeded_project: dict[str, Any],
    httpx_mock: HTTPXMock,
    tmp_path: Path,
    amount: int,
    attach_timeout: bool,
    missing_recovery_ref: bool,
) -> None:
    """Lose report response, recover ref, and reconcile even a lost attach response.

    Known-ref recovery and received-bank evidence are synthetic agent inputs.
    The connector, grants, provider projections, audit and template are real.
    """
    project = seeded_project["data"]["id"]
    credential = _seed_stripe_credential(mcp_client, project)
    run = BillingOccurrence(mcp_client, project, credential, settlement=True)
    invoice_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project,
        credential_ref=credential,
        provider_object_id="in_settlement_fixture",
    )
    customer_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project,
        credential_ref=credential,
        object_type="stripe.customer",
        provider_object_id="cus_settlement_fixture",
    )
    payment_reference = " Synthetic bank ref É-42 "
    reference_hash = hashlib.sha256(payment_reference.encode("utf-8")).hexdigest()
    external = tmp_path / "finance"
    index = initialize(external)
    document = read_document(index)
    source = add_settlement_fixture(
        document,
        amount=amount,
        invoice_ref=invoice_ref,
        customer_ref=customer_ref,
        reference_hash=reference_hash,
    )
    report_key, attach_key = "settlement-v1-report", "settlement-v1-attach"
    for operation, key in (("report", report_key), ("attach", attach_key)):
        document["mutation_attempts"].append(
            finance_record(
                f"attempt:{operation}",
                "prepared",
                action_key=f"fixture:{operation}",
                idempotency_key=key,
                correlation_key=f"fixture-{operation}",
                target_ref=source["record_id"],
                outcome="not-attempted",
            )
        )
    source["attempt_refs"] = ["attempt:report", "attempt:attach"]

    def persist_source() -> None:
        document["revision"] += 1
        write_document(index, document, digest(index))

    def saved_source() -> dict[str, Any]:
        return read_document(index)["settlements"][0]

    persist_source()
    state = {"reported": False, "attached": False}

    def invoice() -> dict[str, Any]:
        paid = amount if state["attached"] else 0
        return stripe_invoice(
            **{
                "id": "in_settlement_fixture",
                "object": "invoice",
                "customer": "cus_settlement_fixture",
                "currency": "usd",
                "livemode": False,
                "amount_due": 10000,
                "amount_paid": paid,
                "amount_remaining": 10000 - paid,
                "subtotal": 10000,
                "total": 10000,
                "status": "paid" if paid == 10000 else "open",
            }
        )

    def payment_record() -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": "pr_settlement_fixture",
            "object": "payment_record",
            "created": 1788566400,
            "livemode": False,
            "reported_by": "self",
            "customer_details": {"customer": "cus_settlement_fixture"},
            "processor_details": {
                "type": "custom",
                "custom": {"payment_reference": payment_reference},
            },
        }
        for field in (
            "amount",
            "amount_requested",
            "amount_guaranteed",
            "amount_authorized",
            "amount_canceled",
            "amount_failed",
            "amount_refunded",
        ):
            result[field] = {
                "currency": "usd",
                "value": amount
                if field in {"amount", "amount_requested", "amount_guaranteed"}
                else 0,
            }
        return result

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Stripe-Version"] == "2026-08-26.dahlia"
        path, method = request.url.path, request.method
        if path == "/v1/invoices/in_settlement_fixture" and method == "GET":
            return httpx.Response(200, json=invoice())
        if path == "/v1/invoice_payments" and method == "GET":
            assert request.url.params["invoice"] == "in_settlement_fixture"
            rows = []
            if state["attached"]:
                rows = [
                    {
                        "id": "inpay_settlement_fixture",
                        "object": "invoice_payment",
                        "invoice": "in_settlement_fixture",
                        "amount_requested": amount,
                        "amount_paid": amount,
                        "created": 1788566401,
                        "currency": "usd",
                        "is_default": False,
                        "livemode": False,
                        "status": "paid",
                        "status_transitions": {"paid_at": 1788566401, "canceled_at": None},
                        "payment": {
                            "type": "payment_record",
                            "payment_record": "pr_settlement_fixture",
                        },
                    }
                ]
            return httpx.Response(200, json={"object": "list", "data": rows, "has_more": False})
        if path == "/v1/payment_records/report_payment" and method == "POST":
            saved = saved_source()
            assert saved["payment_reference_sha256"] == reference_hash
            assert "payment_ref" not in saved
            assert state["reported"] is False, "A recovered source must never be reported twice"
            form = parse_qs(request.content.decode())
            assert form["processor_details[custom][payment_reference]"] == [payment_reference]
            assert form["amount_requested[value]"] == [str(amount)]
            assert form["outcome"] == ["guaranteed"]
            state["reported"] = True
            raise httpx.ReadTimeout("Synthetic response lost after reporting", request=request)
        if path == "/v1/payment_records/pr_settlement_fixture" and method == "GET":
            assert state["reported"] is True
            return httpx.Response(200, json=payment_record())
        if path == "/v1/invoices/in_settlement_fixture/attach_payment" and method == "POST":
            assert saved_source()["payment_ref"].startswith("provider-object:")
            assert state["attached"] is False, "Do not reattach an already linked payment"
            assert parse_qs(request.content.decode()) == {
                "payment_record": ["pr_settlement_fixture"]
            }
            state["attached"] = True
            if attach_timeout:
                raise httpx.ReadTimeout("Synthetic response lost after attachment", request=request)
            return httpx.Response(200, json=invoice())
        raise AssertionError(f"Unexpected settlement HTTP: {method} {path}")

    httpx_mock.add_callback(respond, is_reusable=True)
    run.claim("preflight")
    run.record("scoped")
    run.claim("read-invoice-lifecycle")
    assert (
        run.execute("invoices.retrieve", {"invoice_ref": invoice_ref})["amount_remaining"] == 10000
    )
    assert run.execute("invoice-payments.list", {"invoice_ref": invoice_ref})["items"] == []
    run.record("lifecycle-read")
    run.claim("prepare-settlement")
    run.execute("invoices.retrieve", {"invoice_ref": invoice_ref})
    run.summary.update(
        settlement_state="prepared",
        settlement_route="report-and-attach",
        payment_source_ref="source:fixture-v1",
        payment_allocation_ref="allocation:fixture-v1",
    )
    run.record("settlement-prepared")
    run.claim("review-settlement")
    run.summary.update(
        settlement_state="review-ready",
        settlement_decision_ref="decision:synthetic-v1",
        control_review_ref="review:synthetic-v1",
    )
    run.record("settlement-review-ready")
    run.claim("apply-settlement")
    report_payload = {
        "customer_ref": customer_ref,
        "amount": amount,
        "currency": "usd",
        "initiated_at": 1788566300,
        "guaranteed_at": 1788566400,
        "payment_reference": run.secret(payment_reference),
    }
    denied = mcp_client.call_tool_error(
        "action.execute", run.args("payment-records.report", report_payload)
    )
    assert denied["data"]["approval_ref"] == "owner-payment-record"
    assert state["reported"] is False
    run.approve("owner-payment-record")
    error = mcp_client.call_tool_error(
        "action.execute",
        {
            **run.args("payment-records.report", report_payload),
            "idempotency_key": report_key,
        },
    )
    assert "unknown" in json.dumps(error).lower() or "reconcile" in json.dumps(error).lower()
    assert payment_reference not in json.dumps(error)
    failed_report_id = error["data"]["action_call_id"]
    document["mutation_attempts"][0].update(
        action_call_ref=f"action-call:{failed_report_id}",
        outcome="unknown",
    )
    source["recovery"] = {
        "state": "unknown",
        "original_attempt_ref": "attempt:report",
        "action_refs": [f"action-call:{failed_report_id}"],
    }
    persist_source()
    run.summary.update(settlement_state="reconcile-required", recovery_state="reconcile-required")
    run.record("reconcile-required", blocked=True)
    run.claim("apply-settlement")
    # Inspect the retained failed action before any provider lookup. The audit
    # proves uncertainty, not absence or authority to issue a replacement report.
    audit = run.call("actionCall.get", action_call_id=failed_report_id)
    assert audit["status"] == "failed"
    assert audit["action_ref"] == "finance.stripe.payment-records.report"
    assert audit["outcome_unknown"] is True
    assert payment_reference not in json.dumps(audit)
    if missing_recovery_ref:
        requests_before_list = len(httpx_mock.get_requests())
        failure = mcp_client.call_tool_error("action.execute", run.args("payment-records.list", {}))
        issue = next(
            item for item in failure["data"]["issues"] if item["code"] == "execution_deferred"
        )
        assert "temporarily unavailable in StackOS" in issue["message"]
        assert "never create a replacement report" in issue["message"]
        assert len(httpx_mock.get_requests()) == requests_before_list
        assert "action_call_id" not in failure["data"]
        audit_response = mcp_client.test_client.get(
            f"/api/v1/projects/{project}/action-calls",
            params={"action_key": "stripe.payment-records.list"},
            headers=mcp_client._headers(),
        )
        assert audit_response.status_code == 200
        calls = audit_response.json()["items"]
        assert calls == []
        source["recovery"].update(
            state="held", reason="known-ref or owner/provider endpoint resolution required"
        )
        persist_source()
        run.summary.update(exception_refs=["exception:payment-record-list-unavailable"])
        run.record("reconcile-required", blocked=True)
        step = run.call("runPlan.getStep", run_plan_id=run.plan, step_id="apply-settlement")
        assert step["status"] == "blocked"
        assert run.summary["resend_state"] == "not-requested"
        assert "payment_ref" not in saved_source()
        assert state == {"reported": True, "attached": False}
        posts = [request for request in httpx_mock.get_requests() if request.method == "POST"]
        assert [request.url.path for request in posts] == ["/v1/payment_records/report_payment"]
        assert posts[0].headers["Idempotency-Key"] == report_key
        assert payment_reference not in json.dumps(failure)
        return
    # Synthetic retained evidence supplies the known ref, not the failed response
    # or an unavailable listing. The missing-ref case above must remain on hold.
    record_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project,
        credential_ref=credential,
        object_type="stripe.payment-record",
        provider_object_id="pr_settlement_fixture",
    )
    observed = run.execute("payment-records.retrieve", {"payment_record_ref": record_ref})
    assert observed["customer_ref"] == customer_ref
    assert observed["amount_guaranteed"] == {"currency": "usd", "value": amount}
    assert observed["livemode"] is False and observed["payment_reference_sha256"] == reference_hash
    source["payment_ref"] = record_ref
    source["recovery"].update(verified_payment_ref=record_ref, verified_at=AT)
    persist_source()
    assert saved_source()["payment_ref"] == record_ref
    payload = {"invoice_ref": invoice_ref, "payment_record_ref": record_ref}
    denied = mcp_client.call_tool_error(
        "action.execute", run.args("invoices.attach-payment", payload)
    )
    assert denied["data"]["approval_ref"] == "owner-payment-attachment"
    assert state["attached"] is False
    run.approve("owner-payment-attachment")
    if attach_timeout:
        error = mcp_client.call_tool_error(
            "action.execute",
            {
                **run.args("invoices.attach-payment", payload),
                "idempotency_key": attach_key,
            },
        )
        assert "unknown" in json.dumps(error).lower() or "reconcile" in json.dumps(error).lower()
        run.record("reconcile-required", blocked=True)
        run.claim("apply-settlement")
    else:
        run.execute("invoices.attach-payment", payload, key=attach_key)
    current = run.execute("invoices.retrieve", {"invoice_ref": invoice_ref})
    linked = run.execute("invoice-payments.list", {"invoice_ref": invoice_ref})
    assert current["amount_remaining"] == 10000 - amount
    assert linked["has_more"] is False and len(linked["items"]) == 1
    allocation = linked["items"][0]
    assert allocation["payment_record_ref"] == record_ref
    assert allocation["invoice_ref"] == invoice_ref
    assert allocation["amount_paid"] == amount and allocation["status"] == "paid"
    source["recovery"].update(state="resolved", evidence_refs=[allocation["invoice_payment_ref"]])
    document["provider_observations"].append(
        finance_record(
            "observation:settled-invoice",
            "observed",
            provider_ref="fixture:provider",
            account_ref="fixture:account",
            object_type="invoice",
            object_ref=invoice_ref,
            observed_at=AT,
            observed_status=current["status"],
            amount_remaining=money(current["amount_remaining"]),
            amount_paid=money(amount),
            linked_refs=[allocation["invoice_payment_ref"], record_ref],
            source_refs=[source["record_id"]],
            livemode=False,
        )
    )
    persist_source()
    assert read_document(index)["provider_observations"][0]["amount_remaining"] == money(
        10000 - amount
    )
    assert len(read_document(index)["settlements"]) == 1
    assert list(external.glob("*.json")) == [index]
    run.summary.update(
        settlement_state="partial-applied" if amount < 10000 else "payment-attached",
        recovery_state="reconciled",
        payment_ref=record_ref,
        approval_refs=["approval:report-v1", "approval:attach-v1"],
        external_write_proof_ref="write-proof:settlement-v1",
        handoff_refs=["handoff:bookkeeping-v1"],
    )
    run.record("settlement-recorded")
    for step in ("suppress-ineligible", "review-eligible", "resend-approved", "record-outcome"):
        run.claim(step)
        run.record("recorded" if step == "record-outcome" else "settlement-recorded")
    final = run.call("runPlan.get", run_plan_id=run.plan)
    assert final["status"] == "completed"
    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert [r.url.path for r in posts] == [
        "/v1/payment_records/report_payment",
        "/v1/invoices/in_settlement_fixture/attach_payment",
    ]
    assert payment_reference not in json.dumps(final)
    assert run.summary["resend_state"] == "not-requested"
