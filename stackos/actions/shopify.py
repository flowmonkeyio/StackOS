"""Shopify Admin GraphQL action connector."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from stackos_connectors.connectors.shopify.integration import ShopifyIntegration

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.shopify_payloads import (
    _clean_variables,
    _dict_value,
    _int_value,
    _inventory_risk_item,
    _limit,
    _low_stock_items,
)
from stackos.actions.vendor_utils import (
    issue,
    unknown_operation,
)
from stackos.repositories.base import ValidationError

MAX_GRAPHQL_PAGES = 20


class ShopifyActionConnector:
    """Decision-free adapter for curated Shopify Admin GraphQL actions."""

    key = "shopify"

    def __init__(self, *, client=None, options=None) -> None:
        self._client = client
        self._options = options

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.action_key not in {"low_stock_report", "inventory_risk_report"}:
            from stackos.actions.package_bridge import PackageActionConnector

            return PackageActionConnector(
                "shopify", client=self._client, options=self._options
            ).validate(request)
        if request.operation != "admin.graphql":
            return unknown_operation(request)
        issues: list[ActionValidationIssue] = []
        spec = _shopify_spec(request, issues)
        if not spec:
            return issues
        mode = str(spec.get("mode") or "")
        if mode not in {"graphql", "shopifyql", "tag_mutations", "inventory_risk"}:
            issues.append(
                issue(
                    "$.config.shopify.mode", f"unsupported Shopify mode {mode!r}", "enum_mismatch"
                )
            )
        if mode in {"graphql", "shopifyql", "inventory_risk"} and not spec.get("graphql_file"):
            issues.append(
                issue("$.config.shopify.graphql_file", "graphql_file is required", "required")
            )
        if request.action_key == "manage_product_tags":
            add = request.input_json.get("add")
            remove = request.input_json.get("remove")
            if not add and not remove:
                issues.append(issue("$", "provide at least one tag in add or remove", "required"))
        try:
            if request.action_key == "inventory_risk_report":
                _int_value(
                    request.input_json,
                    "days_of_stock_threshold",
                    default=30,
                    minimum=1,
                    maximum=3650,
                )
            else:
                _int_value(
                    request.input_json, "threshold", default=10, minimum=0, maximum=1_000_000
                )
            _limit(request.input_json, default=25, maximum=100)
        except ValidationError as exc:
            issues.append(issue("$", exc.detail, "validation_error"))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "admin.graphql":
            raise ValidationError(f"unsupported Shopify operation {request.operation!r}")
        spec = _shopify_spec(request)
        if request.action_key in {"low_stock_report", "inventory_risk_report"}:
            from contextlib import nullcontext

            from stackos_connectors import CallOptions, ConnectorClient
            from stackos_connectors.catalog import load_registry

            from stackos.integrations._rate_limit import get_bucket

            if self._client is None:
                self._client = ConnectorClient(
                    registry=load_registry("connectors/shopify/catalog.json")
                )
            options = self._options or CallOptions()
            limiter = (
                options.rate_limiter
                if options.rate_limiter is not None
                else get_bucket(
                    project_id=request.project_id,
                    kind="shopify",
                    qps=ShopifyIntegration.default_qps,
                )
            )
            async with (
                nullcontext(options.http)
                if options.http is not None
                else httpx.AsyncClient(timeout=60.0)
            ) as http:
                pages = _NativeShopifyPages(
                    request,
                    client=self._client,
                    options=replace(options, http=http, rate_limiter=limiter),
                )
                try:
                    if request.action_key == "low_stock_report":
                        return await _execute_low_stock_report(request, pages, spec)
                    return await _execute_inventory_risk(request, pages, spec)
                except ActionConnectorError:
                    raise
                except Exception:
                    if not pages.completed_pages:
                        raise
                    raise ActionConnectorError(
                        "Shopify report computation failed after a provider read",
                        metadata_json={
                            "vendor": "shopify",
                            "operation": request.action_key,
                            "provider_executed": True,
                            "completed_pages": pages.completed_pages,
                            "retry_safe": True,
                        },
                    ) from None
        from stackos.actions.package_bridge import PackageActionConnector

        return await PackageActionConnector(
            "shopify", client=self._client, options=self._options
        ).execute(request)


def _shopify_spec(
    request: ActionConnectorRequest,
    issues: list[ActionValidationIssue] | None = None,
) -> dict[str, Any]:
    raw = request.config_json.get("shopify")
    if not isinstance(raw, dict):
        if issues is not None:
            issues.append(
                issue("$.config.shopify", "shopify action config is required", "required")
            )
            return {}
        raise ValidationError("shopify action config is required")
    action_name = raw.get("action_name")
    if action_name != request.action_key:
        message = "shopify action config action_name must match action key"
        if issues is not None:
            issues.append(issue("$.config.shopify.action_name", message, "validation_error"))
            return raw
        raise ValidationError(message)
    return raw


async def _execute_low_stock_report(
    request: ActionConnectorRequest,
    integration: _NativeShopifyPages,
    spec: dict[str, Any],
) -> ActionConnectorResult:
    del spec
    threshold = _int_value(
        request.input_json,
        "threshold",
        default=10,
        minimum=0,
        maximum=1_000_000,
    )
    limit = _limit(request.input_json, default=25, maximum=100)
    cursor = request.input_json.get("cursor")
    after = cursor if isinstance(cursor, str) else None
    items: list[dict[str, Any]] = []
    truncated = False
    combined_metadata: dict[str, Any] = {}

    for _page in range(MAX_GRAPHQL_PAGES):
        data, metadata = await integration.page(
            "list_inventory_item_availability", _clean_variables({"first": 50, "after": after})
        )
        combined_metadata.update(metadata)
        connection = data.get("inventoryItems") if isinstance(data, dict) else None
        if not isinstance(connection, dict):
            break
        items.extend(_low_stock_items(connection, threshold))
        page_info = _dict_value(connection.get("pageInfo"))
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    else:
        truncated = True

    items.sort(key=lambda item: int(item.get("available") or 0))
    limited = items[:limit]
    metadata = {
        "vendor": "shopify",
        "operation": request.action_key,
        "threshold": threshold,
        "truncated": truncated,
        **combined_metadata,
    }
    return _result(
        request.action_key,
        data={
            "count": len(limited),
            "threshold": threshold,
            "items": limited,
            "truncated": truncated,
        },
        metadata=metadata,
    )


async def _execute_inventory_risk(
    request: ActionConnectorRequest,
    integration: _NativeShopifyPages,
    spec: dict[str, Any],
) -> ActionConnectorResult:
    del spec
    threshold = int(request.input_json.get("days_of_stock_threshold") or 30)
    limit = int(request.input_json.get("limit") or 25)
    variants: list[dict[str, Any]] = []
    after: str | None = None
    truncated = False

    for _page in range(MAX_GRAPHQL_PAGES):
        variables = {"first": 250, "after": after}
        data, _metadata = await integration.page(
            "list_product_variant_inventory", _clean_variables(variables)
        )
        connection = data.get("productVariants") if isinstance(data, dict) else None
        if not isinstance(connection, dict):
            break
        for edge in connection.get("edges") or []:
            node = edge.get("node") if isinstance(edge, dict) else None
            if isinstance(node, dict):
                product = _dict_value(node.get("product"))
                variants.append(
                    {
                        "variantId": node.get("id"),
                        "variantTitle": node.get("title"),
                        "sku": node.get("sku") or "",
                        "inventoryQuantity": node.get("inventoryQuantity") or 0,
                        "productId": product.get("id"),
                        "productTitle": product.get("title") or "Unknown Product",
                    }
                )
        page_info = _dict_value(connection.get("pageInfo"))
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    else:
        truncated = True

    sales_velocity, sales_truncated = await _recent_sales_velocity(integration, request.action_key)
    truncated = truncated or sales_truncated
    items = [_inventory_risk_item(item, sales_velocity, threshold) for item in variants]
    risk_order = {"understock": 0, "overstock": 1, "healthy": 2}
    items.sort(key=lambda item: risk_order.get(str(item.get("riskCategory")), 99))
    limited = items[:limit]
    summary = {
        "totalVariants": len(variants),
        "understock": sum(1 for item in items if item.get("riskCategory") == "understock"),
        "overstock": sum(1 for item in items if item.get("riskCategory") == "overstock"),
        "healthy": sum(1 for item in items if item.get("riskCategory") == "healthy"),
        "truncated": truncated,
    }
    return _result(
        request.action_key,
        data={"items": limited, "summary": summary},
        metadata={"vendor": "shopify", "operation": request.action_key, "truncated": truncated},
    )


async def _recent_sales_velocity(
    integration: _NativeShopifyPages,
    action_key: str,
) -> tuple[dict[str, int], bool]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=30)
    query_text = f"created_at:>={start.isoformat()} created_at:<={end.isoformat()}"
    after: str | None = None
    velocity: dict[str, int] = {}
    truncated = False
    for _page in range(MAX_GRAPHQL_PAGES):
        data, _metadata = await integration.page(
            "list_order_variant_quantities",
            _clean_variables({"first": 250, "query": query_text, "after": after}),
        )
        connection = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(connection, dict):
            break
        for order_edge in connection.get("edges") or []:
            order = order_edge.get("node") if isinstance(order_edge, dict) else None
            line_items = (order or {}).get("lineItems") if isinstance(order, dict) else None
            for item_edge in (line_items or {}).get("edges") or []:
                item = item_edge.get("node") if isinstance(item_edge, dict) else None
                if not isinstance(item, dict):
                    continue
                variant = _dict_value(item.get("variant"))
                variant_id = variant.get("id")
                if isinstance(variant_id, str) and variant_id:
                    velocity[variant_id] = velocity.get(variant_id, 0) + int(
                        item.get("quantity") or 0
                    )
            line_items_page = line_items.get("pageInfo") if isinstance(line_items, dict) else None
            if isinstance(line_items_page, dict) and line_items_page.get("hasNextPage"):
                truncated = True
        page_info = _dict_value(connection.get("pageInfo"))
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    else:
        truncated = True
    return velocity, truncated


def _result(action_key: str, *, data: Any, metadata: dict[str, Any]) -> ActionConnectorResult:
    return ActionConnectorResult(
        output_json={
            "provider": "shopify",
            "operation": action_key,
            "data": data,
        },
        metadata_json={"vendor": "shopify", "operation": action_key, **metadata},
    )


__all__ = ["ShopifyActionConnector"]


class _NativeShopifyPages:
    """Call named native pages inside one authorized host report action."""

    def __init__(self, request: ActionConnectorRequest, *, client, options) -> None:
        from stackos.actions.package_bridge import PackageActionConnector

        self.request = request
        self.adapter = PackageActionConnector("shopify", client=client, options=options)
        self.completed_pages = 0

    async def page(
        self, action_key: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            result = await self.adapter.execute_native(
                replace(
                    self.request, action_key=action_key, operation="admin.graphql", input_json=data
                )
            )
        except ActionConnectorError as exc:
            exc.metadata_json = {**exc.metadata_json, "operation": self.request.action_key}
            if self.completed_pages:
                exc.metadata_json["provider_executed"] = True
                exc.metadata_json["completed_pages"] = self.completed_pages
            raise
        self.completed_pages += 1
        metadata = {**(result.metadata_json or {}), "operation": self.request.action_key}
        return result.output_json["data"], metadata
