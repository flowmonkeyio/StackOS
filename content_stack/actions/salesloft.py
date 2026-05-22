"""Salesloft API action connector.

Official docs verified:
- Cadence membership create: https://developers.salesloft.com/docs/api/cadence-memberships-create/
- Authentication: https://developer.salesloft.com/docs/platform/api-basics/api-key-authentication/
- Rate limits: https://developer.salesloft.com/docs/platform/api-basics/rate-limits/
"""

from __future__ import annotations

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.provider_utils import (
    config_str,
    credential_value,
    required_str,
    resolve_ref,
    result,
    send_json,
    unknown_operation,
)
from content_stack.repositories.base import ValidationError


def _base_url(request: ActionConnectorRequest) -> str:
    return (config_str(request, "base_url", default="https://api.salesloft.com") or "").rstrip("/")


def _headers(request: ActionConnectorRequest) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential_value(request, 'access_token', 'api_key', 'token')}",
        "Content-Type": "application/json",
    }


class SalesloftActionConnector:
    """Decision-free adapter for Salesloft cadence membership creation."""

    key = "salesloft"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation != "cadence_membership.create":
            return unknown_operation(request)
        required_str(payload, "cadence_ref", issues)
        required_str(payload, "person_ref", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "cadence_membership.create":
            raise ValidationError(f"unsupported Salesloft operation {request.operation!r}")
        payload = request.input_json
        body = {
            "cadence_id": resolve_ref(request, payload["cadence_ref"], "cadences"),
            "person_id": resolve_ref(request, payload["person_ref"], "persons", "people"),
        }
        if payload.get("user_ref"):
            body["user_id"] = resolve_ref(request, payload["user_ref"], "users")
        status, response_body, headers = await send_json(
            method="POST",
            url=f"{_base_url(request)}/v2/cadence_memberships",
            headers=_headers(request),
            json_body=body,
        )
        return result(
            provider="salesloft",
            operation=request.operation,
            status_code=status,
            body=response_body,
            headers=headers,
        )


__all__ = ["SalesloftActionConnector"]
