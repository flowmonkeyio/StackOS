"""Jina Reader wrapper tests."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pytest_httpx import HTTPXMock

from stackos.integrations.jina_reader import JinaReaderIntegration


def test_read_returns_markdown(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://r.jina.ai/https://example.com",
        text="# Example\n\nContent",
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = JinaReaderIntegration(payload=b"jina-key", project_id=project_id, http=client)
            return await integ.read(url="https://example.com")

    result = asyncio.run(go())
    assert "Example" in str(result.data)


def test_read_works_without_api_key(httpx_mock: HTTPXMock, project_id: int) -> None:
    """Empty payload → no Authorization header but call succeeds."""
    httpx_mock.add_response(
        method="GET",
        url="https://r.jina.ai/https://example.com",
        text="ok",
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = JinaReaderIntegration(payload=b"", project_id=project_id, http=client)
            return await integ.read(url="https://example.com")

    result = asyncio.run(go())
    assert "ok" in str(result.data)
    # Verify the Authorization header was *not* set on the public-tier call.
    request = httpx_mock.get_requests()[0]
    assert "authorization" not in {h.lower() for h in request.headers}
