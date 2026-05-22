"""Jina Reader action connector."""

from __future__ import annotations

import httpx

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.vendor_utils import (
    credential_payload,
    required_str,
    result,
    unknown_operation,
)
from content_stack.integrations.jina_reader import JinaReaderIntegration


class JinaActionConnector:
    """Decision-free adapter for Jina Reader utility actions."""

    key = "jina"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != "read":
            return unknown_operation(request)
        issues: list[ActionValidationIssue] = []
        required_str(request.input_json, "url", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = JinaReaderIntegration(
                payload=credential_payload(request, required=False),
                project_id=request.project_id,
                http=http,
            )
            call_result = await client.read(url=str(payload["url"]))
        return result("jina", request.operation, call_result.data, call_result.cost_usd)


__all__ = ["JinaActionConnector"]
