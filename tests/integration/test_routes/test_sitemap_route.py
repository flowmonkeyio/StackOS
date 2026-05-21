"""Tests for ``POST /api/v1/projects/{id}/sitemap/fetch``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from content_stack.api import projects as projects_module
from content_stack.integrations.sitemap import SitemapEntry, SitemapFetchError, SitemapFetchResult


@pytest.fixture(autouse=True)
def _stub_sitemap_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(urls: list[str], *, max_entries: int = 5_000) -> SitemapFetchResult:
        entries: list[SitemapEntry] = []
        errors: list[SitemapFetchError] = []
        for url in urls:
            if "broken" in url:
                errors.append(SitemapFetchError(url=url, error="HTTP 404"))
                continue
            entries.append(
                SitemapEntry(
                    url=f"{url.rstrip('/')}/post-1",
                    lastmod="2026-01-02",
                    source_sitemap=url,
                )
            )
        return SitemapFetchResult(
            entries=entries[:max_entries],
            errors=errors,
        )

    monkeypatch.setattr(projects_module, "fetch_sitemap_entries", _fake_fetch)


def test_sitemap_fetch_returns_entries(api: TestClient, project_id: int) -> None:
    resp = api.post(
        f"/api/v1/projects/{project_id}/sitemap/fetch",
        json={"url": "https://good.example/sitemap.xml", "limit": 100},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["url"] == "https://good.example/sitemap.xml"
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["url"].endswith("/post-1")
    assert payload["entries"][0]["lastmod"] == "2026-01-02"


def test_sitemap_fetch_404_on_missing_project(api: TestClient) -> None:
    resp = api.post(
        "/api/v1/projects/9999/sitemap/fetch",
        json={"url": "https://example.com/sitemap.xml"},
    )
    assert resp.status_code == 404


def test_sitemap_fetch_validates_payload(api: TestClient, project_id: int) -> None:
    resp = api.post(
        f"/api/v1/projects/{project_id}/sitemap/fetch",
        json={"url": ""},
    )
    assert resp.status_code == 422
