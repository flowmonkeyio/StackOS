"""Shopify Admin GraphQL integration wrapper.

Authentication is static-token only. StackOS stores an operator-supplied
Shopify Admin API access token as daemon-held secret payload and sends it as
``X-Shopify-Access-Token`` to the versioned Admin GraphQL endpoint.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from stackos.artifacts import redact_secret_text
from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError

SHOPIFY_DEFAULT_API_VERSION = "2026-07"
_STORE_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$", re.IGNORECASE)
_API_VERSION_RE = re.compile(r"^\d{4}-(01|04|07|10)$")


class ShopifyConfigError(ValueError):
    """Raised when safe Shopify credential config is missing or unsafe."""


def normalize_shopify_store_domain(raw_domain: str | None) -> str:
    """Validate and normalize a Shopify Admin API store domain."""
    raw = (raw_domain or "").strip()
    if not raw:
        raise ShopifyConfigError("Shopify credential missing store_domain")

    candidate = raw
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ShopifyConfigError("Shopify store URL must be an https URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ShopifyConfigError(
                "Shopify store URL must not contain credentials, query, or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise ShopifyConfigError("Shopify store URL must not include a path")
        candidate = parsed.hostname

    domain = candidate.lower().rstrip(".")
    if not _STORE_DOMAIN_RE.match(domain):
        raise ShopifyConfigError("Shopify store_domain must be a myshopify.com domain")
    return domain


def normalize_shopify_api_version(raw_version: str | None) -> str:
    """Validate a Shopify Admin API version string."""
    version = (raw_version or SHOPIFY_DEFAULT_API_VERSION).strip()
    if version in {"unstable"} or _API_VERSION_RE.match(version):
        return version
    raise ShopifyConfigError("Shopify api_version must be unstable or a stable YYYY-MM version")


def parse_shopify_access_token(payload: bytes) -> str:
    """Read a Shopify Admin API token from raw or JSON credential bytes."""
    text = payload.decode("utf-8").strip()
    if not text:
        raise ShopifyConfigError("Shopify credential payload is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        token = text
    else:
        if not isinstance(parsed, dict):
            raise ShopifyConfigError("Shopify credential JSON must be an object")
        token = str(
            parsed.get("admin_api_access_token")
            or parsed.get("access_token")
            or parsed.get("token")
            or parsed.get("value")
            or ""
        )
    if not token.strip():
        raise ShopifyConfigError("Shopify credential missing admin_api_access_token")
    return token.strip()


def shopify_graphql_url(store_domain: str, api_version: str) -> str:
    return f"https://{store_domain}/admin/api/{api_version}/graphql.json"


def shopify_headers(access_token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }


class ShopifyIntegration(BaseIntegration):
    """Credential health check and Admin GraphQL request wrapper."""

    kind = "shopify"
    vendor = "shopify"
    default_qps = 2.0

    def __init__(
        self,
        *,
        store_domain: str | None = None,
        api_version: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        try:
            self.store_domain = normalize_shopify_store_domain(store_domain)
            self.api_version = normalize_shopify_api_version(api_version)
            self._access_token = parse_shopify_access_token(self.payload)
        except ShopifyConfigError as exc:
            raise IntegrationDownError(
                redact_secret_text(str(exc)),
                data={"vendor": "shopify"},
            ) from exc

    @property
    def endpoint_url(self) -> str:
        return shopify_graphql_url(self.store_domain, self.api_version)

    async def admin_graphql(
        self,
        *,
        query: str,
        variables: dict[str, Any] | None = None,
        op: str = "admin.graphql",
    ) -> IntegrationCallResult:
        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables
        return await self.call(
            op=op,
            method="POST",
            url=self.endpoint_url,
            json_body=body,
            headers=shopify_headers(self._access_token),
            request_log_body=body,
        )

    def _extract_response_metadata(
        self,
        op: str,
        *,
        request: Any,
        response: Any,
        http_response: Any,
    ) -> dict[str, Any] | None:
        del op, request
        metadata: dict[str, Any] = {
            "store_domain": self.store_domain,
            "api_version": self.api_version,
        }
        request_id = http_response.headers.get("x-request-id") or http_response.headers.get(
            "request-id"
        )
        if request_id:
            metadata["request_id"] = request_id
        if isinstance(response, dict):
            cost = response.get("extensions", {}).get("cost")
            if isinstance(cost, dict):
                metadata["cost"] = cost
        return metadata

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.admin_graphql(
            query=("query StackOSShopifyAuthProbe { shop { id name myshopifyDomain } }"),
            op="auth.test",
        )
        body = result.data
        if not isinstance(body, dict):
            return {
                "ok": False,
                "vendor": "shopify",
                "status": "invalid_response",
                "summary": "Shopify auth probe returned a non-JSON response",
                "store_domain": self.store_domain,
                "api_version": self.api_version,
            }
        errors = body.get("errors")
        if errors:
            return {
                "ok": False,
                "vendor": "shopify",
                "status": "graphql_error",
                "summary": redact_secret_text(_graphql_error_summary(errors)),
                "store_domain": self.store_domain,
                "api_version": self.api_version,
            }
        shop = (body.get("data") or {}).get("shop") if isinstance(body.get("data"), dict) else {}
        return {
            "ok": True,
            "vendor": "shopify",
            "status": "ok",
            "store_domain": self.store_domain,
            "api_version": self.api_version,
            "shop_id": shop.get("id") if isinstance(shop, dict) else None,
            "shop_name": shop.get("name") if isinstance(shop, dict) else None,
            "myshopify_domain": shop.get("myshopifyDomain") if isinstance(shop, dict) else None,
        }


def _graphql_error_summary(errors: Any) -> str:
    if isinstance(errors, list):
        messages = [
            str(item.get("message") or item)
            for item in errors[:5]
            if isinstance(item, dict) or item is not None
        ]
        return "; ".join(messages) or "Shopify GraphQL error"
    if isinstance(errors, dict):
        return str(errors.get("message") or errors)
    return str(errors)


__all__ = [
    "SHOPIFY_DEFAULT_API_VERSION",
    "ShopifyConfigError",
    "ShopifyIntegration",
    "normalize_shopify_api_version",
    "normalize_shopify_store_domain",
    "parse_shopify_access_token",
    "shopify_graphql_url",
    "shopify_headers",
]
