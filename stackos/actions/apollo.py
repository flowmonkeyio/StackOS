"""Apollo API action connector.

Official docs verified:
- People API Search: https://docs.apollo.io/reference/people-api-search
- People Enrichment: https://docs.apollo.io/reference/people-enrichment
- Bulk People Enrichment: https://docs.apollo.io/reference/bulk-people-enrichment
- Organization Enrichment: https://docs.apollo.io/reference/organization-enrichment
- Bulk Organization Enrichment: https://docs.apollo.io/reference/bulk-organization-enrichment
"""

from __future__ import annotations

from typing import Any

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    bearer_headers,
    credential_config,
    dict_field,
    int_range,
    list_field,
    required_str,
    result,
    send_json,
    unknown_operation,
)
from stackos.repositories.base import ValidationError

_BASE_URL = "https://api.apollo.io/api/v1"


def _headers(request: ActionConnectorRequest) -> dict[str, str]:
    headers = bearer_headers(request, "api_key", "access_token", "token")
    headers["Accept"] = "application/json"
    return headers


def _params_from_filters(payload: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    filters = payload.get("filters")
    if isinstance(filters, dict):
        params.update(filters)
    for key in ("page", "per_page"):
        if payload.get(key) is not None:
            params[key] = payload[key]
    return params


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("record"), dict):
        return dict(payload["record"])
    raise ValidationError("Apollo enrichment requires record")


def _validate_phone_reveal_webhook(
    payload: dict[str, Any],
    issues: list[ActionValidationIssue],
) -> None:
    if payload.get("reveal_phone_number") is True and not (
        isinstance(payload.get("webhook_url"), str) and payload["webhook_url"].strip()
    ):
        issues.append(
            ActionValidationIssue(
                path="$.webhook_url",
                message="Apollo reveal_phone_number requires webhook_url",
                code="validation_error",
            )
        )


def _assert_master_api_key(request: ActionConnectorRequest) -> None:
    scope = credential_config(request).get("access_scope")
    if scope != "master":
        raise ValidationError(
            "Apollo people.search requires a credential profile marked access_scope='master'",
            data={
                "provider": "apollo",
                "operation": "people.search",
                "docs": "https://docs.apollo.io/reference/people-api-search",
            },
        )


class ApolloActionConnector:
    """Decision-free adapter for Apollo API search and enrichment endpoints."""

    key = "apollo"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "people.search":
                dict_field(payload, "filters", issues)
                int_range(payload, "page", issues, minimum=1, maximum=500)
                int_range(payload, "per_page", issues, minimum=1, maximum=100)
            case "people.enrich":
                dict_field(payload, "record", issues, required=True)
                _validate_phone_reveal_webhook(payload, issues)
            case "people.bulk_enrich":
                list_field(payload, "details", issues, required=True, max_items=10)
                _validate_phone_reveal_webhook(payload, issues)
            case "organization.enrich":
                required_str(payload, "domain", issues)
            case "organization.bulk_enrich":
                list_field(payload, "domains", issues, required=True, max_items=10)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        if request.operation == "people.search":
            _assert_master_api_key(request)
        headers = _headers(request)
        match request.operation:
            case "people.search":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_BASE_URL}/mixed_people/api_search",
                    headers=headers,
                    params=_params_from_filters(payload),
                )
            case "people.enrich":
                params = _record(payload)
                for key in (
                    "run_waterfall_email",
                    "run_waterfall_phone",
                    "reveal_personal_emails",
                    "reveal_phone_number",
                    "webhook_url",
                ):
                    if key in payload:
                        params[key] = payload[key]
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_BASE_URL}/people/match",
                    headers=headers,
                    params=params,
                )
            case "people.bulk_enrich":
                params = {
                    key: payload[key]
                    for key in (
                        "run_waterfall_email",
                        "run_waterfall_phone",
                        "reveal_personal_emails",
                        "reveal_phone_number",
                        "webhook_url",
                    )
                    if key in payload
                }
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_BASE_URL}/people/bulk_match",
                    headers=headers,
                    params=params,
                    json_body={"details": payload["details"]},
                )
            case "organization.enrich":
                status, body, response_headers = await send_json(
                    method="GET",
                    url=f"{_BASE_URL}/organizations/enrich",
                    headers=headers,
                    params={"domain": payload["domain"]},
                )
            case "organization.bulk_enrich":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_BASE_URL}/organizations/bulk_enrich",
                    headers=headers,
                    params={"domains[]": payload["domains"]},
                )
            case _:
                raise ValidationError(f"unsupported Apollo operation {request.operation!r}")
        return result(
            provider="apollo",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["ApolloActionConnector"]
