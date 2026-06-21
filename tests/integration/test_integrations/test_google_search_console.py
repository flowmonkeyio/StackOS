"""Google Search Console wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pytest_httpx import HTTPXMock

from stackos.integrations.google_search_console import GoogleSearchConsoleIntegration


def _payload() -> bytes:
    return json.dumps({"access_token": "gsc-token"}).encode("utf-8")


def test_search_console_wrapper_maps_read_endpoints(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/webmasters/v3/sites",
        json={"siteEntry": [{"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://www.googleapis.com/webmasters/v3/sites/"
            "https%3A%2F%2Fexample.com%2F/searchAnalytics/query"
        ),
        json={"rows": [{"keys": ["stackos"], "clicks": 1}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://www.googleapis.com/webmasters/v3/sites/"
            "https%3A%2F%2Fexample.com%2F/sitemaps?"
            "sitemapIndex=https%3A%2F%2Fexample.com%2Fsitemap.xml"
        ),
        json={"sitemap": [{"path": "https://example.com/sitemap.xml"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
        json={"inspectionResult": {"inspectionResultLink": "https://search.google.com/test"}},
    )

    async def go() -> list[Any]:
        async with httpx.AsyncClient() as client:
            integration = GoogleSearchConsoleIntegration(
                payload=_payload(),
                project_id=project_id,
                http=client,
                qps_override=1000.0,
            )
            sites = await integration.sites_list()
            analytics = await integration.search_analytics_query(
                site_url="https://example.com/",
                request_body={"startDate": "2026-06-01", "endDate": "2026-06-07"},
            )
            sitemaps = await integration.sitemaps_list(
                site_url="https://example.com/",
                sitemap_index="https://example.com/sitemap.xml",
            )
            inspection = await integration.url_inspect(
                site_url="https://example.com/",
                inspection_url="https://example.com/page",
                language_code="en-US",
            )
            return [sites, analytics, sitemaps, inspection]

    sites, analytics, sitemaps, inspection = asyncio.run(go())
    requests = httpx_mock.get_requests()
    inspection_body = json.loads(requests[3].content.decode("utf-8"))

    assert sites.data["siteEntry"][0]["permissionLevel"] == "siteOwner"
    assert analytics.data["rows"][0]["keys"] == ["stackos"]
    assert sitemaps.data["sitemap"][0]["path"] == "https://example.com/sitemap.xml"
    assert inspection.data["inspectionResult"]["inspectionResultLink"].startswith("https://")
    assert requests[0].headers["Authorization"] == "Bearer gsc-token"
    assert inspection_body == {
        "inspectionUrl": "https://example.com/page",
        "siteUrl": "https://example.com/",
        "languageCode": "en-US",
    }


def test_search_console_test_credentials_returns_sanitized_site_summary(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://www.googleapis.com/webmasters/v3/sites",
        json={
            "siteEntry": [
                {"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"},
                {"siteUrl": "sc-domain:example.org", "permissionLevel": "siteRestrictedUser"},
            ]
        },
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = GoogleSearchConsoleIntegration(
                payload=_payload(),
                project_id=project_id,
                http=client,
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    assert asyncio.run(go()) == {
        "ok": True,
        "vendor": "google-search-console",
        "site_count": 2,
        "permission_levels": ["siteOwner", "siteRestrictedUser"],
    }
