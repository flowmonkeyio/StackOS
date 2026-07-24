"""Linear fixed-document GraphQL transport tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.auth_providers.repository.schema import AuthMethodProbeContext
from stackos.integrations.linear import (
    LINEAR_GRAPHQL_ENDPOINT,
    LINEAR_MAX_REQUEST_BYTES,
    LINEAR_MAX_RESPONSE_BYTES,
    LinearIntegration,
)
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError


async def _execute(
    project_id: int,
    *,
    document_path: str = "graphql/viewer/get.graphql",
    variables: dict[str, Any] | None = None,
    write: bool = False,
) -> Any:
    async with httpx.AsyncClient() as client:
        integration = LinearIntegration(
            payload=json.dumps({"access_token": "linear-access"}).encode(),
            project_id=project_id,
            http=client,
            auth_method_key="oauth2_authorization_code",
        )
        return await integration.execute_document(
            document_path=document_path,
            variables=variables,
            op="test.linear",
            write=write,
        )


async def _execute_with_method(
    project_id: int,
    *,
    payload: bytes,
    auth_method_key: str,
    probe_context: AuthMethodProbeContext | None = None,
) -> Any:
    async with httpx.AsyncClient() as client:
        integration = LinearIntegration(
            payload=payload,
            project_id=project_id,
            http=client,
            auth_method_key=auth_method_key,
            probe_context=probe_context,
        )
        return await integration.execute_document(
            document_path="graphql/viewer/get.graphql",
            op="test.linear.auth-method",
        )


def test_linear_executes_only_fixed_endpoint_documents_and_captures_rate_metadata(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        json={
            "data": {
                "viewer": {
                    "id": "user-1",
                    "name": "Ada",
                    "organization": {"id": "org-1", "name": "Example"},
                }
            }
        },
        headers={
            "x-request-id": "linear-request-1",
            "x-ratelimit-requests-limit": "1500",
            "x-ratelimit-requests-remaining": "1499",
            "x-ratelimit-requests-reset": "42",
            "x-ratelimit-complexity-limit": "250000",
            "x-ratelimit-complexity-remaining": "249995",
            "x-ratelimit-complexity-reset": "43",
        },
    )

    result = asyncio.run(_execute(project_id))
    request = httpx_mock.get_requests()[0]
    request_body = json.loads(request.content)

    assert request.headers["Authorization"] == "Bearer linear-access"
    assert request_body["query"].startswith("query LinearViewerGet")
    assert result.data["data"]["viewer"]["id"] == "user-1"
    assert result.metadata == {
        "endpoint": LINEAR_GRAPHQL_ENDPOINT,
        "request_id": "linear-request-1",
        "rate_limit": "1500",
        "rate_remaining": "1499",
        "rate_reset": "42",
        "complexity_limit": "250000",
        "complexity_remaining": "249995",
        "complexity_reset": "43",
    }


def test_linear_personal_api_key_uses_raw_authorization_value(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        json={
            "data": {
                "viewer": {
                    "id": "user-1",
                    "name": "Ada",
                    "organization": {"id": "org-1", "name": "Example"},
                }
            }
        },
    )

    asyncio.run(
        _execute_with_method(
            project_id,
            payload=b"linear-personal-key-sentinel",
            auth_method_key="personal_api_key",
        )
    )

    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "linear-personal-key-sentinel"
    assert request.headers["Authorization"] != "Bearer linear-personal-key-sentinel"


@pytest.mark.parametrize(
    ("auth_method_key", "probe_context"),
    [
        ("", None),
        ("unknown-linear-method", None),
        (
            "personal_api_key",
            AuthMethodProbeContext(auth_method_key="oauth2_authorization_code"),
        ),
    ],
)
def test_linear_rejects_unknown_or_mismatched_auth_method_before_dispatch(
    httpx_mock: HTTPXMock,
    project_id: int,
    auth_method_key: str,
    probe_context: AuthMethodProbeContext | None,
) -> None:
    with pytest.raises(IntegrationDownError):
        asyncio.run(
            _execute_with_method(
                project_id,
                payload=b"linear-personal-key-sentinel",
                auth_method_key=auth_method_key,
                probe_context=probe_context,
            )
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize(
    "document_path",
    [
        "../linear.graphql",
        "/tmp/linear.graphql",
        "graphql/../schema/introspection-2026-07-23.json",
        "schema/introspection-2026-07-23.json",
    ],
)
def test_linear_rejects_non_fixed_document_paths_without_request(
    httpx_mock: HTTPXMock,
    project_id: int,
    document_path: str,
) -> None:
    with pytest.raises(ValidationError):
        asyncio.run(_execute(project_id, document_path=document_path))

    assert httpx_mock.get_requests() == []


def test_linear_rejects_oversized_request_before_dispatch(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    with pytest.raises(ValidationError) as exc:
        asyncio.run(
            _execute(
                project_id,
                variables={"term": "x" * LINEAR_MAX_REQUEST_BYTES},
            )
        )

    assert exc.value.data["reason_code"] == "request_too_large"
    assert httpx_mock.get_requests() == []


def test_linear_http_200_graphql_errors_fail_even_with_partial_data(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        json={
            "data": {"viewer": {"id": "partial"}},
            "errors": [
                {
                    "message": "Provider-controlled detail",
                    "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
                }
            ],
        },
    )

    with pytest.raises(IntegrationDownError) as exc:
        asyncio.run(_execute(project_id))

    assert exc.value.data["reason_code"] == "graphql_error"
    assert exc.value.data["partial_data"] is True
    assert exc.value.data["outcome_unknown"] is False
    assert exc.value.data["provider_error"]["errors"][0]["extensions"]["code"] == (
        "GRAPHQL_VALIDATION_FAILED"
    )


def test_linear_success_false_is_a_definitive_provider_failure(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        json={
            "data": {
                "issueUpdate": {
                    "success": False,
                    "issue": None,
                }
            }
        },
    )

    with pytest.raises(IntegrationDownError) as exc:
        asyncio.run(
            _execute(
                project_id,
                document_path="graphql/issues/update.graphql",
                variables={"id": "issue-1", "input": {"title": "Updated"}},
                write=True,
            )
        )

    assert exc.value.data["reason_code"] == "provider_unsuccessful"
    assert exc.value.data["outcome_unknown"] is False


def test_linear_http_400_ratelimited_is_typed_and_preserves_safe_quota_context(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        status_code=400,
        json={
            "errors": [
                {
                    "message": "Rate limit exceeded",
                    "extensions": {
                        "code": "RATELIMITED",
                        "type": "COMPLEXITY",
                        "limit": 250000,
                        "remaining": 0,
                        "reset": 1720000000,
                        "requested": 300,
                    },
                }
            ]
        },
    )

    with pytest.raises(RateLimitedError) as exc:
        asyncio.run(_execute(project_id))

    assert exc.value.data["reason_code"] == "graphql_ratelimited"
    assert exc.value.data["status"] == 400
    assert exc.value.data["rate_limit"] == {
        "code": "RATELIMITED",
        "type": "COMPLEXITY",
        "limit": 250000,
        "remaining": 0,
        "reset": 1720000000,
        "requested": 300,
    }


@pytest.mark.parametrize(
    ("status_code", "reason_code"),
    [(401, "authentication_failed"), (403, "authorization_failed")],
)
def test_linear_auth_http_failures_are_classified_without_provider_text(
    httpx_mock: HTTPXMock,
    project_id: int,
    status_code: int,
    reason_code: str,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        status_code=status_code,
        text="provider detail containing linear-access",
    )

    with pytest.raises(IntegrationDownError) as exc:
        asyncio.run(_execute(project_id))

    assert exc.value.data["reason_code"] == reason_code
    assert exc.value.data["retryable"] is False
    assert "linear-access" not in exc.value.detail
    assert "linear-access" not in json.dumps(exc.value.data)


def test_linear_http_429_uses_existing_typed_rate_limit_path(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        status_code=429,
        headers={"Retry-After": "17"},
        json={"errors": [{"message": "slow down"}]},
    )

    with pytest.raises(RateLimitedError) as exc:
        asyncio.run(_execute(project_id, write=True))

    assert exc.value.data["status"] == 429
    assert exc.value.data["retry_after"] == 17.0


def test_linear_write_timeout_is_not_retried_and_marks_unknown_outcome(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("linear write timed out"))

    with pytest.raises(IntegrationDownError) as exc:
        asyncio.run(
            _execute(
                project_id,
                document_path="graphql/issues/update.graphql",
                variables={"id": "issue-1", "input": {"title": "Updated"}},
                write=True,
            )
        )

    assert len(httpx_mock.get_requests()) == 1
    assert exc.value.data["reason_code"] == "transport_failure"
    assert exc.value.data["outcome_unknown"] is True


def test_linear_malformed_and_oversized_responses_are_classified(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        text="not-json",
    )
    with pytest.raises(IntegrationDownError) as malformed:
        asyncio.run(_execute(project_id))
    assert malformed.value.data["reason_code"] == "malformed_response"

    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        content=b"x" * (LINEAR_MAX_RESPONSE_BYTES + 1),
    )
    with pytest.raises(IntegrationDownError) as oversized:
        asyncio.run(_execute(project_id))
    assert oversized.value.data["reason_code"] == "response_too_large"


def test_linear_oversized_write_response_marks_the_outcome_unknown(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=LINEAR_GRAPHQL_ENDPOINT,
        content=b"x" * (LINEAR_MAX_RESPONSE_BYTES + 1),
    )

    with pytest.raises(IntegrationDownError) as exc:
        asyncio.run(
            _execute(
                project_id,
                document_path="graphql/issues/update.graphql",
                variables={"id": "issue-1", "input": {"title": "Updated"}},
                write=True,
            )
        )

    assert exc.value.data["reason_code"] == "response_too_large"
    assert exc.value.data["outcome_unknown"] is True
