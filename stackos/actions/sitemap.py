"""Sitemap action connector."""

from __future__ import annotations

from dataclasses import asdict

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.vendor_utils import (
    float_range,
    int_range,
    issue,
    str_list,
    unknown_operation,
)
from stackos.integrations.sitemap import (
    DEFAULT_TIMEOUT_S,
    MAX_ENTRIES_PER_FETCH,
    MAX_INDEX_DEPTH,
    fetch_sitemap_entries,
)
from stackos.repositories.base import ValidationError


class SitemapActionConnector:
    """Decision-free adapter for public sitemap fetches."""

    key = "sitemap"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != "fetch":
            return unknown_operation(request)
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        str_list(payload, "urls", issues, required=True)
        urls = payload.get("urls")
        if isinstance(urls, list):
            if len(urls) == 0:
                issues.append(issue("$.urls", "urls must contain at least 1 item", "length"))
            if len(urls) > 20:
                issues.append(issue("$.urls", "urls must contain at most 20 items", "length"))
        int_range(payload, "max_entries", issues, minimum=1, maximum=20_000)
        int_range(payload, "max_index_depth", issues, minimum=0, maximum=4)
        float_range(payload, "timeout_s", issues, minimum=0.1, maximum=60)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "fetch":
            raise ValidationError(f"unsupported sitemap operation {request.operation!r}")
        payload = request.input_json
        timeout_s = float(payload.get("timeout_s", DEFAULT_TIMEOUT_S))
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as http:
            call_result = await fetch_sitemap_entries(
                list(payload["urls"]),
                client=http,
                timeout_s=timeout_s,
                max_index_depth=int(payload.get("max_index_depth", MAX_INDEX_DEPTH)),
                max_entries=int(payload.get("max_entries", MAX_ENTRIES_PER_FETCH)),
            )
        return ActionConnectorResult(
            output_json={
                "entries": [asdict(entry) for entry in call_result.entries],
                "errors": [asdict(error) for error in call_result.errors],
            },
            metadata_json={"vendor": "sitemap", "operation": request.operation},
        )


__all__ = ["SitemapActionConnector"]
