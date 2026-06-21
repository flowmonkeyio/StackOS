"""Google Tag Manager integration wrapper."""

from __future__ import annotations

from typing import Any

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations.google_oauth import google_bearer_headers, parse_google_oauth_payload


class GoogleTagManagerIntegration(BaseIntegration):
    """Wrapper for read-only Google Tag Manager API v2 inventory calls."""

    kind = "google-tag-manager"
    vendor = "google-tag-manager"
    default_qps = 0.2

    BASE_URL = "https://tagmanager.googleapis.com/tagmanager/v2"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._oauth_payload = parse_google_oauth_payload(self.payload, provider=self.vendor)

    async def _headers(self) -> dict[str, str]:
        return await google_bearer_headers(
            self._oauth_payload,
            provider=self.vendor,
            http=self._http,
        )

    async def accounts_list(
        self,
        *,
        include_google_tags: bool | None = None,
        page_token: str | None = None,
    ) -> IntegrationCallResult:
        params: dict[str, Any] = {}
        if include_google_tags is not None:
            params["includeGoogleTags"] = "true" if include_google_tags else "false"
        if page_token:
            params["pageToken"] = page_token
        return await self.call(
            op="accounts.list",
            method="GET",
            url=f"{self.BASE_URL}/accounts",
            params=params or None,
            headers=await self._headers(),
        )

    async def containers_list(
        self,
        *,
        account_path: str,
        page_token: str | None = None,
    ) -> IntegrationCallResult:
        params = {"pageToken": page_token} if page_token else None
        return await self.call(
            op="accounts.containers.list",
            method="GET",
            url=f"{self.BASE_URL}/{account_path}/containers",
            params=params,
            headers=await self._headers(),
        )

    async def container_snippet(self, *, container_path: str) -> IntegrationCallResult:
        return await self.call(
            op="accounts.containers.snippet",
            method="GET",
            url=f"{self.BASE_URL}/{container_path}:snippet",
            headers=await self._headers(),
        )

    async def workspaces_list(
        self,
        *,
        container_path: str,
        page_token: str | None = None,
    ) -> IntegrationCallResult:
        params = {"pageToken": page_token} if page_token else None
        return await self.call(
            op="accounts.containers.workspaces.list",
            method="GET",
            url=f"{self.BASE_URL}/{container_path}/workspaces",
            params=params,
            headers=await self._headers(),
        )

    async def workspace_tags_list(
        self,
        *,
        workspace_path: str,
        page_token: str | None = None,
    ) -> IntegrationCallResult:
        params = {"pageToken": page_token} if page_token else None
        return await self.call(
            op="accounts.containers.workspaces.tags.list",
            method="GET",
            url=f"{self.BASE_URL}/{workspace_path}/tags",
            params=params,
            headers=await self._headers(),
        )

    async def workspace_triggers_list(
        self,
        *,
        workspace_path: str,
        page_token: str | None = None,
    ) -> IntegrationCallResult:
        params = {"pageToken": page_token} if page_token else None
        return await self.call(
            op="accounts.containers.workspaces.triggers.list",
            method="GET",
            url=f"{self.BASE_URL}/{workspace_path}/triggers",
            params=params,
            headers=await self._headers(),
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.accounts_list()
        data = result.data if isinstance(result.data, dict) else {}
        accounts = data.get("account") if isinstance(data.get("account"), list) else []
        return {
            "ok": True,
            "vendor": self.vendor,
            "accounts_count": len(accounts),
            "has_next_page": bool(data.get("nextPageToken")),
        }


__all__ = ["GoogleTagManagerIntegration"]
