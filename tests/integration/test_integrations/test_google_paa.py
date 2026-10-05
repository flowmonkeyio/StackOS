"""Google PAA wrapper tests — delegates to Firecrawl."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pytest_httpx import HTTPXMock
from stackos_connectors.connectors.firecrawl.integration import FirecrawlIntegration

from stackos.integrations.google_paa import GooglePaaIntegration
from stackos.mcp.errors import IntegrationDownError


def test_extract_questions_from_serp_markdown(httpx_mock: HTTPXMock, project_id: int) -> None:
    """``extract`` scrapes Google SERP via Firecrawl and parses PAA questions."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={
            "data": {
                "markdown": (
                    "## People also ask\n"
                    "- What is content marketing?\n"
                    "- How does SEO work?\n"
                    "- Why is keyword research important?\n"
                ),
            }
        },
    )

    async def go() -> dict:
        async with httpx.AsyncClient() as client:
            firecrawl = FirecrawlIntegration(payload=b"fc", http=client)
            paa = GooglePaaIntegration(
                firecrawl=firecrawl,
            )
            return await paa.extract(query="content marketing")

    out = asyncio.run(go())
    assert "What is content marketing?" in out["questions"]
    assert len(out["questions"]) >= 3


def test_extract_without_firecrawl_raises(project_id: int) -> None:
    """Constructing without a ``firecrawl`` instance and calling ``extract`` errors."""

    async def go() -> dict:
        paa = GooglePaaIntegration(firecrawl=None)
        return await paa.extract(query="x")

    with pytest.raises(IntegrationDownError):
        asyncio.run(go())


@pytest.mark.parametrize("rate_limited", [False, True])
def test_native_firecrawl_failure_keeps_host_error_type(project_id: int, rate_limited) -> None:
    from stackos_connectors.errors import IntegrationDownError as NativeDown
    from stackos_connectors.errors import RateLimitedError as NativeRate

    from stackos.mcp.errors import RateLimitedError

    class FailedFirecrawl:
        async def scrape(self, **kwargs):
            assert kwargs == {
                "url": "https://www.google.com/search?q=content+marketing",
                "only_main_content": False,
            }
            error = NativeRate if rate_limited else NativeDown
            raise error(
                "provider failed",
                data={"vendor": "firecrawl", "status_code": 429 if rate_limited else 503},
            )

    async def go():
        return await GooglePaaIntegration(firecrawl=FailedFirecrawl()).test_credentials()

    with pytest.raises(RateLimitedError if rate_limited else IntegrationDownError) as caught:
        asyncio.run(go())
    assert caught.value.data["vendor"] == "firecrawl"


def test_native_firecrawl_paa_probe_preserves_question_bound(
    httpx_mock: HTTPXMock, project_id: int
):
    markdown = "\n".join(f"## What is question number {number}?" for number in range(15))
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": markdown}},
    )

    async def go():
        async with httpx.AsyncClient() as http:
            firecrawl = FirecrawlIntegration(payload=b"fc", http=http)
            return await GooglePaaIntegration(firecrawl=firecrawl).test_credentials()

    assert asyncio.run(go()) == {"ok": True, "vendor": "google-paa", "questions_found": 10}
