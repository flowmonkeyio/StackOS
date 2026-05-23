"""Taboola Backstage API action connector.

Official docs verified:
- Client credentials flow: https://developers.taboola.com/backstage-api/reference/client-credentials-flow
- Create campaign: https://developers.taboola.com/backstage-api/reference/create-a-campaign
- Update campaign: https://developers.taboola.com/backstage-api/reference/update-a-campaign
- Create campaign item: https://developers.taboola.com/backstage-api/reference/create-a-campaign-item
- Update campaign item: https://developers.taboola.com/backstage-api/reference/update-a-campaign-item
- Campaign summary report: https://developers.taboola.com/backstage-api/reference/campaign-summary-report
- Conversion rule quick reference: https://developers.taboola.com/backstage-api/reference/conversion-rule-quick-reference
- API reference index/OpenAPI: https://developers.taboola.com/llms.txt
"""

from __future__ import annotations

from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    config_str,
    credential_payload,
    dict_field,
    issue,
    q,
    required_str,
    resolve_ref,
    result,
    send_json,
    unknown_operation,
)
from stackos.repositories.base import ValidationError

_BASE_URL = "https://backstage.taboola.com"
_TOKEN_URL = f"{_BASE_URL}/backstage/oauth/token"


async def _access_token(request: ActionConnectorRequest) -> str:
    payload = credential_payload(request)
    access_token = payload.get("access_token")
    if isinstance(access_token, str) and access_token.strip():
        return access_token.strip()
    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise ValidationError("taboola credential requires access_token or client_id/client_secret")
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.post(
            _TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
        )
    if response.status_code >= 400:
        raise ValidationError(f"taboola token request failed with status {response.status_code}")
    body = response.json()
    token = body.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise ValidationError("taboola token response missing access_token")
    return token.strip()


async def _headers(request: ActionConnectorRequest) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {await _access_token(request)}",
        "Content-Type": "application/json",
    }


def _account_id(request: ActionConnectorRequest) -> str:
    value = request.input_json.get("account_ref") or config_str(
        request,
        "account_ref",
        required=True,
    )
    resolved = resolve_ref(request, value, "accounts", "account_refs")
    return str(resolved)


def _campaign_id(request: ActionConnectorRequest) -> str:
    return str(
        resolve_ref(
            request,
            request.input_json["campaign_ref"],
            "campaigns",
            "campaign_refs",
        )
    )


def _item_id(request: ActionConnectorRequest) -> str:
    return str(resolve_ref(request, request.input_json["item_ref"], "items", "item_refs"))


def _rule_id(request: ActionConnectorRequest) -> str:
    return str(resolve_ref(request, request.input_json["conversion_rule_ref"], "conversion_rules"))


def _body(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"taboola {key} must be an object")
    return value


def _validate_static_item(payload: dict[str, Any], issues: list[ActionValidationIssue]) -> None:
    item = payload.get("item")
    if not isinstance(item, dict):
        return
    if set(item) != {"url"}:
        issues.append(
            issue(
                "$.item",
                "Taboola static item creation accepts exactly the url field",
                "schema_mismatch",
            )
        )
    elif not isinstance(item.get("url"), str) or not item["url"].strip():
        issues.append(issue("$.item.url", "item.url is required", "required"))


def _validate_report_filters(payload: dict[str, Any], issues: list[ActionValidationIssue]) -> None:
    exclusive = [key for key in ("platform", "country", "site", "partner_name") if payload.get(key)]
    if len(exclusive) > 1:
        issues.append(
            issue(
                "$",
                (
                    "Taboola report filters platform, country, site, and partner_name "
                    "are mutually exclusive"
                ),
                "validation_error",
            )
        )


