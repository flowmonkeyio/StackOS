"""Serper.dev wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pytest_httpx import HTTPXMock
from stackos_connectors.connectors.serper.integration import SerperIntegration


def _json_body(request: httpx.Request) -> Any:
    return json.loads(request.content.decode("utf-8"))


def test_search_posts_google_search_payload(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://google.serper.dev/search",
        json={"organic": [{"title": "StackOS"}], "credits": 1},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integration = SerperIntegration(
                payload=b"serper-secret",
                http=client,
            )
            return await integration.search(
                query="stackos",
                num=3,
                country="us",
                language="en",
                page=2,
                tbs="qdr:w",
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]

    assert result.data["organic"][0]["title"] == "StackOS"
    assert _json_body(request) == {
        "q": "stackos",
        "num": 3,
        "gl": "us",
        "hl": "en",
        "page": 2,
        "tbs": "qdr:w",
    }
    assert request.headers["X-API-KEY"] == "serper-secret"


def test_credentials_probe_uses_minimal_search(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://google.serper.dev/search",
        json={"organic": [{"title": "StackOS"}], "credits": 42},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = SerperIntegration(
                payload=b"serper-secret",
                http=client,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]

    assert result == {
        "ok": True,
        "vendor": "serper",
        "credits": 42,
        "result_count": 1,
    }
    assert _json_body(request) == {"q": "stackos", "num": 1}
