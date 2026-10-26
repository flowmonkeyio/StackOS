"""Indexing batch wire, correlation and safe audit contracts."""

import asyncio
import json

import httpx
import pytest
from stackos_connectors.connectors.google_indexing.integration import GoogleIndexingIntegration
from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.shared.google.batch import encode_http_request

from stackos.repositories.base import ConflictError

from .test_google_search_console_batch import multipart

BATCH_URL = "https://indexing.googleapis.com/batch"
TOKEN = "synthetic-indexing-batch-token"
URLS = ["https://example.com/job/a", "https://example.com/job/b"]


def run_batch(project_id, urls=URLS, *, kind="URL_UPDATED", audit=None):
    async def run():
        if audit is not None:
            return await audit.execute(
                "google-indexing",
                "indexing.batch.publish" if kind else "indexing.batch.metadata.get",
                {"urls": urls, **({"type": kind} if kind else {})},
                secret_payload=json.dumps({"access_token": TOKEN}).encode(),
            )
        async with httpx.AsyncClient() as http:
            client = GoogleIndexingIntegration(
                payload=json.dumps({"access_token": TOKEN}).encode(),
                http=http,
            )
            if kind:
                return await client.batch_publish(urls=urls, notification_type=kind)
            return await client.batch_metadata(urls=urls)

    return asyncio.run(run())


def test_indexing_batch_partial_reordered_results_preserve_receipt_before_audit(
    httpx_mock, project_id, host_audit
):
    audit = host_audit
    httpx_mock.add_response(
        url=BATCH_URL,
        is_optional=True,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
        content=multipart(
            [
                (1, 403, {"error": {"code": 403, "message": TOKEN}}),
                (0, 200, {"urlNotificationMetadata": {"url": URLS[0]}}),
            ]
        ),
    )
    with pytest.raises((IntegrationDownError, ConflictError)) as caught:
        run_batch(project_id, audit=audit)
    assert "provider_error" in caught.value.data, caught.value.data
    summary = caught.value.data["provider_error"]
    assert summary["summary"] == {"total": 2, "succeeded": 1, "failed": 1, "unknown": 0}
    assert summary["items"][0]["result"]["notification_received"] is True
    assert summary["items"][0]["result"]["indexing_status"] == "unverified"
    assert summary["partial_success"] and summary["reconcile_before_retry"]
    assert summary["retry_safe"] is False
    rendered = json.dumps(audit.record_call.call_args.kwargs)
    for secret in (TOKEN, "Content-ID", "HTTP/1.1", "batch_reply"):
        assert secret not in rendered
    assert len(httpx_mock.get_requests()) == 1


def respond(httpx_mock, parts):
    httpx_mock.add_response(
        url=BATCH_URL,
        content=multipart(parts),
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )


@pytest.mark.parametrize("kind", ["URL_UPDATED", "URL_DELETED", None])
def test_indexing_batch_100_requests_mapping_and_notification_semantics(
    httpx_mock, project_id, kind
):
    urls = [f"https://example.com/job/{i}?a=1&b=two" for i in range(100)]
    respond(
        httpx_mock,
        [
            (i, 200, {"urlNotificationMetadata": {"url": urls[i]}} if kind else {})
            for i in reversed(range(100))
        ],
    )
    output = run_batch(project_id, urls, kind=kind).data
    assert output["summary"] == {"total": 100, "succeeded": 100, "failed": 0, "unknown": 0}
    assert [item["index"] for item in output["items"]] == list(range(100))
    assert [item["result"]["url"] for item in output["items"]] == urls
    assert all(item["result"]["indexing_status"] == "unverified" for item in output["items"])
    wire = httpx_mock.get_requests()[0]
    assert wire.headers["authorization"] == f"Bearer {TOKEN}"
    body = wire.content.decode()
    assert TOKEN not in body and "Authorization" not in body
    assert body.count("Content-ID: <item-") == 100
    if kind:
        assert body.count("POST /v3/urlNotifications:publish HTTP/1.1") == 100
        assert body.count("Content-Type: application/json") == 100
        assert body.count(f'"type": "{kind}"') == 100
        assert all(item["result"]["notification_received"] for item in output["items"])
    else:
        assert body.count("GET /v3/urlNotifications/metadata?url=") == 100
        assert "%3Fa%3D1%26b%3Dtwo HTTP/1.1\r\n\r\n\r\n" in body
        assert all(item["result"]["notification_metadata"] == {} for item in output["items"])


