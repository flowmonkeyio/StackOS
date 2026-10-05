"""Shopify Admin GraphQL integration wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock
from stackos_connectors.connectors.shopify.integration import ShopifyIntegration
from stackos_connectors.errors import IntegrationDownError


@pytest.mark.parametrize("bad_quantity", [False, True])
def test_report_composer_uses_fixed_native_pages_and_frozen_window(
    monkeypatch, project_id, bad_quantity
):
    from datetime import UTC, datetime

    from stackos_connectors import CallOptions

    from stackos.actions import shopify as host
    from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
    from stackos.auth_providers.repository.schema import ResolvedCredential
    from stackos.db.models import Credential, IntegrationCredential

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 1, 12, tzinfo=UTC)

    monkeypatch.setattr(host, "datetime", FrozenClock)
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.extensions["timeout"]["read"] == 1.25
        if "InventoryRiskReport" in body["query"]:
            assert body["variables"] == {"first": 250}
            return httpx.Response(
                200,
                json={
                    "data": {
                        "productVariants": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "v1",
                                        "title": "Variant",
                                        "sku": "sku",
                                        "inventoryQuantity": "invalid" if bad_quantity else 2,
                                        "product": {"id": "p1", "title": "Product"},
                                    }
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                },
            )
        assert "query RecentSales" in body["query"]
        assert body["variables"] == {
            "first": 250,
            "query": "created_at:>=2026-06-01 created_at:<=2026-07-01",
        }
        return httpx.Response(
            200,
            json={
                "data": {
                    "orders": {
                        "edges": [
                            {
                                "node": {
                                    "lineItems": {
                                        "edges": [
                                            {"node": {"quantity": 30, "variant": {"id": "v1"}}}
                                        ],
                                        "pageInfo": {"hasNextPage": True},
                                    }
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False},
                    }
                }
            },
        )

    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="shopify",
        action_key="inventory_risk_report",
        action_ref="shopify.inventory_risk_report",
        provider_key="shopify",
        operation="admin.graphql",
        input_json={"days_of_stock_threshold": 30, "limit": 1},
        config_json={
            "shopify": {
                "action_name": "inventory_risk_report",
                "mode": "inventory_risk",
                "graphql_file": "graphql/analytics/inventory-risk-report.graphql",
            }
        },
        credential=ResolvedCredential(
            secret_payload=b"synthetic",
            config_json={"store_domain": "demo.myshopify.com"},
            credential=Credential(provider_key="shopify", auth_method_key="admin-api-token"),
            integration=IntegrationCredential(encrypted_payload=b"unused", nonce=b"0" * 12),
        ),
    )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=99) as http:
            result = await host.ShopifyActionConnector(
                options=CallOptions(http=http, timeout=1.25)
            ).execute(request)
            assert not http.is_closed and http.timeout.read == 99
            return result

    if bad_quantity:
        with pytest.raises(ActionConnectorError) as caught:
            asyncio.run(go())
        assert caught.value.metadata_json["provider_executed"] is True
        assert caught.value.metadata_json["completed_pages"] == 2
        assert len(seen) == 2
        return
    result = asyncio.run(go())
    assert len(seen) == 2
    assert result.output_json["data"]["summary"] == {
        "totalVariants": 1,
        "understock": 1,
        "overstock": 0,
        "healthy": 0,
        "truncated": True,
    }
    item = result.output_json["data"]["items"][0]
    assert item["unitsSoldLast30Days"] == 30
    assert item["estimatedDaysOfStock"] == 2
    assert item["riskCategory"] == "understock"


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
                http=client,
                store_domain="https://example.com",
            )
            return await integration.test_credentials()

    with pytest.raises(IntegrationDownError):
        asyncio.run(go())

    assert httpx_mock.get_requests() == []
