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


def _create_account(mcp: MCPClient, document: str, project_id: int | None = None) -> str:
    response = mcp.test_client.post(
        "/api/v1/auth/accounts/google-search-console",
        headers=mcp._headers(),
        json={
            "auth_method_key": "service-account",
            "display_name": "Synthetic page reader",
            "attach_project_id": project_id,
            "fields": {"service_account_json": document},
        },
    )
    assert response.status_code == 201
    rendered = json.dumps(response.json())
    assert "BEGIN PRIVATE KEY" not in rendered
    assert document not in rendered
    return response.json()["data"]["credential_ref"]


def _token_exchange(httpx_mock: HTTPXMock, public_key: rsa.RSAPublicKey) -> list[str]:
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
        assert body["scope"] == SCOPE
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


def _action_tool(mcp: MCPClient, project_id: int, ref: str, granted: bool) -> tuple[str, dict]:
    args = {"project_id": project_id, "action_ref": ACTION, "credential_ref": ref}
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
                            "action_refs": [ACTION],
                        }
                    ]
                },
                "steps": [
                    {"id": "read", "title": "Read page performance", "action_refs": [ACTION]}
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
