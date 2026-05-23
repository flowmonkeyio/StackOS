"""Ahrefs action connector."""

from __future__ import annotations

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.vendor_utils import (
    credential_payload,
    int_range,
    optional_str,
    required_str,
    result,
    unknown_operation,
)
from stackos.integrations.ahrefs import AhrefsIntegration
from stackos.repositories.base import ValidationError


class AhrefsActionConnector:
    """Decision-free adapter for Ahrefs SEO actions."""

    key = "ahrefs"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "competitor.keywords" | "keywords_for_site":
                required_str(payload, "target", issues)
                optional_str(payload, "country", issues)
                optional_str(payload, "date", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=1000)
            case "backlink.research" | "top_backlinks":
                required_str(payload, "target", issues)
                optional_str(payload, "mode", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=1000)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = AhrefsIntegration(
                payload=credential_payload(request),
                project_id=request.project_id,
                http=http,
            )
            match request.operation:
                case "competitor.keywords" | "keywords_for_site":
                    call_result = await client.keywords_for_site(
                        target=str(payload["target"]),
                        country=str(payload.get("country", "us")),
                        limit=int(payload.get("limit", 100)),
                        date_=payload.get("date"),
                    )
                case "backlink.research" | "top_backlinks":
                    call_result = await client.top_backlinks(
                        target=str(payload["target"]),
                        mode=str(payload.get("mode", "domain")),
                        limit=int(payload.get("limit", 100)),
                    )
                case _:
                    raise ValidationError(f"unsupported Ahrefs operation {request.operation!r}")
        return result("ahrefs", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["AhrefsActionConnector"]
