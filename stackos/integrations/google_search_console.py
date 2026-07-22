"""Google Search Console integration wrapper."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations.google_oauth import google_bearer_headers, parse_google_oauth_payload


class GoogleSearchConsoleIntegration(BaseIntegration):
    """Wrapper for Search Console and URL Inspection read APIs."""

    kind = "google-search-console"
    vendor = "google-search-console"
    default_qps = 2.0

    WEBMASTERS_BASE_URL = "https://www.googleapis.com/webmasters/v3"
    INSPECTION_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._oauth_payload = parse_google_oauth_payload(self.payload, provider=self.vendor)

    def _headers(self) -> dict[str, str]:
        return google_bearer_headers(
            self._oauth_payload,
            provider=self.vendor,
        )

    async def sites_list(self) -> IntegrationCallResult:
        return await self.call(
            op="sites.list",
            method="GET",
            url=f"{self.WEBMASTERS_BASE_URL}/sites",
            headers=self._headers(),
        )

    async def search_analytics_query(
        self,
        *,
        site_url: str,
        request_body: dict[str, Any],
    ) -> IntegrationCallResult:
        encoded_site = quote(site_url, safe="")
        return await self.call(
            op="search_analytics.query",
            method="POST",
            url=f"{self.WEBMASTERS_BASE_URL}/sites/{encoded_site}/searchAnalytics/query",
            json_body=request_body,
            headers=self._headers(),
        )

    async def sitemaps_list(
        self,
        *,
        site_url: str,
        sitemap_index: str | None = None,
    ) -> IntegrationCallResult:
        params = {"sitemapIndex": sitemap_index} if sitemap_index else None
        encoded_site = quote(site_url, safe="")
        return await self.call(
            op="sitemaps.list",
            method="GET",
            url=f"{self.WEBMASTERS_BASE_URL}/sites/{encoded_site}/sitemaps",
            params=params,
            headers=self._headers(),
        )

    async def url_inspect(
        self,
        *,
        site_url: str,
        inspection_url: str,
        language_code: str | None = None,
    ) -> IntegrationCallResult:
        body = {"inspectionUrl": inspection_url, "siteUrl": site_url}
        if language_code:
            body["languageCode"] = language_code
        return await self.call(
            op="url.inspect",
            method="POST",
            url=self.INSPECTION_URL,
            json_body=body,
            headers=self._headers(),
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.sites_list()
        data = result.data if isinstance(result.data, dict) else {}
        entries_raw = data.get("siteEntry")
        entries = entries_raw if isinstance(entries_raw, list) else []
        permission_levels = sorted(
            {
                level
                for entry in entries
                if isinstance(entry, dict)
                for level in [entry.get("permissionLevel")]
                if isinstance(level, str)
            }
        )
        return {
            "ok": True,
            "vendor": self.vendor,
            "site_count": len(entries),
            "permission_levels": permission_levels,
        }


__all__ = ["GoogleSearchConsoleIntegration"]
