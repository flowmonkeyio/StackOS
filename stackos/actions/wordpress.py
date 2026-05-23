"""WordPress action connector."""

from __future__ import annotations

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.vendor_utils import (
    credential_config_str,
    credential_payload,
    required_dict,
    result,
    unknown_operation,
)
from stackos.integrations.wordpress import WordPressIntegration
from stackos.repositories.base import ValidationError


class WordPressActionConnector:
    """Decision-free adapter for WordPress publishing actions."""

    key = "wordpress"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != "post.create":
            return unknown_operation(request)
        issues: list[ActionValidationIssue] = []
        required_dict(request.input_json, "post", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "post.create":
            raise ValidationError(f"unsupported WordPress operation {request.operation!r}")
        payload = request.input_json
        site_url = credential_config_str(
            request,
            "wp_url",
            "site_url",
            "base_url",
            label="config_json.wp_url",
        )
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = WordPressIntegration(
                payload=credential_payload(request),
                project_id=request.project_id,
                http=http,
                site_url=site_url,
            )
            call_result = await client.create_post(post=dict(payload["post"]))
        return result("wordpress", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["WordPressActionConnector"]
