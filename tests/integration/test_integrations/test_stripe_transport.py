"""Stripe's documented retry and idempotency-conflict transport contract."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import httpcore
import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.actions.stripe import _connector_error
from stackos.integrations._base import BaseIntegration
from stackos.integrations.stripe import (
    STRIPE_API_VERSION,
    StripeIntegration,
    _safe_provider_error,
    parse_stripe_api_key_payload,
)
from stackos.mcp.errors import IntegrationDownError, RateLimitedError

STRIPE_ROOT = "https://api.stripe.com/v1"


@pytest.mark.parametrize("unsafe", ["\n", "\r", "\0", "\x7f", "\u00e9", "\t", " "])
def test_stripe_rejects_header_unsafe_key_before_real_h11_or_shared_logging(unsafe: str) -> None:
    """Use real HTTPX/httpcore/h11 serialization, with an in-memory network only."""
    synthetic_key = f"synthetic-private-key{unsafe}suffix"

    async def probe() -> None:
        transport = httpx.AsyncHTTPTransport()
        await transport._pool.aclose()
        body = b'{"object":"account","id":"acct_fixture"}'
        transport._pool = httpcore.AsyncConnectionPool(
            network_backend=httpcore.AsyncMockBackend(
                [
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                ]
            )
        )
        async with httpx.AsyncClient(transport=transport) as http:
            with (
                patch("stackos.integrations._base._log") as log,
                patch.object(
                    transport, "handle_async_request", wraps=transport.handle_async_request
                ) as dispatch,
            ):
                failure: Exception | None = None
                try:
                    integration = StripeIntegration(
                        payload=json.dumps({"api_key": synthetic_key}).encode(),
                        project_id=1,
                        http=http,
                        auth_method_key="api_key",
                    )
                    await integration.test_credentials()
                except Exception as exc:
                    failure = exc
            assert isinstance(failure, IntegrationDownError)
            assert failure.data == {"vendor": "stripe", "reason_code": "invalid_credential"}
            assert dispatch.call_count == 0
            assert log.warning.call_count == 0
            assert "synthetic-private-key" not in str(failure)

    asyncio.run(probe())


@pytest.mark.parametrize(
    "key", ["sk_test_fixture", "rk_live_fixture", "opaque-provider-key_+/=.-", "  fixture-key\n"]
)
def test_stripe_header_safe_key_keeps_trim_and_has_no_prefix_restriction(key: str) -> None:
    assert parse_stripe_api_key_payload(json.dumps({"api_key": key}).encode()) == key.strip()

    async def probe() -> dict[str, Any]:
        transport = httpx.AsyncHTTPTransport()
        await transport._pool.aclose()
        body = b'{"object":"account","id":"acct_fixture"}'
        transport._pool = httpcore.AsyncConnectionPool(
            network_backend=httpcore.AsyncMockBackend(
                [
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                ]
            )
        )
        async with httpx.AsyncClient(transport=transport) as http:
            integration = StripeIntegration(
                payload=json.dumps({"api_key": key}).encode(),
                project_id=1,
                http=http,
                auth_method_key="api_key",
            )
            return await integration.test_credentials()

    assert asyncio.run(probe())["ok"] is True


async def _request(*, method: str = "GET", probe: bool = False) -> Any:
    async with httpx.AsyncClient() as http:
        integration = StripeIntegration(
            payload=b'{"api_key":"fixture-only-not-a-stripe-key"}',
            project_id=1,
            http=http,
            auth_method_key="api_key",
        )
        if probe:
            return await integration.test_credentials()
        return await integration.request(
            method=method,
            path="/customers",
            op="transport.contract-test",
            form={"description": "fixture"} if method == "POST" else None,
            idempotency_key="transport-fixture-original-key" if method == "POST" else None,
        )


@pytest.mark.parametrize("status", [200, 404])
@pytest.mark.parametrize("response_version", [None, "2025-10-29.clover"])
def test_stripe_transport_diagnostics_distinguish_sent_and_returned_versions(
    httpx_mock: HTTPXMock, status: int, response_version: str | None
) -> None:
    headers = {"Request-Id": "req_version_fixture"}
    if response_version is not None:
        headers["Stripe-Version"] = response_version
    httpx_mock.add_response(
        url=f"{STRIPE_ROOT}/customers",
        status_code=status,
        headers=headers,
        json={"object": "list", "data": []}
        if status == 200
        else {"error": {"type": "invalid_request_error"}},
    )
    if status == 200:
        result = asyncio.run(_request())
        diagnostics = result.metadata
        assert diagnostics["api_version"] == STRIPE_API_VERSION
    else:
        with pytest.raises(IntegrationDownError) as failure:
            asyncio.run(_request())
        diagnostics = failure.value.data["provider_error"]
    assert diagnostics["request_method"] == "GET"
    assert diagnostics["request_path"] == "/v1/customers"
    assert (
        diagnostics["request_api_version"] == httpx_mock.get_requests()[0].headers["Stripe-Version"]
    )
    assert diagnostics["response_api_version"] == response_version
    assert diagnostics["request_id"] == "req_version_fixture"


@pytest.mark.parametrize("request_version", [None, "2020-08-27", "2025-10-29.clover"])
def test_stripe_transport_diagnostics_never_substitute_configured_request_version(
    request_version: str | None,
) -> None:
    response = httpx.Response(
        404,
        json={"error": {"type": "invalid_request_error"}},
        request=httpx.Request(
            "POST",
            f"{STRIPE_ROOT}/payment_records/report_payment",
            headers={"Stripe-Version": request_version} if request_version else {},
        ),
    )
    diagnostics = StripeIntegration._provider_error(response)
    assert diagnostics["request_api_version"] == request_version
    assert diagnostics["response_api_version"] is None
    assert diagnostics["request_method"] == "POST"
    assert diagnostics["request_path"] == "/v1/payment_records/report_payment"
    assert _safe_provider_error(diagnostics) == diagnostics


@pytest.mark.parametrize(
    "unsafe_version",
    [
        "sk_test_private",
        "2026-08-26.dahlia; secret=private",
        "2026-08-26.privatecustomer",
        "2026-08-26.dahlia\nprivate",
        "2026-08-26.dahlia " + "private" * 100,
    ],
)
def test_stripe_transport_diagnostics_withhold_unsafe_headers(unsafe_version: str) -> None:
    response = httpx.Response(
        404,
        headers={"Stripe-Version": unsafe_version, "X-Private": "private-response-header"},
        json={"error": {"type": "invalid_request_error"}},
        request=httpx.Request(
            "GET",
            f"{STRIPE_ROOT}/payment_records?customer=private-customer",
            headers={"Stripe-Version": unsafe_version, "Authorization": "Bearer sk_test_private"},
        ),
    )
    diagnostics = StripeIntegration._provider_error(response)
    assert diagnostics["request_api_version"] is None
    assert diagnostics["response_api_version"] is None
    assert diagnostics["request_path"] == "/v1/payment_records"
    rendered = json.dumps(diagnostics)
    for withheld in (
        unsafe_version,
        "sk_test_private",
        "private-customer",
        "private-response-header",
    ):
        assert withheld not in rendered
    assert _safe_provider_error(diagnostics) == diagnostics


def test_stripe_transport_diagnostics_ignore_response_body_claims_and_unknown_method() -> None:
    response = httpx.Response(
        404,
        json={
            "error": {"type": "invalid_request_error"},
            "request_api_version": STRIPE_API_VERSION,
            "response_api_version": STRIPE_API_VERSION,
            "request_method": "GET",
            "request_path": "/v1/customers",
        },
        request=httpx.Request("PRIVATE", f"{STRIPE_ROOT}/privatecustomer"),
    )
    diagnostics = StripeIntegration._provider_error(response)
    for key in ("request_method", "request_path", "request_api_version", "response_api_version"):
        assert diagnostics[key] is None
    assert "PRIVATE" not in json.dumps(diagnostics)
    assert _safe_provider_error(diagnostics) == diagnostics


@pytest.mark.parametrize(
    "path",
    [
        "/customers/cus_private",
        "/privatecustomer",
        "/sk_test_private",
        "/payment_records/pr_private/report_refund",
        "/payment_%72ecords",
    ],
)
def test_stripe_transport_diagnostics_withhold_nonstatic_paths(path: str) -> None:
    response = httpx.Response(
        404,
        json={"error": {"message": f"Unrecognized request URL (GET: /v1{path})."}},
        request=httpx.Request("GET", f"{STRIPE_ROOT}{path}?email=private-email"),
    )
    diagnostics = StripeIntegration._provider_error(response)
    assert diagnostics["request_path"] is None
    assert diagnostics["request_method"] == "GET"
    assert diagnostics["message"] == "Unrecognized request URL."
    assert "private" not in json.dumps(diagnostics)
    assert _safe_provider_error(diagnostics) == diagnostics


@pytest.mark.parametrize("status", [429, 500])
def test_stripe_read_honors_do_not_retry_before_another_dispatch(
    httpx_mock: HTTPXMock, status: int
) -> None:
    httpx_mock.add_response(
        url=f"{STRIPE_ROOT}/customers",
        status_code=status,
        headers={"Stripe-Should-Retry": "false", "Request-Id": "req_transport_fixture"},
        json={"error": {"type": "api_error"}},
        is_reusable=True,
    )
    error_type = RateLimitedError if status == 429 else IntegrationDownError
    with pytest.raises(error_type) as failure:
        asyncio.run(_request())
    assert len(httpx_mock.get_requests()) == 1
    assert failure.value.data["provider_error"]["should_retry"] is False
    assert failure.value.data["outcome_unknown"] is False
    # GET remains side-effect-safe, even when another retry would not help.
    assert failure.value.data["retry_safe"] is True


def test_stripe_read_retry_header_can_make_a_conflict_retryable(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{STRIPE_ROOT}/customers",
        status_code=409,
        headers={"Stripe-Should-Retry": "true"},
        json={"error": {"type": "invalid_request_error"}},
    )
    httpx_mock.add_response(
        url=f"{STRIPE_ROOT}/customers", json={"object": "list", "data": []}, is_optional=True
    )
    result = asyncio.run(_request())
    assert result.data == {"object": "list", "data": []}
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize("retry_header", [None, "not-a-boolean"])
def test_stripe_read_without_a_valid_override_keeps_default_retries(
    httpx_mock: HTTPXMock, retry_header: str | None
) -> None:
    httpx_mock.add_response(
        url=f"{STRIPE_ROOT}/customers",
        status_code=500,
        headers={"Stripe-Should-Retry": retry_header} if retry_header else {},
        json={"error": {"type": "api_error"}},
    )
    httpx_mock.add_response(url=f"{STRIPE_ROOT}/customers", json={"object": "list", "data": []})
    asyncio.run(_request())
    assert len(httpx_mock.get_requests()) == 2


def test_stripe_probe_does_not_recommend_a_retry_the_provider_rejected(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=f"{STRIPE_ROOT}/account",
        status_code=500,
        headers={"Stripe-Should-Retry": "false"},
        json={"error": {"type": "api_error"}},
        is_reusable=True,
    )
    result = asyncio.run(_request(probe=True))
    assert result["ok"] is False
    assert result["retryable"] is False
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    ("status", "error", "reason_code"),
    [
        (409, {"type": "invalid_request_error"}, "idempotency_conflict"),
        (
            400,
            {"type": "invalid_request_error", "code": "idempotency_key_in_use"},
            "idempotency_conflict",
        ),
        (400, {"type": "idempotency_error"}, "idempotency_mismatch"),
    ],
)
def test_stripe_idempotency_rejection_preserves_original_operation_uncertainty(
    httpx_mock: HTTPXMock, status: int, error: dict[str, str], reason_code: str
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        status_code=status,
        headers={"Stripe-Should-Retry": "true", "Request-Id": "req_transport_fixture"},
        json={"error": error},
    )
    with pytest.raises(IntegrationDownError) as failure:
        asyncio.run(_request(method="POST"))
    assert len(httpx_mock.get_requests()) == 1
    assert failure.value.data["outcome_unknown"] is True
    assert failure.value.data["retry_safe"] is False
    provider_error = failure.value.data["provider_error"]
    assert provider_error["reason_code"] == reason_code
    assert provider_error["request_id"] == "req_transport_fixture"
    recovery = failure.value.data["recovery"]
    assert "original" in recovery
    assert "reconcile" in recovery.lower()
    assert "Never use a new key" in recovery
    assert "fresh idempotency key" not in recovery
    action_error = _connector_error(failure.value)
    assert action_error.provider_error["outcome_unknown"] is True
    assert action_error.provider_error["retry_safe"] is False
    assert action_error.provider_error["recovery"] == recovery


@pytest.mark.parametrize("status", [429, 500])
def test_stripe_provider_retry_advice_never_automatically_repeats_a_post(
    httpx_mock: HTTPXMock, status: int
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        status_code=status,
        headers={"Stripe-Should-Retry": "true"},
        json={"error": {"type": "api_error"}},
    )
    error_type = RateLimitedError if status == 429 else IntegrationDownError
    with pytest.raises(error_type):
        asyncio.run(_request(method="POST"))
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].headers["Idempotency-Key"] == "transport-fixture-original-key"


def test_stripe_correctable_content_error_keeps_existing_recovery(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{STRIPE_ROOT}/customers",
        status_code=400,
        json={"error": {"type": "invalid_request_error", "code": "parameter_missing"}},
    )
    with pytest.raises(IntegrationDownError) as failure:
        asyncio.run(_request(method="POST"))
    assert failure.value.data["outcome_unknown"] is False
    assert "fresh idempotency key" in failure.value.data["recovery"]


@pytest.mark.parametrize("status", [429, 500])
def test_default_transport_does_not_apply_a_stripe_specific_header(
    httpx_mock: HTTPXMock, status: int
) -> None:
    httpx_mock.add_response(
        url="https://fixture.invalid/read",
        status_code=status,
        headers={"Stripe-Should-Retry": "false"},
    )
    httpx_mock.add_response(url="https://fixture.invalid/read", json={"ok": True})

    async def read() -> httpx.Response:
        async with httpx.AsyncClient() as http:
            integration = BaseIntegration(payload=b"", project_id=1, http=http, qps_override=25)
            return await integration._request_with_retry(
                "GET", "https://fixture.invalid/read", op="default-policy", max_retries=1
            )

    assert asyncio.run(read()).json() == {"ok": True}
    assert len(httpx_mock.get_requests()) == 2
