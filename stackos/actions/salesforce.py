"""Salesforce REST API action connector.

Official docs verified:
- REST API Developer Guide PDF: https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf
- External-ID updateOnly parameter: https://help.salesforce.com/s/articleView?id=release-notes.rn_api_new_updateonly_param.htm&language=en_US&release=250&type=5
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
    config_str,
    credential_config,
    dict_field,
    q,
    required_str,
    result,
    send_json,
    unknown_operation,
)
from stackos.repositories.base import ValidationError

_OBJECT_BY_OPERATION = {
    "account.upsert_by_external_id": ("Account", "account"),
    "lead.upsert_by_external_id": ("Lead", "lead"),
    "contact.upsert_by_external_id": ("Contact", "contact"),
}


def _instance_url(request: ActionConnectorRequest) -> str:
    value = config_str(request, "instance_url", required=True)
    assert value is not None
    return value.rstrip("/")


def _api_version(request: ActionConnectorRequest) -> str:
    return config_str(request, "api_version", default="v61.0") or "v61.0"


def _external_id_field(request: ActionConnectorRequest, policy_ref: str) -> str:
    mapping = credential_config(request).get("external_id_fields")
    if isinstance(mapping, dict):
        value = mapping.get(policy_ref)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return policy_ref


def _record_payload(
    payload: dict[str, Any],
    key: str,
    external_id_field: str,
) -> tuple[str, dict[str, Any]]:
    record = payload.get(key)
    if not isinstance(record, dict):
        raise ValidationError(f"Salesforce {key} must be an object")
    properties = record.get("properties") if isinstance(record.get("properties"), dict) else record
    if not isinstance(properties, dict):
        raise ValidationError(f"Salesforce {key}.properties must be an object")
    external_id_value = payload.get("external_id_value") or record.get("external_id_value")
    if external_id_value is None:
        external_id_value = properties.get(external_id_field)
    if external_id_value is None or not str(external_id_value).strip():
        raise ValidationError("Salesforce upsert requires external_id_value")
    body = dict(properties)
    body.pop(external_id_field, None)
    return str(external_id_value), body


def _query_from_payload(request: ActionConnectorRequest) -> str:
    payload = request.input_json
    query_ref = payload.get("query_template_ref")
    mapping = credential_config(request).get("query_templates")
    if isinstance(query_ref, str) and isinstance(mapping, dict):
        query = mapping.get(query_ref)
        if isinstance(query, str) and query.strip():
            return query.strip()
    raise ValidationError("Salesforce query requires configured query_template_ref")


class SalesforceActionConnector:
    """Decision-free adapter for Salesforce REST sObject and query resources."""

    key = "salesforce"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case (
                "account.upsert_by_external_id"
                | "lead.upsert_by_external_id"
                | "contact.upsert_by_external_id"
            ):
                required_str(payload, "external_id_policy_ref", issues)
                dict_field(
                    payload,
                    _OBJECT_BY_OPERATION[request.operation][1],
                    issues,
                    required=True,
                )
            case "task.create":
                dict_field(payload, "task", issues, required=True)
            case "opportunities.query":
                required_str(payload, "query_template_ref", issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        headers = bearer_headers(request, "access_token")
        base = f"{_instance_url(request)}/services/data/{_api_version(request)}"
        payload = request.input_json
        match request.operation:
            case (
                "account.upsert_by_external_id"
                | "lead.upsert_by_external_id"
                | "contact.upsert_by_external_id"
            ):
                object_name, payload_key = _OBJECT_BY_OPERATION[request.operation]
                external_id_field = _external_id_field(
                    request,
                    str(payload["external_id_policy_ref"]),
                )
                external_id_value, body_json = _record_payload(
                    payload,
                    payload_key,
                    external_id_field,
                )
                params = {"updateOnly": str(bool(payload.get("update_only", False))).lower()}
                status, body, response_headers = await send_json(
                    method="PATCH",
                    url=f"{base}/sobjects/{object_name}/{q(external_id_field)}/{q(external_id_value)}",
                    headers=headers,
                    params=params,
                    json_body=body_json,
                )
            case "task.create":
                raw_task = payload.get("task")
                if not isinstance(raw_task, dict):
                    raise ValidationError("Salesforce task must be an object")
                properties = raw_task.get("properties")
                body_json = properties if isinstance(properties, dict) else raw_task
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{base}/sobjects/Task",
                    headers=headers,
                    json_body=body_json,
                )
            case "opportunities.query":
                status, body, response_headers = await send_json(
                    method="GET",
                    url=f"{base}/query",
                    headers=headers,
                    params={"q": _query_from_payload(request)},
                )
            case _:
                raise ValidationError(f"unsupported Salesforce operation {request.operation!r}")
        return result(
            provider="salesforce",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["SalesforceActionConnector"]
