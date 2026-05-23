"""Outreach API action connector.

Official docs verified:
- Sequence State API: https://developers.outreach.io/api/reference/tag/Sequence-State/
- Making requests / JSON:API media type: https://developers.outreach.io/api/making-requests/
- OAuth: https://developers.outreach.io/api/oauth/
"""

from __future__ import annotations

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    config_str,
    credential_value,
    required_str,
    resolve_ref,
    result,
    send_json,
    unknown_operation,
)
from stackos.repositories.base import ValidationError


def _base_url(request: ActionConnectorRequest) -> str:
    return (config_str(request, "base_url", default="https://api.outreach.io") or "").rstrip("/")


def _headers(request: ActionConnectorRequest) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential_value(request, 'access_token', 'token')}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }


class OutreachActionConnector:
    """Decision-free adapter for Outreach JSON:API endpoints."""

    key = "outreach"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation != "sequence_state.create":
            return unknown_operation(request)
        required_str(payload, "sequence_ref", issues)
        required_str(payload, "prospect_ref", issues)
        required_str(payload, "mailbox_ref", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "sequence_state.create":
            raise ValidationError(f"unsupported Outreach operation {request.operation!r}")
        payload = request.input_json
        body = {
            "data": {
                "type": "sequenceState",
                "relationships": {
                    "sequence": {
                        "data": {
                            "type": "sequence",
                            "id": str(resolve_ref(request, payload["sequence_ref"], "sequences")),
                        }
                    },
                    "prospect": {
                        "data": {
                            "type": "prospect",
                            "id": str(resolve_ref(request, payload["prospect_ref"], "prospects")),
                        }
                    },
                    "mailbox": {
                        "data": {
                            "type": "mailbox",
                            "id": str(resolve_ref(request, payload["mailbox_ref"], "mailboxes")),
                        }
                    },
                },
            }
        }
        status, response_body, headers = await send_json(
            method="POST",
            url=f"{_base_url(request)}/api/v2/sequenceStates",
            headers=_headers(request),
            json_body=body,
        )
        return result(
            provider="outreach",
            operation=request.operation,
            status_code=status,
            body=response_body,
            headers=headers,
        )


__all__ = ["OutreachActionConnector"]
