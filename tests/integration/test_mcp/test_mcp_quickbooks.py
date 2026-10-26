"""QuickBooks host registration, Accounts, grants, files and audit with mocked HTTP."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from .test_mcp_google_service_accounts import _action_tool

TOKEN = "synthetic-qbo-access"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
BASE = "https://sandbox-quickbooks.api.intuit.com/v3/company/1234"
COMPANY = "finance.quickbooks-online.company-info.get"
INVOICES = "finance.quickbooks-online.invoices.list"
PAGE = {
    "txn_date_from": "2026-01-01",
    "txn_date_to": "2026-01-31",
    "start_position": 1,
    "max_results": 10,
}
RAW = (
    '{"Id":"invoice-1", "SyncToken":"2", "TxnDate":"2026-01-15", '
    '"TotalAmt":9007199254740993.123456789, "Balance":0.00}'
)


def account(mcp, httpx_mock, method, project=None):
    created = mcp.test_client.post(
        "/api/v1/auth/accounts/quickbooks-online",
        headers=mcp._headers(),
        json={
            "auth_method_key": method,
            "display_name": "Synthetic QuickBooks",
            "attach_project_id": project,
            "fields": {
                "realm_id": "1234",
                "environment": "sandbox",
                **(
                    {"access_token": TOKEN}
                    if method == "oauth2_token"
                    else {
                        "client_id": "synthetic-client",
                        "client_secret": "synthetic-client-secret",
                    }
                ),
            },
        },
    )
    assert created.status_code == 201, created.text
    ref = created.json()["data"]["credential_ref"]
    if method != "oauth2_token":
        started = mcp.test_client.post(
            "/api/v1/auth/accounts/quickbooks-online/start",
            headers=mcp._headers(),
            json={"credential_ref": ref, "auth_method_key": method},
        )
        assert started.status_code == 200, started.text
        state = parse_qs(urlparse(started.json()["data"]["authorization_url"]).query)["state"][0]
        httpx_mock.add_response(
            method="POST",
            url=TOKEN_URL,
            json={
                "access_token": TOKEN,
                "refresh_token": "synthetic-refresh",
                "expires_in": 3600,
            },
        )
        completed = mcp.test_client.get(
            "/api/v1/auth/oauth/callback",
            headers=mcp._headers(),
            follow_redirects=False,
            params={"state": state, "code": "synthetic-code", "realmId": "1234"},
        )
        assert completed.status_code == 303
        assert "oauth_status=connected" in completed.headers["location"]
    return ref


def mock_read(httpx_mock, action, *, status=200):
    if action == COMPANY:
        url = f"{BASE}/companyinfo/1234"
        content = '{"CompanyInfo":{"Id":"company-entity-7","CompanyName":"Synthetic Company"}}'
    else:
        query = (
            "select * from Invoice where TxnDate >= '2026-01-01' "
            "and TxnDate <= '2026-01-31' STARTPOSITION 1 MAXRESULTS 10"
        )
        url = httpx.URL(f"{BASE}/query", params={"query": query})
        content = '{"QueryResponse":{"Invoice":[' + RAW + '],"startPosition":1,"maxResults":1}}'
    if status != 200:
        content = '{"Fault":{"Error":[{"Message":"' + TOKEN + ' private-provider-body"}]}}'
    httpx_mock.add_response(method="GET", url=url, content=content, status_code=status)


def audit(mcp, project, action, status):
    response = mcp.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp._headers(),
        params={"action_key": action.removeprefix("finance."), "status": status},
    )
    assert response.status_code == 200
    return response.json()["items"][0]


@pytest.mark.parametrize("method", ["oauth2_token", "oauth2_authorization_code"])
@pytest.mark.parametrize("action", [COMPANY, INVOICES])
@pytest.mark.parametrize("granted", [False, True])
def test_read_actions_preserve_exact_source_files_audit_and_grants(
    mcp_client, seeded_project, httpx_mock, method, action, granted
):
    project = seeded_project["data"]["id"]
    ref = account(mcp_client, httpx_mock, method, project)
    described = mcp_client.call_tool_structured(
        "action.describe", {"action_ref": action, "response_mode": "raw"}
    )
    assert described["manifest"]["risk_level"] == "read"
    tool, args = _action_tool(mcp_client, project, ref, granted, action)
    if granted:
        error = mcp_client.call_tool_error(
            tool,
            {**args, "action_ref": INVOICES if action == COMPANY else COMPANY, "input_json": {}},
        )
        assert error["code"] == -32007
    mock_read(httpx_mock, action)
    result = mcp_client.call_tool_structured(
        tool, {**args, "input_json": PAGE if action == INVOICES else {}}
    )["data"]
    assert result["status"] == "success"
    saved = json.loads(Path(result["output"]["path"]).read_text())
    output = saved["response"]["output_json"]
    if action == INVOICES:
        assert output["invoices"][0]["raw_json"] == RAW
        assert output["count"] == 1
        assert "currency" not in output["invoices"][0]
    else:
        assert output == {
            "realm_id": "1234",
            "company_id": "company-entity-7",
            "company_name": "Synthetic Company",
        }
    recorded = audit(mcp_client, project, action, "success")
    assert recorded["response_json"]["file"]["path"] == result["output"]["path"]
    if granted:
        assert recorded["run_plan_id"] and recorded["run_plan_step_id"]
    assert httpx_mock.get_requests()[-1].headers["authorization"] == f"Bearer {TOKEN}"
    rendered = json.dumps({"result": result, "file": saved, "audit": recorded})
    assert all(
        secret not in rendered
        for secret in (TOKEN, "synthetic-client-secret", "synthetic-refresh", "synthetic-code")
    )


@pytest.mark.parametrize("method", ["oauth2_token", "oauth2_authorization_code"])
def test_account_probe_requires_attachment_and_never_infers_grants(
    mcp_client, seeded_project, httpx_mock, method
):
    project = seeded_project["data"]["id"]
    ref = account(mcp_client, httpx_mock, method)
    before = len(httpx_mock.get_requests())
    for tool, args in (
        ("account.test", {}),
        ("action.run", {"action_ref": COMPANY, "input_json": {}}),
    ):
        error = mcp_client.call_tool_error(
            tool, {"project_id": project, "credential_ref": ref, **args}
        )
        assert "not attached" in json.dumps(error)
    assert len(httpx_mock.get_requests()) == before
    attached = mcp_client.test_client.post(
        f"/api/v1/projects/{project}/connections/accounts/{ref}", headers=mcp_client._headers()
    )
    assert attached.status_code == 200, attached.text
    mock_read(httpx_mock, COMPANY)
    result = mcp_client.call_tool_structured(
        "account.test", {"project_id": project, "credential_ref": ref, "response_mode": "raw"}
    )["data"]
    assert result["ok"] is True
    connection = mcp_client.call_tool_structured(
        "connection.list",
        {"project_id": project, "provider_key": "quickbooks-online", "response_mode": "raw"},
    )["accounts"][0]
    assert connection["scopes"] == []
    assert TOKEN not in json.dumps(result)
    assert len(httpx_mock.get_requests()) == before + 1


@pytest.mark.parametrize("granted", [False, True])
def test_provider_failure_has_safe_action_audit(mcp_client, seeded_project, httpx_mock, granted):
    project = seeded_project["data"]["id"]
    ref = account(mcp_client, httpx_mock, "oauth2_token", project)
    tool, args = _action_tool(mcp_client, project, ref, granted, COMPANY)
    mock_read(httpx_mock, COMPANY, status=403)
    error = mcp_client.call_tool_error(tool, {**args, "input_json": {}})
    assert error["data"]["provider_status_code"] == 403
    assert error["data"]["provider_error"] == {"reason_code": "permission_denied"}
    recorded = audit(mcp_client, project, COMPANY, "failed")
    assert recorded["response_json"]["provider_status_code"] == 403
    assert all(
        value not in json.dumps({"error": error, "audit": recorded})
        for value in (TOKEN, "private-provider-body")
    )
    assert len(httpx_mock.get_requests()) == 1
