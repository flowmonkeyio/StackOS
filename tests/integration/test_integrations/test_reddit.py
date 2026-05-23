"""Reddit wrapper tests — token grant + search/top calls."""

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
            "client_id": "cid",
            "client_secret": "csecret",
            "user_agent": "tester/1.0",
        }
    ).encode("utf-8")


def test_search_subreddit_grants_then_searches(httpx_mock: HTTPXMock, project_id: int) -> None:
    """First request grants the app-only token; second hits the search endpoint."""
    httpx_mock.add_response(
        method="POST",
        url="https://www.reddit.com/api/v1/access_token",
        json={"access_token": "ya29.tok", "expires_in": 3600},
    )
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
    assert result.data["data"]["children"][0]["data"]["title"] == "ELI5 async"


def test_test_credentials_grants_token(httpx_mock: HTTPXMock, project_id: int) -> None:
    """``test_credentials`` succeeds once the token grant returns 200."""
    httpx_mock.add_response(
        method="POST",
        url="https://www.reddit.com/api/v1/access_token",
        json={"access_token": "ya29.tok", "expires_in": 3600},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = RedditIntegration(payload=_payload(), project_id=project_id, http=client)
            return await integ.test_credentials()

    out = asyncio.run(go())
    assert out["ok"] is True
    assert out["vendor"] == "reddit"
