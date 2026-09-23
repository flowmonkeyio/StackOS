"""Agent-driven identifier and memo proof through actual finance MCP grants."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import parse_qs

import httpx
from pytest_httpx import HTTPXMock

from tests.helpers.stripe import stripe_customer, stripe_invoice

from .conftest import MCPClient
from .test_mcp_finance_billing_workflow import BillingOccurrence
from .test_mcp_stripe_actions import STRIPE_SECRET, _seed_stripe_credential, _seed_stripe_object_ref


def test_finance_mcp_identifiers_duplicate_preflight_preserves_fields_and_reads_memo_back(
    mcp_client: MCPClient, seeded_project: dict[str, Any], httpx_mock: HTTPXMock
) -> None:
    """The test agent owns decisions; each connector action remains one request."""
    project = seeded_project["data"]["id"]
    credential = _seed_stripe_credential(mcp_client, project)
    customer_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project,
        credential_ref=credential,
        object_type="stripe.customer",
        provider_object_id="cus_identifier_mcp",
    )
    invoice_ref = _seed_stripe_object_ref(
        mcp_client,
        project_id=project,
        credential_ref=credential,
        provider_object_id="in_identifier_mcp",
    )
    retained = {"name": "Project", "value": "SYNTHETIC-PROJECT"}
    registration = {"name": "Registration", "value": "REG-SYNTHETIC-MCP"}
    tax_value = "DE123456789"
    state: dict[str, Any] = {"fields": [retained], "memo": "Old synthetic memo", "requests": []}

    def tax(identifier: str, value: str) -> dict[str, Any]:
        return {
            "object": "tax_id",
            "id": identifier,
            "customer": "cus_identifier_mcp",
            "type": "eu_vat",
            "value": value,
            "country": "DE",
            "created": 1790140800,
            "livemode": False,
            "verification": {"status": "pending", "verified_name": None, "verified_address": None},
        }

    def respond(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        state["requests"].append((method, path))
        if method == "GET":
            assert "Idempotency-Key" not in request.headers
        if path == "/v1/customers/cus_identifier_mcp/tax_ids" and method == "GET":
            assert "include_business_details" not in request.url.params
            if "starting_after" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "has_more": True,
                        "data": [tax("txi_other_mcp", "DE987654321")],
                    },
                )
            assert request.url.params["starting_after"] == "txi_other_mcp"
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "has_more": False,
                    "data": [tax("txi_exact_mcp", tax_value)],
                },
            )
        if path == "/v1/customers/cus_identifier_mcp/tax_ids/txi_exact_mcp" and method == "GET":
            return httpx.Response(200, json=tax("txi_exact_mcp", tax_value))
        if path == "/v1/customers/cus_identifier_mcp":
            if method == "POST":
                form = parse_qs(request.content.decode(), keep_blank_values=True)
                assert form == {
                    f"invoice_settings[custom_fields][{i}][{key}]": [entry[key]]
                    for i, entry in enumerate([retained, registration])
                    for key in ("name", "value")
                }
                state["fields"] = [retained, registration]
            return httpx.Response(
                200,
                json=stripe_customer(
                    id="cus_identifier_mcp", invoice_settings={"custom_fields": state["fields"]}
                ),
            )
        if path == "/v1/invoices/in_identifier_mcp":
            if method == "POST":
                form = parse_qs(request.content.decode(), keep_blank_values=True)
                assert set(form) == {"description"}
                state["memo"] = form["description"][0]
            return httpx.Response(
                200,
                json=stripe_invoice(
                    id="in_identifier_mcp",
                    customer="cus_identifier_mcp",
                    description=state["memo"],
                    custom_fields=[retained],
                    customer_tax_ids=[],
                ),
            )
        raise AssertionError(f"Unrequested provider mutation: {method} {path}")

    httpx_mock.add_callback(respond, is_reusable=True)
    run = BillingOccurrence(mcp_client, project, credential)
    run.claim("preflight")
    run.record("scoped")
    run.claim("resolve-customer")
    plan = run.call("runPlan.get", run_plan_id=run.plan)
    grant = next(
        row
        for row in plan["grant_snapshot_json"]["mcp_tool_grants"]
        if row["step_id"] == "resolve-customer" and row["tool"] == "action.execute"
    )
    assert {
        f"finance.stripe.customers.tax-ids.{verb}" for verb in ("list", "create", "retrieve")
    } <= set(grant["action_refs"])
    current = run.execute(
        "customers.retrieve",
        {"customer_ref": customer_ref, "include_business_details": True},
    )
    # Caller preserves the complete existing list; no hidden connector merge.
    approved_fields = [
        *current["business_details"]["invoice_settings"]["custom_fields"],
        registration,
    ]
    pages, cursor = [], None
    while True:
        page = run.execute(
            "customers.tax-ids.list",
            {
                "customer_ref": customer_ref,
                "limit": 1,
                **({"page_cursor": cursor} if cursor else {}),
            },
        )
        pages.extend(page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_page_cursor"]
    matches = [
        entry
        for entry in pages
        if entry["type"] == "eu_vat"
        and entry["value_sha256"] == hashlib.sha256(tax_value.encode()).hexdigest()
    ]
    assert len(matches) == 1 and len(pages) == 2
    observed_tax = run.execute(
        "customers.tax-ids.retrieve",
        {"customer_ref": customer_ref, "tax_id_ref": matches[0]["tax_id_ref"]},
    )
    assert observed_tax["verification"]["status"] == "pending"
    run.execute(
        "customers.update",
        {
            "customer_ref": customer_ref,
            "invoice_settings": {
                "custom_fields": [
                    {key: run.secret(value) for key, value in entry.items()}
                    for entry in approved_fields
                ]
            },
        },
        key="mcp-custom-fields-preserve",
    )
    observed_customer = run.execute("customers.retrieve", {"customer_ref": customer_ref})
    assert observed_customer["invoice_settings"]["custom_fields"] == [
        {
            f"{key}_sha256": hashlib.sha256(value.encode()).hexdigest()
            for key, value in entry.items()
        }
        for entry in approved_fields
    ]
    run.summary["customer_ref"] = customer_ref
    run.record("customer-resolved")
    run.claim("create-draft")
    # Existing discovered draft is recovered by safe ref; no replacement invoice.
    draft = run.execute("invoices.retrieve", {"invoice_ref": invoice_ref})
    assert (
        len(draft["custom_fields"]) == 1
        and len(observed_customer["invoice_settings"]["custom_fields"]) == 2
    )
    for i, memo in enumerate(("New synthetic memo", "")):
        run.execute(
            "invoices.update",
            {"invoice_ref": invoice_ref, "description": run.secret(memo) if memo else ""},
            key=f"mcp-memo-{i}",
        )
        # An ordinary workflow read must observe the provider again after the
        # mutation; only an explicit caller key requests replay of a read.
        observed = run.execute(
            "invoices.retrieve",
            {"invoice_ref": invoice_ref},
        )
        assert observed["description_sha256"] == hashlib.sha256(memo.encode()).hexdigest()
        assert observed["status"] == "draft"
    assert state["requests"].count(("GET", "/v1/customers/cus_identifier_mcp/tax_ids")) == 2
    assert state["requests"].count(("GET", "/v1/invoices/in_identifier_mcp")) == 3
    assert ("POST", "/v1/customers/cus_identifier_mcp/tax_ids") not in state["requests"]
    assert all(
        not any(path.endswith(suffix) for suffix in ("/send", "/finalize", "/void"))
        for _, path in state["requests"]
    )
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        params={"action_key": "stripe.invoices.update"},
        headers=mcp_client._headers(),
    ).json()["items"]
    assert len(audit) == 2 and all(row["run_plan_id"] == run.plan for row in audit)
    assert {json.dumps(row["request_json"]["description"]) for row in audit} >= {json.dumps("")}
    for row in audit:
        value = row["request_json"]["description"]
        assert value == "" or set(value) == {"$secret_ref"}
    for private in (STRIPE_SECRET, "New synthetic memo", tax_value, registration["value"]):
        assert private not in json.dumps(audit)
