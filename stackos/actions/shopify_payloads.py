"""Pure payload shaping and validation for Shopify actions."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import uuid4

from stackos.repositories.base import ValidationError

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESERIES_VALUES = {"day", "week", "month"}
_SALES_COMPARE_VALUES = {"previous_period", "previous_year"}
_GEOGRAPHY_GROUP_VALUES = {"country", "region"}


def _inventory_risk_item(
    variant: dict[str, Any],
    sales_velocity: dict[str, int],
    threshold: int,
) -> dict[str, Any]:
    variant_id = str(variant.get("variantId") or "")
    inventory_quantity = int(variant.get("inventoryQuantity") or 0)
    units_sold = sales_velocity.get(variant_id, 0)
    daily_velocity = units_sold / 30
    if daily_velocity > 0:
        days_of_stock = inventory_quantity / daily_velocity
    elif inventory_quantity > 0:
        days_of_stock = float("inf")
    else:
        days_of_stock = 0.0
    if (days_of_stock == 0 and inventory_quantity == 0) or days_of_stock < threshold:
        risk = "understock"
    elif days_of_stock > threshold * 3:
        risk = "overstock"
    else:
        risk = "healthy"
    return {
        **variant,
        "unitsSoldLast30Days": units_sold,
        "dailyVelocity": round(daily_velocity, 2),
        "estimatedDaysOfStock": None if days_of_stock == float("inf") else round(days_of_stock),
        "riskCategory": risk,
    }


def _variables_for_action(action_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action_key in {
        "add_customer_tag",
        "remove_customer_tag",
        "add_order_tag",
    }:
        return {"id": _required_str(payload, "id"), "tags": payload.get("tags") or []}
    if action_key in {
        "get_customer",
        "get_inventory_item",
        "get_order",
        "get_order_fulfillment_status",
        "get_order_timeline",
        "get_collection",
        "get_product",
        "get_product_variant",
    }:
        return {"id": _required_str(payload, "id")}
    if action_key == "get_customer_lifetime_value":
        return {"id": _required_str(payload, "customer_id")}
    if action_key == "get_customer_orders":
        return {
            "id": _required_str(payload, "customer_id"),
            "first": _limit(payload, default=10, maximum=250),
            "after": payload.get("cursor"),
        }
    if action_key in {"list_customers", "list_collections", "list_inventory_levels"}:
        return {
            "first": _limit(payload, default=10, maximum=250),
            "after": payload.get("cursor"),
        }
    if action_key in {"search_customers", "search_products", "search_orders"}:
        return {
            "first": _limit(payload, default=10, maximum=250),
            "query": _required_str(payload, "query"),
            "after": payload.get("cursor"),
        }
    if action_key == "create_customer":
        return {"input": _customer_input(payload, include_id=False)}
    if action_key == "update_customer":
        return {"input": _customer_input(payload, include_id=True)}
    if action_key == "adjust_inventory":
        return {
            "input": {
                "reason": payload.get("reason") or "correction",
                "name": "available",
                "changes": [
                    {
                        "inventoryItemId": _required_str(payload, "inventory_item_id"),
                        "locationId": _required_str(payload, "location_id"),
                        "delta": payload.get("delta"),
                        "changeFromQuantity": payload.get("change_from_quantity"),
                    }
                ],
            },
            "idempotencyKey": str(uuid4()),
        }
    if action_key == "set_inventory_level":
        return {
            "input": {
                "reason": payload.get("reason") or "correction",
                "name": "available",
                "quantities": [
                    {
                        "inventoryItemId": _required_str(payload, "inventory_item_id"),
                        "locationId": _required_str(payload, "location_id"),
                        "quantity": payload.get("quantity"),
                        "changeFromQuantity": payload.get("change_from_quantity"),
                    }
                ],
            },
            "idempotencyKey": str(uuid4()),
        }
    if action_key == "get_inventory_by_sku":
        return {
            "query": f"sku:{_shopify_search_quote(_required_str(payload, 'sku'))}",
            "first": _limit(payload, default=10, maximum=50),
        }
    if action_key == "get_location_inventory":
        return {
            "id": _required_str(payload, "location_id"),
            "first": _limit(payload, default=10, maximum=250),
            "after": payload.get("cursor"),
        }
    if action_key == "low_stock_report":
        return {"first": 50, "after": payload.get("cursor")}
    if action_key in {"add_order_note", "update_order_note"}:
        return {
            "input": {"id": _required_str(payload, "id"), "note": _required_str(payload, "note")}
        }
    if action_key == "update_order_tags":
        return {"input": {"id": _required_str(payload, "id"), "tags": payload.get("tags") or []}}
    if action_key == "create_draft_order":
        return {"input": _draft_order_input(payload)}
    if action_key == "get_order_by_name":
        name = _required_str(payload, "name")
        return {"query": f"name:{_shopify_search_quote(name)}"}
    if action_key == "list_orders":
        parts = []
        for field in ("status", "financial_status", "fulfillment_status"):
            if payload.get(field):
                parts.append(f"{field}:{payload[field]}")
        return {
            "first": _limit(payload, default=10, maximum=50),
            "query": " ".join(parts) if parts else None,
            "after": payload.get("cursor"),
        }
    if action_key == "mark_order_paid":
        return {"input": {"id": _required_str(payload, "id")}}
    if action_key == "create_collection":
        collection = {"title": _required_str(payload, "title")}
        if payload.get("description") is not None:
            collection["descriptionHtml"] = payload["description"]
        return {"collection": collection}
    if action_key == "create_product":
        return {"product": _product_input(payload, include_id=False)}
    if action_key == "update_product":
        return {"product": _product_input(payload, include_id=True)}
    if action_key == "update_product_status":
        return {
            "product": {
                "id": _required_str(payload, "id"),
                "status": _required_str(payload, "status"),
            }
        }
    if action_key == "create_product_variant":
        return {
            "productId": _required_str(payload, "product_id"),
            "variants": [_variant_input(payload, include_id=False)],
        }
    if action_key == "update_product_variant":
        return {
            "productId": _required_str(payload, "product_id"),
            "variants": [_variant_input(payload, include_id=True)],
        }
    if action_key == "get_product_by_handle":
        return {"handle": _required_str(payload, "handle")}
    if action_key == "list_product_variants":
        return {
            "id": _required_str(payload, "product_id"),
            "first": _limit(payload, default=10, maximum=250),
            "after": payload.get("cursor"),
        }
    if action_key == "list_products":
        parts = []
        if payload.get("status"):
            parts.append(f"status:{str(payload['status']).lower()}")
        if payload.get("vendor"):
            parts.append(f"vendor:{_shopify_search_quote(str(payload['vendor']))}")
        if payload.get("product_type"):
            parts.append(f"product_type:{_shopify_search_quote(str(payload['product_type']))}")
        return {
            "first": _limit(payload, default=10, maximum=250),
            "query": " AND ".join(parts) if parts else None,
            "after": payload.get("cursor"),
        }
    return dict(payload)


def _shopifyql_queries(action_key: str, payload: dict[str, Any]) -> list[tuple[str, str]]:
    if action_key == "customer_lifetime_value":
        sort_by = _enum_value(payload, "sort_by", {"amount", "orders"}, default="amount")
        sort_column = "total_amount_spent" if sort_by == "amount" else "total_orders"
        return [
            (
                "customer_lifetime_value",
                (
                    "FROM customers SHOW customer_name, total_amount_spent, total_orders "
                    f"GROUP BY customer_name ORDER BY {sort_column} DESC "
                    f"LIMIT {_limit(payload, default=25, maximum=100)}"
                ),
            )
        ]

    start = _date_value(payload, "start_date")
    end = _date_value(payload, "end_date")
    if action_key == "conversion_funnel":
        return [
            (
                "sales",
                f"FROM sales SHOW orders, total_sales, customers SINCE {start} UNTIL {end}",
            ),
            ("sessions", f"FROM sessions SHOW sessions SINCE {start} UNTIL {end}"),
            (
                "conversion_rate",
                f"FROM sessions SHOW conversion_rate SINCE {start} UNTIL {end}",
            ),
        ]
    if action_key == "customer_cohort_analysis":
        group_by = _enum_value(payload, "group_by", _TIMESERIES_VALUES, default="month")
        return [
            (
                "cohorts",
                (
                    "FROM sales SHOW customers, orders, total_sales "
                    f"TIMESERIES {group_by} SINCE {start} UNTIL {end}"
                ),
            )
        ]
    if action_key == "discount_performance":
        return [
            (
                "all_sales",
                f"FROM sales SHOW total_sales, orders, discounts SINCE {start} UNTIL {end}",
            ),
            (
                "discounted_sales",
                (
                    "FROM sales SHOW total_sales, orders, discounts "
                    f"WHERE is_discounted_sale = true SINCE {start} UNTIL {end}"
                ),
            ),
        ]
    if action_key == "orders_by_date_range":
        group_by = _enum_value(payload, "group_by", _TIMESERIES_VALUES, default="day")
        return [
            (
                "orders",
                (
                    "FROM sales SHOW orders, total_sales "
                    f"TIMESERIES {group_by} SINCE {start} UNTIL {end}"
                ),
            )
        ]
    if action_key == "product_vendor_performance":
        sort_by = _enum_value(payload, "sort_by", {"revenue", "orders"}, default="revenue")
        sort_column = "orders" if sort_by == "orders" else "total_sales"
        return [
            (
                "vendors",
                (
                    "FROM sales SHOW total_sales, net_sales, orders "
                    f"GROUP BY product_vendor SINCE {start} UNTIL {end} "
                    f"ORDER BY {sort_column} DESC "
                    f"LIMIT {_limit(payload, default=10, maximum=50)}"
                ),
            )
        ]
    if action_key == "refund_rate_summary":
        return [
            (
                "refunds",
                (
                    "FROM sales SHOW orders, sales_reversals, gross_sales, net_sales, discounts "
                    f"SINCE {start} UNTIL {end}"
                ),
            )
        ]
    if action_key == "repeat_customer_rate":
        return [
            ("repeat_customers", f"FROM sales SHOW customers, orders SINCE {start} UNTIL {end}")
        ]
    if action_key == "sales_by_channel":
        return [
            (
                "channels",
                (
                    "FROM sales SHOW total_sales, net_sales, orders "
                    f"GROUP BY sales_channel SINCE {start} UNTIL {end} "
                    f"ORDER BY total_sales DESC LIMIT {_limit(payload, default=10, maximum=50)}"
                ),
            )
        ]
    if action_key == "sales_by_geography":
        group_by = _enum_value(payload, "group_by", _GEOGRAPHY_GROUP_VALUES, default="country")
        dimension = "billing_country" if group_by == "country" else "billing_region"
        return [
            (
                "geography",
                (
                    "FROM sales SHOW total_sales, orders "
                    f"GROUP BY {dimension} SINCE {start} UNTIL {end} "
                    f"ORDER BY total_sales DESC LIMIT {_limit(payload, default=20, maximum=100)}"
                ),
            )
        ]
    if action_key == "sales_comparison":
        group_by = _enum_value(payload, "group_by", _TIMESERIES_VALUES, default="month")
        compare_to = _enum_value(
            payload,
            "compare_to",
            _SALES_COMPARE_VALUES,
            default="previous_period",
        )
        return [
            (
                "comparison",
                (
                    "FROM sales SHOW total_sales, orders, net_sales "
                    f"TIMESERIES {group_by} SINCE {start} UNTIL {end} "
                    f"COMPARE TO {compare_to} WITH PERCENT_CHANGE"
                ),
            )
        ]
    if action_key == "sales_summary":
        return [
            (
                "summary",
                (
                    "FROM sales SHOW total_sales, net_sales, gross_sales, "
                    f"orders, average_order_value SINCE {start} UNTIL {end}"
                ),
            )
        ]
    if action_key == "top_products":
        sort_by = _enum_value(payload, "sort_by", {"revenue", "orders"}, default="revenue")
        sort_column = "orders" if sort_by == "orders" else "total_sales"
        return [
            (
                "products",
                (
                    "FROM sales SHOW total_sales, orders, net_sales "
                    f"GROUP BY product_title SINCE {start} UNTIL {end} "
                    f"ORDER BY {sort_column} DESC "
                    f"LIMIT {_limit(payload, default=10, maximum=50)}"
                ),
            )
        ]
    if action_key == "traffic_analytics":
        group_by = _enum_value(payload, "group_by", _TIMESERIES_VALUES, default="day")
        return [
            (
                "traffic",
                f"FROM sessions SHOW sessions TIMESERIES {group_by} SINCE {start} UNTIL {end}",
            )
        ]
    raise ValidationError(f"unsupported ShopifyQL action {action_key!r}")


def _validate_action_payload(action_key: str, payload: dict[str, Any]) -> None:
    if action_key in {"search_customers", "search_products", "search_orders"}:
        _validate_search_query(_required_str(payload, "query"))
    if action_key in {
        "conversion_funnel",
        "customer_cohort_analysis",
        "discount_performance",
        "orders_by_date_range",
        "product_vendor_performance",
        "refund_rate_summary",
        "repeat_customer_rate",
        "sales_by_channel",
        "sales_by_geography",
        "sales_comparison",
        "sales_summary",
        "top_products",
        "traffic_analytics",
    }:
        _date_value(payload, "start_date")
        _date_value(payload, "end_date")
        if _date_value(payload, "start_date") > _date_value(payload, "end_date"):
            raise ValidationError("start_date must be before or equal to end_date")
    if action_key == "customer_cohort_analysis":
        _enum_value(payload, "group_by", _TIMESERIES_VALUES, default="month")
    if action_key in {"orders_by_date_range", "traffic_analytics"}:
        _enum_value(payload, "group_by", _TIMESERIES_VALUES, default="day")
    if action_key == "sales_comparison":
        _enum_value(payload, "group_by", _TIMESERIES_VALUES, default="month")
        _enum_value(payload, "compare_to", _SALES_COMPARE_VALUES, default="previous_period")
    if action_key == "sales_by_geography":
        _enum_value(payload, "group_by", _GEOGRAPHY_GROUP_VALUES, default="country")
    if action_key in {"customer_lifetime_value", "product_vendor_performance", "top_products"}:
        if action_key == "customer_lifetime_value":
            _enum_value(payload, "sort_by", {"amount", "orders"}, default="amount")
        else:
            _enum_value(payload, "sort_by", {"revenue", "orders"}, default="revenue")
    if action_key == "inventory_risk_report":
        _int_value(payload, "days_of_stock_threshold", default=30, minimum=1, maximum=3650)
        _limit(payload, default=25, maximum=100)
    if action_key == "low_stock_report":
        _int_value(payload, "threshold", default=10, minimum=0, maximum=1_000_000)
        _limit(payload, default=25, maximum=100)
    if action_key == "create_draft_order":
        _draft_order_input(payload)
    if action_key == "adjust_inventory":
        _int_value(payload, "delta", minimum=-1_000_000, maximum=1_000_000)
        if payload.get("change_from_quantity") is not None:
            _int_value(
                payload,
                "change_from_quantity",
                minimum=-1_000_000_000,
                maximum=1_000_000_000,
            )
    if action_key == "set_inventory_level":
        _int_value(payload, "quantity", minimum=0, maximum=1_000_000)
        if payload.get("change_from_quantity") is not None:
            _int_value(
                payload,
                "change_from_quantity",
                minimum=-1_000_000_000,
                maximum=1_000_000_000,
            )


def _validate_search_query(query: str) -> None:
    text = query.strip()
    lowered = text.lower()
    if len(text) > 500:
        raise ValidationError("Shopify search query is too long")
    if any(token in lowered for token in ("mutation", "fragment", "__schema", "__type")):
        raise ValidationError("Shopify search query cannot contain GraphQL operation tokens")
    if any(token in text for token in ("{", "}", ";", "--", "/*", "*/")):
        raise ValidationError("Shopify search query contains unsupported syntax")


def _date_value(payload: dict[str, Any], key: str) -> str:
    value = _required_str(payload, key)
    if not _DATE_RE.match(value):
        raise ValidationError(f"{key} must use YYYY-MM-DD format")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError(f"{key} must be a valid date") from exc
    return value


def _enum_value(
    payload: dict[str, Any],
    key: str,
    accepted: Iterable[str],
    *,
    default: str,
) -> str:
    accepted_set = set(accepted)
    value = payload.get(key) or default
    if not isinstance(value, str) or value not in accepted_set:
        raise ValidationError(f"{key} must be one of {', '.join(sorted(accepted_set))}")
    return value


def _int_value(
    payload: dict[str, Any],
    key: str,
    *,
    default: int | None = None,
    minimum: int,
    maximum: int,
) -> int:
    raw = payload.get(key, default)
    if raw is None:
        raise ValidationError(f"{key} is required")
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValidationError(f"{key} must be an integer")
    if raw < minimum or raw > maximum:
        raise ValidationError(f"{key} must be between {minimum} and {maximum}")
    return raw


def _shopify_search_quote(value: str) -> str:
    escaped = value.strip().replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _low_stock_items(connection: dict[str, Any], threshold: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item_edge in connection.get("edges") or []:
        item = item_edge.get("node") if isinstance(item_edge, dict) else None
        if not isinstance(item, dict):
            continue
        variant = _first_variant(item)
        for level_edge in _dict_value(item.get("inventoryLevels")).get("edges") or []:
            level = level_edge.get("node") if isinstance(level_edge, dict) else None
            if not isinstance(level, dict):
                continue
            available = _quantity_value(level.get("quantities"), "available")
            if available >= threshold:
                continue
            location = _dict_value(level.get("location"))
            product = _dict_value(variant.get("product"))
            out.append(
                {
                    "inventoryItemId": item.get("id"),
                    "variantId": variant.get("id"),
                    "productId": product.get("id"),
                    "productTitle": product.get("title") or "Unknown",
                    "variantTitle": variant.get("title") or "Default",
                    "sku": item.get("sku") or "",
                    "available": available,
                    "locationId": location.get("id"),
                    "location": location.get("name") or "Unknown",
                }
            )
    return out


def _first_variant(item: dict[str, Any]) -> dict[str, Any]:
    variants = _dict_value(item.get("variants"))
    nodes = variants.get("nodes")
    if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict):
        return nodes[0]
    edges = variants.get("edges")
    if isinstance(edges, list) and edges:
        node = edges[0].get("node") if isinstance(edges[0], dict) else None
        if isinstance(node, dict):
            return node
    return {}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _quantity_value(quantities: Any, name: str) -> int:
    for quantity in quantities or []:
        if isinstance(quantity, dict) and quantity.get("name") == name:
            value = quantity.get("quantity")
            return value if isinstance(value, int) and not isinstance(value, bool) else 0
    return 0


def _customer_input(payload: dict[str, Any], *, include_id: bool) -> dict[str, Any]:
    fields = ["firstName", "lastName", "email", "phone", "tags", "note"]
    out = _pick(payload, fields)
    if include_id:
        out["id"] = _required_str(payload, "id")
    return out


def _product_input(payload: dict[str, Any], *, include_id: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if include_id:
        out["id"] = _required_str(payload, "id")
    mapping = {
        "title": "title",
        "description": "descriptionHtml",
        "vendor": "vendor",
        "product_type": "productType",
        "status": "status",
        "tags": "tags",
    }
    for source, target in mapping.items():
        if payload.get(source) is not None:
            out[target] = payload[source]
    if not include_id:
        out["title"] = _required_str(payload, "title")
    return out


def _variant_input(payload: dict[str, Any], *, include_id: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if include_id:
        out["id"] = _required_str(payload, "id")
    if payload.get("price") is not None:
        out["price"] = payload["price"]
    elif not include_id:
        out["price"] = _required_str(payload, "price")
    if payload.get("sku") is not None:
        out["inventoryItem"] = {"sku": payload["sku"]}
    if payload.get("barcode") is not None:
        out["barcode"] = payload["barcode"]
    if payload.get("options") is not None:
        out["optionValues"] = [_option_value(value) for value in payload["options"]]
    return out


def _draft_order_input(payload: dict[str, Any]) -> dict[str, Any]:
    line_items = payload.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        raise ValidationError("line_items is required")
    out: dict[str, Any] = {"lineItems": [_draft_order_line_item(item) for item in line_items]}
    if payload.get("customer_id"):
        out["customerId"] = payload["customer_id"]
    if payload.get("note"):
        out["note"] = payload["note"]
    if payload.get("shipping_address") is not None:
        out["shippingAddress"] = _shipping_address_input(payload["shipping_address"])
    return out


def _draft_order_line_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValidationError("line_items entries must be objects")
    return {
        "variantId": _required_str(item, "variant_id"),
        "quantity": _int_value(item, "quantity", minimum=1, maximum=1_000_000),
    }


def _shipping_address_input(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("shipping_address must be an object")
    out = {
        "address1": _required_str(value, "address1"),
        "city": _required_str(value, "city"),
        "zip": _required_str(value, "zip"),
        "country": _required_str(value, "country"),
    }
    for key in ("address2", "province", "firstName", "lastName"):
        if value.get(key) is not None:
            out[key] = _required_str(value, key)
    return out


def _option_value(value: Any) -> dict[str, Any]:
    text = str(value)
    if ":" in text:
        option_name, name = text.split(":", 1)
        if option_name:
            return {"optionName": option_name, "name": name}
    return {"name": text}


def _pick(payload: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: payload[field] for field in fields if payload.get(field) is not None}


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} is required")
    return value.strip()


def _limit(payload: dict[str, Any], *, default: int, maximum: int) -> int:
    raw = payload.get("limit", default)
    if raw is None:
        raw = default
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValidationError("limit must be an integer")
    return max(1, min(raw, maximum))


def _clean_variables(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}