@pytest.mark.parametrize("kind", ["URL_UPDATED", None])
@pytest.mark.parametrize("count", [0, 101])
def test_indexing_batch_count_bound_before_http(httpx_mock, project_id, count, kind):
    with pytest.raises(ValueError, match="1 through 100"):
        run_batch(project_id, [URLS[0]] * count, kind=kind)
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("kind", ["URL_UPDATED", None])
def test_indexing_batch_exact_inner_request_byte_bound(httpx_mock, project_id, kind):
    prefix = "https://example.com/"
    base_size = len(encode_http_request(*GoogleIndexingIntegration.batch_call(prefix, kind)))
    url = prefix + "a" * (1_000_000 - base_size)
    respond(httpx_mock, [(0, 200, {"urlNotificationMetadata": {}} if kind else {})])
    assert run_batch(project_id, [url], kind=kind).data["summary"]["succeeded"] == 1
    with pytest.raises(ValueError, match="1 MB"):
        run_batch(project_id, [url + "a"], kind=kind)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "defect,expected",
    [
        ("missing", ["success", "unknown"]),
        ("duplicate", ["unknown", "success"]),
        ("malformed", ["unknown", "success"]),
        ("bad_metadata", ["unknown", "success"]),
        ("extra", ["success", "success"]),
        ("missing_close", ["success", "success"]),
    ],
)
def test_indexing_batch_defects_preserve_proven_receipts(
    httpx_mock, project_id, defect, expected, host_audit
):
    parts = [(i, 200, {"urlNotificationMetadata": {"url": url}}) for i, url in enumerate(URLS)]
    if defect == "missing":
        parts.pop()
    elif defect == "duplicate":
        parts.append(parts[0])
    elif defect == "malformed":
        parts[0] = (0, 200, "MALFORMED-SECRET")
    elif defect == "bad_metadata":
        parts[0] = (0, 200, {})
    elif defect == "extra":
        parts.append((9, 200, {}))
    body = multipart(parts)
    if defect == "missing_close":
        body = body.removesuffix(b"--batch_reply--\r\n")
    audit = host_audit
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    with pytest.raises((IntegrationDownError, ConflictError)) as caught:
        run_batch(project_id, audit=audit)
    summary = caught.value.data["provider_error"]
    assert [item["outcome"] for item in summary["items"]] == expected
    assert summary["summary"]["total"] == 2
    if defect in {"extra", "missing_close"}:
        assert summary["protocol_error"] and summary["summary"]["succeeded"] == 2
    assert "MALFORMED-SECRET" not in json.dumps(audit.record_call.call_args.kwargs)


@pytest.mark.parametrize("kind", ["URL_UPDATED", None])
@pytest.mark.parametrize("status", [403, 429, 503, 302, None])
def test_indexing_batch_outer_failure_safe_summary_and_no_retry(
    httpx_mock, project_id, status, kind, host_audit
):
    audit = host_audit
    if status is None:
        httpx_mock.add_exception(httpx.ReadTimeout("synthetic timeout"), url=BATCH_URL)
    else:
        httpx_mock.add_response(
            url=BATCH_URL,
            status_code=status,
            content=b"RAW-MIME-SECRET Content-ID: unsafe",
            headers={"Content-Type": "multipart/mixed; boundary=unsafe", "Retry-After": "17"},
        )
    with pytest.raises((IntegrationDownError, RateLimitedError, ConflictError)) as caught:
        run_batch(project_id, kind=kind, audit=audit)
    summary = caught.value.data["provider_error"]
    unknown = status is None or status >= 500
    assert [item["outcome"] for item in summary["items"]] == ["unknown" if unknown else "error"] * 2
    assert summary["summary"] == {
        "total": 2,
        "succeeded": 0,
        "failed": 0 if unknown else 2,
        "unknown": 2 if unknown else 0,
    }
    if status == 429:
        assert summary["retry_after"] == 17
        assert all(item["provider_error"]["retry_after"] == 17 for item in summary["items"])
    rendered = json.dumps({"error": caught.value.data, "audit": audit.record_call.call_args.kwargs})
    for secret in (TOKEN, "RAW-MIME-SECRET", "Content-ID", "HTTP/1.1"):
        assert secret not in rendered
    assert len(httpx_mock.get_requests()) == 1


def test_indexing_batch_inner_retry_advice_and_server_unknown(httpx_mock, project_id):
    body = multipart([(0, 429, {"error": {"code": 429}}), (1, 503, {"error": {"code": 503}})])
    body = body.replace(b"X-Request-Id:", b"Retry-After: 17\r\nX-Request-Id:")
    httpx_mock.add_response(
        url=BATCH_URL,
        content=body,
        headers={"Content-Type": "multipart/mixed; boundary=batch_reply"},
    )
    with pytest.raises((IntegrationDownError, ConflictError)) as caught:
        run_batch(project_id)
    items = caught.value.data["provider_error"]["items"]
    assert [item["outcome"] for item in items] == ["error", "unknown"]
    assert all(item["retry_after"] == item["provider_error"]["retry_after"] == 17 for item in items)
