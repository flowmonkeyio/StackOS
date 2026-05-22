"""Reddit action connector."""

from __future__ import annotations

import httpx

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.vendor_utils import (
    credential_payload,
    int_range,
    optional_str,
    required_str,
    result,
    unknown_operation,
)
from content_stack.integrations.reddit import RedditIntegration
from content_stack.repositories.base import ValidationError


class RedditActionConnector:
    """Decision-free adapter for Reddit research utility actions."""

    key = "reddit"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "search_subreddit":
                required_str(payload, "subreddit", issues)
                required_str(payload, "query", issues)
                optional_str(payload, "sort", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
            case "top_questions":
                required_str(payload, "subreddit", issues)
                optional_str(payload, "time_filter", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = RedditIntegration(
                payload=credential_payload(request),
                project_id=request.project_id,
                http=http,
            )
            match request.operation:
                case "search_subreddit":
                    call_result = await client.search_subreddit(
                        subreddit=str(payload["subreddit"]),
                        query=str(payload["query"]),
                        sort=str(payload.get("sort", "relevance")),
                        limit=int(payload.get("limit", 25)),
                    )
                case "top_questions":
                    call_result = await client.top_questions(
                        subreddit=str(payload["subreddit"]),
                        time_filter=str(payload.get("time_filter", "month")),
                        limit=int(payload.get("limit", 50)),
                    )
                case _:
                    raise ValidationError(f"unsupported Reddit operation {request.operation!r}")
        return result("reddit", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["RedditActionConnector"]
