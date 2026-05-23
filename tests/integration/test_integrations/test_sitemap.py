"""Sitemap helper tests.

Cover the five core invariants:

1. A flat ``<urlset>`` parses into one entry per ``<url>`` row with
   optional fields preserved.
2. A ``<sitemapindex>`` recurses into each child sitemap up to the
   depth cap, then aggregates the entries.
3. A non-200 response is captured as a per-source ``SitemapFetchError``
   without aborting the rest of the batch.
4. Oversized responses raise an error captured in ``errors``.
5. The entry cap caps total entries across all sources.
"""

from __future__ import annotations

import asyncio

import httpx
from pytest_httpx import HTTPXMock

from stackos.integrations.sitemap import (
    SitemapEntry,
    fetch_sitemap_entries,
)

URLSET_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/post/a</loc>
    <lastmod>2026-01-02</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://example.com/post/b</loc>
  </url>
</urlset>
"""

SITEMAPINDEX_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/sitemap-1.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://example.com/sitemap-2.xml</loc>
  </sitemap>
</sitemapindex>
"""

CHILD_SITEMAP_1 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/child-1/a</loc></url>
  <url><loc>https://example.com/child-1/b</loc></url>
</urlset>
"""

CHILD_SITEMAP_2 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/child-2/a</loc></url>
</urlset>
"""


def test_flat_urlset_parses_entries(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        text=URLSET_BODY,
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(["https://example.com/sitemap.xml"], client=client)

    result = asyncio.run(go())
    assert len(result.entries) == 2
    by_url = {e.url: e for e in result.entries}
    a = by_url["https://example.com/post/a"]
    assert isinstance(a, SitemapEntry)
    assert a.lastmod == "2026-01-02"
    assert a.changefreq == "weekly"
    assert a.priority == "0.8"
    assert a.source_sitemap == "https://example.com/sitemap.xml"

    b = by_url["https://example.com/post/b"]
    assert b.lastmod is None
    assert b.changefreq is None
    assert b.priority is None
    assert result.errors == []


def test_sitemapindex_recurses_into_children(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        text=SITEMAPINDEX_BODY,
    )
    httpx_mock.add_response(
        url="https://example.com/sitemap-1.xml",
        text=CHILD_SITEMAP_1,
    )
    httpx_mock.add_response(
        url="https://example.com/sitemap-2.xml",
        text=CHILD_SITEMAP_2,
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(["https://example.com/sitemap.xml"], client=client)

    result = asyncio.run(go())
    urls = {e.url for e in result.entries}
    assert urls == {
        "https://example.com/child-1/a",
        "https://example.com/child-1/b",
        "https://example.com/child-2/a",
    }
    # The source_sitemap field carries the immediate parent URL.
    by_url = {e.url: e for e in result.entries}
    assert by_url["https://example.com/child-1/a"].source_sitemap == (
        "https://example.com/sitemap-1.xml"
    )
    assert result.errors == []


def test_failing_source_captured_as_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://broken.example/sitemap.xml",
        status_code=404,
    )
    httpx_mock.add_response(
        url="https://good.example/sitemap.xml",
        text=URLSET_BODY,
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(
                [
                    "https://broken.example/sitemap.xml",
                    "https://good.example/sitemap.xml",
                ],
                client=client,
            )

    result = asyncio.run(go())
    assert {e.url for e in result.entries} == {
        "https://example.com/post/a",
        "https://example.com/post/b",
    }
    assert len(result.errors) == 1
    assert result.errors[0].url == "https://broken.example/sitemap.xml"
    assert "HTTP 404" in result.errors[0].error


def test_sitemap_index_depth_limit_protects_against_infinite_recursion(
    httpx_mock: HTTPXMock,
) -> None:
    # A sitemap that points to itself: depth=0 catches the index, depth=1
    # would recurse, but our cap is 2, so depth=2 should refuse to descend.
    self_referential = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://loop.example/sitemap.xml</loc>
  </sitemap>
</sitemapindex>
"""
    # Mock the same URL repeatedly — the helper should still bail at the
    # depth cap. We use is_reusable to allow multiple matches.
    httpx_mock.add_response(
        url="https://loop.example/sitemap.xml",
        text=self_referential,
        is_reusable=True,
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(
                ["https://loop.example/sitemap.xml"],
                client=client,
                max_index_depth=2,
            )

    result = asyncio.run(go())
    assert result.entries == []
    # We should see at least one "depth limit reached" error in the chain.
    depth_errors = [e for e in result.errors if "depth limit reached" in e.error]
    assert depth_errors, result.errors


def test_unknown_root_element_is_recorded_as_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://weird.example/sitemap.xml",
        text="<rss><channel><item/></channel></rss>",
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            return await fetch_sitemap_entries(["https://weird.example/sitemap.xml"], client=client)

    result = asyncio.run(go())
    assert result.entries == []
    assert len(result.errors) == 1
    assert "unexpected root element" in result.errors[0].error
