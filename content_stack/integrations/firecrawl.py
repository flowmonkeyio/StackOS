"""Firecrawl integration wrapper (PLAN.md L1048).

Authentication: Bearer token via ``Authorization: Bearer <api_key>`` —
the API key bytes live in the encrypted credential payload.

Operations:

- ``scrape(url, formats)`` — single-URL scrape returning markdown +
  HTML.
- ``crawl(url, max_depth, limit)`` — submit a multi-page crawl and
  return Firecrawl's job id/status URL.
- ``map(url, search)`` — return every URL found on a domain.
- ``extract(url, schema, prompt)`` — structured-data extraction with
  schema-bound JSON output.

Costs are estimated from Firecrawl's published pricing (``$0.001 /
scrape`` is the floor; crawl is ``$0.002 / page``). The vendor doesn't
report per-call cost in the response so we use the estimate verbatim;
the budget cap on ``firecrawl`` keeps a project from runaway crawls.
"""

from __future__ import annotations

from typing import Any, ClassVar

from content_stack.integrations._base import BaseIntegration, IntegrationCallResult


class FirecrawlIntegration(BaseIntegration):
    """Wrapper for ``https://api.firecrawl.dev/v2``."""

    kind = "firecrawl"
    vendor = "firecrawl"
    default_qps = 2.0

    BASE_URL = "https://api.firecrawl.dev/v2"

    # Official refs: https://docs.firecrawl.dev/api-reference/v2-introduction
    # and https://docs.firecrawl.dev/rate-limits. Firecrawl does not return
    # per-call spend, so these remain StackOS pre-call estimates.
    _ESTIMATED_COSTS: ClassVar[dict[str, float]] = {
        "scrape": 0.001,
        "map": 0.001,
        "extract": 0.001,
        "crawl": 0.002,  # per page; crawl returns N pages — caller multiplies.
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = self.payload.decode("utf-8")

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        del kwargs
        return self._ESTIMATED_COSTS.get(op, 0.001)

    # ------------------------------------------------------------------
    # Public ops.
    # ------------------------------------------------------------------

    async def scrape(
        self,
        *,
        url: str,
        formats: list[str] | None = None,
        only_main_content: bool = True,
    ) -> IntegrationCallResult:
        """Scrape a single URL; returns ``markdown`` + optional HTML."""
        # Endpoint ref: https://docs.firecrawl.dev/api-reference/v2-endpoint/scrape
        body: dict[str, Any] = {
            "url": url,
            "formats": formats or ["markdown"],
            "onlyMainContent": only_main_content,
        }
        return await self.call(
            op="scrape",
            method="POST",
            url=f"{self.BASE_URL}/scrape",
            json_body=body,
            headers=self._auth_headers(),
        )

    async def crawl(
        self,
        *,
        url: str,
        max_depth: int = 2,
        limit: int = 25,
    ) -> IntegrationCallResult:
        """Submit a crawl job; the response includes a polling ``id``."""
        # Endpoint refs:
        # POST https://docs.firecrawl.dev/api-reference/v2-endpoint/crawl-post
        # GET status https://docs.firecrawl.dev/api-reference/v2-endpoint/crawl-get
        # TODO: expose a crawl-status action before templates assume crawl content.
        body: dict[str, Any] = {
            "url": url,
            "limit": limit,
            "scrapeOptions": {
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
            "maxDiscoveryDepth": max_depth,
        }
        return await self.call(
            op="crawl",
            method="POST",
            url=f"{self.BASE_URL}/crawl",
            json_body=body,
            headers=self._auth_headers(),
        )

    async def map(
        self,
        *,
        url: str,
        search: str | None = None,
    ) -> IntegrationCallResult:
        """List every URL Firecrawl can discover on the domain."""
        # Endpoint ref: https://docs.firecrawl.dev/api-reference/v2-endpoint/map
        body: dict[str, Any] = {"url": url}
        if search:
            body["search"] = search
        return await self.call(
            op="map",
            method="POST",
            url=f"{self.BASE_URL}/map",
            json_body=body,
            headers=self._auth_headers(),
        )

    async def extract(
        self,
        *,
        url: str,
        schema: dict[str, Any] | None = None,
        prompt: str | None = None,
    ) -> IntegrationCallResult:
        """Structured-data extraction with optional JSON schema."""
        # Endpoint ref: https://docs.firecrawl.dev/api-reference/v2-endpoint/extract
        body: dict[str, Any] = {"urls": [url]}
        if schema is not None:
            body["schema"] = schema
        if prompt is not None:
            body["prompt"] = prompt
        return await self.call(
            op="extract",
            method="POST",
            url=f"{self.BASE_URL}/extract",
            json_body=body,
            headers=self._auth_headers(),
        )

    # ------------------------------------------------------------------
    # Health check.
    # ------------------------------------------------------------------

    async def test_credentials(self) -> dict[str, Any]:
        """Cheap auth probe — scrapes ``https://example.com``."""
        result = await self.scrape(url="https://example.com")
        return {"ok": True, "vendor": "firecrawl", "sample_bytes": len(str(result.data))}


__all__ = ["FirecrawlIntegration"]
