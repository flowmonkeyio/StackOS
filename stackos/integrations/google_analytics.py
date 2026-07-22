"""Google Analytics 4 integration wrapper."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations.google_oauth import google_bearer_headers, parse_google_oauth_payload


class GoogleAnalyticsIntegration(BaseIntegration):
    """Wrapper for GA4 Admin and Data API read calls."""

    kind = "google-analytics"
    vendor = "google-analytics"
    default_qps = 1.0

    ADMIN_BASE_URL = "https://analyticsadmin.googleapis.com/v1beta"
    DATA_BASE_URL = "https://analyticsdata.googleapis.com/v1beta"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._oauth_payload = parse_google_oauth_payload(self.payload, provider=self.vendor)

    def _headers(self) -> dict[str, str]:
        return google_bearer_headers(
            self._oauth_payload,
            provider=self.vendor,
        )

    @staticmethod
    def property_path(property_ref: str) -> str:
        if property_ref.startswith("properties/"):
            return "/".join(quote(part, safe="") for part in property_ref.split("/"))
        return f"properties/{quote(property_ref, safe='')}"

    async def account_summaries_list(
        self,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> IntegrationCallResult:
        params: dict[str, Any] = {}
        if page_size is not None:
            params["pageSize"] = page_size
        if page_token:
            params["pageToken"] = page_token
        return await self.call(
            op="account_summaries.list",
            method="GET",
            url=f"{self.ADMIN_BASE_URL}/accountSummaries",
            params=params or None,
            headers=self._headers(),
        )

    async def metadata_get(self, *, property_ref: str) -> IntegrationCallResult:
        property_path = self.property_path(property_ref)
        return await self.call(
            op="properties.metadata.get",
            method="GET",
            url=f"{self.DATA_BASE_URL}/{property_path}/metadata",
            headers=self._headers(),
        )

    async def run_report(
        self,
        *,
        property_ref: str,
        request_body: dict[str, Any],
    ) -> IntegrationCallResult:
        property_path = self.property_path(property_ref)
        return await self.call(
            op="properties.run_report",
            method="POST",
            url=f"{self.DATA_BASE_URL}/{property_path}:runReport",
            json_body=request_body,
            headers=self._headers(),
        )

    async def run_realtime_report(
        self,
        *,
        property_ref: str,
        request_body: dict[str, Any],
    ) -> IntegrationCallResult:
        property_path = self.property_path(property_ref)
        return await self.call(
            op="properties.run_realtime_report",
            method="POST",
            url=f"{self.DATA_BASE_URL}/{property_path}:runRealtimeReport",
            json_body=request_body,
            headers=self._headers(),
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.account_summaries_list(page_size=1)
        data = result.data if isinstance(result.data, dict) else {}
        summaries_raw = data.get("accountSummaries")
        summaries = summaries_raw if isinstance(summaries_raw, list) else []
        return {
            "ok": True,
            "vendor": self.vendor,
            "account_count": len(summaries),
            "has_next_page": bool(data.get("nextPageToken")),
        }


__all__ = ["GoogleAnalyticsIntegration"]
