"""Host Shopify inventory report computation and validation."""

from typing import Any

from stackos.repositories.base import ValidationError


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


def _limit(payload: dict[str, Any], *, default: int, maximum: int) -> int:
    raw = payload.get("limit", default)
    if raw is None:
        raw = default
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValidationError("limit must be an integer")
    return max(1, min(raw, maximum))


def _clean_variables(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}
