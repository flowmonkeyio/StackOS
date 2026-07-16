"""Cloudflare v4 DNS wrapper contract tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from stackos.integrations.cloudflare import CloudflareIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError

_ZONE_ID = "023e105f4ecef8ad9ca31a8372d0c353"
_RECORD_ID = "372e67954025e0ba6aaa6d586b9e0b59"


def _envelope(result: Any, *, result_info: dict[str, int] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "success": True,
        "errors": [],
        "messages": [],
        "result": result,
    }
    if result_info is not None:
        body["result_info"] = result_info
    return body


def _run(
    handler: Callable[[httpx.Request], httpx.Response],
    operation: Callable[[CloudflareIntegration], Any],
) -> Any:
    async def go() -> Any:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            integration = CloudflareIntegration(
                payload=b"cloudflare-token",
                project_id=1,
                http=client,
            )
            return await operation(integration)

    return asyncio.run(go())


def test_reads_use_exact_paths_dotted_filters_pagination_and_bearer_auth() -> None:
    requests: list[httpx.Request] = []
    result_info = {
        "page": 2,
        "per_page": 25,
        "count": 1,
        "total_count": 1,
        "total_pages": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/client/v4/zones":
            body = _envelope([{"id": _ZONE_ID, "name": "example.org"}], result_info=result_info)
        elif request.url.path == f"/client/v4/zones/{_ZONE_ID}/dns_records":
            body = _envelope(
                [{"id": _RECORD_ID, "name": "www.example.org", "type": "A"}],
                result_info=result_info,
            )
        else:
            body = _envelope({"id": _RECORD_ID, "name": "www.example.org", "type": "A"})
        return httpx.Response(
            200,
            json=body,
            headers={
                "CF-Ray": "abc123-LAX",
                "Ratelimit": '"default";r=1199;t=299',
                "Ratelimit-Policy": '"default";q=1200;w=300',
            },
        )

    zone_result = _run(
        handler,
        lambda client: client.list_zones(
            params={
                "name": "contains:.org",
                "account.id": "account-id",
                "type": ["full", "partial"],
                "status": "active",
                "page": 2,
                "per_page": 25,
                "order": "account.name",
                "direction": "desc",
                "match": "all",
            }
        ),
    )
    records_result = _run(
        handler,
        lambda client: client.list_dns_records(
            zone_id=_ZONE_ID,
            params={
                "name.exact": "www.example.org",
                "content.contains": "198.51.100",
                "comment.absent": "comment",
                "tag.startswith": "owner:dns",
                "tag_match": "all",
                "include_shadow_metadata": True,
                "type": "A",
                "proxied": False,
                "page": 2,
                "per_page": 25,
            },
        ),
    )
    record_result = _run(
        handler,
        lambda client: client.get_dns_record(
            zone_id=_ZONE_ID,
            dns_record_id=_RECORD_ID,
            params={"include_shadow_metadata": False},
        ),
    )

    zone_request, records_request, record_request = requests
    assert zone_request.method == "GET"
    assert zone_request.url.path == "/client/v4/zones"
    assert dict(zone_request.url.params) == {
        "name": "contains:.org",
        "account.id": "account-id",
        "type": "full,partial",
        "status": "active",
        "page": "2",
        "per_page": "25",
        "order": "account.name",
        "direction": "desc",
        "match": "all",
    }
    assert records_request.method == "GET"
    assert records_request.url.path == f"/client/v4/zones/{_ZONE_ID}/dns_records"
    assert dict(records_request.url.params) == {
        "name.exact": "www.example.org",
        "content.contains": "198.51.100",
        "comment.absent": "comment",
        "tag.startswith": "owner:dns",
        "tag_match": "all",
        "include_shadow_metadata": "true",
        "type": "A",
        "proxied": "false",
        "page": "2",
        "per_page": "25",
    }
    assert record_request.method == "GET"
    assert record_request.url.path.endswith(f"/{_RECORD_ID}")
    assert dict(record_request.url.params) == {"include_shadow_metadata": "false"}
    assert all(
        request.headers["Authorization"] == "Bearer cloudflare-token" for request in requests
    )
    assert all(request.headers["Accept"] == "application/json" for request in requests)
    assert zone_result.data["result_info"] == result_info
    assert records_result.data["result_info"] == result_info
    assert record_result.data["result"]["id"] == _RECORD_ID
    assert zone_result.metadata == {
        "status_code": 200,
        "cf_ray": "abc123-LAX",
        "ratelimit": '"default";r=1199;t=299',
        "ratelimit_policy": '"default";q=1200;w=300',
    }


def test_mutations_use_exact_methods_bodies_and_never_retry() -> None:
    requests: list[httpx.Request] = []
    record = {
        "name": "www.example.org",
        "ttl": 3600,
        "type": "A",
        "content": "198.51.100.4",
        "proxied": True,
        "tags": ["owner:dns-team"],
    }

    def success_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200, json={"result": {"id": _RECORD_ID}})
        return httpx.Response(200, json=_envelope({"id": _RECORD_ID, **record}))

    _run(
        success_handler,
        lambda client: client.create_dns_record(
            zone_id=_ZONE_ID,
            record=record,
            params={"include_shadow_metadata": True},
        ),
    )
    _run(
        success_handler,
        lambda client: client.edit_dns_record(
            zone_id=_ZONE_ID,
            dns_record_id=_RECORD_ID,
            record={**record, "comment": "edited"},
            params={"include_shadow_metadata": False},
        ),
    )
    _run(
        success_handler,
        lambda client: client.replace_dns_record(
            zone_id=_ZONE_ID,
            dns_record_id=_RECORD_ID,
            record={**record, "content": "198.51.100.5"},
            params={"include_shadow_metadata": True},
        ),
    )
    deleted = _run(
        success_handler,
        lambda client: client.delete_dns_record(zone_id=_ZONE_ID, dns_record_id=_RECORD_ID),
    )

    assert [request.method for request in requests] == ["POST", "PATCH", "PUT", "DELETE"]
    assert requests[0].url.path == f"/client/v4/zones/{_ZONE_ID}/dns_records"
    assert all(
        request.url.path == f"/client/v4/zones/{_ZONE_ID}/dns_records/{_RECORD_ID}"
        for request in requests[1:]
    )
    assert json.loads(requests[0].content) == record
    assert json.loads(requests[1].content) == {**record, "comment": "edited"}
    assert json.loads(requests[2].content) == {**record, "content": "198.51.100.5"}
    assert requests[3].content == b""
    assert [dict(request.url.params) for request in requests] == [
        {"include_shadow_metadata": "true"},
        {"include_shadow_metadata": "false"},
        {"include_shadow_metadata": "true"},
        {},
    ]
    assert deleted.data == {"result": {"id": _RECORD_ID}}

    failed_requests: list[httpx.Request] = []

    def ambiguous_handler(request: httpx.Request) -> httpx.Response:
        failed_requests.append(request)
        return httpx.Response(
            503,
            json={
                "success": False,
                "errors": [{"code": 10000, "message": "temporarily unavailable"}],
                "messages": [],
                "result": None,
            },
            headers={"CF-Ray": "failed-ray-LAX"},
        )

    with pytest.raises(IntegrationDownError) as exc_info:
        _run(
            ambiguous_handler,
            lambda client: client.create_dns_record(zone_id=_ZONE_ID, record=record),
        )

    assert len(failed_requests) == 1
    assert exc_info.value.data["retry_safe"] is False
    assert exc_info.value.data["outcome_unknown"] is True
    assert exc_info.value.data["reconciliation_action"] == "utils.cloudflare.dns.records.list"
    assert exc_info.value.data["provider_error"]["cf_ray"] == "failed-ray-LAX"


def test_success_false_is_failure_and_auth_probe_claims_only_zone_read() -> None:
    calls = 0

    def failure_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": False,
                "errors": [
                    {
                        "code": 9109,
                        "message": "Invalid access token",
                        "documentation_url": "https://developers.cloudflare.com/",
                        "source": {"pointer": "/headers/authorization"},
                    }
                ],
                "messages": [],
                "result": None,
            },
            headers={"CF-Ray": "denied-ray-LAX"},
        )

    with pytest.raises(IntegrationDownError) as exc_info:
        _run(failure_handler, lambda client: client.list_zones(params={"page": 1, "per_page": 5}))
    assert exc_info.value.data["status"] == 200
    assert exc_info.value.data["provider_error"]["errors"][0]["code"] == 9109
    assert exc_info.value.data["provider_error"]["cf_ray"] == "denied-ray-LAX"

    def probe_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        assert request.url.path == "/client/v4/user/tokens/verify"
        assert dict(request.url.params) == {}
        return httpx.Response(
            200,
            json=_envelope({"id": "token-id", "status": "active"}),
        )

    result = _run(probe_handler, lambda client: client.test_credentials())
    assert calls == 1
    assert result == {
        "ok": True,
        "vendor": "cloudflare",
        "zone_read_verified": False,
        "dns_read_verified": False,
        "dns_write_verified": False,
    }
    assert "token-id" not in json.dumps(result)

    def disabled_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_envelope({"id": "token-id", "status": "disabled"}),
        )

    with pytest.raises(IntegrationDownError) as exc_info:
        _run(disabled_handler, lambda client: client.test_credentials())
    assert exc_info.value.data["reason_code"] == "token_not_active"
    assert exc_info.value.data["token_status"] == "disabled"
    assert "token-id" not in json.dumps(exc_info.value.to_dict())


@pytest.mark.parametrize(
    "operation",
    ["create", "edit", "replace", "delete"],
)
def test_every_mutation_issues_one_request_on_ambiguous_server_failure(
    operation: str,
) -> None:
    requests: list[httpx.Request] = []
    record = {
        "name": "www.example.org",
        "ttl": 3600,
        "type": "A",
        "content": "198.51.100.4",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            503,
            json={
                "success": False,
                "errors": [{"code": 10000, "message": "upstream unavailable"}],
                "messages": [],
                "result": None,
            },
        )

    def invoke(client: CloudflareIntegration) -> Any:
        if operation == "create":
            return client.create_dns_record(zone_id=_ZONE_ID, record=record)
        if operation == "edit":
            return client.edit_dns_record(
                zone_id=_ZONE_ID,
                dns_record_id=_RECORD_ID,
                record=record,
            )
        if operation == "replace":
            return client.replace_dns_record(
                zone_id=_ZONE_ID,
                dns_record_id=_RECORD_ID,
                record=record,
            )
        return client.delete_dns_record(zone_id=_ZONE_ID, dns_record_id=_RECORD_ID)

    with pytest.raises(IntegrationDownError) as exc_info:
        _run(handler, invoke)

    assert len(requests) == 1
    assert exc_info.value.data["automatic_retry_count"] == 0
    assert exc_info.value.data["retry_safe"] is False
    assert exc_info.value.data["outcome_unknown"] is True
    assert exc_info.value.data["reconciliation_action"] == "utils.cloudflare.dns.records.list"


def test_mutation_rate_limit_is_not_retried_and_is_a_known_rejection() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            429,
            json={
                "success": False,
                "errors": [{"code": 1015, "message": "rate limited"}],
                "messages": [],
                "result": None,
            },
            headers={"Retry-After": "17", "CF-Ray": "rate-ray-LAX"},
        )

    with pytest.raises(RateLimitedError) as exc_info:
        _run(
            handler,
            lambda client: client.create_dns_record(
                zone_id=_ZONE_ID,
                record={
                    "name": "www.example.org",
                    "ttl": 3600,
                    "type": "A",
                    "content": "198.51.100.4",
                },
            ),
        )

    assert len(requests) == 1
    assert exc_info.value.data["automatic_retry_count"] == 0
    assert exc_info.value.data["retry_safe"] is True
    assert exc_info.value.data["outcome_unknown"] is False
    assert exc_info.value.data["retry_after"] == 17
    assert exc_info.value.data["provider_error"]["cf_ray"] == "rate-ray-LAX"


def test_read_retries_transient_failures_then_preserves_success_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    async def no_wait(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("stackos.integrations._base.asyncio.sleep", no_wait)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(503, text="temporary")
        return httpx.Response(
            200,
            json=_envelope([], result_info={"page": 1, "per_page": 5, "count": 0}),
            headers={"CF-Ray": "success-ray-LAX"},
        )

    result = _run(handler, lambda client: client.list_zones(params={"page": 1, "per_page": 5}))

    assert len(requests) == 3
    assert sleeps == [0.5, 1.0]
    assert result.metadata["cf_ray"] == "success-ray-LAX"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(
                403,
                json={
                    "type": "https://developers.cloudflare.com/api/errors/permission-denied",
                    "title": "Permission denied",
                    "status": 403,
                    "detail": "The token lacks DNS Write.",
                },
                headers={"CF-Ray": "problem-ray-LAX"},
            ),
            "Permission denied",
        ),
        (httpx.Response(400, text="invalid DNS record body"), "invalid DNS record body"),
    ],
)
def test_problem_details_and_text_errors_remain_actionable(
    response: httpx.Response,
    expected: str,
) -> None:
    with pytest.raises(IntegrationDownError) as exc_info:
        _run(
            lambda request: httpx.Response(
                response.status_code,
                content=response.content,
                headers=response.headers,
                request=request,
            ),
            lambda client: client.get_dns_record(
                zone_id=_ZONE_ID,
                dns_record_id=_RECORD_ID,
            ),
        )

    assert expected in json.dumps(exc_info.value.data["provider_error"])


@pytest.mark.parametrize(
    "body",
    [
        {"errors": [], "messages": [], "result": {"id": _RECORD_ID}},
        {"success": True, "errors": [], "messages": [], "result": []},
    ],
)
def test_common_success_envelope_requires_success_and_expected_result_shape(
    body: dict[str, Any],
) -> None:
    with pytest.raises(IntegrationDownError):
        _run(
            lambda _request: httpx.Response(200, json=body),
            lambda client: client.get_dns_record(
                zone_id=_ZONE_ID,
                dns_record_id=_RECORD_ID,
            ),
        )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="unexpected text receipt"),
        httpx.Response(200, json={"result": {"id": _RECORD_ID}}),
        httpx.Response(200, json=_envelope([])),
    ],
)
def test_mutation_malformed_success_receipts_are_unknown_and_require_reconciliation(
    response: httpx.Response,
) -> None:
    record = {
        "name": "www.example.org",
        "ttl": 3600,
        "type": "A",
        "content": "198.51.100.4",
    }

    with pytest.raises(IntegrationDownError) as excinfo:
        _run(
            lambda _request: response,
            lambda client: client.create_dns_record(zone_id=_ZONE_ID, record=record),
        )

    assert excinfo.value.data["outcome_unknown"] is True
    assert excinfo.value.data["retry_safe"] is False
    assert excinfo.value.data["automatic_retry_count"] == 0
    assert excinfo.value.data["reconciliation_action"] == "utils.cloudflare.dns.records.list"
    assert "reconciliation_guidance" in excinfo.value.data


def test_mutation_transport_failure_is_not_retried_and_outcome_is_unknown() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise httpx.ConnectError("connection lost", request=request)

    record = {
        "name": "www.example.org",
        "ttl": 3600,
        "type": "A",
        "content": "198.51.100.4",
    }
    with pytest.raises(IntegrationDownError) as excinfo:
        _run(
            handler,
            lambda client: client.create_dns_record(zone_id=_ZONE_ID, record=record),
        )

    assert requests == 1
    assert excinfo.value.data["outcome_unknown"] is True
    assert excinfo.value.data["retry_safe"] is False
    assert excinfo.value.data["reconciliation_action"] == "utils.cloudflare.dns.records.list"
