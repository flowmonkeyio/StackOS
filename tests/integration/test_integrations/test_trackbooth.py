"""Trackbooth setup-probe wrapper tests."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.trackbooth import TrackboothIntegration
from stackos.mcp.errors import IntegrationDownError


def test_trackbooth_test_credentials_uses_production_default_api_url(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://apis.trackbooth.com/api/agent-api/catalog",
        json={"data": [{"operation_id": "LinksController.findAll"}]},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = TrackboothIntegration(
                payload=b'{"api_key":"tb-secret"}',
                project_id=project_id,
                http=client,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]

    assert result["ok"] is True
    assert result["vendor"] == "trackbooth"
    assert result["endpoint_count"] == 1
    assert result["api_base_url"] == "https://apis.trackbooth.com"
    assert request.headers["X-API-Key"] == "tb-secret"


def test_trackbooth_test_credentials_supports_custom_localhost_url(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://localhost:3030/api/agent-api/catalog",
        json={"data": []},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = TrackboothIntegration(
                payload=b"tb-secret",
                project_id=project_id,
                http=client,
                api_base_url="http://localhost:3030",
            )
            return await integration.test_credentials()

    result = asyncio.run(go())

    assert result["ok"] is True
    assert result["api_base_url"] == "http://localhost:3030"


def test_trackbooth_test_credentials_rejects_unsafe_custom_url_without_request(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = TrackboothIntegration(
                payload=b"tb-secret",
                project_id=project_id,
                http=client,
                api_base_url="http://10.0.0.1",
            )
            return await integration.test_credentials()

    with pytest.raises(IntegrationDownError):
        asyncio.run(go())

    assert httpx_mock.get_requests() == []
