"""Pipedrive API action connector.

Official docs verified:
- Deals API v2: https://developers.pipedrive.com/docs/api/v1/Deals
- API authentication concepts: https://pipedrive.readme.io/docs/core-api-concepts-authentication
- API v2 overview: https://pipedrive.readme.io/docs/pipedrive-api-v2
"""

from __future__ import annotations

from typing import Any

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    config_str,
    credential_payload,
    credential_value,
    int_range,
    optional_str,
    resolve_ref,
    result,
    send_json,
    unknown_operation,
)
from stackos.repositories.base import ValidationError


def _base_url(request: ActionConnectorRequest) -> str:
    base = config_str(request, "base_url")
    if base:
        return base.rstrip("/")
    domain = config_str(request, "company_domain", required=True)
    assert domain is not None
    return f"https://{domain}.pipedrive.com"


def _headers(request: ActionConnectorRequest) -> dict[str, str]:
    payload = credential_payload(request)
    if isinstance(payload.get("access_token"), str) and str(payload["access_token"]).strip():
        return {"Authorization": f"Bearer {payload['access_token']}"}
    return {"x-api-token": credential_value(request, "api_token", "token")}


def _list_params(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    params: dict[str, Any] = {}
    key_map = {
        "filter_id": "filter_id",
        "owner_ref": "owner_id",
        "person_ref": "person_id",
        "organization_ref": "org_id",
        "pipeline_ref": "pipeline_id",
        "stage_ref": "stage_id",
        "status": "status",
        "updated_since": "updated_since",
        "updated_until": "updated_until",
        "sort_by": "sort_by",
        "sort_direction": "sort_direction",
        "include_fields": "include_fields",
        "custom_fields": "custom_fields",
        "limit": "limit",
        "cursor": "cursor",
    }
    ref_keys = {
        "owner_ref": "owners",
        "person_ref": "persons",
        "organization_ref": "organizations",
        "pipeline_ref": "pipelines",
        "stage_ref": "stages",
    }
    for source, target in key_map.items():
        value = payload.get(source)
        if value is None:
            continue
        params[target] = resolve_ref(request, value, ref_keys.get(source, "refs"))
    return params


def _search_params(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    term = payload.get("term")
    if not isinstance(term, str) or not term.strip():
        raise ValidationError("Pipedrive deal search requires term")
    params: dict[str, Any] = {"term": term}
    if "organization_ref" in payload:
        params["organization_id"] = resolve_ref(
            request,
            payload["organization_ref"],
            "organizations",
        )
    if "person_ref" in payload:
        params["person_id"] = resolve_ref(request, payload["person_ref"], "persons")
    for key in ("status", "include_fields", "limit", "cursor"):
        if payload.get(key) is not None:
            params[key] = payload[key]
    if "exact_match" in payload:
        params["exact_match"] = bool(payload["exact_match"])
    if "fields" in payload:
        fields = payload["fields"]
        params["fields"] = ",".join(fields) if isinstance(fields, list) else str(fields)
    return params


class PipedriveActionConnector:
    """Decision-free adapter for Pipedrive deals read/search endpoints."""

    key = "pipedrive"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "deals.list":
                optional_str(payload, "status", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=500)
            case "deals.search":
                optional_str(payload, "status", issues)
                optional_str(payload, "term", issues)
                if not payload.get("term"):
                    issues.append(
                        ActionValidationIssue(
                            path="$.term",
                            message="term is required",
                            code="required",
                        )
                    )
                int_range(payload, "limit", issues, minimum=1, maximum=500)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        match request.operation:
            case "deals.list":
                status, body, response_headers = await send_json(
                    method="GET",
                    url=f"{_base_url(request)}/api/v2/deals",
                    headers=_headers(request),
                    params=_list_params(request),
                )
            case "deals.search":
                status, body, response_headers = await send_json(
                    method="GET",
                    url=f"{_base_url(request)}/api/v2/deals/search",
                    headers=_headers(request),
                    params=_search_params(request),
                )
            case _:
                raise ValidationError(f"unsupported Pipedrive operation {request.operation!r}")
        return result(
            provider="pipedrive",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["PipedriveActionConnector"]
