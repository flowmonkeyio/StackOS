"""Synthetic Indexing service-account setup and native MCP action proof."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from stackos.db.models import Credential

from ..test_integrations.test_google_search_console_batch import multipart
from .test_mcp_google_service_accounts import (
    TOKEN_URL,
    _action_tool,
    _token_exchange,
)
from .test_mcp_google_service_accounts import (
    synthetic_key as synthetic_key,
)

SCOPE = "https://www.googleapis.com/auth/indexing"
PUBLISH = "seo.indexing.url-notifications.publish"
METADATA = "seo.indexing.url-notifications.metadata.get"
URL = "https://example.com/jobs/engineer?a=1&b=two"
PUBLISH_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
METADATA_URL = "https://indexing.googleapis.com/v3/urlNotifications/metadata"
BATCH_URL = "https://indexing.googleapis.com/batch"
BATCH_PUBLISH = "seo.indexing.batch.publish"
BATCH_METADATA = "seo.indexing.batch.metadata.get"


def create_account(mcp, document, project):
    response = mcp.test_client.post(
        "/api/v1/auth/accounts/google-indexing",
        headers=mcp._headers(),
        json={
            "auth_method_key": "service-account",
            "display_name": "Synthetic Indexing account",
            "attach_project_id": project,
            "fields": {"service_account_json": document},
        },
    )
    assert response.status_code == 201
    assert "BEGIN PRIVATE KEY" not in json.dumps(response.json())
    return response.json()["data"]["credential_ref"]


def test_indexing_account_test_acquires_token_without_provider_probe(
    mcp_client, seeded_project, httpx_mock, synthetic_key
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    tokens = _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    tested = mcp_client.call_tool_structured(
        "account.test",
        {
            "project_id": project,
            "credential_ref": ref,
            "response_mode": "raw",
        },
    )["data"]
    assert tested["ok"] is True
    assert tested["metadata"]["verification"] == "token_acquisition_only"
    for field in ("resource_access", "api_access", "site_access"):
        assert tested["metadata"][field] == "unverified"
    assert [str(request.url) for request in httpx_mock.get_requests()] == [TOKEN_URL]
    assert all(token not in json.dumps(tested) for token in tokens)


def audit(mcp, project, operation, status):
    response = mcp.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp._headers(),
        params={"action_key": f"indexing.url-notifications.{operation}", "status": status},
    )
    assert response.status_code == 200
    return response.json()["items"][0]


@pytest.mark.parametrize("granted", [False, True])
@pytest.mark.parametrize("kind", ["URL_UPDATED", "URL_DELETED"])
def test_indexing_publish_direct_granted_receipt_replay_and_static_risk(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted, kind
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    described = mcp_client.call_tool_structured(
        "action.describe", {"action_ref": PUBLISH, "response_mode": "raw"}
    )
    assert described["manifest"]["risk_level"] == "destructive"
    payload = {"url": URL, "type": kind}
    validated = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project,
            "action_ref": PUBLISH,
            "credential_ref": ref,
            "input_json": payload,
            "response_mode": "raw",
        },
    )
    assert validated["valid"]
    tool, args = _action_tool(mcp_client, project, ref, granted, PUBLISH)
    if granted:
        denied = mcp_client.call_tool_error(
            tool, {**args, "action_ref": METADATA, "input_json": {"url": URL}}
        )
        assert denied["code"] == -32007
    else:
        denied = mcp_client.call_tool_error(tool, {**args, "input_json": payload})
        assert denied["code"] < 0
        args.update(confirm_direct=True, intent_summary="Notify Google of this eligible URL")
    assert not httpx_mock.get_requests()
    tokens = _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    metadata = {
        "url": URL,
        "latestUpdate" if kind == "URL_UPDATED" else "latestRemove": {
            "url": URL,
            "type": kind,
            "notifyTime": "2026-09-28T12:00:00Z",
        },
    }
    httpx_mock.add_response(
        method="POST", url=PUBLISH_URL, json={"urlNotificationMetadata": metadata}
    )
    args.update(input_json=payload, idempotency_key="indexing-publish-success")
    result = mcp_client.call_tool_structured(tool, args)["data"]
    assert result["status"] == "success"
    saved = json.loads(Path(result["output"]["path"]).read_text())
    output = saved["response"]["output_json"]
    assert output == {
        **payload,
        "notification_received": True,
        "notification_metadata": metadata,
        "indexing_status": "unverified",
    }
    request = httpx_mock.get_requests()[-1]
    assert json.loads(request.content) == payload
    assert request.headers["authorization"] == f"Bearer {tokens[-1]}"
    replay = mcp_client.call_tool_structured(tool, {**args, "response_mode": "raw"})["data"]
    assert (replay["action_call"] if granted else replay)["status"] == "success"
    assert len(httpx_mock.get_requests()) == 2
    recorded = audit(mcp_client, project, "publish", "success")
    assert recorded["response_json"]["file"]["path"] == result["output"]["path"]
    if granted:
        assert recorded["run_plan_id"] and recorded["run_plan_step_id"]
    rendered = json.dumps({"result": result, "saved": saved, "audit": recorded, "replay": replay})
    for secret in (*tokens, "BEGIN PRIVATE KEY", synthetic_key[0]):
        assert secret not in rendered


@pytest.mark.parametrize("granted", [False, True])
def test_indexing_metadata_empty_history_and_expired_token_renewal(
    mcp_client, seeded_project, httpx_mock, synthetic_key, granted
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    tool, args = _action_tool(mcp_client, project, ref, granted, METADATA)
    tokens = _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    for phase in range(3):
        if phase == 2:
            with Session(mcp_client.test_client.app.state.engine) as session:
                credential = session.exec(
                    select(Credential).where(Credential.credential_ref == ref)
                ).one()
                credential.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
                    seconds=1
                )
                session.add(credential)
                session.commit()
        httpx_mock.add_response(
            method="GET", url=httpx.URL(METADATA_URL, params={"url": URL}), json={}
        )
        result = mcp_client.call_tool_structured(
            tool, {**args, "input_json": {"url": URL}, "idempotency_key": f"metadata-{phase}"}
        )["data"]
        saved = json.loads(Path(result["output"]["path"]).read_text())
        assert saved["response"]["output_json"] == {
            "url": URL,
            "notification_metadata": {},
            "indexing_status": "unverified",
        }
        request = httpx_mock.get_requests()[-1]
        assert request.content == b""
        assert request.headers["authorization"] == f"Bearer {tokens[-1]}"
        assert len(tokens) == (2 if phase == 2 else 1)
    recorded = audit(mcp_client, project, "metadata.get", "success")
    if granted:
        assert recorded["run_plan_id"] and recorded["run_plan_step_id"]
    assert all(
        token not in json.dumps({"result": result, "saved": saved, "audit": recorded})
        for token in tokens
    )


def test_indexing_project_provider_and_invalid_input_denied_before_http(
    mcp_client, seeded_project, httpx_mock, synthetic_key
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    other = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "other-indexing",
            "name": "Other Indexing",
            "domain": "other.example",
            "locale": "en-US",
        },
    )["data"]["id"]
    for args, expected in (
        ({"project_id": other, "action_ref": METADATA, "input_json": {"url": URL}}, "not attached"),
        (
            {
                "project_id": project,
                "action_ref": "seo.search-console.sites.list",
                "input_json": {},
            },
            "provider",
        ),
        (
            {"project_id": project, "action_ref": METADATA, "input_json": {"url": "https://["}},
            "url",
        ),
    ):
        error = mcp_client.call_tool_error("action.run", {**args, "credential_ref": ref})
        assert error["code"] < 0
        assert expected in json.dumps(error).lower()
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("status", [403, 429, 503, None])
@pytest.mark.parametrize("granted", [False, True])
def test_indexing_publish_error_retains_safe_status_and_unknown_audit_without_retry(
    mcp_client, seeded_project, httpx_mock, synthetic_key, status, granted
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    tool, args = _action_tool(mcp_client, project, ref, granted, PUBLISH)
    if not granted:
        args.update(confirm_direct=True, intent_summary="Notify Google of this eligible URL")
    tokens = _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    if status is None:
        httpx_mock.add_exception(
            httpx.ReadTimeout("synthetic timeout"), method="POST", url=PUBLISH_URL
        )
    else:
        httpx_mock.add_response(
            method="POST",
            url=PUBLISH_URL,
            status_code=status,
            headers={"Retry-After": "17"},
            json={
                "error": {
                    "code": status,
                    "message": "Google test failure",
                    "access_token": "synthetic-error-secret",
                }
            },
        )
    error = mcp_client.call_tool_error(
        tool, {**args, "input_json": {"url": URL, "type": "URL_UPDATED"}}
    )
    assert len(httpx_mock.get_requests()) == 2
    detail = error["data"]["provider_error"]
    unknown = status is None or status >= 500
    assert detail["outcome_unknown"] is unknown
    assert detail["reconcile_before_retry"] is unknown
    assert detail["retry_safe"] is False
    if status:
        assert error["data"]["provider_status_code"] == status
        assert detail["error"]["code"] == status
    if status == 429:
        assert detail["retry_after"] == 17
    recorded = audit(mcp_client, project, "publish", "failed")
    if granted:
        assert recorded["run_plan_id"] and recorded["run_plan_step_id"]
    output = recorded["response_json"]
    assert output["provider_error"] == detail
    assert output["outcome_unknown"] is unknown
    assert output["retry_safe"] is False
    rendered = json.dumps({"error": error, "audit": recorded})
    for secret in (*tokens, "BEGIN PRIVATE KEY", "synthetic-error-secret"):
        assert secret not in rendered


def batch_audit(mcp, project, action, status):
    return mcp.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        headers=mcp._headers(),
        params={"action_key": action.removeprefix("seo."), "status": status},
    ).json()["items"][0]


@pytest.mark.parametrize("publish", [False, True])
@pytest.mark.parametrize("granted", [False, True])
def test_indexing_batch_direct_granted_all_results_file_replay_and_grants(
    mcp_client, seeded_project, httpx_mock, synthetic_key, publish, granted
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    action = BATCH_PUBLISH if publish else BATCH_METADATA
    tool, args = _action_tool(mcp_client, project, ref, granted, action)
    payload = {"urls": [URL] * 100, **({"type": "URL_DELETED"} if publish else {})}
    if granted:
        denied = mcp_client.call_tool_error(
            tool, {**args, "action_ref": METADATA, "input_json": {"url": URL}}
        )
        assert denied["code"] == -32007
    elif publish:
        denied = mcp_client.call_tool_error(tool, {**args, "input_json": payload})
        assert denied["code"] < 0
        args.update(confirm_direct=True, intent_summary="Notify Google for these eligible URLs")
    assert not httpx_mock.get_requests()
    tokens = _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    httpx_mock.add_response(
        url=BATCH_URL,
        content=multipart(
            [
                (i, 200, {"urlNotificationMetadata": {}} if publish else {})
                for i in reversed(range(100))
            ]
        ),
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    args.update(input_json=payload, idempotency_key="indexing-batch-success")
    result = mcp_client.call_tool_structured(tool, args)["data"]
    saved = json.loads(Path(result["output"]["path"]).read_text())
    output = saved["response"]["output_json"]
    assert output["summary"] == {"total": 100, "succeeded": 100, "failed": 0, "unknown": 0}
    assert [item["index"] for item in output["items"]] == list(range(100))
    assert all(item["result"]["indexing_status"] == "unverified" for item in output["items"])
    if publish:
        assert all(
            item["result"]["type"] == "URL_DELETED" and item["result"]["notification_received"]
            for item in output["items"]
        )
    replay = mcp_client.call_tool_structured(tool, {**args, "response_mode": "raw"})["data"]
    assert (replay["action_call"] if granted else replay)["status"] == "success"
    assert len(httpx_mock.get_requests()) == 2
    recorded = batch_audit(mcp_client, project, action, "success")
    assert recorded["response_json"]["file"]["path"] == result["output"]["path"]
    if granted:
        assert recorded["run_plan_id"] and recorded["run_plan_step_id"]
    rendered = json.dumps({"result": result, "saved": saved, "audit": recorded, "replay": replay})
    for secret in (*tokens, "BEGIN PRIVATE KEY", "Content-ID", "HTTP/1.1"):
        assert secret not in rendered


@pytest.mark.parametrize("publish", [False, True])
@pytest.mark.parametrize("granted", [False, True])
def test_indexing_batch_partial_failure_retains_100_items_in_mcp_and_audit(
    mcp_client, seeded_project, httpx_mock, synthetic_key, publish, granted
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    action = BATCH_PUBLISH if publish else BATCH_METADATA
    tool, args = _action_tool(mcp_client, project, ref, granted, action)
    if publish and not granted:
        args.update(confirm_direct=True, intent_summary="Notify Google for these eligible URLs")
    tokens = _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    body = multipart(
        [
            (1, 429, {"error": {"code": 429, "access_token": "part-secret"}}),
            (0, 200, {"urlNotificationMetadata": {}} if publish else {}),
        ]
    )
    body = body.replace(b"X-Request-Id: part-1", b"Retry-After: 17\r\nX-Request-Id: part-1")
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    error = mcp_client.call_tool_error(
        tool,
        {
            **args,
            "input_json": {"urls": [URL] * 100, **({"type": "URL_UPDATED"} if publish else {})},
        },
    )
    summary = error["data"]["provider_error"]
    assert summary["summary"] == {"total": 100, "succeeded": 1, "failed": 1, "unknown": 98}
    assert [item["index"] for item in summary["items"]] == list(range(100))
    assert (
        summary["items"][1]["retry_after"]
        == summary["items"][1]["provider_error"]["retry_after"]
        == 17
    )
    assert summary["partial_success"] and summary["reconcile_before_retry"]
    assert summary["retry_safe"] is False
    recorded = batch_audit(mcp_client, project, action, "failed")
    assert recorded["response_json"]["items"] == summary["items"]
    assert recorded["response_json"]["provider_error"] == summary
    assert "file" not in recorded["response_json"]
    polled = mcp_client.call_tool_structured(
        "actionCall.get",
        {"project_id": project, "action_call_id": recorded["id"], "response_mode": "raw"},
    )
    assert polled["output_json"]["provider_error"]["items"] == summary["items"]
    if granted:
        assert recorded["run_plan_id"] and recorded["run_plan_step_id"]
    rendered = json.dumps({"error": error, "audit": recorded, "polled": polled})
    for secret in (*tokens, "BEGIN PRIVATE KEY", "part-secret", "Content-ID", "HTTP/1.1"):
        assert secret not in rendered
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize("defect", ["extra", "missing_close"])
def test_indexing_batch_protocol_defect_preserves_known_receipts_in_failed_audit(
    mcp_client, seeded_project, httpx_mock, synthetic_key, defect
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    tool, args = _action_tool(mcp_client, project, ref, True, BATCH_PUBLISH)
    _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    parts = [(0, 200, {"urlNotificationMetadata": {}})]
    if defect == "extra":
        parts.append((9, 200, {}))
    body = multipart(parts)
    if defect == "missing_close":
        body = body.removesuffix(b"--batch_reply--\r\n")
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    error = mcp_client.call_tool_error(
        tool, {**args, "input_json": {"urls": [URL], "type": "URL_UPDATED"}}
    )
    summary = error["data"]["provider_error"]
    assert summary["summary"] == {"total": 1, "succeeded": 1, "failed": 0, "unknown": 0}
    assert summary["protocol_error"]
    assert summary["items"][0]["result"]["notification_received"]
    recorded = batch_audit(mcp_client, project, BATCH_PUBLISH, "failed")
    assert recorded["response_json"]["provider_error"] == summary


@pytest.mark.parametrize("status", [403, 429, 503, None])
def test_indexing_batch_outer_failure_granted_audit_and_no_retry(
    mcp_client, seeded_project, httpx_mock, synthetic_key, status
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    tool, args = _action_tool(mcp_client, project, ref, True, BATCH_PUBLISH)
    tokens = _token_exchange(httpx_mock, synthetic_key[1], SCOPE)
    if status is None:
        httpx_mock.add_exception(httpx.ReadTimeout("synthetic timeout"), url=BATCH_URL)
    else:
        httpx_mock.add_response(
            url=BATCH_URL,
            status_code=status,
            content=b"RAW-MIME-SECRET Content-ID: unsafe",
            headers={"Retry-After": "17"},
        )
    error = mcp_client.call_tool_error(
        tool, {**args, "input_json": {"urls": [URL] * 2, "type": "URL_UPDATED"}}
    )
    summary = error["data"]["provider_error"]
    assert [item["outcome"] for item in summary["items"]] == [
        "unknown" if status is None or status >= 500 else "error"
    ] * 2
    if status == 429:
        assert summary["retry_after"] == 17
    recorded = batch_audit(mcp_client, project, BATCH_PUBLISH, "failed")
    assert recorded["response_json"]["provider_error"] == summary
    rendered = json.dumps({"error": error, "audit": recorded})
    for secret in (*tokens, "BEGIN PRIVATE KEY", "RAW-MIME-SECRET", "Content-ID"):
        assert secret not in rendered
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize("publish", [False, True])
def test_indexing_batch_mcp_validation_and_project_denials_precede_http(
    mcp_client, seeded_project, httpx_mock, synthetic_key, publish
):
    project = seeded_project["data"]["id"]
    ref = create_account(mcp_client, synthetic_key[0], project)
    args = {
        "project_id": project,
        "credential_ref": ref,
        "action_ref": BATCH_PUBLISH if publish else BATCH_METADATA,
    }
    if publish:
        args.update(confirm_direct=True, intent_summary="Notify Google for these eligible URLs")
    base = {"type": "URL_UPDATED"} if publish else {}
    for payload in (
        {**base, "urls": []},
        {**base, "urls": [URL] * 101},
        {**base, "urls": [URL, "https://["]},
        {**base, "urls": [URL], "endpoint": "https://other.example/"},
        {**base, "urls": [URL + "é" * 200_000]},
    ):
        error = mcp_client.call_tool_error("action.run", {**args, "input_json": payload})
        assert error["code"] < 0
        assert "RepositoryError" not in json.dumps(error)
    other = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "other-indexing-batch",
            "name": "Other Indexing batch",
            "domain": "other.example",
            "locale": "en-US",
        },
    )["data"]["id"]
    error = mcp_client.call_tool_error(
        "action.run", {**args, "project_id": other, "input_json": {**base, "urls": [URL]}}
    )
    assert "not attached" in json.dumps(error)
    assert not httpx_mock.get_requests()
