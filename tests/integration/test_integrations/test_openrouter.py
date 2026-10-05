"""OpenRouter setup-probe wrapper tests."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pytest_httpx import HTTPXMock
from stackos_connectors.connectors.openrouter.integration import OpenRouterIntegration


def test_models_uses_bearer_and_attribution_headers(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://openrouter.ai/api/v1/models",
        json={"data": [{"id": "openai/gpt-4.1"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integration = OpenRouterIntegration(
                payload=b"or-secret",
                http=client,
                http_referer="https://stackos.local",
                app_title="StackOS",
            )
            return await integration.models()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]

    assert result.data["data"][0]["id"] == "openai/gpt-4.1"
    assert request.headers["Authorization"] == "Bearer or-secret"
    assert request.headers["HTTP-Referer"] == "https://stackos.local"
    assert request.headers["X-OpenRouter-Title"] == "StackOS"


def test_credentials_probe_uses_models_endpoint(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://openrouter.ai/api/v1/models",
        json={"data": [{"id": "anthropic/claude-3.5-sonnet"}]},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = OpenRouterIntegration(
                payload=b"or-secret",
                http=client,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())

    assert result == {"ok": True, "vendor": "openrouter", "models_count": 1}
