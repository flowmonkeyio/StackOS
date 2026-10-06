"""Mocked Indexing wire contracts and notification-only results."""

import asyncio
import json
from urllib.parse import urlencode

import httpx
import pytest
from stackos_connectors.connectors.google_indexing.integration import GoogleIndexingIntegration
from stackos_connectors.errors import IntegrationDownError, RateLimitedError

from stackos.repositories.base import ConflictError

URL = "https://example.com/jobs/a?x=1&next=/two#part"
TOKEN = "synthetic-indexing-token"
PUBLISH_URL = GoogleIndexingIntegration.PUBLISH_URL
METADATA_URL = GoogleIndexingIntegration.METADATA_URL


def run(project_id, *, publish=False, kind="URL_UPDATED", audit=None):
    async def call():
        if audit is not None:
            return await audit.execute(
                "google-indexing",
                "indexing.url-notifications.publish"
                if publish
                else "indexing.url-notifications.metadata.get",
                {"url": URL, **({"type": kind} if publish else {})},
                secret_payload=json.dumps({"access_token": TOKEN}).encode(),
            )
        async with httpx.AsyncClient() as http:
            client = GoogleIndexingIntegration(
                payload=json.dumps({"access_token": TOKEN}).encode(),
                http=http,
            )
            return (
                await client.publish(url=URL, notification_type=kind)
                if publish
                else await client.get_metadata(url=URL)
            )

    return asyncio.run(call())


@pytest.mark.parametrize("kind", ["URL_UPDATED", "URL_DELETED"])
def test_publish_exact_body_and_notification_receipt(httpx_mock, project_id, kind):
    metadata = {"url": URL, "latestUpdate": {"type": kind, "notifyTime": "2026-09-28T00:00:00Z"}}
    httpx_mock.add_response(
        url=PUBLISH_URL, method="POST", json={"urlNotificationMetadata": metadata}
    )
    result = run(project_id, publish=True, kind=kind)
    request = httpx_mock.get_requests()[0]
    assert json.loads(request.content) == {"url": URL, "type": kind}
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert result.data == {
        "url": URL,
        "type": kind,
        "notification_received": True,
        "notification_metadata": metadata,
        "indexing_status": "unverified",
    }


@pytest.mark.parametrize(
    "metadata", [{}, {"url": URL}, {"url": URL, "latestRemove": {"type": "URL_DELETED"}}]
)
def test_metadata_encodes_url_and_preserves_empty_history(httpx_mock, project_id, metadata):
    httpx_mock.add_response(
        url=METADATA_URL + "?" + urlencode({"url": URL}), method="GET", json=metadata
    )
    result = run(project_id)
    assert httpx_mock.get_requests()[0].content == b""
    assert result.data == {
        "url": URL,
        "notification_metadata": metadata,
        "indexing_status": "unverified",
    }


@pytest.mark.parametrize("status", [403, 404, 429, 503, 302, None])
def test_publish_failures_never_retry_and_audit_safe_unknown_facts(
    httpx_mock, project_id, status, host_audit
):
    audit = host_audit
    if status is None:
        httpx_mock.add_exception(httpx.ReadTimeout("synthetic timeout"), url=PUBLISH_URL)
    else:
        httpx_mock.add_response(
            url=PUBLISH_URL,
            status_code=status,
            json={"error": {"code": status, "message": TOKEN, "access_token": "unsafe-echo"}},
            headers={"Retry-After": "17"},
        )
    with pytest.raises((IntegrationDownError, RateLimitedError, ConflictError)) as caught:
        run(project_id, publish=True, audit=audit)
    assert "provider_error" in caught.value.data, audit.record_call.call_args.kwargs
    error = caught.value.data["provider_error"]
    assert error["outcome_unknown"] is (status is None or status >= 500)
    assert error["retry_safe"] is False
    assert error["reconcile_before_retry"] is error["outcome_unknown"]
    assert len(httpx_mock.get_requests()) == 1
    serialized = json.dumps(
        {"error": caught.value.data, "audit": audit.record_call.call_args.kwargs}
    )
    assert TOKEN not in serialized and "unsafe-echo" not in serialized


@pytest.mark.parametrize("body", [b"private response text", b"[]", b"{}"])
def test_malformed_publish_response_is_unknown_and_safe_before_audit(
    httpx_mock, project_id, body, host_audit
):
    audit = host_audit
    httpx_mock.add_response(url=PUBLISH_URL, content=body)
    with pytest.raises((IntegrationDownError, ConflictError)) as caught:
        run(project_id, publish=True, audit=audit)
    assert caught.value.data["provider_error"]["outcome_unknown"] is True
    assert "private response text" not in json.dumps(audit.record_call.call_args.kwargs)
    assert len(httpx_mock.get_requests()) == 1


def test_metadata_404_remains_provider_failure(httpx_mock, project_id):
    httpx_mock.add_response(
        url=METADATA_URL + "?" + urlencode({"url": URL}),
        status_code=404,
        json={"error": {"code": 404, "message": "No notification found"}},
    )
    with pytest.raises((IntegrationDownError, ConflictError)) as caught:
        run(project_id)
    assert caught.value.data["status"] == 404
    assert caught.value.data["provider_error"]["error"]["code"] == 404


@pytest.mark.parametrize("retry_after", ["nan", "inf", "-1"])
def test_publish_discards_unsafe_retry_advice(httpx_mock, project_id, retry_after):
    httpx_mock.add_response(
        url=PUBLISH_URL,
        status_code=429,
        headers={"Retry-After": retry_after},
        json={"error": {"code": 429}},
    )
    with pytest.raises(RateLimitedError) as caught:
        run(project_id, publish=True)
    assert "retry_after" not in caught.value.data
    assert "retry_after" not in caught.value.data["provider_error"]
    assert len(httpx_mock.get_requests()) == 1
