"""Firecrawl wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.firecrawl import FirecrawlIntegration
from stackos.mcp.errors import IntegrationDownError


def _json_body(request: httpx.Request) -> Any:
    return json.loads(request.content.decode("utf-8"))


def test_scrape_returns_markdown(httpx_mock: HTTPXMock, project_id: int) -> None:
    """Happy-path scrape decodes the markdown payload."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# Hello", "url": "https://example.com"}},
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = FirecrawlIntegration(
                payload=b"fc-key",
                project_id=project_id,
                http=client,
            )
            return await integ.scrape(url="https://example.com")

    result = asyncio.run(go())
    assert "Hello" in str(result.data)
    assert _json_body(httpx_mock.get_requests()[0]) == {
        "url": "https://example.com",
        "formats": ["markdown"],
        "onlyMainContent": True,
    }


def test_5xx_retries_then_raises_integration_down(httpx_mock: HTTPXMock, project_id: int) -> None:
    """Three 503s exhaust retries and raise ``IntegrationDownError``."""
    for _ in range(4):
        httpx_mock.add_response(
            method="POST",
            url="https://api.firecrawl.dev/v2/scrape",
            status_code=503,
        )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = FirecrawlIntegration(
                payload=b"fc-key",
                project_id=project_id,
                http=client,
            )
            return await integ.scrape(url="https://example.com")

    with pytest.raises(IntegrationDownError):
        asyncio.run(go())
    assert len(httpx_mock.get_requests()) == 4


def test_test_credentials_calls_scrape(httpx_mock: HTTPXMock, project_id: int) -> None:
    """``test_credentials`` issues a scrape against ``example.com``."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "ok"}},
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = FirecrawlIntegration(
                payload=b"fc-key",
                project_id=project_id,
                http=client,
            )
            return await integ.test_credentials()

    out = asyncio.run(go())
    assert out["ok"] is True
    assert out["vendor"] == "firecrawl"


def test_4xx_other_than_429_surfaces_immediately(httpx_mock: HTTPXMock, project_id: int) -> None:
    """A 401 (auth failure) raises ``IntegrationDownError`` after one call."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        status_code=401,
        json={"error": "unauthorized"},
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = FirecrawlIntegration(
                payload=b"fc-key",
                project_id=project_id,
                http=client,
            )
            return await integ.scrape(url="https://example.com")

    with pytest.raises(IntegrationDownError):
        asyncio.run(go())
    assert len(httpx_mock.get_requests()) == 1


def test_crawl_uses_v2_discovery_depth(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/crawl",
        json={"success": True, "id": "crawl-1"},
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = FirecrawlIntegration(payload=b"fc-key", project_id=project_id, http=client)
            return await integ.crawl(url="https://example.com", max_depth=3, limit=10)

    asyncio.run(go())
    body = _json_body(httpx_mock.get_requests()[0])
    assert body["maxDiscoveryDepth"] == 3
    assert "maxDepth" not in body


def test_extract_posts_urls_array(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/extract",
        json={"success": True, "id": "extract-1"},
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = FirecrawlIntegration(payload=b"fc-key", project_id=project_id, http=client)
            return await integ.extract(
                url="https://example.com",
                schema={"type": "object"},
                prompt="Extract facts",
            )

    asyncio.run(go())
    assert _json_body(httpx_mock.get_requests()[0]) == {
        "urls": ["https://example.com"],
        "schema": {"type": "object"},
        "prompt": "Extract facts",
    }
