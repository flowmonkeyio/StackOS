"""MCP ``sitemap.fetch`` happy-path + validation tests."""

from __future__ import annotations

import pytest

from stackos.integrations.sitemap import (
    SitemapEntry,
    SitemapFetchError,
    SitemapFetchResult,
)

from .conftest import MCPClient


@pytest.fixture(autouse=True)
def _stub_sitemap_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the helper with a deterministic stub.

    Real sitemap fetches against arbitrary external hosts are out of
    scope for an MCP wire-shape test; the helper itself has its own
    pytest-httpx unit tests.
    """

    async def _fake_fetch(
        urls: list[str],
        *,
        client: object | None = None,
        timeout_s: float = 15.0,
        max_index_depth: int = 2,
        max_entries: int = 5_000,
    ) -> SitemapFetchResult:
        entries: list[SitemapEntry] = []
        errors: list[SitemapFetchError] = []
        for u in urls:
            if "broken" in u:
                errors.append(SitemapFetchError(url=u, error="HTTP 404"))
                continue
            entries.append(
                SitemapEntry(
                    url=f"{u.rstrip('/')}/post-1",
                    lastmod="2026-02-01",
                    source_sitemap=u,
                )
            )
        return SitemapFetchResult(entries=entries, errors=errors)

    import stackos.integrations.sitemap as helper_module
    import stackos.mcp.tools.sitemap as tool_module

    monkeypatch.setattr(helper_module, "fetch_sitemap_entries", _fake_fetch)
    monkeypatch.setattr(tool_module, "fetch_sitemap_entries", _fake_fetch)


def test_sitemap_fetch_returns_entries_and_errors(mcp_client: MCPClient) -> None:
    payload = mcp_client.call_tool_structured(
        "sitemap.fetch",
        {
            "urls": [
                "https://good.example/sitemap.xml",
                "https://broken.example/sitemap.xml",
            ],
            "max_entries": 200,
            "response_mode": "raw",
        },
    )
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["url"].endswith("/post-1")
    assert payload["entries"][0]["lastmod"] == "2026-02-01"
    assert payload["entries"][0]["source_sitemap"] == ("https://good.example/sitemap.xml")
    assert len(payload["errors"]) == 1
    assert payload["errors"][0]["url"] == "https://broken.example/sitemap.xml"
    assert payload["errors"][0]["error"] == "HTTP 404"


def test_sitemap_fetch_rejects_empty_url_list(mcp_client: MCPClient) -> None:
    """Empty list violates the min_length=1 contract."""
    error = mcp_client.call_tool_error("sitemap.fetch", {"urls": []})
    assert error.get("code") == -32602, error
