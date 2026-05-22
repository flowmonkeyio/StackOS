"""HubSpot action connector.

Official docs verified:
- CRM batch upsert: https://developers.hubspot.com/docs/api-reference/latest/crm/objects/companies/batch/upsert-companies
- CRM search: https://developers.hubspot.com/docs/api/crm/search
- Tasks API: https://developers.hubspot.com/docs/reference/api/crm/engagements/tasks
"""

from __future__ import annotations

from typing import Any

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.provider_utils import (
    bearer_headers,
    dict_field,
    int_range,
    list_field,
    optional_str,
    required_str,
    result,
    send_json,
    unknown_operation,
)
from content_stack.repositories.base import ValidationError

_BASE_URL = "https://api.hubapi.com"
_LATEST_CRM_OBJECTS_VERSION = "2026-03"


def _object_api_url(object_type: str, suffix: str = "") -> str:
    path = f"/crm/objects/{_LATEST_CRM_OBJECTS_VERSION}/{object_type}"
    return f"{_BASE_URL}{path}{suffix}"


def _v3_object_url(object_type: str) -> str:
    return f"{_BASE_URL}/crm/v3/objects/{object_type}"


def _upsert_inputs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    id_property = str(payload["id_property"])
    inputs: list[dict[str, Any]] = []
    for raw in payload["inputs"]:
        if not isinstance(raw, dict):
            raise ValidationError("HubSpot batch upsert inputs must be objects")
        properties = raw.get("properties")
        if not isinstance(properties, dict):
            raise ValidationError("HubSpot batch upsert input.properties is required")
        record_id = raw.get("id") or properties.get(id_property)
        if record_id is None or not str(record_id).strip():
            raise ValidationError(
                f"HubSpot batch upsert input missing id or properties.{id_property}"
            )
        rendered = {
            "id": str(record_id),
            "idProperty": id_property,
            "properties": properties,
        }
        if raw.get("objectWriteTraceId"):
            rendered["objectWriteTraceId"] = str(raw["objectWriteTraceId"])
        inputs.append(rendered)
    return inputs


def _note_body(raw_note: Any) -> dict[str, Any]:
    if not isinstance(raw_note, dict):
        raise ValidationError("HubSpot note must be an object")
    if isinstance(raw_note.get("properties"), dict):
        return raw_note
    properties: dict[str, Any] = {}
    body = raw_note.get("body")
    if isinstance(body, str) and body.strip():
        properties["hs_note_body"] = body
    timestamp = raw_note.get("timestamp") or raw_note.get("hs_timestamp")
    if timestamp:
        properties["hs_timestamp"] = timestamp
    if not properties:
        raise ValidationError("HubSpot note requires properties or body")
    rendered: dict[str, Any] = {"properties": properties}
    if isinstance(raw_note.get("associations"), list):
        rendered["associations"] = raw_note["associations"]
    return rendered


def _task_body(raw_task: Any) -> dict[str, Any]:
    if not isinstance(raw_task, dict):
        raise ValidationError("HubSpot task must be an object")
    if isinstance(raw_task.get("properties"), dict):
        return raw_task
    properties: dict[str, Any] = {}
    title = raw_task.get("title")
    if isinstance(title, str) and title.strip():
        properties["hs_task_subject"] = title
    body = raw_task.get("body") or raw_task.get("instructions")
    if isinstance(body, str) and body.strip():
        properties["hs_task_body"] = body
    due_at = raw_task.get("due_at") or raw_task.get("hs_timestamp")
    if due_at:
        properties["hs_timestamp"] = due_at
    if not properties:
        raise ValidationError("HubSpot task requires properties or title")
    rendered: dict[str, Any] = {"properties": properties}
    if isinstance(raw_task.get("associations"), list):
        rendered["associations"] = raw_task["associations"]
    return rendered


def _deal_search_body(payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key in ("query", "filterGroups", "sorts", "properties", "after"):
        if payload.get(key) is not None:
            body[key] = payload[key]
    limit = payload.get("limit")
    if isinstance(limit, int):
        body["limit"] = limit
    return body


class HubSpotActionConnector:
    """Decision-free adapter for HubSpot CRM APIs."""

    key = "hubspot"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "crm.companies.batch_upsert" | "crm.contacts.batch_upsert":
                required_str(payload, "id_property", issues)
                list_field(payload, "inputs", issues, required=True, max_items=100)
            case "crm.notes.create":
                dict_field(payload, "note", issues, required=True)
            case "crm.tasks.create":
                dict_field(payload, "task", issues, required=True)
            case "crm.deals.search":
                optional_str(payload, "query", issues)
                list_field(payload, "filterGroups", issues)
                list_field(payload, "sorts", issues)
                list_field(payload, "properties", issues)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=200)
            case "crm.deals.list":
                list_field(payload, "properties", issues)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        headers = bearer_headers(request, "access_token")
        payload = request.input_json
        match request.operation:
            case "crm.companies.batch_upsert":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_object_api_url("companies", "/batch/upsert"),
                    headers=headers,
                    json_body={"inputs": _upsert_inputs(payload)},
                )
            case "crm.contacts.batch_upsert":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_object_api_url("contacts", "/batch/upsert"),
                    headers=headers,
                    json_body={"inputs": _upsert_inputs(payload)},
                )
            case "crm.notes.create":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_v3_object_url("notes"),
                    headers=headers,
                    json_body=_note_body(payload["note"]),
                )
            case "crm.tasks.create":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_v3_object_url("tasks"),
                    headers=headers,
                    json_body=_task_body(payload["task"]),
                )
            case "crm.deals.search":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_object_api_url("deals", "/search"),
                    headers=headers,
                    json_body=_deal_search_body(payload),
                )
            case "crm.deals.list":
                params: dict[str, Any] = {}
                if isinstance(payload.get("limit"), int):
                    params["limit"] = payload["limit"]
                fields = payload.get("properties")
                if isinstance(fields, list):
                    params["properties"] = ",".join(str(field) for field in fields)
                after = payload.get("after")
                if after:
                    params["after"] = str(after)
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_object_api_url("deals"),
                    headers=headers,
                    params=params,
                )
            case _:
                raise ValidationError(f"unsupported HubSpot operation {request.operation!r}")
        return result(
            provider="hubspot",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


__all__ = ["HubSpotActionConnector"]
