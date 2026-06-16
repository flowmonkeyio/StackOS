"""Ahrefs integration wrapper (PLAN.md L1047).

Authentication: Bearer ``Authorization: Bearer <api_key>``. Ahrefs only
serves this wrapper when the operator has a paid plan/API units that
allow direct API v3 calls; for solo/SMB operators DataForSEO covers most
of the same surface area.

Operations:

- ``keywords_for_site(target, country)`` — keyword inventory.
- ``top_backlinks(target, mode, limit)`` — top inbound links.

Graceful degrade: if no credential payload is present,
``test_credentials`` raises ``IntegrationDownError`` with a documented
fallback hint. Wrapper construction itself does NOT raise — the skill code
checks ``test_credentials`` before issuing the first real op.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError

_AHREFS_UNIT_HEADERS = {
    "x-api-rows": "rows",
    "x-api-units-cost-row": "cost_row",
    "x-api-units-cost-total": "cost_total",
    "x-api-units-cost-total-actual": "cost_total_actual",
}


class AhrefsIntegration(BaseIntegration):
    """Wrapper for ``https://api.ahrefs.com/v3``."""

    kind = "ahrefs"
    vendor = "ahrefs"
    # Official refs: https://docs.ahrefs.com/en/api/docs/introduction and
    # https://docs.ahrefs.com/en/api/docs/limits-consumption. Ahrefs bills in
    # API units; unit headers are metadata until StackOS models unit budgets.
    default_qps = 1.0

    BASE_URL = "https://api.ahrefs.com/v3"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = self.payload.decode("utf-8").strip() if self.payload else ""

    def _require_key(self) -> str:
        if not self._api_key:
            raise IntegrationDownError(
                "Ahrefs API key is not configured. The keyword-discovery skill "
                "works without it because DataForSEO covers most use cases.",
                data={
                    "vendor": "ahrefs",
                    "hint": "docs/api-keys.md — Ahrefs section",
                },
            )
        return self._api_key

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._require_key()}",
            "Accept": "application/json",
        }

    @staticmethod
    def _default_report_date() -> str:
        """Use yesterday: Ahrefs reports are date-bound and may lag today."""
        return (date.today() - timedelta(days=1)).isoformat()

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _extract_response_metadata(
        self,
        op: str,
        *,
        request: Any,
        response: Any,
        http_response: Any,
    ) -> dict[str, Any] | None:
        """Capture Ahrefs unit-accounting headers documented for API v3."""
        del op, request, response
        api_units: dict[str, Any] = {}
        for header, key in _AHREFS_UNIT_HEADERS.items():
            parsed = self._parse_int(http_response.headers.get(header))
            if parsed is not None:
                api_units[key] = parsed
        cache_header = http_response.headers.get("x-api-cache")
        if cache_header:
            api_units["cache"] = cache_header
        if not api_units:
            return None
        metadata: dict[str, Any] = {"api_units": api_units}
        if isinstance(cache_header, str) and cache_header.lower() == "hit":
            metadata["cached"] = True
        return metadata

    async def limits_and_usage(self) -> IntegrationCallResult:
        """Read the free subscription/usage endpoint before paid row calls."""
        return await self.call(
            op="limits_and_usage",
            method="GET",
            url=f"{self.BASE_URL}/subscription-info/limits-and-usage",
            headers=self._auth_headers(),
        )

    async def keywords_for_site(
        self,
        *,
        target: str,
        country: str = "us",
        limit: int = 100,
        date_: str | None = None,
    ) -> IntegrationCallResult:
        """Keyword inventory for the target domain."""
        # Endpoint ref:
        # https://docs.ahrefs.com/en/api/reference/site-explorer/get-organic-keywords
        params = {
            "target": target,
            "country": country,
            "limit": str(limit),
            "date": date_ or self._default_report_date(),
            "select": "keyword,volume,cpc,best_position,keyword_difficulty",
        }
        return await self.call(
            op="keywords_for_site",
            method="GET",
            url=f"{self.BASE_URL}/site-explorer/organic-keywords",
            params=params,
            headers=self._auth_headers(),
        )

    async def top_backlinks(
        self,
        *,
        target: str,
        mode: str = "domain",
        limit: int = 100,
    ) -> IntegrationCallResult:
        """Top inbound backlinks; ``mode`` is ``domain`` or ``exact``."""
        # Endpoint ref:
        # https://docs.ahrefs.com/en/api/reference/site-explorer/get-all-backlinks
        params = {
            "target": target,
            "mode": mode,
            "limit": str(limit),
            "select": "url_from,url_to,domain_rating_source,first_seen",
        }
        return await self.call(
            op="top_backlinks",
            method="GET",
            url=f"{self.BASE_URL}/site-explorer/all-backlinks",
            params=params,
            headers=self._auth_headers(),
        )

    async def test_credentials(self) -> dict[str, Any]:
        """Cheap auth probe — call a free ``domain-rating`` query.

        Raises ``IntegrationDownError`` with the Ahrefs setup hint when no
        key is configured (graceful degrade — the keyword-discovery skill
        picks this up and falls back to DataForSEO).
        """
        self._require_key()
        result = await self.call(
            op="test",
            method="GET",
            url=f"{self.BASE_URL}/site-explorer/domain-rating",
            params={"target": "wordcount.com", "date": self._default_report_date()},
            headers=self._auth_headers(),
        )
        return {"ok": True, "vendor": "ahrefs", "data": result.data}


__all__ = ["AhrefsIntegration"]
