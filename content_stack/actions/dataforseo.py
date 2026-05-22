"""DataForSEO action connector."""

from __future__ import annotations

import httpx

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.vendor_utils import (
    cost_cents,
    int_range,
    optional_str,
    required_str,
    result,
    str_list,
    unknown_operation,
)
from content_stack.integrations.dataforseo import DataForSeoIntegration
from content_stack.repositories.base import ValidationError


class DataForSeoActionConnector:
    """Decision-free adapter for DataForSEO actions."""

    key = "dataforseo"

    _OP_COSTS = DataForSeoIntegration._PRE_EMPT_COSTS

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "keyword.research" | "keyword_volume":
                str_list(payload, "keywords", issues, required=True)
                optional_str(payload, "location", issues)
                optional_str(payload, "language", issues)
            case "serp.analyze" | "serp":
                required_str(payload, "keyword", issues)
                optional_str(payload, "location", issues)
                optional_str(payload, "language", issues)
                int_range(payload, "depth", issues, minimum=1, maximum=700)
            case "domain_intersection":
                str_list(payload, "domains", issues, required=True, length=2)
                optional_str(payload, "location", issues)
                optional_str(payload, "language", issues)
            case "keywords_for_site":
                required_str(payload, "target", issues)
                optional_str(payload, "location", issues)
                optional_str(payload, "language", issues)
            case "paa":
                required_str(payload, "keyword", issues)
                optional_str(payload, "location", issues)
                optional_str(payload, "language", issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        operation = self._wrapper_operation(request.operation)
        return cost_cents(self._OP_COSTS.get(operation, 0.001))

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("DataForSEO action requires a resolved credential")
        login = (request.credential.config_json or {}).get("login")
        if not isinstance(login, str) or not login:
            raise ValidationError("DataForSEO credential missing config_json.login")
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = DataForSeoIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                login=login,
            )
            match request.operation:
                case "keyword.research" | "keyword_volume":
                    call_result = await client.keyword_volume(
                        keywords=list(payload["keywords"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case "serp.analyze" | "serp":
                    call_result = await client.serp(
                        keyword=str(payload["keyword"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                        depth=int(payload.get("depth", 100)),
                    )
                case "domain_intersection":
                    call_result = await client.intersection(
                        domains=list(payload["domains"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case "keywords_for_site":
                    call_result = await client.keywords_for_site(
                        target=str(payload["target"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case "paa":
                    call_result = await client.paa(
                        keyword=str(payload["keyword"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case _:
                    raise ValidationError(f"unsupported DataForSEO operation {request.operation!r}")
        return result("dataforseo", request.operation, call_result.data, call_result.cost_usd)

    def _wrapper_operation(self, operation: str) -> str:
        return {
            "keyword.research": "keyword_volume",
            "serp.analyze": "serp",
        }.get(operation, operation)


__all__ = ["DataForSeoActionConnector"]
