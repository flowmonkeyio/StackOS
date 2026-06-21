"""Google Tag Manager wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pytest_httpx import HTTPXMock

from stackos.integrations.google_tag_manager import GoogleTagManagerIntegration


def _payload() -> bytes:
    return json.dumps({"access_token": "gtm-token"}).encode("utf-8")


def test_google_tag_manager_wrapper_maps_read_endpoints(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://tagmanager.googleapis.com/tagmanager/v2/accounts?"
            "includeGoogleTags=true&pageToken=acct-page"
        ),
        json={"account": [{"accountId": "111"}], "nextPageToken": "containers"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://tagmanager.googleapis.com/tagmanager/v2/accounts/111/containers?pageToken=c",
        json={"container": [{"containerId": "GTM-ABC"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://tagmanager.googleapis.com/tagmanager/v2/accounts/111/containers/GTM-ABC:snippet",
        json={"snippet": "<script>...</script>"},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://tagmanager.googleapis.com/tagmanager/v2/"
            "accounts/111/containers/GTM-ABC/workspaces?pageToken=w"
        ),
        json={"workspace": [{"workspaceId": "7"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://tagmanager.googleapis.com/tagmanager/v2/"
            "accounts/111/containers/GTM-ABC/workspaces/7/tags?pageToken=t"
        ),
        json={"tag": [{"name": "GA4 config"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://tagmanager.googleapis.com/tagmanager/v2/"
            "accounts/111/containers/GTM-ABC/workspaces/7/triggers?pageToken=r"
        ),
        json={"trigger": [{"name": "All Pages"}]},
    )

    async def go() -> list[Any]:
        async with httpx.AsyncClient() as client:
            integration = GoogleTagManagerIntegration(
                payload=_payload(),
                project_id=project_id,
                http=client,
                qps_override=1000.0,
            )
            accounts = await integration.accounts_list(
                include_google_tags=True,
                page_token="acct-page",
            )
            containers = await integration.containers_list(
                account_path="accounts/111",
                page_token="c",
            )
            snippet = await integration.container_snippet(
                container_path="accounts/111/containers/GTM-ABC",
            )
            workspaces = await integration.workspaces_list(
                container_path="accounts/111/containers/GTM-ABC",
                page_token="w",
            )
            tags = await integration.workspace_tags_list(
                workspace_path="accounts/111/containers/GTM-ABC/workspaces/7",
                page_token="t",
            )
            triggers = await integration.workspace_triggers_list(
                workspace_path="accounts/111/containers/GTM-ABC/workspaces/7",
                page_token="r",
            )
            return [accounts, containers, snippet, workspaces, tags, triggers]

    accounts, containers, snippet, workspaces, tags, triggers = asyncio.run(go())
    requests = httpx_mock.get_requests()

    assert accounts.data["account"][0]["accountId"] == "111"
    assert containers.data["container"][0]["containerId"] == "GTM-ABC"
    assert snippet.data["snippet"].startswith("<script>")
    assert workspaces.data["workspace"][0]["workspaceId"] == "7"
    assert tags.data["tag"][0]["name"] == "GA4 config"
    assert triggers.data["trigger"][0]["name"] == "All Pages"
    assert requests[0].headers["Authorization"] == "Bearer gtm-token"


def test_google_tag_manager_test_credentials_returns_sanitized_account_summary(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://tagmanager.googleapis.com/tagmanager/v2/accounts",
        json={"account": [{"accountId": "111"}]},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = GoogleTagManagerIntegration(
                payload=_payload(),
                project_id=project_id,
                http=client,
                qps_override=1000.0,
            )
            return await integration.test_credentials()

    assert asyncio.run(go()) == {
        "ok": True,
        "vendor": "google-tag-manager",
        "accounts_count": 1,
        "has_next_page": False,
    }
