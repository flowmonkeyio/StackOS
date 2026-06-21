"""Google Search Console action connector."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import connector_error_from_integration
from stackos.actions.vendor_utils import (
    credential_payload,
    int_range,
    issue,
    optional_str,
    required_str,
    result,
    str_list,
    unknown_operation,
)
from stackos.integrations.google_search_console import GoogleSearchConsoleIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

_SEARCH_TYPES = {"web", "image", "video", "news", "discover", "googleNews"}
_AGGREGATION_TYPES = {"auto", "byProperty", "byPage", "byNewsShowcasePanel"}
_DATA_STATES = {"final", "all", "hourly_all"}
_DIMENSIONS = {"country", "device", "page", "query", "searchAppearance", "date", "hour"}
_FILTER_DIMENSIONS = {"country", "device", "page", "query", "searchAppearance"}
_FILTER_OPERATORS = {
    "contains",
    "equals",
    "notContains",
    "notEquals",
    "includingRegex",
    "excludingRegex",
}
_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class GoogleSearchConsoleActionConnector:
    """Decision-free adapter for Google Search Console read actions."""

    key = "google-search-console"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "sites.list":
                pass
            case "search_analytics.query":
                required_str(payload, "site_url", issues)
                required_str(payload, "start_date", issues)
                required_str(payload, "end_date", issues)
                str_list(payload, "dimensions", issues)
                start_date = self._date(payload.get("start_date"), "$.start_date", issues)
                end_date = self._date(payload.get("end_date"), "$.end_date", issues)
                if start_date is not None and end_date is not None and start_date > end_date:
                    issues.append(
                        issue("$.end_date", "end_date must be on or after start_date", "range")
                    )
                self._dimensions(payload, issues)
                self._enum(payload, "type", _SEARCH_TYPES, issues)
                self._enum(payload, "aggregation_type", _AGGREGATION_TYPES, issues)
                self._enum(payload, "data_state", _DATA_STATES, issues)
                int_range(payload, "row_limit", issues, minimum=1, maximum=25_000)
                self._non_negative_int(payload, "start_row", issues)
                self._dimension_filter_groups(payload, issues)
                self._site_url(payload.get("site_url"), "$.site_url", issues)
            case "sitemaps.list":
                required_str(payload, "site_url", issues)
                optional_str(payload, "sitemap_index", issues)
                self._site_url(payload.get("site_url"), "$.site_url", issues)
                self._sitemap_index(payload.get("sitemap_index"), issues)
            case "url.inspect":
                required_str(payload, "site_url", issues)
                required_str(payload, "inspection_url", issues)
                optional_str(payload, "language_code", issues)
                self._site_url(payload.get("site_url"), "$.site_url", issues)
                self._inspection_url(payload, issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                client = GoogleSearchConsoleIntegration(
                    payload=credential_payload(request),
                    project_id=request.project_id,
                    http=http,
                )
                match request.operation:
                    case "sites.list":
                        call_result = await client.sites_list()
                    case "search_analytics.query":
                        body = self._search_analytics_body(payload)
                        call_result = await client.search_analytics_query(
                            site_url=str(payload["site_url"]),
                            request_body=body,
                        )
                        return self._search_analytics_result(payload, call_result.data)
                    case "sitemaps.list":
                        call_result = await client.sitemaps_list(
                            site_url=str(payload["site_url"]),
                            sitemap_index=(
                                str(payload["sitemap_index"])
                                if payload.get("sitemap_index")
                                else None
                            ),
                        )
                    case "url.inspect":
                        call_result = await client.url_inspect(
                            site_url=str(payload["site_url"]),
                            inspection_url=str(payload["inspection_url"]),
                            language_code=(
                                str(payload["language_code"])
                                if payload.get("language_code")
                                else None
                            ),
                        )
                    case _:
                        raise ValidationError(
                            f"unsupported Google Search Console operation {request.operation!r}"
                        )
        except (IntegrationDownError, RateLimitedError) as exc:
            raise connector_error_from_integration(
                exc,
                provider=self.key,
                operation=request.operation,
            ) from exc
        return result(self.key, request.operation, call_result.data, call_result.cost_usd)

    @staticmethod
    def _search_analytics_body(payload: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "startDate": payload["start_date"],
            "endDate": payload["end_date"],
        }
        mapping = {
            "dimensions": "dimensions",
            "type": "type",
            "dimension_filter_groups": "dimensionFilterGroups",
            "aggregation_type": "aggregationType",
            "row_limit": "rowLimit",
            "start_row": "startRow",
            "data_state": "dataState",
        }
        for src, dest in mapping.items():
            if src in payload and payload[src] is not None:
                body[dest] = payload[src]
        return body

    def _search_analytics_result(self, payload: dict[str, Any], data: Any) -> ActionConnectorResult:
        output = data if isinstance(data, dict) else {"data": data}
        row_limit = payload.get("row_limit")
        start_row = payload.get("start_row", 0)
        rows = output.get("rows")
        if (
            isinstance(row_limit, int)
            and isinstance(start_row, int)
            and isinstance(rows, list)
            and len(rows) == row_limit
        ):
            output = dict(output)
            output["next_start_row"] = start_row + row_limit
        return result(self.key, "search_analytics.query", output, 0.0)

    @staticmethod
    def _enum(
        payload: dict[str, Any],
        key: str,
        allowed: set[str],
        issues: list[ActionValidationIssue],
    ) -> None:
        value = payload.get(key)
        if value is not None and value not in allowed:
            issues.append(issue(f"$.{key}", f"{key} must be one of {sorted(allowed)}", "enum"))

    @staticmethod
    def _non_negative_int(
        payload: dict[str, Any],
        key: str,
        issues: list[ActionValidationIssue],
    ) -> None:
        value = payload.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            issues.append(issue(f"$.{key}", f"{key} must be a non-negative integer", "range"))

    @staticmethod
    def _date(
        value: Any,
        path: str,
        issues: list[ActionValidationIssue],
    ) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            issues.append(issue(path, "date must use YYYY-MM-DD format", "format"))
            return None
        if parsed.isoformat() != value:
            issues.append(issue(path, "date must use YYYY-MM-DD format", "format"))
            return None
        return parsed

    @staticmethod
    def _dimensions(payload: dict[str, Any], issues: list[ActionValidationIssue]) -> None:
        value = payload.get("dimensions")
        if not isinstance(value, list):
            return
        seen: set[str] = set()
        for idx, dimension in enumerate(value):
            path = f"$.dimensions[{idx}]"
            if not isinstance(dimension, str) or not dimension:
                return
            if dimension not in _DIMENSIONS:
                issues.append(
                    issue(path, f"dimension must be one of {sorted(_DIMENSIONS)}", "enum")
                )
                continue
            if dimension in seen:
                issues.append(issue(path, "dimensions cannot repeat the same value", "duplicate"))
            seen.add(dimension)

    @staticmethod
    def _dimension_filter_groups(
        payload: dict[str, Any],
        issues: list[ActionValidationIssue],
    ) -> None:
        value = payload.get("dimension_filter_groups")
        if value is None:
            return
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            issues.append(
                issue(
                    "$.dimension_filter_groups",
                    "dimension_filter_groups must be an array of objects",
                    "type_mismatch",
                )
            )
            return
        for group_idx, group in enumerate(value):
            prefix = f"$.dimension_filter_groups[{group_idx}]"
            group_type = group.get("groupType")
            if group_type is not None and group_type != "and":
                issues.append(
                    issue(f"{prefix}.groupType", "groupType must be 'and'", "enum")
                )
            filters = group.get("filters")
            if filters is None:
                continue
            if not isinstance(filters, list) or not all(isinstance(item, dict) for item in filters):
                issues.append(
                    issue(
                        f"{prefix}.filters",
                        "filters must be an array of objects",
                        "type_mismatch",
                    )
                )
                continue
            for filter_idx, item in enumerate(filters):
                filter_path = f"{prefix}.filters[{filter_idx}]"
                dimension = item.get("dimension")
                if not isinstance(dimension, str) or not dimension:
                    issues.append(
                        issue(f"{filter_path}.dimension", "dimension is required", "required")
                    )
                elif dimension not in _FILTER_DIMENSIONS:
                    issues.append(
                        issue(
                            f"{filter_path}.dimension",
                            f"dimension must be one of {sorted(_FILTER_DIMENSIONS)}",
                            "enum",
                        )
                    )
                operator = item.get("operator")
                if operator is not None and operator not in _FILTER_OPERATORS:
                    issues.append(
                        issue(
                            f"{filter_path}.operator",
                            f"operator must be one of {sorted(_FILTER_OPERATORS)}",
                            "enum",
                        )
                    )
                expression = item.get("expression")
                if not isinstance(expression, str) or not expression:
                    issues.append(
                        issue(f"{filter_path}.expression", "expression is required", "required")
                    )
                elif len(expression) > 4096:
                    issues.append(
                        issue(
                            f"{filter_path}.expression",
                            "expression must be at most 4096 characters",
                            "length",
                        )
                    )

    @staticmethod
    def _site_url(value: Any, path: str, issues: list[ActionValidationIssue]) -> None:
        if not isinstance(value, str):
            return
        if value.startswith("sc-domain:"):
            domain = value.removeprefix("sc-domain:")
            if not domain or domain.strip() != domain or not _DOMAIN_RE.fullmatch(domain):
                issues.append(issue(path, "sc-domain site_url must include a domain", "format"))
            return
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append(
                issue(
                    path, "site_url must be a URL-prefix property or sc-domain property", "format"
                )
            )
            return
        if not value.endswith("/"):
            issues.append(
                issue(path, "URL-prefix Search Console site_url must end with /", "format")
            )

    @staticmethod
    def _sitemap_index(value: Any, issues: list[ActionValidationIssue]) -> None:
        if value is None or not isinstance(value, str):
            return
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append(
                issue("$.sitemap_index", "sitemap_index must be an absolute URL", "format")
            )

    @staticmethod
    def _inspection_url(
        payload: dict[str, Any],
        issues: list[ActionValidationIssue],
    ) -> None:
        inspection_url = payload.get("inspection_url")
        if not isinstance(inspection_url, str):
            return
        parsed = urlparse(inspection_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append(
                issue("$.inspection_url", "inspection_url must be an absolute URL", "format")
            )
            return
        site_url = payload.get("site_url")
        if (
            isinstance(site_url, str)
            and site_url.startswith(("http://", "https://"))
            and site_url.endswith("/")
            and not inspection_url.startswith(site_url)
        ):
            issues.append(
                issue(
                    "$.inspection_url",
                    "inspection_url must be under the URL-prefix site_url",
                    "format",
                )
            )


__all__ = ["GoogleSearchConsoleActionConnector"]
