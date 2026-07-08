"""Repository tests for the Shopify action catalog and connector."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.plugins import PluginRepository


def _shopify_credential_ref(session: Session, project_id: int) -> str:
    return (
        AuthRepository(session)
        .store_credential(
            project_id=project_id,
            provider_key="shopify",
            auth_method_key="admin-api-token",
            profile_key="primary",
            fields={
                "admin_api_access_token": "shpat-secret",
                "store_domain": "demo.myshopify.com",
                "api_version": "2026-07",
            },
        )
        .data.credential_ref
    )


def test_shopify_catalog_exposes_58_static_actions(
    session: Session,
    project_id: int,
) -> None:
    repo = PluginRepository(session)
    repo.get_plugin("shopify")

    actions = repo.list_actions(plugin_slug="shopify", project_id=project_id)
    keys = {action.key for action in actions}

    assert len(actions) == 58
    assert {"list_products", "create_product", "sales_summary"} <= keys
    assert "shopifyql_query" not in keys
    list_products = next(action for action in actions if action.key == "list_products")
    assert list_products.provider_key == "shopify"
    assert "List products" in list_products.description
    create_product = next(action for action in actions if action.key == "create_product")
    update_product = next(action for action in actions if action.key == "update_product")
    create_statuses = create_product.input_schema_json["properties"]["status"]["enum"]
    update_statuses = update_product.input_schema_json["properties"]["status"]["enum"]
    assert create_statuses == ["ACTIVE", "DRAFT", "ARCHIVED", "UNLISTED"]
    assert update_statuses == ["ACTIVE", "DRAFT", "ARCHIVED", "UNLISTED"]


def test_shopify_list_products_executes_curated_graphql(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "products": {
                    "edges": [
                        {
                            "node": {
                                "id": "gid://shopify/Product/1",
                                "title": "StackOS Tee",
                                "variants": {"edges": []},
                            }
                        }
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            },
            "extensions": {
                "cost": {
                    "requestedQueryCost": 3,
                    "actualQueryCost": 2,
                    "throttleStatus": {"currentlyAvailable": 998},
                }
            },
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "products": {
                    "edges": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        },
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.list_products",
            input_json={"limit": 5, "status": "ACTIVE", "vendor": "Acme"},
            credential_ref=credential_ref,
        )
    )
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)

    assert request.headers["X-Shopify-Access-Token"] == "shpat-secret"
    assert "query ListProducts" in body["query"]
    assert body["variables"] == {
        "first": 5,
        "query": 'status:active AND vendor:"Acme"',
    }
    assert result.data.output_json["data"]["products"]["edges"][0]["node"]["title"] == "StackOS Tee"
    assert result.data.metadata_json["cost"]["actualQueryCost"] == 2

    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.list_products",
            input_json={"status": "UNLISTED"},
            credential_ref=credential_ref,
        )
    )
    second_body = json.loads(httpx_mock.get_requests()[1].content)
    assert second_body["variables"] == {
        "first": 10,
        "query": "status:unlisted",
    }


def test_shopify_rejects_removed_generic_shopifyql_action(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)

    with pytest.raises(NotFoundError):
        ActionRepository(session).validate(
            project_id=project_id,
            action_ref="shopify.shopifyql_query",
            input_json={"query": "FROM sales SHOW total_sales"},
            credential_ref=credential_ref,
        )


def test_shopify_mutation_user_errors_are_safe_agent_repair_data(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "productCreate": {
                    "product": None,
                    "userErrors": [
                        {
                            "field": ["title"],
                            "message": "Title is reserved for this test",
                        }
                    ],
                }
            }
        },
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="shopify.create_product",
                input_json={"title": "Bad Product"},
                credential_ref=credential_ref,
            )
        )

    rendered = json.dumps(excinfo.value.data)
    assert excinfo.value.data["status"] == "failed"
    assert excinfo.value.data["provider_error"]["userErrors"][0]["field"] == ["title"]
    assert "Title is reserved for this test" in rendered
    assert "shpat-secret" not in rendered


def test_shopify_create_draft_order_maps_line_items_and_shipping_address(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "draftOrderCreate": {
                    "draftOrder": {
                        "id": "gid://shopify/DraftOrder/1",
                        "name": "#D1",
                        "status": "OPEN",
                        "lineItems": {"edges": []},
                    },
                    "userErrors": [],
                }
            }
        },
    )

    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.create_draft_order",
            input_json={
                "line_items": [
                    {"variant_id": "gid://shopify/ProductVariant/1", "quantity": 2}
                ],
                "note": "Ship together",
                "shipping_address": {
                    "address1": "1 Main St",
                    "city": "Austin",
                    "zip": "78701",
                    "country": "US",
                },
            },
            credential_ref=credential_ref,
        )
    )
    body = json.loads(httpx_mock.get_requests()[0].content)

    assert "\n      note2\n" in body["query"]
    assert "\n      note\n" not in body["query"]
    assert body["variables"]["input"]["lineItems"] == [
        {"variantId": "gid://shopify/ProductVariant/1", "quantity": 2}
    ]
    assert body["variables"]["input"]["shippingAddress"]["address1"] == "1 Main St"


def test_shopify_inventory_write_actions_send_idempotency_keys(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    endpoint = "https://demo.myshopify.com/admin/api/2026-07/graphql.json"
    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        json={
            "data": {
                "inventoryAdjustQuantities": {
                    "inventoryAdjustmentGroup": {"reason": "correction", "changes": []},
                    "userErrors": [],
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        json={
            "data": {
                "inventorySetQuantities": {
                    "inventoryAdjustmentGroup": {"reason": "correction", "changes": []},
                    "userErrors": [],
                }
            }
        },
    )

    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.adjust_inventory",
            input_json={
                "inventory_item_id": "gid://shopify/InventoryItem/1",
                "location_id": "gid://shopify/Location/1",
                "delta": -2,
            },
            credential_ref=credential_ref,
        )
    )
    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.set_inventory_level",
            input_json={
                "inventory_item_id": "gid://shopify/InventoryItem/1",
                "location_id": "gid://shopify/Location/1",
                "quantity": 10,
            },
            credential_ref=credential_ref,
        )
    )

    bodies = [json.loads(request.content) for request in httpx_mock.get_requests()]
    assert "@idempotent(key: $idempotencyKey)" in bodies[0]["query"]
    assert "@idempotent(key: $idempotencyKey)" in bodies[1]["query"]
    assert bodies[0]["variables"]["idempotencyKey"]
    assert bodies[1]["variables"]["idempotencyKey"]
    assert bodies[0]["variables"]["input"]["changes"][0]["changeFromQuantity"] is None
    assert bodies[1]["variables"]["input"]["quantities"][0]["changeFromQuantity"] is None
    assert "ignoreCompareQuantity" not in bodies[1]["variables"]["input"]


def test_shopify_order_name_and_tag_replacement_mappings(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    endpoint = "https://demo.myshopify.com/admin/api/2026-07/graphql.json"
    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        json={"data": {"orders": {"edges": [], "pageInfo": {"hasNextPage": False}}}},
    )
    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        json={
            "data": {
                "orderUpdate": {
                    "order": {"id": "gid://shopify/Order/1", "tags": ["vip"]},
                    "userErrors": [],
                }
            }
        },
    )

    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.get_order_by_name",
            input_json={"name": "EN1001"},
            credential_ref=credential_ref,
        )
    )
    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.update_order_tags",
            input_json={"id": "gid://shopify/Order/1", "tags": ["vip"]},
            credential_ref=credential_ref,
        )
    )

    bodies = [json.loads(request.content) for request in httpx_mock.get_requests()]
    assert bodies[0]["variables"] == {"query": 'name:"EN1001"'}
    assert "orderUpdate(input: $input)" in bodies[1]["query"]
    assert bodies[1]["variables"] == {
        "input": {"id": "gid://shopify/Order/1", "tags": ["vip"]}
    }


def test_shopify_collection_and_product_status_mappings(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    endpoint = "https://demo.myshopify.com/admin/api/2026-07/graphql.json"
    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        json={
            "data": {
                "collectionCreate": {
                    "collection": {"id": "gid://shopify/Collection/1"},
                    "userErrors": [],
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=endpoint,
        json={
            "data": {
                "productUpdate": {
                    "product": {"id": "gid://shopify/Product/1", "status": "UNLISTED"},
                    "userErrors": [],
                }
            }
        },
    )

    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.create_collection",
            input_json={"title": "Outlet", "description": "Sale items"},
            credential_ref=credential_ref,
        )
    )
    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.update_product_status",
            input_json={"id": "gid://shopify/Product/1", "status": "UNLISTED"},
            credential_ref=credential_ref,
        )
    )

    bodies = [json.loads(request.content) for request in httpx_mock.get_requests()]
    assert "collectionCreate(collection: $collection)" in bodies[0]["query"]
    assert bodies[0]["variables"] == {
        "collection": {"title": "Outlet", "descriptionHtml": "Sale items"}
    }
    assert bodies[1]["variables"] == {
        "product": {"id": "gid://shopify/Product/1", "status": "UNLISTED"}
    }


def test_shopify_variant_user_error_codes_are_preserved(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "productVariantsBulkUpdate": {
                    "productVariants": [],
                    "userErrors": [
                        {
                            "code": "INVALID_INPUT",
                            "field": ["variants", "0", "id"],
                            "message": "Variant is invalid",
                        }
                    ],
                }
            }
        },
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="shopify.update_product_variant",
                input_json={
                    "product_id": "gid://shopify/Product/1",
                    "id": "gid://shopify/ProductVariant/1",
                    "price": "10.00",
                },
                credential_ref=credential_ref,
            )
        )

    assert (
        excinfo.value.data["provider_error"]["userErrors"][0]["code"]
        == "INVALID_INPUT"
    )


def test_shopify_low_stock_report_filters_and_sorts_by_threshold(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "inventoryItems": {
                    "edges": [
                        _inventory_item_edge("sku-low", 2, "Low Tee"),
                        _inventory_item_edge("sku-ok", 20, "Okay Tee"),
                        _inventory_item_edge("sku-empty", 0, "Empty Tee"),
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        },
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="shopify.low_stock_report",
            input_json={"threshold": 5, "limit": 10},
            credential_ref=credential_ref,
        )
    )

    data = result.data.output_json["data"]
    assert data["count"] == 2
    assert [item["sku"] for item in data["items"]] == ["sku-empty", "sku-low"]
    assert data["items"][0]["available"] == 0
    assert data["threshold"] == 5


def test_shopify_connector_rejects_unbounded_analytics_inputs(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _shopify_credential_ref(session, project_id)
    repo = ActionRepository(session)

    bad_compare = repo.validate(
        project_id=project_id,
        action_ref="shopify.sales_comparison",
        input_json={
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "compare_to": "next_year",
            "group_by": "month",
        },
        credential_ref=credential_ref,
    )
    bad_risk_threshold = repo.validate(
        project_id=project_id,
        action_ref="shopify.inventory_risk_report",
        input_json={"days_of_stock_threshold": "thirty"},
        credential_ref=credential_ref,
    )

    assert any(item.code == "validation_error" for item in bad_compare.issues)
    assert any(item.code == "validation_error" for item in bad_risk_threshold.issues)


def _inventory_item_edge(sku: str, available: int, product_title: str) -> dict[str, object]:
    return {
        "node": {
            "id": f"gid://shopify/InventoryItem/{sku}",
            "sku": sku,
            "tracked": True,
            "variants": {
                "nodes": [
                    {
                        "id": f"gid://shopify/ProductVariant/{sku}",
                        "title": "Default",
                        "product": {
                            "id": f"gid://shopify/Product/{sku}",
                            "title": product_title,
                        },
                    }
                ]
            },
            "inventoryLevels": {
                "edges": [
                    {
                        "node": {
                            "location": {
                                "id": "gid://shopify/Location/1",
                                "name": "Warehouse",
                            },
                            "quantities": [
                                {"name": "available", "quantity": available}
                            ],
                        }
                    }
                ]
            },
        }
    }
