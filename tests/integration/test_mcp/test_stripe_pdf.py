"""Public action and grant audit proof for private Stripe PDF transfer."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.helpers.stripe import stripe_invoice
from tests.integration.test_mcp.test_mcp_stripe_actions import (
    STRIPE_SECRET,
    _seed_stripe_credential,
    _seed_stripe_object_ref,
)

PDF = b"%PDF-1.7\nprivate invoice fixture\n%%EOF"
PDF_URL = "https://pay.stripe.com/invoice/acct_fixture/opaque-fixture/pdf"
PDF_REDIRECT_URL = "https://stripe-upload-api.s3.us-west-1.amazonaws.com/private-invoice.pdf?signature=private-token"
ACTION = "finance.stripe.invoices.pdf.download"


@pytest.mark.parametrize("content_type", ["application/pdf", "application/octet-stream"])
def test_mcp_pdf_download_is_file_backed_audited_and_workflow_granted(
    mcp_client, seeded_project, httpx_mock, content_type
) -> None:
    project_id = seeded_project["data"]["id"]
    credential = _seed_stripe_credential(mcp_client, project_id)
    invoice_ref = _seed_stripe_object_ref(
        mcp_client, project_id=project_id, credential_ref=credential
    )
    plan = {
        "schema_version": "stackos.run-plan.v1",
        "key": "pdf-proof.run",
        "title": "PDF proof",
        "steps": [{"id": "read", "title": "Read", "action_refs": [ACTION]}],
        "grants": {
            "mcp_tool_grants": [
                {"step_id": "read", "tool": "action.execute", "action_refs": [ACTION]}
            ]
        },
    }
    created = mcp_client.call_tool_structured(
        "runPlan.create", {"project_id": project_id, "run_plan_json": plan}
    )
    plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": plan_id}
    )
    token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep", {"run_plan_id": plan_id, "step_id": "read", "run_token": token}
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoices/in_mcp_fixture",
        json=stripe_invoice(
            id="in_mcp_fixture",
            status="open",
            invoice_pdf=PDF_URL,
            status_transitions={"finalized_at": 1790118000},
        ),
    )
    httpx_mock.add_response(
        method="GET", url=PDF_URL, status_code=302, headers={"Location": PDF_REDIRECT_URL}
    )
    httpx_mock.add_response(
        method="GET", url=PDF_REDIRECT_URL, content=PDF, headers={"Content-Type": content_type}
    )
    result = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": ACTION,
            "credential_ref": credential,
            "input_json": {"invoice_ref": invoice_ref},
            "run_token": token,
        },
    )["data"]
    assert result["status"] == "success"
    saved = json.loads(Path(result["output"]["path"]).read_text())
    data = saved["response"]["output_json"]["data"]
    assert Path(data["local_path"]).read_bytes() == PDF
    assert data["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert PDF_URL not in json.dumps(saved)
    assert PDF_REDIRECT_URL not in json.dumps(saved)
    assert STRIPE_SECRET not in json.dumps(saved)
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"action_key": "stripe.invoices.pdf.download"},
        headers=mcp_client._headers(),
    ).json()["items"][0]
    assert audit["run_plan_id"] == plan_id
    assert audit["run_plan_step_id"] is not None
    assert audit["status"] == "success"
    # The private bytes cannot be fetched through the otherwise-public generated-assets route.
    relative = Path(data["local_path"]).parts
    private_suffix = "/".join(relative[relative.index("stripe-invoice-transfers") :])
    assert mcp_client.test_client.get(f"/generated-assets/{private_suffix}").status_code == 404


def test_mcp_pdf_draft_fails_with_structured_audit_without_pdf_http(
    mcp_client, seeded_project, httpx_mock
) -> None:
    project_id = seeded_project["data"]["id"]
    credential = _seed_stripe_credential(mcp_client, project_id)
    invoice_ref = _seed_stripe_object_ref(
        mcp_client, project_id=project_id, credential_ref=credential
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoices/in_mcp_fixture",
        json=stripe_invoice(id="in_mcp_fixture", status="draft", invoice_pdf=None),
    )
    result = mcp_client.call_tool(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": ACTION,
            "credential_ref": credential,
            "input_json": {"invoice_ref": invoice_ref},
        },
    )
    assert result["result"]["isError"] is True
    assert "invoice_not_finalized" in json.dumps(result)
    assert len(httpx_mock.get_requests()) == 1
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"action_key": "stripe.invoices.pdf.download"},
        headers=mcp_client._headers(),
    ).json()["items"][0]
    assert audit["status"] == "failed"
    assert "invoice_not_finalized" in json.dumps(audit["response_json"])


@pytest.mark.parametrize("phase", ["initial_url", "redirect_url"])
@pytest.mark.parametrize("response_mode", ["compact", "raw"])
def test_mcp_pdf_url_failure_preserves_safe_phase_host_in_response_and_audit(
    mcp_client, seeded_project, httpx_mock, phase, response_mode
) -> None:
    project_id = seeded_project["data"]["id"]
    credential = _seed_stripe_credential(mcp_client, project_id)
    invoice_ref = _seed_stripe_object_ref(
        mcp_client, project_id=project_id, credential_ref=credential
    )
    rejected = "ftp://cdn.example.test/private-invoice-path?signature=private-query-token"
    httpx_mock.add_response(
        method="GET",
        url="https://api.stripe.com/v1/invoices/in_mcp_fixture",
        headers={"Request-Id": "req_pdf_diagnostic"},
        json=stripe_invoice(
            id="in_mcp_fixture",
            status="open",
            invoice_pdf=rejected if phase == "initial_url" else PDF_URL,
            status_transitions={"finalized_at": 1790118000},
        ),
    )
    if phase == "redirect_url":
        httpx_mock.add_response(
            method="GET", url=PDF_URL, status_code=302, headers={"Location": rejected}
        )
    failure = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": ACTION,
            "credential_ref": credential,
            "input_json": {"invoice_ref": invoice_ref},
            "response_mode": response_mode,
        },
    )
    detail = failure["data"]["provider_error"]
    assert detail["reason_code"] == "unsupported_pdf_url"
    assert detail["pdf_phase"] == phase
    assert detail["pdf_hostname"] == "cdn.example.test"
    assert detail["pdf_redirect_hop"] == (1 if phase == "redirect_url" else 0)
    requests = httpx_mock.get_requests()
    assert len(requests) == (2 if phase == "redirect_url" else 1)
    if phase == "redirect_url":
        assert "Authorization" not in requests[-1].headers
        assert "Cookie" not in requests[-1].headers
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"action_key": "stripe.invoices.pdf.download"},
        headers=mcp_client._headers(),
    ).json()["items"][0]
    assert audit["status"] == "failed"
    for field in ("pdf_phase", "pdf_hostname", "pdf_redirect_hop"):
        assert audit["response_json"]["provider_error"][field] == detail[field]
    serialized = json.dumps([failure, audit])
    for private in (
        STRIPE_SECRET,
        PDF_URL,
        rejected,
        "private-invoice-path",
        "private-query-token",
    ):
        assert private not in serialized
