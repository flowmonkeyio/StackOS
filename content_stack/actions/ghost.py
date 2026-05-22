"""Ghost action connector."""

from __future__ import annotations

import httpx

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.vendor_utils import (
    credential_config_str,
    credential_payload,
    optional_str,
    required_dict,
    result,
    unknown_operation,
)
from content_stack.integrations.ghost import GhostIntegration
from content_stack.repositories.base import ValidationError


class GhostActionConnector:
    """Decision-free adapter for Ghost publishing actions."""

    key = "ghost"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != "post.create":
            return unknown_operation(request)
        issues: list[ActionValidationIssue] = []
        required_dict(request.input_json, "post", issues)
        optional_str(request.input_json, "source", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "post.create":
            raise ValidationError(f"unsupported Ghost operation {request.operation!r}")
        payload = request.input_json
        site_url = credential_config_str(
            request,
            "ghost_url",
            "site_url",
            "base_url",
            label="config_json.ghost_url",
        )
        config = request.credential.config_json if request.credential is not None else {}
        api_version = "v5.0"
        if isinstance(config, dict) and isinstance(config.get("api_version"), str):
            api_version = str(config["api_version"])
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = GhostIntegration(
                payload=credential_payload(request),
                project_id=request.project_id,
                http=http,
                site_url=site_url,
                api_version=api_version,
            )
            call_result = await client.create_post(
                post=dict(payload["post"]),
                source=str(payload.get("source", "html")),
            )
        return result("ghost", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["GhostActionConnector"]
