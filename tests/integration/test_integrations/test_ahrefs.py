"""Ahrefs wrapper tests — optional API key with graceful degrade."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.ahrefs import AhrefsIntegration
from stackos.mcp.errors import IntegrationDownError


def test_keywords_for_site_with_key(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.ahrefs.com/v3/site-explorer/organic-keywords?"
            "target=example.com&country=us&limit=100"
            "&date=2025-01-01"
            "&select=keyword%2Cvolume%2Ccpc%2Cbest_position%2Ckeyword_difficulty"
        ),
        json={"keywords": [{"keyword": "x", "volume": 1000}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AhrefsIntegration(payload=b"ah-key", project_id=project_id, http=client)
            return await integ.keywords_for_site(target="example.com", date_="2025-01-01")

    result = asyncio.run(go())
    assert result.data["keywords"][0]["keyword"] == "x"


def test_top_backlinks_uses_v3_all_backlinks_endpoint(
    httpx_mock: HTTPXMock, project_id: int
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.ahrefs.com/v3/site-explorer/all-backlinks?"
            "target=example.com&mode=domain&limit=100"
            "&select=url_from%2Curl_to%2Cdomain_rating_source%2Cfirst_seen"
        ),
        json={"backlinks": [{"url_from": "https://source.example/a"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AhrefsIntegration(payload=b"ah-key", project_id=project_id, http=client)
            return await integ.top_backlinks(target="example.com")

    result = asyncio.run(go())
    assert result.data["backlinks"][0]["url_from"] == "https://source.example/a"


def test_test_credentials_without_key_raises_with_hint(project_id: int) -> None:
    """Empty payload → graceful optional-integration error pointing at docs."""

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AhrefsIntegration(payload=b"", project_id=project_id, http=client)
            return await integ.test_credentials()

    with pytest.raises(IntegrationDownError) as exc_info:
        asyncio.run(go())
    assert "Ahrefs API key is not configured" in exc_info.value.detail
    assert "docs/api-keys.md" in exc_info.value.data["hint"]
