"""Google Ads API action connector.

Official docs verified:
- REST auth and required headers: https://developers.google.com/google-ads/api/rest/auth
- Mutate semantics: https://developers.google.com/google-ads/api/rest/common/mutate
- Search semantics: https://developers.google.com/google-ads/api/rest/common/search
- Offline click conversions: https://developers.google.com/google-ads/api/docs/conversions/upload-clicks
- Current release notes: https://developers.google.com/google-ads/api/docs/release-notes
"""

from __future__ import annotations

from typing import Any

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    clean_customer_id,
    config_str,
    credential_config,
    credential_payload,
    dict_field,
    list_field,
    required_str,
    resolve_ref,
    result,
    send_json,
    unknown_operation,
)
from stackos.repositories.base import ValidationError

_BASE_URL = "https://googleads.googleapis.com"
_DEFAULT_VERSION = "v24"

_MUTATE_RESOURCE_BY_OPERATION = {
    "campaign_budget.create": "campaignBudgets",
    "campaign_budget.update": "campaignBudgets",
    "campaign.create": "campaigns",
    "ad_group.create": "adGroups",
    "asset.create": "assets",
    "ad_group_ad.create": "adGroupAds",
    "conversion_action.create": "conversionActions",
}
_PAYLOAD_KEY_BY_OPERATION = {
    "campaign_budget.create": "budget",
    "campaign_budget.update": "changes",
    "campaign.create": "campaign",
    "ad_group.create": "ad_group",
    "asset.create": "asset",
    "ad_group_ad.create": "ad_group_ad",
    "conversion_action.create": "conversion_action",
}


def _access_token(request: ActionConnectorRequest) -> str:
    payload = credential_payload(request)
    token = payload.get("access_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    raise ValidationError(
        "google-ads credential missing access_token; reconnect or renew the credential"
    )


def _headers(request: ActionConnectorRequest) -> dict[str, str]:
    payload = credential_payload(request)
    developer_token = payload.get("developer_token")
    if not isinstance(developer_token, str) or not developer_token.strip():
        raise ValidationError("google-ads credential missing developer_token")
    headers = {
        "Authorization": f"Bearer {_access_token(request)}",
        "developer-token": developer_token.strip(),
        "Content-Type": "application/json",
    }
    config = credential_config(request)
    manager_account_ref = config.get("manager_account_ref") or payload.get("manager_account_ref")
    if manager_account_ref:
        headers["login-customer-id"] = clean_customer_id(manager_account_ref)
    return headers


def _version(request: ActionConnectorRequest) -> str:
    return config_str(request, "api_version", default=_DEFAULT_VERSION) or _DEFAULT_VERSION


def _customer_id(request: ActionConnectorRequest) -> str:
    value = request.input_json.get("customer_ref") or config_str(
        request,
        "default_customer_ref",
        required=True,
    )
    resolved = resolve_ref(request, value, "customers", "customer_refs")
    return clean_customer_id(resolved)


def _mutate_body(request: ActionConnectorRequest) -> dict[str, Any]:
    payload = request.input_json
    body_key = _PAYLOAD_KEY_BY_OPERATION[request.operation]
    operation_kind = "update" if request.operation.endswith(".update") else "create"
    body_obj = payload.get(body_key)
    if not isinstance(body_obj, dict):
        raise ValidationError(f"google-ads {body_key} must be an object")
    body_obj = dict(body_obj)
    customer_id = _customer_id(request)
    if request.operation == "campaign.create" and "campaign_budget_ref" in payload:
        budget_id = resolve_ref(request, payload["campaign_budget_ref"], "campaign_budgets")
        budget_resource = f"customers/{customer_id}/campaignBudgets/{budget_id}"
        body_obj.setdefault("campaignBudget", budget_resource)
    if request.operation == "campaign_budget.update" and "campaign_budget_ref" in payload:
        budget_id = resolve_ref(request, payload["campaign_budget_ref"], "campaign_budgets")
        body_obj.setdefault("resourceName", f"customers/{customer_id}/campaignBudgets/{budget_id}")
    if request.operation == "ad_group.create" and "campaign_ref" in payload:
        campaign_id = resolve_ref(request, payload["campaign_ref"], "campaigns")
        body_obj.setdefault("campaign", f"customers/{customer_id}/campaigns/{campaign_id}")
    if request.operation == "ad_group_ad.create" and "ad_group_ref" in payload:
        ad_group_id = resolve_ref(request, payload["ad_group_ref"], "ad_groups")
        body_obj.setdefault("adGroup", f"customers/{customer_id}/adGroups/{ad_group_id}")
    operation: dict[str, Any] = {operation_kind: body_obj}
    if operation_kind == "update":
        update_mask = payload.get("update_mask") or payload.get("updateMask")
        if not isinstance(update_mask, str) or not update_mask.strip():
            raise ValidationError("google-ads update requires update_mask")
        operation["updateMask"] = update_mask
    rendered: dict[str, Any] = {"operations": [operation]}
    for key in ("partialFailure", "partial_failure", "validateOnly", "validate_only"):
        value = payload.get(key)
        if value is not None:
            rendered[_camel_flag(key)] = bool(value)
    return rendered


