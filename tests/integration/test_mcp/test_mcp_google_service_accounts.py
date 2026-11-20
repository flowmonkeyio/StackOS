"""Synthetic local-admin setup through native MCP Google page-performance reads."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.db.models import Credential

from ..test_integrations.test_google_search_console_batch import BATCH_URL, multipart
from .conftest import MCPClient

ACTION = "seo.search-console.search-analytics.query"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
QUERY_URL = (
    "https://www.googleapis.com/webmasters/v3/sites/sc-domain%3Aexample.com/searchAnalytics/query"
)
PAGE = "https://example.com/guides/welcome-sequence/"
ISSUER = "page-reader@synthetic-project.iam.gserviceaccount.com"


@pytest.fixture
def synthetic_key() -> tuple[str, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    document = {
        "type": "service_account",
        "client_email": ISSUER,
        "private_key_id": "synthetic-key-1",
        "private_key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "token_uri": TOKEN_URL,
        "universe_domain": "googleapis.com",
    }
    return json.dumps(document, indent=2), key.public_key()


def _create_account(mcp: MCPClient, document: str, project_id: int | None = None, **fields) -> str:
    response = mcp.test_client.post(
        "/api/v1/auth/accounts/google-search-console",
        headers=mcp._headers(),
        json={
            "auth_method_key": "service-account",
            "display_name": "Synthetic page reader",
            "attach_project_id": project_id,
            "fields": {"service_account_json": document, **fields},
        },
    )
    assert response.status_code == 201
    rendered = json.dumps(response.json())
    assert "BEGIN PRIVATE KEY" not in rendered
    assert document not in rendered
    return response.json()["data"]["credential_ref"]


def _token_exchange(
    httpx_mock: HTTPXMock, public_key: rsa.RSAPublicKey, scope: str = SCOPE
) -> list[str]:
    tokens: list[str] = []

    def decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def exchange(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert set(form) == {"grant_type", "assertion"}
        assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        header, claims, signature = form["assertion"][0].split(".")
        public_key.verify(
            decode(signature),
            f"{header}.{claims}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        assert json.loads(decode(header)) == {
            "alg": "RS256",
            "typ": "JWT",
            "kid": "synthetic-key-1",
        }
        body = json.loads(decode(claims))
        assert set(body) == {"iss", "aud", "scope", "iat", "exp"}
        assert body["iss"] == ISSUER
        assert body["aud"] == TOKEN_URL
        assert body["scope"] == scope
        assert abs(body["iat"] - time.time()) < 60
        assert body["exp"] - body["iat"] == 3600
        tokens.append(f"synthetic-gsc-access-{len(tokens) + 1}")
        return httpx.Response(
            200,
            json={"access_token": tokens[-1], "token_type": "Bearer", "expires_in": 3600},
        )

    httpx_mock.add_callback(exchange, method="POST", url=TOKEN_URL, is_reusable=True)
    return tokens


def _input() -> dict:
    return {
        "site_url": "sc-domain:example.com",
        "start_date": "2026-08-01",
        "end_date": "2026-08-28",
        "type": "web",
        "dimensions": ["query", "page"],
        "dimension_filter_groups": [
            {
                "groupType": "and",
                "filters": [{"dimension": "page", "operator": "equals", "expression": PAGE}],
            }
        ],
    }


def _action_tool(
    mcp: MCPClient, project_id: int, ref: str, granted: bool, action: str = ACTION
) -> tuple[str, dict]:
    args = {"project_id": project_id, "action_ref": action, "credential_ref": ref}
    if not granted:
        return "action.run", args
    created = mcp.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "run_plan_json": {
                "schema_version": "stackos.run-plan.v1",
                "key": "google.service-account-page-proof",
                "title": "Service account exact-page performance proof",
                "grants": {
                    "mcp_tool_grants": [
                        {
                            "step_id": "read",
                            "tool": "action.execute",
                            "action_refs": [action],
                        }
                    ]
                },
                "steps": [
                    {"id": "read", "title": "Provider action proof", "action_refs": [action]}
                ],
            },
        },
    )
    plan_id = created["data"]["id"]
    started = mcp.call_tool_structured(
        "runPlan.start",
        {
            "project_id": project_id,
            "run_plan_id": plan_id,
        },
    )
    token = started["data"]["run_token"]
    mcp.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": plan_id,
            "step_id": "read",
            "run_token": token,
        },
    )
    args["run_token"] = token
    return "action.execute", args


@pytest.mark.parametrize("granted", [False, True])
def test_service_account_exact_page_performance_files_audit_and_renewal(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    synthetic_key: tuple[str, rsa.RSAPublicKey],
    granted: bool,
) -> None:
    project_id = seeded_project["data"]["id"]
    document, public_key = synthetic_key
    ref = _create_account(mcp_client, document)
    denied = mcp_client.call_tool_error(
        "account.test",
        {
            "project_id": project_id,
            "credential_ref": ref,
        },
    )
    assert denied["code"] < 0
    assert "not attached" in json.dumps(denied)
    assert httpx_mock.get_requests() == []
    attached = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/connections/accounts/{ref}",
        headers=mcp_client._headers(),
    )
    assert attached.status_code == 200
    tool, args = _action_tool(mcp_client, project_id, ref, granted)
    if granted:
        denied = mcp_client.call_tool_error(
            tool,
            {
                **args,
                "action_ref": "seo.search-console.sites.list",
                "input_json": {},
            },
        )
        assert denied["code"] == -32007
        assert httpx_mock.get_requests() == []
    tokens = _token_exchange(httpx_mock, public_key)
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/webmasters/v3/sites",
        json={
            "siteEntry": [
                {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteRestrictedUser"}
            ]
        },
    )
    tested = mcp_client.call_tool_structured(
        "account.test",
        {
            "project_id": project_id,
            "credential_ref": ref,
            "response_mode": "raw",
        },
    )
    assert tested["data"]["ok"] is True
    assert len(tokens) == 1
    engine = mcp_client.test_client.app.state.engine
    for phase in (1, 2, 3):
        if phase == 3:
            with Session(engine) as session:
                credential = session.exec(
                    select(Credential).where(Credential.credential_ref == ref)
                ).one()
                credential.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
                    seconds=1
                )
                session.add(credential)
                session.commit()
        rows = [
            {
                "keys": ["welcome sequence", PAGE],
                "clicks": 6,
                "impressions": 40,
                "ctr": 0.15,
                "position": 8,
            }
        ]
        httpx_mock.add_response(
            method="POST",
            url=QUERY_URL,
            json={
                "rows": rows,
                "responseAggregationType": "byPage",
                "access_token": "synthetic-output-secret",
            },
        )
        result = mcp_client.call_tool_structured(
            tool,
            {
                **args,
                "input_json": _input(),
                "idempotency_key": f"page-read-{phase}",
            },
        )["data"]
        assert result["status"] == "success"
        assert result["output"]["output_mode"] == "file"
        assert result["output"]["schema_ref"] == "stackos.action-output.v1"
        saved = json.loads(Path(result["output"]["path"]).read_text())
        assert saved["response"]["output_json"]["rows"] == rows
        request = httpx_mock.get_requests()[-1]
        assert request.headers["authorization"] == f"Bearer {tokens[-1]}"
        assert (
            json.loads(request.content)["dimensionFilterGroups"]
            == _input()["dimension_filter_groups"]
        )
        assert len(tokens) == (2 if phase == 3 else 1)
        audit_response = mcp_client.test_client.get(
            f"/api/v1/projects/{project_id}/action-calls",
            params={"action_key": "search-console.search-analytics.query", "status": "success"},
            headers=mcp_client._headers(),
        )
        assert audit_response.status_code == 200
        audit = audit_response.json()["items"][0]
        assert audit["response_json"]["file"]["path"] == result["output"]["path"]
        if granted:
            assert audit["run_plan_id"] is not None
            assert audit["run_plan_step_id"] is not None
        rendered = json.dumps({"result": result, "file": saved, "audit": audit, "test": tested})
        for secret in (*tokens, "synthetic-output-secret", "BEGIN PRIVATE KEY"):
            assert secret not in rendered


def test_service_account_cross_project_and_wrong_provider_denied_before_http(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    synthetic_key: tuple[str, rsa.RSAPublicKey],
) -> None:
    project_id = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project_id)
    other = mcp_client.call_tool_structured(
        "project.create",
        {"slug": "other", "name": "Other", "domain": "other.example", "locale": "en-US"},
    )["data"]["id"]
    for args, message in (
        ({"project_id": other, "action_ref": ACTION, "input_json": _input()}, "not attached"),
        (
            {
                "project_id": project_id,
                "action_ref": "seo.ga4.account_summaries.list",
                "input_json": {},
            },
            "provider",
        ),
    ):
        error = mcp_client.call_tool_error("action.run", {**args, "credential_ref": ref})
        assert error["code"] < 0
        assert message in json.dumps(error).lower()
    assert httpx_mock.get_requests() == []


def test_service_account_property_permission_failure_is_not_authentication_success(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    synthetic_key: tuple[str, rsa.RSAPublicKey],
) -> None:
    project_id = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project_id)
    tokens = _token_exchange(httpx_mock, synthetic_key[1])
    httpx_mock.add_response(
        method="POST",
        url=QUERY_URL,
        status_code=403,
        json={
            "error": {
                "code": 403,
                "message": "Property permission denied",
                "status": "PERMISSION_DENIED",
            },
        },
    )
    error = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": ACTION,
            "credential_ref": ref,
            "input_json": _input(),
        },
    )
    assert error["code"] < 0
    assert len(tokens) == 1
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"status": "failed"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    assert len(audit_response.json()["items"]) == 1
    rendered = json.dumps({"error": error, "audit": audit_response.json()})
    assert "BEGIN PRIVATE KEY" not in rendered
    assert tokens[0] not in rendered


def test_service_account_invalid_unicode_is_safe_rest_error_without_persistence(
    mcp_client: MCPClient,
    httpx_mock: HTTPXMock,
) -> None:
    response = mcp_client.test_client.post(
        "/api/v1/auth/accounts/google-search-console",
        headers=mcp_client._headers(),
        content=json.dumps(
            {
                "auth_method_key": "service-account",
                "display_name": "Invalid key",
                "fields": {"service_account_json": "\ud800"},
            },
            ensure_ascii=True,
        ),
    )
    assert response.status_code == 422
    assert "UTF-8" in response.text
    assert "UnicodeEncodeError" not in response.text
    assert "\\ud800" not in response.text
    with Session(mcp_client.test_client.app.state.engine) as session:
        assert session.exec(select(Credential)).all() == []
    assert httpx_mock.get_requests() == []


SUBMIT_ACTION = "seo.search-console.sitemaps.submit"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"
SUBMIT_INPUT = {
    "site_url": "sc-domain:example.com",
    "sitemap_url": "https://example.com/sitemap.xml",
}
SUBMIT_URL = (
    "https://www.googleapis.com/webmasters/v3/sites/sc-domain%3Aexample.com/sitemaps/"
    "https%3A%2F%2Fexample.com%2Fsitemap.xml"
)


@pytest.mark.parametrize("granted", [False, True])
def test_sitemap_submit_direct_and_granted_receipt_audit_and_success_replay(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project, access_mode="sitemap_write")
    tool, args = _action_tool(mcp_client, project, ref, granted, SUBMIT_ACTION)
    if not granted:
        args.update(confirm_direct=True, intent_summary="Submit the explicit sitemap URL")
    else:
        denied = mcp_client.call_tool_error(
            tool,
            {
                **args,
                "action_ref": "seo.search-console.sites.list",
                "input_json": {},
            },
        )
        assert denied["code"] == -32007
        assert httpx_mock.get_requests() == []
    tokens = _token_exchange(httpx_mock, synthetic_key[1], WRITE_SCOPE)
    httpx_mock.add_response(method="PUT", url=SUBMIT_URL, status_code=204)
    args.update(input_json=SUBMIT_INPUT, idempotency_key="sitemap-success-proof")
    result = mcp_client.call_tool_structured(tool, args)["data"]
    assert result["status"] == "success"
    assert result["output"]["output_mode"] == "file"
    saved = json.loads(Path(result["output"]["path"]).read_text())
    assert saved["response"]["output_json"] == {**SUBMIT_INPUT, "submitted": True}
    assert httpx_mock.get_requests()[-1].content == b""
    replay = mcp_client.call_tool_structured(tool, {**args, "response_mode": "raw"})["data"]
    assert (replay["action_call"] if granted else replay)["status"] == "success"
    assert len(httpx_mock.get_requests()) == 2
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp_client._headers(),
        params={"action_key": "search-console.sitemaps.submit", "status": "success"},
    ).json()["items"][0]
    assert audit["response_json"]["file"]["path"] == result["output"]["path"]
    if granted:
        assert audit["run_plan_id"] and audit["run_plan_step_id"]
    serialized = json.dumps({"result": result, "saved": saved, "audit": audit, "replay": replay})
    for secret in (*tokens, "BEGIN PRIVATE KEY", synthetic_key[0]):
        assert secret not in serialized


@pytest.mark.parametrize("granted", [False, True])
def test_sitemap_submit_readonly_account_denied_before_http(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project)
    tool, args = _action_tool(mcp_client, project, ref, granted, SUBMIT_ACTION)
    if not granted:
        args.update(confirm_direct=True, intent_summary="Submit sitemap")
    error = mcp_client.call_tool_error(tool, {**args, "input_json": SUBMIT_INPUT})
    assert "access mode" in json.dumps(error)
    assert not httpx_mock.get_requests()


def test_sitemap_submit_cross_project_and_invalid_payload_denied_before_http(
    mcp_client, seeded_project, httpx_mock, synthetic_key
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project, access_mode="sitemap_write")
    other = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "other-submit",
            "name": "Other submit",
            "domain": "other.example",
            "locale": "en-US",
        },
    )["data"]["id"]
    args = {
        "project_id": project,
        "credential_ref": ref,
        "action_ref": SUBMIT_ACTION,
        "input_json": SUBMIT_INPUT,
        "confirm_direct": True,
        "intent_summary": "Submit sitemap",
    }
    error = mcp_client.call_tool_error("action.run", {**args, "project_id": other})
    assert "not attached" in json.dumps(error)
    for payload in (
        {**SUBMIT_INPUT, "sitemap_url": "not-a-url"},
        {**SUBMIT_INPUT, "site_url": "https://example.com"},
        {"site_url": "sc-domain:example.com"},
        {**SUBMIT_INPUT, "method": "DELETE"},
    ):
        error = mcp_client.call_tool_error("action.run", {**args, "input_json": payload})
        assert error["code"] < 0
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("field", ["site_url", "sitemap_url"])
def test_sitemap_submit_url_parser_failure_is_field_validation_before_http(
    mcp_client, seeded_project, httpx_mock, synthetic_key, field
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project, access_mode="sitemap_write")
    error = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project,
            "credential_ref": ref,
            "action_ref": SUBMIT_ACTION,
            "input_json": {**SUBMIT_INPUT, field: "https://["},
            "confirm_direct": True,
            "intent_summary": "Submit sitemap",
        },
    )
    assert f"$.{field}" in json.dumps(error)
    assert "ValueError" not in json.dumps(error)
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("status", [403, 429, 503, None])
@pytest.mark.parametrize("granted", [False, True])
def test_sitemap_submit_failure_retains_provider_error_and_unknown_outcome_without_retry(
    mcp_client, seeded_project, httpx_mock, synthetic_key, status, granted
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project, access_mode="sitemap_write")
    tool, args = _action_tool(mcp_client, project, ref, granted, SUBMIT_ACTION)
    if not granted:
        args.update(confirm_direct=True, intent_summary="Submit sitemap")
    tokens = _token_exchange(httpx_mock, synthetic_key[1], WRITE_SCOPE)
    if status is None:
        httpx_mock.add_exception(
            httpx.ReadTimeout("synthetic timeout"), method="PUT", url=SUBMIT_URL
        )
    else:
        httpx_mock.add_response(
            method="PUT",
            url=SUBMIT_URL,
            status_code=status,
            headers={"Retry-After": "17"},
            json={
                "error": {
                    "code": status,
                    "message": "Google test failure",
                    "access_token": "synthetic-error-secret",
                },
            },
        )
    error = mcp_client.call_tool_error(
        tool,
        {**args, "input_json": SUBMIT_INPUT},
    )
    assert len(httpx_mock.get_requests()) == 2
    provider_error = error["data"]["provider_error"]
    assert provider_error["outcome_unknown"] is (status is None or status >= 500)
    assert provider_error["retry_safe"] is False
    if status:
        assert error["data"]["provider_status_code"] == status
        assert provider_error["error"]["code"] == status
    if status == 429:
        assert provider_error["retry_after"] == 17
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp_client._headers(),
        params={"action_key": "search-console.sitemaps.submit", "status": "failed"},
    ).json()["items"][0]
    if status == 429:
        assert audit["response_json"]["provider_error"]["retry_after"] == 17
    if granted:
        assert audit["run_plan_id"] and audit["run_plan_step_id"]
    serialized = json.dumps({"error": error, "audit": audit})
    assert '"retry_safe": false' in serialized
    for secret in (*tokens, "BEGIN PRIVATE KEY", "synthetic-error-secret"):
        assert secret not in serialized


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("granted", [False, True])
def test_gsc_batch_direct_granted_file_audit_successful_replay(
    mcp_client, seeded_project, httpx_mock, synthetic_key, write, granted
):
    project = seeded_project["data"]["id"]
    ref = _create_account(
        mcp_client, synthetic_key[0], project, access_mode="sitemap_write" if write else "readonly"
    )
    action = (
        "seo.search-console.sitemaps.submit.batch" if write else "seo.search-console.batch.read"
    )
    tool, args = _action_tool(mcp_client, project, ref, granted, action)
    if granted:
        error = mcp_client.call_tool_error(
            tool, {**args, "action_ref": SUBMIT_ACTION, "input_json": SUBMIT_INPUT}
        )
        assert error["code"] == -32007
    elif write:
        error = mcp_client.call_tool_error(
            tool, {**args, "input_json": {"requests": [SUBMIT_INPUT]}}
        )
        assert error["code"] < 0
        args.update(confirm_direct=True, intent_summary="Submit selected sitemaps")
    assert not httpx_mock.get_requests()
    tokens = _token_exchange(httpx_mock, synthetic_key[1], WRITE_SCOPE if write else SCOPE)
    httpx_mock.add_response(
        url=BATCH_URL,
        content=multipart(
            [(i, 204, "") if write else (i, 200, {"siteEntry": []}) for i in range(30)]
        ),
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    item = SUBMIT_INPUT if write else {"operation": "sites.list", "input": {}}
    args.update(input_json={"requests": [item] * 30}, idempotency_key="batch-success-proof")
    result = mcp_client.call_tool_structured(tool, args)["data"]
    assert result["status"] == "success"
    saved = json.loads(Path(result["output"]["path"]).read_text())
    output = saved["response"]["output_json"]
    assert output["summary"] == {"total": 30, "succeeded": 30, "failed": 0, "unknown": 0}
    assert [item["index"] for item in output["items"]] == list(range(30))
    replay = mcp_client.call_tool_structured(tool, {**args, "response_mode": "raw"})["data"]
    assert (replay["action_call"] if granted else replay)["status"] == "success"
    assert len(httpx_mock.get_requests()) == 2
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp_client._headers(),
        params={"action_key": action.removeprefix("seo."), "status": "success"},
    ).json()["items"][0]
    assert audit["response_json"]["file"]["path"] == result["output"]["path"]
    if granted:
        assert audit["run_plan_id"] and audit["run_plan_step_id"]
    serialized = json.dumps({"saved": saved, "audit": audit, "replay": replay})
    for secret in (*tokens, "BEGIN PRIVATE KEY", "Content-ID", "HTTP/1.1"):
        assert secret not in serialized


@pytest.mark.parametrize("granted", [False, True])
@pytest.mark.parametrize("count", [3, 1000])
def test_gsc_batch_partial_failure_retains_every_item_in_error_and_audit(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted, count
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project, access_mode="sitemap_write")
    action = "seo.search-console.sitemaps.submit.batch"
    tool, args = _action_tool(mcp_client, project, ref, granted, action)
    if not granted:
        args.update(confirm_direct=True, intent_summary="Submit selected sitemaps")
    tokens = _token_exchange(httpx_mock, synthetic_key[1], WRITE_SCOPE)
    httpx_mock.add_response(
        url=BATCH_URL,
        content=multipart(
            [(1, 403, {"error": {"code": 403, "access_token": "part-secret"}}), (0, 204, "")]
        ),
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    error = mcp_client.call_tool_error(
        tool, {**args, "input_json": {"requests": [SUBMIT_INPUT] * count}}
    )
    summary = error["data"]["provider_error"]
    assert summary["summary"] == {"total": count, "succeeded": 1, "failed": 1, "unknown": count - 2}
    assert [item["outcome"] for item in summary["items"]] == ["success", "error"] + ["unknown"] * (
        count - 2
    )
    assert [item["index"] for item in summary["items"]] == list(range(count))
    assert summary["partial_success"] and summary["reconcile_before_retry"]
    assert summary["retry_safe"] is False
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp_client._headers(),
        params={"action_key": action.removeprefix("seo."), "status": "failed"},
    ).json()["items"][0]
    assert audit["response_json"]["items"] == summary["items"]
    assert audit["response_json"]["provider_error"]["items"] == summary["items"]
    if granted:
        assert audit["run_plan_id"] and audit["run_plan_step_id"]
    assert len(httpx_mock.get_requests()) == 2
    serialized = json.dumps({"error": error, "audit": audit})
    for secret in (*tokens, "BEGIN PRIVATE KEY", "part-secret", "Content-ID", "HTTP/1.1"):
        assert secret not in serialized


@pytest.mark.parametrize("granted", [False, True])
def test_gsc_batch_write_readonly_denied_before_token_exchange(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project)
    tool, args = _action_tool(
        mcp_client, project, ref, granted, "seo.search-console.sitemaps.submit.batch"
    )
    if not granted:
        args.update(confirm_direct=True, intent_summary="Submit selected sitemaps")
    error = mcp_client.call_tool_error(tool, {**args, "input_json": {"requests": [SUBMIT_INPUT]}})
    assert "access mode" in json.dumps(error)
    assert not httpx_mock.get_requests()


def test_gsc_batch_all_items_and_project_validated_before_http(
    mcp_client, seeded_project, httpx_mock, synthetic_key
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project)
    args = {
        "project_id": project,
        "credential_ref": ref,
        "action_ref": "seo.search-console.batch.read",
    }
    valid = {"operation": "sites.list", "input": {}}
    for payload in (
        {"requests": []},
        {"requests": [valid] * 1001},
        {"requests": [{"operation": "batch.read", "input": {"requests": [valid]}}]},
        {"requests": [{"operation": "unknown", "input": {}}]},
        {"requests": [valid], "url": "https://elsewhere.example"},
        {"requests": [{**valid, "method": "DELETE"}]},
        {"requests": [{**valid, "input": {"headers": {"Authorization": "invented"}}}]},
        {
            "requests": [valid] * 999
            + [{"operation": "sitemaps.list", "input": {"site_url": "https://["}}]
        },
        {
            "requests": [
                {
                    "operation": "url.inspect",
                    "input": {"site_url": "sc-domain:example.com", "inspection_url": "https://["},
                }
            ]
        },
        {
            "requests": [
                {
                    "operation": "search_analytics.query",
                    "input": {**_input(), "start_date": "bad-date"},
                }
            ]
        },
        {"requests": [{"operation": "search_analytics.query", "input": {**_input(), "type": []}}]},
        {
            "requests": [
                {
                    "operation": "search_analytics.query",
                    "input": {
                        **_input(),
                        "dimension_filter_groups": [
                            {"filters": [{"dimension": "page", "operator": {}, "expression": "a"}]}
                        ],
                    },
                }
            ]
        },
    ):
        error = mcp_client.call_tool_error("action.run", {**args, "input_json": payload})
        assert error["code"] < 0
        assert "RepositoryError" not in json.dumps(error)
    other = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "other-batch",
            "name": "Other batch",
            "domain": "other.example",
            "locale": "en-US",
        },
    )["data"]["id"]
    error = mcp_client.call_tool_error(
        "action.run", {**args, "project_id": other, "input_json": {"requests": [valid]}}
    )
    assert "not attached" in json.dumps(error)
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("status", [403, 429, 503, None])
def test_gsc_batch_outer_failure_in_granted_action_has_complete_safe_audit(
    mcp_client, seeded_project, httpx_mock, synthetic_key, status
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project, access_mode="sitemap_write")
    action = "seo.search-console.sitemaps.submit.batch"
    tool, args = _action_tool(mcp_client, project, ref, True, action)
    tokens = _token_exchange(httpx_mock, synthetic_key[1], WRITE_SCOPE)
    if status is None:
        httpx_mock.add_exception(httpx.ReadTimeout("synthetic batch timeout"), url=BATCH_URL)
    else:
        httpx_mock.add_response(
            url=BATCH_URL,
            status_code=status,
            content=b"RAW-MIME-SECRET Content-ID: private",
            headers={"Content-Type": "multipart/mixed; boundary=private", "Retry-After": "7"},
        )
    error = mcp_client.call_tool_error(
        tool, {**args, "input_json": {"requests": [SUBMIT_INPUT] * 2}}
    )
    summary = error["data"]["provider_error"]
    expected = "unknown" if status is None or status >= 500 else "error"
    assert [item["outcome"] for item in summary["items"]] == [expected] * 2
    if status == 429:
        assert summary["retry_after"] == 7
        assert all(item["retry_after"] == 7 for item in summary["items"])
        assert all(item["provider_error"]["retry_after"] == 7 for item in summary["items"])
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp_client._headers(),
        params={"action_key": action.removeprefix("seo."), "status": "failed"},
    ).json()["items"][0]
    assert audit["response_json"]["items"] == summary["items"]
    assert audit["run_plan_id"] and audit["run_plan_step_id"]
    assert len(httpx_mock.get_requests()) == 2
    serialized = json.dumps({"error": error, "audit": audit})
    for secret in (*tokens, "RAW-MIME-SECRET", "Content-ID", "BEGIN PRIVATE KEY"):
        assert secret not in serialized


@pytest.mark.parametrize("granted", [False, True])
@pytest.mark.parametrize("defect", ["extra_part", "missing_close"])
def test_gsc_batch_protocol_failure_preserves_receipt_in_mcp_and_audit(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted, defect
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project, access_mode="sitemap_write")
    action = "seo.search-console.sitemaps.submit.batch"
    tool, args = _action_tool(mcp_client, project, ref, granted, action)
    if not granted:
        args.update(confirm_direct=True, intent_summary="Submit selected sitemaps")
    _token_exchange(httpx_mock, synthetic_key[1], WRITE_SCOPE)
    parts = [(0, 204, "")]
    if defect == "extra_part":
        parts.append((9, 204, ""))
    body = multipart(parts)
    if defect == "missing_close":
        body = body.removesuffix(b"--batch_reply--\r\n")
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    error = mcp_client.call_tool_error(tool, {**args, "input_json": {"requests": [SUBMIT_INPUT]}})
    summary = error["data"]["provider_error"]
    assert summary["summary"] == {"total": 1, "succeeded": 1, "failed": 0, "unknown": 0}
    assert summary["items"][0]["status"] == 204
    assert summary["items"][0]["result"] == {"submitted": True}
    assert summary["protocol_error"]
    assert summary["reconcile_before_retry"] and summary["retry_safe"] is False
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp_client._headers(),
        params={"action_key": action.removeprefix("seo."), "status": "failed"},
    ).json()["items"][0]
    assert audit["response_json"]["provider_error"] == summary
    assert audit["response_json"]["items"] == summary["items"]
    assert audit["response_json"]["protocol_error"] == summary["protocol_error"]
    assert len(httpx_mock.get_requests()) == 2
    assert "Content-ID" not in json.dumps({"error": error, "audit": audit})


@pytest.mark.parametrize("granted", [False, True])
def test_gsc_batch_inner_quota_advice_survives_mcp_and_audit(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted
):
    project = seeded_project["data"]["id"]
    ref = _create_account(mcp_client, synthetic_key[0], project)
    action = "seo.search-console.batch.read"
    tool, args = _action_tool(mcp_client, project, ref, granted, action)
    _token_exchange(httpx_mock, synthetic_key[1])
    body = multipart([(0, 429, {"error": {"code": 429, "message": "Quota reached"}})]).replace(
        b"X-Request-Id: part-0\r\n", b"X-Request-Id: part-0\r\nRetry-After: 17\r\n"
    )
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    error = mcp_client.call_tool_error(
        tool, {**args, "input_json": {"requests": [{"operation": "sites.list", "input": {}}]}}
    )
    summary = error["data"]["provider_error"]
    item = summary["items"][0]
    assert item["status"] == 429 and item["retry_after"] == 17
    assert item["provider_error"]["retry_after"] == 17
    assert summary["retry_safe"] is False
    audit = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp_client._headers(),
        params={"action_key": action.removeprefix("seo."), "status": "failed"},
    ).json()["items"][0]
    assert audit["response_json"]["provider_error"] == summary
    assert audit["response_json"]["items"] == [item]
    assert len(httpx_mock.get_requests()) == 2
