"""Google Ads paging survives the real MCP/file/audit and granted action paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialScope
from tests.integration.account_test_support import seed_test_account

from .conftest import MCPClient


@pytest.mark.parametrize("granted", [False, True])
def test_google_ads_two_pages_through_mcp_files_and_audit(
    mcp_client: MCPClient, seeded_project: dict, httpx_mock: HTTPXMock, granted: bool
) -> None:
    project_id = seeded_project["data"]["id"]
    action_ref = "media-buying.google.report.search"
    engine = mcp_client.test_client.app.state.engine
    with Session(engine) as session:
        seed_test_account(
            session,
            project_id=project_id,
            provider_key="google-ads",
            secret_payload=json.dumps(
                {"access_token": "google-access-canary", "developer_token": "google-dev-canary"}
            ).encode(),
            config_json={"api_version": "v22", "customers": {"main": "444-555-6666"}},
        )
        credential_ref = (
            AuthRepository(session)
            .status(project_id=project_id, provider_key="google-ads")
            .accounts[0]
            .credential_ref
        )
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        credential.config_json = {**(credential.config_json or {}), "scope_status": "known"}
        session.add(credential)
        session.add(
            CredentialScope(
                credential_id=credential.id, scope="https://www.googleapis.com/auth/adwords"
            )
        )
        session.commit()

    described = mcp_client.call_tool_structured(
        "action.describe",
        {"project_id": project_id, "action_ref": action_ref, "response_mode": "raw"},
    )
    properties = described["manifest"]["input_schema_json"]["properties"]
    assert properties["page_cursor"]["type"] == "string"
    assert "page_token" not in properties
    args = {"project_id": project_id, "action_ref": action_ref, "credential_ref": credential_ref}
    tool = "action.run"
    if granted:
        created = mcp_client.call_tool_structured(
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "google-ads.paging-proof",
                    "title": "Google Ads paging proof",
                    "grants": {
                        "mcp_tool_grants": [
                            {
                                "step_id": "read",
                                "tool": "action.execute",
                                "action_refs": [action_ref],
                            }
                        ]
                    },
                    "steps": [{"id": "read", "title": "Read report", "action_refs": [action_ref]}],
                },
            },
        )
        run_plan_id = created["data"]["id"]
        started = mcp_client.call_tool_structured(
            "runPlan.start", {"project_id": project_id, "run_plan_id": run_plan_id}
        )
        run_token = started["data"]["run_token"]
        mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {"run_plan_id": run_plan_id, "step_id": "read", "run_token": run_token},
        )
        args["run_token"] = run_token
        tool = "action.execute"
        denied = mcp_client.call_tool_error(
            tool, {**args, "action_ref": "media-buying.google.customer.list", "input_json": {}}
        )
        assert denied["code"] == -32007
        assert httpx_mock.get_requests() == []

    query = "SELECT campaign.id FROM campaign"
    payload = {"customer_ref": "main", "query": query}
    for page_number in (1, 2):
        provider_body = {
            "results": [{"campaign": {"id": str(page_number)}}],
            "fieldMask": "campaign.id",
            "totalResultsCount": "2",
            "access_token": "google-access-canary",
            "developer_token": "google-dev-canary",
        }
        if page_number == 1:
            provider_body["nextPageToken"] = "google-ads-cursor-2"
        httpx_mock.add_response(
            method="POST",
            url="https://googleads.googleapis.com/v22/customers/4445556666/googleAds:search",
            json=provider_body,
            headers={"request-id": f"ads-request-{page_number}"},
        )
        result = mcp_client.call_tool_structured(tool, {**args, "input_json": payload})["data"]
        assert result["status"] == "success"
        assert result["output"]["schema_ref"] == "stackos.action-output.v1"
        saved = json.loads(Path(result["output"]["path"]).read_text())
        body = saved["response"]["output_json"]["body"]
        assert body["results"] == [{"campaign": {"id": str(page_number)}}]
        assert body["fieldMask"] == "campaign.id"
        assert body["totalResultsCount"] == "2"
        assert "nextPageToken" not in body
        assert saved["response"]["metadata_json"]["request_id"] == f"ads-request-{page_number}"
        audit_result = mcp_client.test_client.get(
            f"/api/v1/projects/{project_id}/action-calls",
            params={"action_key": "google.report.search", "status": "success"},
            headers=mcp_client._headers(),
        )
        assert audit_result.status_code == 200
        audit = audit_result.json()["items"][0]
        assert audit["response_json"]["file"]["path"] == result["output"]["path"]
        serialized = json.dumps({"result": result, "file": saved, "audit": audit})
        for secret in ("google-access-canary", "google-dev-canary"):
            assert secret not in serialized
        request = httpx_mock.get_requests()[-1]
        expected = {"query": query}
        if page_number == 2:
            expected["pageToken"] = "google-ads-cursor-2"
            assert saved["request"]["input_json"]["page_cursor"] == "google-ads-cursor-2"
            assert "next_page_cursor" not in body
        else:
            assert body["next_page_cursor"] == "google-ads-cursor-2"
            payload = {**payload, "page_cursor": body["next_page_cursor"]}
        assert json.loads(request.content) == expected
        assert request.headers["authorization"] == "Bearer google-access-canary"
        assert request.headers["developer-token"] == "google-dev-canary"
    assert len(httpx_mock.get_requests()) == 2