def _camel_flag(key: str) -> str:
    return {
        "debug_enabled": "debugEnabled",
        "job_id": "jobId",
        "partial_failure": "partialFailure",
        "validate_only": "validateOnly",
    }.get(key, key)


class GoogleAdsActionConnector:
    """Decision-free adapter for Google Ads REST APIs."""

    key = "google-ads"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "customer.list":
                pass
            case (
                "campaign_budget.create"
                | "campaign_budget.update"
                | "campaign.create"
                | "ad_group.create"
                | "asset.create"
                | "ad_group_ad.create"
                | "conversion_action.create"
            ):
                required_str(payload, "customer_ref", issues)
                dict_field(
                    payload,
                    _PAYLOAD_KEY_BY_OPERATION[request.operation],
                    issues,
                    required=True,
                )
                if request.operation.endswith(".update"):
                    required_str(payload, "update_mask", issues)
            case "report.search":
                required_str(payload, "customer_ref", issues)
                required_str(payload, "query", issues)
            case "conversion_upload.clicks":
                required_str(payload, "customer_ref", issues)
                list_field(payload, "conversions", issues, required=True, max_items=2000)
                if payload.get("partial_failure") is False:
                    issues.append(
                        ActionValidationIssue(
                            path="$.partial_failure",
                            message=(
                                "Google Ads click conversion upload requires partial_failure=true"
                            ),
                            code="validation_error",
                        )
                    )
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        headers = _headers(request)
        version = _version(request)
        payload = request.input_json
        match request.operation:
            case "customer.list":
                status, body, response_headers = await send_json(
                    method="GET",
                    url=f"{_BASE_URL}/{version}/customers:listAccessibleCustomers",
                    headers=headers,
                )
            case (
                "campaign_budget.create"
                | "campaign_budget.update"
                | "campaign.create"
                | "ad_group.create"
                | "asset.create"
                | "ad_group_ad.create"
                | "conversion_action.create"
            ):
                resource = _MUTATE_RESOURCE_BY_OPERATION[request.operation]
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_BASE_URL}/{version}/customers/{_customer_id(request)}/{resource}:mutate",
                    headers=headers,
                    json_body=_mutate_body(request),
                )
            case "report.search":
                body_json: dict[str, Any] = {"query": payload["query"]}
                if payload.get("page_token"):
                    body_json["pageToken"] = payload["page_token"]
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_BASE_URL}/{version}/customers/{_customer_id(request)}/googleAds:search",
                    headers=headers,
                    json_body=body_json,
                )
            case "conversion_upload.clicks":
                body_json = {
                    "conversions": payload["conversions"],
                    "partialFailure": True,
                }
                for key in ("job_id", "debug_enabled", "validate_only"):
                    if key in payload:
                        body_json[_camel_flag(key)] = payload[key]
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_BASE_URL}/{version}/customers/{_customer_id(request)}:uploadClickConversions",
                    headers=headers,
                    json_body=body_json,
                )
            case _:
                raise ValidationError(f"unsupported Google Ads operation {request.operation!r}")
        return result(
            provider="google-ads",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["GoogleAdsActionConnector"]