class TaboolaActionConnector:
    """Decision-free adapter for Taboola account-scoped Backstage endpoints."""

    key = "taboola"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "account.get":
                pass
            case "campaign.create":
                required_str(payload, "account_ref", issues)
                dict_field(payload, "campaign", issues, required=True)
            case "campaign.update":
                required_str(payload, "account_ref", issues)
                required_str(payload, "campaign_ref", issues)
                dict_field(payload, "changes", issues, required=True)
            case "campaign.pause" | "campaign.resume":
                required_str(payload, "account_ref", issues)
                required_str(payload, "campaign_ref", issues)
            case "item.create":
                required_str(payload, "account_ref", issues)
                required_str(payload, "campaign_ref", issues)
                dict_field(payload, "item", issues, required=True)
                _validate_static_item(payload, issues)
            case "item.update":
                required_str(payload, "account_ref", issues)
                required_str(payload, "campaign_ref", issues)
                required_str(payload, "item_ref", issues)
                dict_field(payload, "changes", issues, required=True)
            case "report.fetch":
                required_str(payload, "account_ref", issues)
                required_str(payload, "dimension", issues)
                required_str(payload, "start_date", issues)
                required_str(payload, "end_date", issues)
                _validate_report_filters(payload, issues)
            case "conversion_rule.create":
                required_str(payload, "account_ref", issues)
                dict_field(payload, "conversion_rule", issues, required=True)
            case "conversion_rule.update":
                required_str(payload, "account_ref", issues)
                required_str(payload, "conversion_rule_ref", issues)
                dict_field(payload, "changes", issues, required=True)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        headers = await _headers(request)
        account_id = _account_id(request) if request.operation != "account.get" else None
        base = f"{_BASE_URL}/backstage/api/1.0"
        payload = request.input_json
        match request.operation:
            case "account.get":
                status, body, response_headers = await send_json(
                    method="GET",
                    url=f"{base}/users/current/account",
                    headers=headers,
                )
            case "campaign.create":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{base}/{q(account_id)}/campaigns/",
                    headers=headers,
                    json_body=_body(payload, "campaign"),
                )
            case "campaign.update":
                status, body, response_headers = await send_json(
                    method="PUT",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}",
                    headers=headers,
                    json_body=_body(payload, "changes"),
                )
            case "campaign.pause" | "campaign.resume":
                status, body, response_headers = await send_json(
                    method="PUT",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}",
                    headers=headers,
                    json_body={"is_active": request.operation.endswith("resume")},
                )
            case "item.create":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}/items/",
                    headers=headers,
                    json_body=_body(payload, "item"),
                )
            case "item.update":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{base}/{q(account_id)}/campaigns/{q(_campaign_id(request))}/items/{q(_item_id(request))}",
                    headers=headers,
                    json_body=_body(payload, "changes"),
                )
            case "report.fetch":
                params = {
                    "start_date": payload["start_date"],
                    "end_date": payload["end_date"],
                }
                if payload.get("campaign_ref") is not None:
                    params["campaign"] = resolve_ref(
                        request,
                        payload["campaign_ref"],
                        "campaigns",
                    )
                for key in ("site", "platform", "country", "partner_name"):
                    if payload.get(key) is not None:
                        params[key] = payload[key]
                status, body, response_headers = await send_json(
                    method="GET",
                    url=f"{base}/{q(account_id)}/reports/campaign-summary/dimensions/{q(payload['dimension'])}",
                    headers=headers,
                    params=params,
                )
            case "conversion_rule.create":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{base}/{q(account_id)}/universal_pixel/conversion_rule",
                    headers=headers,
                    json_body=_body(payload, "conversion_rule"),
                )
            case "conversion_rule.update":
                status, body, response_headers = await send_json(
                    method="PUT",
                    url=f"{base}/{q(account_id)}/universal_pixel/conversion_rule/{q(_rule_id(request))}",
                    headers=headers,
                    json_body=_body(payload, "changes"),
                )
            case _:
                raise ValidationError(f"unsupported Taboola operation {request.operation!r}")
        return result(
            provider="taboola",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["TaboolaActionConnector"]
