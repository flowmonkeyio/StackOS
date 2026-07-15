"""MCP ``guide.gettingStarted`` canonical-source tests."""

from __future__ import annotations

from hashlib import sha256

from pytest_httpx import HTTPXMock

from stackos.mcp.tools.guides import (
    GETTING_STARTED_GUIDE_URL,
    GETTING_STARTED_MARKDOWN_URL,
)

from .conftest import MCPClient

GUIDE_MARKDOWN = """---
title: StackOS is installed. What happens next?
canonicalUrl: https://stackos.flowmonkey.io/getting-started
---

# Getting started

Open StackOS and choose one useful first job.
"""


def test_getting_started_guide_fetches_canonical_markdown(
    mcp_client: MCPClient,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=GETTING_STARTED_MARKDOWN_URL,
        text=GUIDE_MARKDOWN,
        headers={"Content-Type": "text/markdown; charset=utf-8"},
    )

    payload = mcp_client.call_tool_structured("guide.gettingStarted")

    assert payload["guide_url"] == GETTING_STARTED_GUIDE_URL
    assert payload["markdown_url"] == GETTING_STARTED_MARKDOWN_URL
    assert payload["status"] == "fetched"
    assert payload["content_available"] is True
    assert payload["content"] == GUIDE_MARKDOWN
    assert payload["size_bytes"] == len(GUIDE_MARKDOWN.encode())
    assert payload["sha256"] == sha256(GUIDE_MARKDOWN.encode()).hexdigest()
    request = httpx_mock.get_requests()[0]
    assert request.headers["accept"].startswith("text/markdown")
    assert request.headers["user-agent"].startswith("StackOS/")


def test_getting_started_guide_keeps_references_when_fetch_is_unavailable(
    mcp_client: MCPClient,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=GETTING_STARTED_MARKDOWN_URL, status_code=503)

    payload = mcp_client.call_tool_structured("guide.gettingStarted")

    assert payload["guide_url"] == GETTING_STARTED_GUIDE_URL
    assert payload["markdown_url"] == GETTING_STARTED_MARKDOWN_URL
    assert payload["status"] == "unavailable"
    assert payload["content_available"] is False
    assert payload["content"] is None
    assert payload["warnings"] == [
        "The public Markdown guide returned HTTP 503. Link the website guide and retry later."
    ]


def test_getting_started_guide_compact_mode_keeps_links_and_omits_content(
    mcp_client: MCPClient,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=GETTING_STARTED_MARKDOWN_URL, text=GUIDE_MARKDOWN)

    payload = mcp_client.call_tool_structured(
        "guide.gettingStarted",
        {"response_mode": "compact"},
    )
    data = payload["data"]

    assert data["guide_url"] == GETTING_STARTED_GUIDE_URL
    assert data["markdown_url"] == GETTING_STARTED_MARKDOWN_URL
    assert data["content_omitted"] is True
    assert "content" not in data
