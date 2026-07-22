"""Reddit wrapper tests for access tokens resolved by the auth core."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pytest_httpx import HTTPXMock

from stackos.integrations.reddit import RedditIntegration


def _payload() -> bytes:
    return json.dumps(
        {
            "access_token": "reddit-access",
            "user_agent": "tester/1.0",
        }
    ).encode("utf-8")


def test_search_subreddit_uses_resolved_access_token(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://oauth.reddit.com/r/python/search?q=async&restrict_sr=true"
            "&sort=relevance&limit=25"
        ),
        json={"data": {"children": [{"data": {"title": "ELI5 async"}}]}},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = RedditIntegration(payload=_payload(), project_id=project_id, http=client)
            return await integ.search_subreddit(subreddit="python", query="async")

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert result.data["data"]["children"][0]["data"]["title"] == "ELI5 async"
    assert request.headers["Authorization"] == "Bearer reddit-access"
    assert request.headers["User-Agent"] == "tester/1.0"


def test_test_credentials_uses_resolved_token_without_grant(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = RedditIntegration(payload=_payload(), project_id=project_id, http=client)
            return await integ.test_credentials()

    out = asyncio.run(go())
    assert out["ok"] is True
    assert out["vendor"] == "reddit"
    assert httpx_mock.get_requests() == []
