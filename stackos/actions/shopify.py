"""Shopify Admin GraphQL action connector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import connector_error_from_integration
from stackos.actions.shopify_payloads import (
    _clean_variables,
    _dict_value,
    _int_value,
    _inventory_risk_item,
    _limit,
    _low_stock_items,
    _required_str,
    _shopifyql_queries,
    _validate_action_payload,
    _variables_for_action,
)
from stackos.actions.vendor_utils import (
    credential_config_str,
    credential_payload,
    issue,
    unknown_operation,
)
from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.integrations.shopify import SHOPIFY_DEFAULT_API_VERSION, ShopifyIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

MAX_GRAPHQL_PAGES = 20
_TAGS_ADD = """mutation TagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}"""

_TAGS_REMOVE = """mutation TagsRemove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}"""

_RECENT_SALES_QUERY = """query RecentSales($first: Int!, $query: String, $after: String) {
  orders(first: $first, query: $query, after: $after) {
    edges {
      node {
        lineItems(first: 250) {
          edges {
            node {
              quantity
              variant { id }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""


class ShopifyActionConnector:
    """Decision-free adapter for curated Shopify Admin GraphQL actions."""

    key = "shopify"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
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
            _validate_action_payload(request.action_key, request.input_json)
        except ValidationError as exc:
            issues.append(issue("$", exc.detail, "validation_error"))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "admin.graphql":
            raise ValidationError(f"unsupported Shopify operation {request.operation!r}")
        spec = _shopify_spec(request)
        store_domain = credential_config_str(
            request,
            "store_domain",
            "shop_domain",
            "shop",
            label="config_json.store_domain",
        )
        config = request.credential.config_json if request.credential is not None else {}
        api_version = (
            str(config.get("api_version")).strip()
            if isinstance(config, dict) and config.get("api_version") is not None
            else SHOPIFY_DEFAULT_API_VERSION
        )
        async with httpx.AsyncClient(timeout=60.0) as http:
            integration = ShopifyIntegration(
                payload=credential_payload(request),
                project_id=request.project_id,
                http=http,
                store_domain=store_domain,
                api_version=api_version,
            )
            try:
                mode = str(spec.get("mode") or "graphql")
                if mode == "shopifyql":
                    return await _execute_shopifyql_action(request, integration, spec)
                if mode == "tag_mutations":
                    return await _execute_tag_mutations(request, integration)
                if mode == "inventory_risk":
                    return await _execute_inventory_risk(request, integration, spec)
                return await _execute_graphql_action(request, integration, spec)
            except (IntegrationDownError, RateLimitedError) as exc:
                raise connector_error_from_integration(
                    exc,
                    provider="shopify",
                    operation=request.action_key,
                ) from exc


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


async def _execute_graphql_action(
    request: ActionConnectorRequest,
    integration: ShopifyIntegration,
    spec: dict[str, Any],
) -> ActionConnectorResult:
    if request.action_key == "low_stock_report":
        return await _execute_low_stock_report(request, integration, spec)
    query = _read_shopify_asset(str(spec["graphql_file"]))
    variables = _variables_for_action(request.action_key, request.input_json)
    body, metadata = await _admin_graphql(
        integration,
        action_key=request.action_key,
        query=query,
        variables=variables,
    )
    data = _successful_graphql_data(body, action_key=request.action_key, metadata=metadata)
    return _result(request.action_key, data=data, metadata=metadata)


async def _execute_low_stock_report(
    request: ActionConnectorRequest,
    integration: ShopifyIntegration,
    spec: dict[str, Any],
) -> ActionConnectorResult:
    query = _read_shopify_asset(str(spec["graphql_file"]))
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
        body, metadata = await _admin_graphql(
            integration,
            action_key=request.action_key,
            query=query,
            variables=_clean_variables({"first": 50, "after": after}),
        )
        combined_metadata.update(metadata)
        data = _successful_graphql_data(
            body,
            action_key=request.action_key,
            metadata=metadata,
        )
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


async def _execute_tag_mutations(
    request: ActionConnectorRequest,
    integration: ShopifyIntegration,
) -> ActionConnectorResult:
    payload = request.input_json
    product_id = _required_str(payload, "id")
    output: dict[str, Any] = {"productId": product_id}
    combined_metadata: dict[str, Any] = {}
    if payload.get("add"):
        variables = {"id": product_id, "tags": list(payload["add"])}
        body, metadata = await _admin_graphql(
            integration,
            action_key=request.action_key,
            query=_TAGS_ADD,
            variables=variables,
        )
        output["add"] = _successful_graphql_data(
            body, action_key=request.action_key, metadata=metadata
        )
        combined_metadata.update(metadata)
    if payload.get("remove"):
        variables = {"id": product_id, "tags": list(payload["remove"])}
        body, metadata = await _admin_graphql(
            integration,
            action_key=request.action_key,
            query=_TAGS_REMOVE,
            variables=variables,
        )
        output["remove"] = _successful_graphql_data(
            body, action_key=request.action_key, metadata=metadata
        )
        combined_metadata.update(metadata)
    return _result(request.action_key, data=output, metadata=combined_metadata)


async def _execute_shopifyql_action(
    request: ActionConnectorRequest,
    integration: ShopifyIntegration,
    spec: dict[str, Any],
) -> ActionConnectorResult:
    wrapper_query = _read_shopify_asset(str(spec["graphql_file"]))
    queries = _shopifyql_queries(request.action_key, request.input_json)
    results: list[dict[str, Any]] = []
    combined_metadata: dict[str, Any] = {}
    for label, query in queries:
        body, metadata = await _admin_graphql(
            integration,
            action_key=request.action_key,
            query=wrapper_query,
            variables={"query": query},
        )
        data = _successful_graphql_data(body, action_key=request.action_key, metadata=metadata)
        results.append({"name": label, "query": query, "result": _shopifyql_result(data)})
        combined_metadata.update(metadata)
    output: dict[str, Any] = results[0] if len(results) == 1 else {"queries": results}
    return _result(request.action_key, data=output, metadata=combined_metadata)


def _shopifyql_result(data: dict[str, Any]) -> dict[str, Any]:
    result = data.get("shopifyqlQuery")
    if not isinstance(result, dict):
        raise ActionConnectorError(
            "ShopifyQL query failed: missing shopifyqlQuery result",
            provider_error={"message": "missing shopifyqlQuery"},
        )
    parse_errors = result.get("parseErrors")
    if parse_errors:
        provider_error = {"parseErrors": redact_secrets(parse_errors)}
        raise ActionConnectorError(
            f"ShopifyQL parse error: {_user_error_summary(parse_errors)}",
            provider_error=provider_error,
            output_json={
                "status": "failed",
                "provider_error": provider_error,
                "parseErrors": redact_secrets(parse_errors),
            },
        )
    table_data = result.get("tableData")
    if not isinstance(table_data, dict):
        return {"data": [], "columns": []}
    columns = [
        {
            "name": col.get("name"),
            "dataType": col.get("dataType"),
            "displayName": col.get("displayName"),
        }
        for col in table_data.get("columns") or []
        if isinstance(col, dict)
    ]
    rows: list[dict[str, Any]] = []
    for row in table_data.get("rows") or []:
        if isinstance(row, list):
            rows.append(
                {
                    str(columns[idx].get("name")): row[idx] if idx < len(row) else None
                    for idx in range(len(columns))
                }
            )
        elif isinstance(row, dict):
            rows.append(row)
    return {"data": rows, "columns": columns}


async def _execute_inventory_risk(
    request: ActionConnectorRequest,
    integration: ShopifyIntegration,
    spec: dict[str, Any],
) -> ActionConnectorResult:
    inventory_query = _read_shopify_asset(str(spec["graphql_file"]))
    threshold = int(request.input_json.get("days_of_stock_threshold") or 30)
    limit = int(request.input_json.get("limit") or 25)
    variants: list[dict[str, Any]] = []
    after: str | None = None
    truncated = False

    for _page in range(MAX_GRAPHQL_PAGES):
        variables = {"first": 250, "after": after}
        body, _metadata = await _admin_graphql(
            integration,
            action_key=request.action_key,
            query=inventory_query,
            variables=_clean_variables(variables),
        )
        data = _successful_graphql_data(body, action_key=request.action_key, metadata={})
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
    integration: ShopifyIntegration,
    action_key: str,
) -> tuple[dict[str, int], bool]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=30)
    query_text = f"created_at:>={start.isoformat()} created_at:<={end.isoformat()}"
    after: str | None = None
    velocity: dict[str, int] = {}
    truncated = False
    for _page in range(MAX_GRAPHQL_PAGES):
        body, _metadata = await _admin_graphql(
            integration,
            action_key=action_key,
            query=_RECENT_SALES_QUERY,
            variables=_clean_variables({"first": 250, "query": query_text, "after": after}),
        )
        data = _successful_graphql_data(body, action_key=action_key, metadata={})
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


async def _admin_graphql(
    integration: ShopifyIntegration,
    *,
    action_key: str,
    query: str,
    variables: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await integration.admin_graphql(
        query=query,
        variables=_clean_variables(variables or {}),
        op=f"action.{action_key}",
    )
    if not isinstance(result.data, dict):
        raise ActionConnectorError(
            "Shopify returned a non-JSON GraphQL response",
            provider_error={"message": "non-json-response"},
            metadata_json={"vendor": "shopify", "operation": action_key},
        )
    metadata = {
        "vendor": "shopify",
        "operation": action_key,
        "store_domain": integration.store_domain,
        "api_version": integration.api_version,
    }
    if result.metadata:
        metadata.update(result.metadata)
    return result.data, metadata


def _successful_graphql_data(
    body: dict[str, Any],
    *,
    action_key: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    errors = body.get("errors")
    if errors:
        provider_error = redact_secrets({"errors": errors})
        raise ActionConnectorError(
            f"Shopify GraphQL error: {_graphql_error_summary(errors)}",
            provider_error=provider_error,
            output_json={
                "status": "failed",
                "provider_error": provider_error,
                "errors": redact_secrets(errors),
            },
            metadata_json=metadata,
        )
    data = body.get("data")
    if not isinstance(data, dict):
        provider_error = {"message": "missing data"}
        raise ActionConnectorError(
            "Shopify GraphQL response missing data object",
            provider_error=provider_error,
            output_json={
                "status": "failed",
                "provider_error": provider_error,
                "body": redact_secrets(body),
            },
            metadata_json=metadata,
        )
    user_errors = _collect_user_errors(data)
    if user_errors:
        provider_error = {"userErrors": redact_secrets(user_errors)}
        raise ActionConnectorError(
            f"Shopify userErrors: {_user_error_summary(user_errors)}",
            provider_error=provider_error,
            output_json={
                "status": "failed",
                "provider_error": provider_error,
                "data": data,
                "userErrors": redact_secrets(user_errors),
            },
            metadata_json=metadata,
        )
    del action_key
    return data


def _result(action_key: str, *, data: Any, metadata: dict[str, Any]) -> ActionConnectorResult:
    return ActionConnectorResult(
        output_json={
            "provider": "shopify",
            "operation": action_key,
            "data": data,
        },
        metadata_json={"vendor": "shopify", "operation": action_key, **metadata},
    )


def _read_shopify_asset(relative_path: str) -> str:
    asset_path = Path(relative_path)
    if (
        asset_path.is_absolute()
        or ".." in asset_path.parts
        or not asset_path.parts
        or asset_path.parts[0] != "graphql"
    ):
        raise ValidationError("Shopify asset path must stay under graphql/")
    repo_path = Path(__file__).resolve().parents[2] / "plugins" / "shopify" / asset_path
    if repo_path.is_file():
        return repo_path.read_text(encoding="utf-8")
    node = (
        resources.files("stackos")
        .joinpath("_assets")
        .joinpath("plugins")
        .joinpath("shopify")
        .joinpath(relative_path)
    )
    if node.is_file():
        return node.read_text(encoding="utf-8")
    raise ValidationError(f"Shopify asset not found: {relative_path}")


def _collect_user_errors(value: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        user_errors = value.get("userErrors")
        if isinstance(user_errors, list) and user_errors:
            found.extend(user_errors)
        for item in value.values():
            found.extend(_collect_user_errors(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_user_errors(item))
    return found


def _graphql_error_summary(errors: Any) -> str:
    if isinstance(errors, list):
        messages = [
            str(item.get("message") or item)
            for item in errors[:5]
            if isinstance(item, dict) or item is not None
        ]
        return redact_secret_text("; ".join(messages) or "Shopify GraphQL error")
    if isinstance(errors, dict):
        return redact_secret_text(str(errors.get("message") or errors))
    return redact_secret_text(str(errors))


def _user_error_summary(errors: Any) -> str:
    if isinstance(errors, list):
        messages = [
            str(item.get("message") or item)
            for item in errors[:5]
            if isinstance(item, dict) or item is not None
        ]
        return redact_secret_text("; ".join(messages) or "Shopify user error")
    return redact_secret_text(str(errors))


__all__ = ["ShopifyActionConnector"]
