"""Shopify Admin GraphQL integration wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.shopify import ShopifyIntegration
from stackos.mcp.errors import IntegrationDownError


def test_shopify_test_credentials_posts_admin_graphql_with_static_token(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "shop": {
                    "id": "gid://shopify/Shop/1",
                    "name": "Demo Shop",
                    "myshopifyDomain": "demo.myshopify.com",
                }
            },
            "extensions": {"cost": {"requestedQueryCost": 1}},
        },
        headers={"x-request-id": "shopify-req-1"},
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = ShopifyIntegration(
                payload=b"shpat_secret",
                project_id=project_id,
                http=client,
                store_domain="demo.myshopify.com",
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)

    assert result["ok"] is True
    assert result["vendor"] == "shopify"
    assert result["shop_name"] == "Demo Shop"
    assert request.headers["X-Shopify-Access-Token"] == "shpat_secret"
    assert body["query"].startswith("query StackOSShopifyAuthProbe")


def test_shopify_rejects_non_myshopify_domain_without_request(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = ShopifyIntegration(
                payload=b"shpat_secret",
                project_id=project_id,
                http=client,
                store_domain="https://example.com",
            )
            return await integration.test_credentials()

    with pytest.raises(IntegrationDownError):
        asyncio.run(go())

    assert httpx_mock.get_requests() == []
