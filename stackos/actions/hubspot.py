"""HubSpot action connector.

Official docs verified:
- CRM batch upsert: https://developers.hubspot.com/docs/api-reference/latest/crm/objects/companies/batch/upsert-companies
- CRM search: https://developers.hubspot.com/docs/api/crm/search
- Tasks API: https://developers.hubspot.com/docs/reference/api/crm/engagements/tasks
- Marketing events: https://developers.hubspot.com/docs/api-reference/latest/marketing/marketing-events/guide
- Behavioral event definitions: https://developers.hubspot.com/docs/api-reference/latest/events/define-events/guide
- Behavioral event occurrences: https://developers.hubspot.com/docs/api-reference/latest/events/send-event-data/guide
- Transactional email: https://developers.hubspot.com/docs/api-reference/latest/marketing/transactional-emails/guide
- CRM exports: https://developers.hubspot.com/docs/api-reference/latest/crm/exports/guide
- CRM imports: https://developers.hubspot.com/docs/api-reference/latest/crm/imports/guide
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import mimetypes
import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
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
    optional_str,
    q,
    required_str,
    send_json,
    unknown_operation,
)
from stackos.artifacts import redact_secret_text
from stackos.config import Settings
from stackos.repositories.base import ValidationError
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from stackos.repositories.resources import ArtifactRepository, ResourceRepository
from stackos.secret_refs import redact_secret_values

_BASE_URL = "https://api.hubapi.com"
_LATEST_CRM_OBJECTS_VERSION = "2026-03"
_CONTACT_OBJECT_TYPE_ID = "0-1"
_CAMPAIGN_PROPERTIES = {
    "name": "hs_name",
    "start_date": "hs_start_date",
    "end_date": "hs_end_date",
    "notes": "hs_notes",
    "audience": "hs_audience",
    "currency_code": "hs_currency_code",
    "status": "hs_campaign_status",
    "utm": "hs_utm",
}
_CAMPAIGN_STATUSES = {"planned", "in_progress", "active", "paused", "completed"}
_CAMPAIGN_ASSET_TYPES = {
    "EMAIL": "marketing-email",
    "MARKETING_EMAIL": "marketing-email",
    "FORM": "form",
    "STATIC_LIST": "segment",
    "LIST": "segment",
    "MARKETING_EVENT": "marketing-event",
}
_OBJECT_SINGULAR = {
    "contacts": "contact",
    "companies": "company",
    "deals": "deal",
    "leads": "lead",
    "products": "product",
    "line_items": "line-item",
    "quotes": "quote",
    "goal_targets": "goal-target",
}
_ASSOCIATION_CONTRACTS = {
    "crm.contact_company": ("contact", "company", "contacts", "companies"),
    "crm.contact_deal": ("contact", "deal", "contacts", "deals"),
    "crm.company_deal": ("company", "deal", "companies", "deals"),
}
_ACTIVITY_ASSOCIATION_TYPE_IDS = {
    "notes": {"contact": 202, "company": 190, "deal": 214, "lead": 855},
    "tasks": {"contact": 204, "company": 192, "deal": 216, "lead": 647},
    "calls": {"contact": 194, "company": 182, "deal": 206, "lead": 597},
    "meetings": {"contact": 200, "company": 188, "deal": 212, "lead": 601},
}
_ACTIVITY_SINGULAR = {
    "notes": "note",
    "tasks": "task",
    "calls": "call",
    "meetings": "meeting",
}
_EVENT_PROPERTY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
_BEHAVIORAL_EVENT_NAME_RE = re.compile(r"^pe[0-9]+_[a-z][a-z0-9_]*$")
_BULK_EXPORT_OBJECT_NAMES = {
    "contacts": "CONTACT",
    "companies": "COMPANY",
    "deals": "DEAL",
    "leads": "LEAD",
    "products": "PRODUCT",
    "line_items": "LINE_ITEM",
}
_BULK_EXPORT_ASSOCIATION_OBJECTS = {"contacts", "companies", "deals", "leads"}
_BULK_EXPORT_FORMATS = {"CSV", "XLS", "XLSX"}
_BULK_EXPORT_LANGUAGES = {
    "AF_ZA",
    "AR_EG",
    "BG",
    "BN",
    "CA_ES",
    "CS",
    "DA_DK",
    "DE",
    "EL_GR",
    "EN",
    "EN_GB",
    "ES",
    "ES_MX",
    "ET_EE",
    "FI",
    "FR",
    "FR_CA",
    "HE_IL",
    "HI_IN",
    "HR",
    "HU",
    "ID",
    "IT",
    "JA",
    "KO_KR",
    "LT_LT",
    "MS",
    "NL",
    "NO",
    "PL",
    "PT_BR",
    "PT_PT",
    "RO",
    "RU",
    "SK_SK",
    "SL",
    "SV",
    "TH",
    "TL",
    "TR",
    "UK",
    "VI_VN",
    "ZH_CN",
    "ZH_HK",
    "ZH_TW",
}
_BULK_EXPORT_STATES = {
    "CANCELED": "canceled",
    "CONFLICT": "blocked",
    "DEFERRED": "deferred",
    "DELETED": "deleted",
    "DONE": "complete",
    "ENQUEUED": "queued",
    "FAILED": "failed",
    "PENDING_APPROVAL": "pending_approval",
    "PROCESSING": "processing",
    "COMPLETE": "complete",
    "PENDING": "pending",
}
_DEFAULT_BULK_EXPORT_MAX_BYTES = 262_144_000
_SAFE_EXPORT_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _object_api_url(object_type: str, suffix: str = "") -> str:
    path = f"/crm/objects/{_LATEST_CRM_OBJECTS_VERSION}/{object_type}"
    return f"{_BASE_URL}{path}{suffix}"


def _metadata_url(path: str) -> str:
    return f"{_BASE_URL}{path}"


def _ref_context(request: ActionConnectorRequest) -> tuple[ProviderObjectReferenceRepository, Any]:
    if request.session is None or request.credential is None:
        raise ValidationError("HubSpot safe references require a resolved project credential")
    return (
        ProviderObjectReferenceRepository(request.session),
        request.credential.credential,
    )


def _required_safe_ref(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"HubSpot {key} is required")
    return value


def _request_id(headers: Mapping[str, str], body: Any) -> str | None:
    for key in (
        "x-hubspot-correlation-id",
        "x-request-id",
        "request-id",
    ):
        value = headers.get(key)
        if value:
            return str(value)
    if isinstance(body, dict):
        value = body.get("correlationId")
        if value:
            return str(value)
    return None


def _paging(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    paging = body.get("paging")
    if not isinstance(paging, dict):
        return None
    next_page = paging.get("next")
    if not isinstance(next_page, dict):
        return None
    after = next_page.get("after")
    if after is None:
        return None
    return {"after": str(after)}


def _safe_metadata_result(
    *,
    operation: str,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    results: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> ActionConnectorResult:
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": operation,
        "status_code": status_code,
        "results": results,
    }
    cursor = _paging(body)
    if cursor is not None:
        output["paging"] = cursor
    request_id = _request_id(headers, body)
    if request_id is not None:
        output["request_id"] = request_id
    if extra:
        output.update(extra)
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": operation,
        "status_code": status_code,
        "response_contract": "safe-provider-refs-v1",
    }
    if request_id is not None:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _property_metadata(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    body: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs, credential = _ref_context(request)
    raw_results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(raw_results, list):
        raise ValidationError("HubSpot properties response missing results")
    singular = _singular(object_type)
    normalized: list[dict[str, Any]] = []
    lifecycle_stages: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        label = str(raw.get("label") or name).strip()
        property_ref = refs.upsert(
            credential=credential,
            object_type=f"{singular}-property",
            provider_object_id=name,
            display_name=label,
            metadata_json={
                "hubspot_defined": bool(raw.get("hubspotDefined")),
                "has_unique_value": bool(raw.get("hasUniqueValue")),
            },
        )
        group_ref: str | None = None
        group_name = str(raw.get("groupName") or "").strip()
        if group_name:
            group_ref = refs.upsert(
                credential=credential,
                object_type=f"{singular}-property-group",
                provider_object_id=group_name,
                display_name=group_name,
            )
        options: list[dict[str, Any]] = []
        for raw_option in raw.get("options") or []:
            if not isinstance(raw_option, dict):
                continue
            value = str(raw_option.get("value") or "").strip()
            if not value:
                continue
            option_label = str(raw_option.get("label") or value).strip()
            option_ref = refs.upsert(
                credential=credential,
                object_type=f"{singular}-property-option",
                provider_object_id=json.dumps([name, value], separators=(",", ":")),
                display_name=option_label,
                metadata_json={"property_ref": property_ref},
            )
            option = {
                "option_ref": option_ref,
                "label": option_label,
                "display_order": raw_option.get("displayOrder"),
                "hidden": bool(raw_option.get("hidden")),
            }
            options.append(option)
            if singular == "contact" and name == "lifecyclestage":
                lifecycle_stages.append(
                    {
                        "stage_ref": option_ref,
                        "label": option_label,
                        "display_order": raw_option.get("displayOrder"),
                        "hidden": bool(raw_option.get("hidden")),
                    }
                )
        item: dict[str, Any] = {
            "property_ref": property_ref,
            "label": label,
            "type": raw.get("type"),
            "field_type": raw.get("fieldType"),
            "group_ref": group_ref,
            "description": raw.get("description") or None,
            "is_custom": not bool(raw.get("hubspotDefined")),
            "has_unique_value": bool(raw.get("hasUniqueValue")),
            "hidden": bool(raw.get("hidden")),
            "sensitive": bool(raw.get("sensitiveDataCategories")),
            "options": options,
        }
        normalized.append(item)
    return normalized, lifecycle_stages


def _owner_metadata(request: ActionConnectorRequest, body: Any) -> list[dict[str, Any]]:
    refs, credential = _ref_context(request)
    raw_results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(raw_results, list):
        raise ValidationError("HubSpot owners response missing results")
    normalized: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        display_name = " ".join(
            part
            for part in (
                str(raw.get("firstName") or "").strip(),
                str(raw.get("lastName") or "").strip(),
            )
            if part
        )
        owner_ref = refs.upsert(
            credential=credential,
            object_type="owner",
            provider_object_id=raw["id"],
            display_name=display_name or str(raw.get("email") or "Owner"),
        )
        teams: list[dict[str, Any]] = []
        for raw_team in raw.get("teams") or []:
            if not isinstance(raw_team, dict) or raw_team.get("id") is None:
                continue
            team_name = str(raw_team.get("name") or "Team").strip()
            teams.append(
                {
                    "team_ref": refs.upsert(
                        credential=credential,
                        object_type="team",
                        provider_object_id=raw_team["id"],
                        display_name=team_name,
                    ),
                    "name": team_name,
                    "primary": bool(raw_team.get("primary")),
                }
            )
        normalized.append(
            {
                "owner_ref": owner_ref,
                "email": raw.get("email"),
                "first_name": raw.get("firstName"),
                "last_name": raw.get("lastName"),
                "type": raw.get("type"),
                "archived": bool(raw.get("archived")),
                "teams": teams,
            }
        )
    return normalized


def _pipeline_metadata(request: ActionConnectorRequest, body: Any) -> list[dict[str, Any]]:
    refs, credential = _ref_context(request)
    raw_results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(raw_results, list):
        raise ValidationError("HubSpot pipelines response missing results")
    normalized: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        pipeline_id = str(raw["id"])
        pipeline_ref = refs.upsert(
            credential=credential,
            object_type="deal-pipeline",
            provider_object_id=pipeline_id,
            display_name=str(raw.get("label") or "Deal pipeline"),
        )
        stages: list[dict[str, Any]] = []
        for raw_stage in raw.get("stages") or []:
            if not isinstance(raw_stage, dict) or raw_stage.get("id") is None:
                continue
            metadata = raw_stage.get("metadata")
            safe_metadata: dict[str, Any] = {}
            if isinstance(metadata, dict) and metadata.get("probability") is not None:
                safe_metadata["probability"] = metadata["probability"]
            stages.append(
                {
                    "stage_ref": refs.upsert(
                        credential=credential,
                        object_type="deal-pipeline-stage",
                        provider_object_id=json.dumps(
                            [pipeline_id, str(raw_stage["id"])],
                            separators=(",", ":"),
                        ),
                        display_name=str(raw_stage.get("label") or "Deal stage"),
                        metadata_json={"pipeline_ref": pipeline_ref},
                    ),
                    "label": raw_stage.get("label"),
                    "display_order": raw_stage.get("displayOrder"),
                    "archived": bool(raw_stage.get("archived")),
                    "metadata": safe_metadata,
                }
            )
        normalized.append(
            {
                "pipeline_ref": pipeline_ref,
                "label": raw.get("label"),
                "display_order": raw.get("displayOrder"),
                "archived": bool(raw.get("archived")),
                "stages": stages,
            }
        )
    return normalized


def _association_label_metadata(
    request: ActionConnectorRequest,
    *,
    relationship: str,
    body: Any,
) -> list[dict[str, Any]]:
    refs, credential = _ref_context(request)
    raw_results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(raw_results, list):
        raise ValidationError("HubSpot association labels response missing results")
    normalized: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict) or raw.get("typeId") is None:
            continue
        label = raw.get("label")
        is_unlabeled = label is None
        normalized.append(
            {
                "association_label_ref": refs.upsert(
                    credential=credential,
                    object_type=f"association-label:{relationship}",
                    provider_object_id=raw["typeId"],
                    display_name=str(label or "Unlabeled"),
                    metadata_json={
                        "category": raw.get("category"),
                        "is_unlabeled": is_unlabeled,
                    },
                ),
                "label": label,
                "category": raw.get("category"),
            }
        )
    return normalized


def _association_contract(operation: str) -> tuple[str, str, str, str, str]:
    action = operation.rsplit(".", 1)[0]
    try:
        from_type, to_type, from_plural, to_plural = _ASSOCIATION_CONTRACTS[action]
    except KeyError as exc:
        raise ValidationError(f"unsupported HubSpot association operation {operation!r}") from exc
    relationship = action.removeprefix("crm.").replace("_", "-")
    return relationship, from_type, to_type, from_plural, to_plural


def _resolved_association(
    request: ActionConnectorRequest,
) -> tuple[Any, Any, Any, str, int, str, str, str, str]:
    refs, credential = _ref_context(request)
    relationship, from_type, to_type, from_plural, to_plural = _association_contract(
        request.operation
    )
    from_record = refs.resolve(
        credential=credential,
        safe_ref=str(request.input_json.get("from_ref")),
        expected_object_type=from_type,
    )
    to_record = refs.resolve(
        credential=credential,
        safe_ref=str(request.input_json.get("to_ref")),
        expected_object_type=to_type,
    )
    label = refs.resolve(
        credential=credential,
        safe_ref=str(request.input_json.get("association_label_ref")),
        expected_object_type=f"association-label:{relationship}",
    )
    category = str((label.metadata_json or {}).get("category") or "")
    if category not in {"HUBSPOT_DEFINED", "USER_DEFINED"}:
        raise ValidationError("HubSpot association label category is invalid or stale")
    if (
        request.operation.endswith(".dissociate")
        and (label.metadata_json or {}).get("is_unlabeled") is not False
    ):
        raise ValidationError(
            "HubSpot label-specific removal requires a provider-verified labeled association; "
            "unlabeled and legacy ambiguous refs are rejected"
        )
    try:
        type_id = int(label.provider_object_id)
    except ValueError as exc:
        raise ValidationError("HubSpot association label type is invalid") from exc
    return (
        from_record,
        to_record,
        label,
        category,
        type_id,
        from_type,
        to_type,
        from_plural,
        to_plural,
    )


def _association_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    relationship_state: str,
) -> ActionConnectorResult:
    request_id = _request_id(headers, body)
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "relationship_state": relationship_state,
        "from_ref": request.input_json["from_ref"],
        "to_ref": request.input_json["to_ref"],
        "association_label_ref": request.input_json["association_label_ref"],
    }
    if request_id:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-provider-refs-v1",
    }
    if request_id:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _resolved_line_item_deal(request: ActionConnectorRequest) -> tuple[Any, Any]:
    refs, credential = _ref_context(request)
    line_item = refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "line_item_ref"),
        expected_object_type="line-item",
    )
    deal = refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "deal_ref"),
        expected_object_type="deal",
    )
    return line_item, deal


def _line_item_deal_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    relationship_state: str,
) -> ActionConnectorResult:
    request_id = _request_id(headers, body)
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "relationship_state": relationship_state,
        "line_item_ref": request.input_json["line_item_ref"],
        "deal_ref": request.input_json["deal_ref"],
    }
    if request_id:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-provider-refs-v1",
    }
    if request_id:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _singular(object_type: str) -> str:
    try:
        return _OBJECT_SINGULAR[object_type]
    except KeyError as exc:
        raise ValidationError(f"unsupported HubSpot object type {object_type!r}") from exc


def _resolved_property_id(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    property_ref: Any,
) -> str:
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=str(property_ref),
        expected_object_type=f"{_singular(object_type)}-property",
    ).provider_object_id


def _resolved_unique_property_id(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    property_ref: Any,
) -> str:
    refs, credential = _ref_context(request)
    resolved = refs.resolve(
        credential=credential,
        safe_ref=str(property_ref),
        expected_object_type=f"{_singular(object_type)}-property",
    )
    if object_type == "contacts" and resolved.provider_object_id == "email":
        return resolved.provider_object_id
    metadata = resolved.metadata_json or {}
    if not metadata.get("has_unique_value"):
        raise ValidationError(
            "HubSpot batch upsert id_property_ref must be provider-verified as unique"
        )
    if resolved.provider_object_id == "hs_object_id":
        raise ValidationError(
            "HubSpot internal object IDs cannot be used as reusable upsert identifiers"
        )
    return resolved.provider_object_id


def _render_property_value(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    property_name: str,
    value: Any,
) -> Any:
    if isinstance(value, list):
        return ";".join(
            str(
                _render_property_value(
                    request,
                    object_type=object_type,
                    property_name=property_name,
                    value=item,
                )
            )
            for item in value
        )
    entity_types = {
        "hubspot_owner_id": "owner",
        "hs_associated_contact_id": "contact",
        "hs_associated_company_id": "company",
        "hs_associated_deal_id": "deal",
        "pipeline": "deal-pipeline",
        "dealstage": "deal-pipeline-stage",
    }
    if object_type == "line_items" and property_name == "hs_product_id":
        entity_types[property_name] = "product"
    is_safe_ref = isinstance(value, str) and value.startswith("provider-object:")
    if property_name in entity_types and not is_safe_ref:
        raise ValidationError(f"HubSpot {property_name} must use a typed provider-object ref")
    if not is_safe_ref:
        return value
    refs, credential = _ref_context(request)
    singular = _singular(object_type)
    expected = entity_types.get(property_name, f"{singular}-property-option")
    resolved = refs.resolve(
        credential=credential,
        safe_ref=value,
        expected_object_type=expected,
    )
    provider_value = resolved.provider_object_id
    if expected.endswith("-property-option"):
        try:
            option_property, option_value = json.loads(provider_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValidationError("HubSpot property option ref is malformed") from exc
        if option_property != property_name:
            raise ValidationError("HubSpot property option ref belongs to another property")
        return option_value
    if expected == "deal-pipeline-stage":
        try:
            _pipeline_id, stage_id = json.loads(provider_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValidationError("HubSpot deal stage ref is malformed") from exc
        return stage_id
    return provider_value


def _validate_property_value(
    *,
    object_type: str,
    property_name: str,
    value: Any,
) -> None:
    if object_type != "line_items" or property_name != "price" or value in (None, ""):
        return
    if isinstance(value, bool):
        raise ValidationError("HubSpot line-item price must be a non-negative number")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("HubSpot line-item price must be a non-negative number") from exc
    if not price.is_finite() or price < 0:
        raise ValidationError("HubSpot line-item price must be a non-negative number")


def _upsert_inputs(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    id_property = _resolved_unique_property_id(
        request,
        object_type=object_type,
        property_ref=payload["id_property_ref"],
    )
    inputs: list[dict[str, Any]] = []
    for raw in payload["inputs"]:
        if not isinstance(raw, dict):
            raise ValidationError("HubSpot batch upsert inputs must be objects")
        raw_properties = raw.get("properties")
        if not isinstance(raw_properties, list):
            raise ValidationError("HubSpot batch upsert input.properties is required")
        properties: dict[str, Any] = {}
        for property_value in raw_properties:
            if not isinstance(property_value, dict):
                raise ValidationError("HubSpot property values must be objects")
            property_name = _resolved_property_id(
                request,
                object_type=object_type,
                property_ref=property_value.get("property_ref"),
            )
            if property_name in properties:
                raise ValidationError("HubSpot batch upsert properties must be unique")
            rendered_value = _render_property_value(
                request,
                object_type=object_type,
                property_name=property_name,
                value=property_value.get("value"),
            )
            _validate_property_value(
                object_type=object_type,
                property_name=property_name,
                value=rendered_value,
            )
            properties[property_name] = rendered_value
        record_id = raw.get("id") or properties.get(id_property)
        if record_id is None or not str(record_id).strip():
            raise ValidationError(
                "HubSpot batch upsert input missing id or the selected unique property"
            )
        rendered = {
            "id": str(record_id),
            "idProperty": id_property,
            "properties": properties,
        }
        if raw.get("trace_id"):
            rendered["objectWriteTraceId"] = str(raw["trace_id"])
        inputs.append(rendered)
    return inputs


def _safe_property_value(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    property_name: str,
    value: Any,
    raw_properties: Mapping[str, Any],
) -> tuple[str, Any]:
    if value in (None, ""):
        return "value", value
    refs, credential = _ref_context(request)
    singular = _singular(object_type)
    ref_type: str | None = None
    provider_id: str | None = None
    if property_name == "hubspot_owner_id":
        ref_type, provider_id = "owner", str(value)
    elif property_name == "hs_associated_contact_id":
        ref_type, provider_id = "contact", str(value)
    elif property_name == "hs_associated_company_id":
        ref_type, provider_id = "company", str(value)
    elif property_name == "hs_associated_deal_id":
        ref_type, provider_id = "deal", str(value)
    elif object_type == "line_items" and property_name == "hs_product_id":
        ref_type, provider_id = "product", str(value)
    elif object_type == "deals" and property_name == "pipeline":
        ref_type, provider_id = "deal-pipeline", str(value)
    elif object_type == "deals" and property_name == "dealstage":
        pipeline_id = raw_properties.get("pipeline")
        if pipeline_id not in (None, ""):
            ref_type = "deal-pipeline-stage"
            provider_id = json.dumps([str(pipeline_id), str(value)], separators=(",", ":"))
    elif object_type == "contacts" and property_name == "lifecyclestage":
        ref_type = "contact-property-option"
        provider_id = json.dumps([property_name, str(value)], separators=(",", ":"))
    if ref_type is None or provider_id is None:
        return "value", value
    return (
        "value_ref",
        refs.upsert(
            credential=credential,
            object_type=ref_type,
            provider_object_id=provider_id,
            display_name=str(value),
            metadata_json={"source_property_type": f"{singular}-property"},
        ),
    )


def _normalized_properties(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    raw_properties: Any,
) -> list[dict[str, Any]]:
    if not isinstance(raw_properties, dict):
        return []
    refs, credential = _ref_context(request)
    singular = _singular(object_type)
    normalized: list[dict[str, Any]] = []
    for property_name, value in raw_properties.items():
        if property_name == "hs_object_id":
            continue
        property_ref = refs.upsert(
            credential=credential,
            object_type=f"{singular}-property",
            provider_object_id=property_name,
            display_name=property_name,
        )
        value_key, safe_value = _safe_property_value(
            request,
            object_type=object_type,
            property_name=property_name,
            value=value,
            raw_properties=raw_properties,
        )
        normalized.append({"property_ref": property_ref, value_key: safe_value})
    return normalized


def _normalized_property_history(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    raw_history: Any,
    raw_properties: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(raw_history, dict):
        return []
    refs, credential = _ref_context(request)
    singular = _singular(object_type)
    normalized: list[dict[str, Any]] = []
    for property_name, raw_values in raw_history.items():
        if property_name == "hs_object_id" or not isinstance(raw_values, list):
            continue
        property_ref = refs.upsert(
            credential=credential,
            object_type=f"{singular}-property",
            provider_object_id=property_name,
            display_name=property_name,
        )
        values: list[dict[str, Any]] = []
        for raw_value in raw_values:
            if not isinstance(raw_value, dict):
                continue
            value_key, value = _safe_property_value(
                request,
                object_type=object_type,
                property_name=property_name,
                value=raw_value.get("value"),
                raw_properties=raw_properties,
            )
            entry: dict[str, Any] = {
                value_key: value,
                "timestamp": raw_value.get("timestamp"),
                "source_type": raw_value.get("sourceType"),
            }
            values.append(entry)
        normalized.append({"property_ref": property_ref, "values": values})
    return normalized


def _record_results(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    body: Any,
) -> list[dict[str, Any]]:
    refs, credential = _ref_context(request)
    raw_results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(raw_results, list):
        raise ValidationError("HubSpot record response missing results")
    singular = _singular(object_type)
    normalized: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        raw_properties = raw.get("properties")
        if not isinstance(raw_properties, dict):
            raw_properties = {}
        display_name = next(
            (
                str(raw_properties[key])
                for key in (
                    "email",
                    "name",
                    "dealname",
                    "hs_lead_name",
                    "hs_title",
                    "hs_goal_name",
                    "hs_sku",
                    "domain",
                )
                if raw_properties.get(key)
            ),
            singular,
        )
        item: dict[str, Any] = {
            "record_ref": refs.upsert(
                credential=credential,
                object_type=singular,
                provider_object_id=raw["id"],
                display_name=display_name,
            ),
            "properties": _normalized_properties(
                request,
                object_type=object_type,
                raw_properties=raw_properties,
            ),
            "created_at": raw.get("createdAt"),
            "updated_at": raw.get("updatedAt"),
            "archived": bool(raw.get("archived")),
        }
        history = _normalized_property_history(
            request,
            object_type=object_type,
            raw_history=raw.get("propertiesWithHistory"),
            raw_properties=raw_properties,
        )
        if history:
            item["property_history"] = history
        trace_id = raw.get("objectWriteTraceId")
        if trace_id:
            item["trace_id"] = str(trace_id)
        if raw.get("new") is not None:
            item["created"] = bool(raw.get("new"))
        normalized.append(item)
    return normalized


def _record_result(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    extra: dict[str, Any] = {}
    if isinstance(body, dict) and isinstance(body.get("total"), int):
        extra["total"] = body["total"]
    return _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body=body,
        headers=headers,
        results=_record_results(request, object_type=object_type, body=body),
        extra=extra,
    )


def _batch_result(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    if not isinstance(body, dict):
        raise ValidationError("HubSpot batch response must be an object")
    results = _record_results(request, object_type=object_type, body=body)
    failures: list[dict[str, Any]] = []
    for raw_error in body.get("errors") or []:
        if not isinstance(raw_error, dict):
            continue
        context = raw_error.get("context")
        trace_ids: list[str] = []
        if isinstance(context, dict):
            raw_trace_ids = context.get("objectWriteTraceId")
            if isinstance(raw_trace_ids, list):
                trace_ids = [str(item) for item in raw_trace_ids]
            elif raw_trace_ids is not None:
                trace_ids = [str(raw_trace_ids)]
        failures.append(
            {
                "category": raw_error.get("category"),
                "message": redact_secret_text(str(raw_error.get("message") or "Batch row failed")),
                "trace_ids": trace_ids,
            }
        )
    request_id = _request_id(headers, body)
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": "partial" if failures else "success",
        "provider_status": body.get("status"),
        "success_count": len(results),
        "failure_count": len(failures),
        "results": results,
        "failures": failures,
        "started_at": body.get("startedAt"),
        "completed_at": body.get("completedAt"),
    }
    if request_id:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-provider-refs-v1",
        "partial_failure": bool(failures),
    }
    if request_id:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _activity_timestamp_issue(
    payload: dict[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        issues.append(
            ActionValidationIssue(
                path=f"$.{key}",
                message=f"{key} must be a UTC timestamp or Unix milliseconds",
                code="type_error",
            )
        )
    elif isinstance(value, str) and not value.strip():
        issues.append(
            ActionValidationIssue(
                path=f"$.{key}",
                message=f"{key} must not be empty",
                code="required",
            )
        )


def _activity_associations(
    request: ActionConnectorRequest,
    *,
    activity_type: str,
    safe_refs: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if safe_refs is None:
        return [], []
    if not isinstance(safe_refs, list):
        raise ValidationError("HubSpot activity associations must be a list")
    refs, credential = _ref_context(request)
    type_ids = _ACTIVITY_ASSOCIATION_TYPE_IDS[activity_type]
    rendered: list[dict[str, Any]] = []
    safe_output: list[dict[str, str]] = []
    for safe_ref in safe_refs:
        resolved = refs.resolve_one_of(
            credential=credential,
            safe_ref=safe_ref,
            expected_object_types=set(type_ids),
        )
        rendered.append(
            {
                "to": {"id": resolved.provider_object_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": type_ids[resolved.object_type],
                    }
                ],
            }
        )
        safe_output.append(
            {
                "record_ref": resolved.safe_ref,
                "object_type": resolved.object_type,
            }
        )
    return rendered, safe_output


def _activity_owner_id(
    request: ActionConnectorRequest,
    *,
    owner_ref: Any,
) -> str | None:
    if owner_ref is None:
        return None
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=owner_ref,
        expected_object_type="owner",
    ).provider_object_id


def _activity_body(
    request: ActionConnectorRequest,
    *,
    activity_type: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    properties: dict[str, str] = {}
    if activity_type == "notes":
        properties = {
            "hs_timestamp": str(payload["timestamp"]),
            "hs_note_body": payload["body"],
        }
    elif activity_type == "tasks":
        properties = {
            "hs_timestamp": str(payload["due_at"]),
            "hs_task_subject": payload["title"],
        }
        for input_key, property_key in (
            ("body", "hs_task_body"),
            ("status", "hs_task_status"),
            ("priority", "hs_task_priority"),
        ):
            if payload.get(input_key) is not None:
                properties[property_key] = str(payload[input_key])
    elif activity_type == "calls":
        properties = {
            "hs_timestamp": str(payload["timestamp"]),
            "hs_call_direction": payload["direction"],
        }
        for input_key, property_key in (
            ("title", "hs_call_title"),
            ("body", "hs_call_body"),
            ("status", "hs_call_status"),
            ("duration_ms", "hs_call_duration"),
        ):
            if payload.get(input_key) is not None:
                properties[property_key] = str(payload[input_key])
    elif activity_type == "meetings":
        properties = {
            "hs_timestamp": str(payload["timestamp"]),
            "hs_meeting_title": payload["title"],
        }
        for input_key, property_key in (
            ("body", "hs_meeting_body"),
            ("start_time", "hs_meeting_start_time"),
            ("end_time", "hs_meeting_end_time"),
            ("outcome", "hs_meeting_outcome"),
        ):
            if payload.get(input_key) is not None:
                properties[property_key] = str(payload[input_key])
    else:  # pragma: no cover - guarded by the manifest operation contract
        raise ValidationError(f"unsupported HubSpot activity type {activity_type!r}")

    owner_id = _activity_owner_id(request, owner_ref=payload.get("owner_ref"))
    if owner_id is not None:
        properties["hubspot_owner_id"] = owner_id
    associations, safe_associations = _activity_associations(
        request,
        activity_type=activity_type,
        safe_refs=payload.get("associations"),
    )
    rendered: dict[str, Any] = {"properties": properties}
    if associations:
        rendered["associations"] = associations
    return rendered, safe_associations


def _activity_result(
    request: ActionConnectorRequest,
    *,
    activity_type: str,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    safe_associations: list[dict[str, str]],
) -> ActionConnectorResult:
    if not isinstance(body, dict) or body.get("id") is None:
        raise ValidationError("HubSpot activity response missing id")
    refs, credential = _ref_context(request)
    singular = _ACTIVITY_SINGULAR[activity_type]
    activity_ref = refs.upsert(
        credential=credential,
        object_type=singular,
        provider_object_id=body["id"],
        display_name=f"HubSpot {singular}",
    )
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "activity_ref": activity_ref,
        "activity_type": singular,
        "associations": safe_associations,
    }
    if payload_owner_ref := request.input_json.get("owner_ref"):
        output["owner_ref"] = payload_owner_ref
    for provider_key, output_key in (
        ("createdAt", "created_at"),
        ("updatedAt", "updated_at"),
    ):
        if body.get(provider_key) is not None:
            output[output_key] = body[provider_key]
    request_id = _request_id(headers, body)
    if request_id is not None:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-provider-refs-v1",
    }
    if request_id is not None:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _safe_page_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _form_results(
    request: ActionConnectorRequest,
    *,
    body: Any,
) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValidationError("HubSpot forms response missing results")
    refs, credential = _ref_context(request)
    normalized: list[dict[str, Any]] = []
    for raw in body["results"]:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        form_id = str(raw["id"])
        name = str(raw.get("name") or "HubSpot form")
        form_ref = refs.upsert(
            credential=credential,
            object_type="form",
            provider_object_id=form_id,
            display_name=name,
            metadata_json={"form_type": raw.get("formType")},
        )
        fields: list[dict[str, Any]] = []
        seen_fields: set[str] = set()
        for group in raw.get("fieldGroups") or []:
            if not isinstance(group, dict):
                continue
            for field in group.get("fields") or []:
                if not isinstance(field, dict):
                    continue
                field_name = str(field.get("name") or "").strip()
                if not field_name or field_name in seen_fields:
                    continue
                seen_fields.add(field_name)
                object_type_id = str(field.get("objectTypeId") or "")
                object_type = {
                    "0-1": "contact",
                    "0-2": "company",
                    "0-3": "deal",
                }.get(object_type_id, "other")
                label = str(field.get("label") or field_name)
                field_ref = refs.upsert(
                    credential=credential,
                    object_type="form-field",
                    provider_object_id=json.dumps(
                        [form_id, field_name],
                        separators=(",", ":"),
                    ),
                    display_name=label,
                    metadata_json={
                        "form_ref": form_ref,
                        "field_type": field.get("fieldType"),
                        "object_type_id": object_type_id,
                    },
                )
                fields.append(
                    {
                        "field_ref": field_ref,
                        "label": label,
                        "field_type": field.get("fieldType"),
                        "object_type": object_type,
                        "required": bool(field.get("required")),
                        "hidden": bool(field.get("hidden")),
                    }
                )
        consent = raw.get("legalConsentOptions")
        consent_type = consent.get("type") if isinstance(consent, dict) else None
        normalized.append(
            {
                "form_ref": form_ref,
                "name": name,
                "form_type": raw.get("formType"),
                "archived": bool(raw.get("archived")),
                "created_at": raw.get("createdAt"),
                "updated_at": raw.get("updatedAt"),
                "legal_consent_type": consent_type,
                "fields": fields,
            }
        )
    return normalized


def _resolved_form(request: ActionConnectorRequest) -> Any:
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "form_ref"),
        expected_object_type="form",
    )


def _form_submission_result(
    request: ActionConnectorRequest,
    *,
    form: Any,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValidationError("HubSpot form submissions response missing results")
    refs, credential = _ref_context(request)
    submissions: list[dict[str, Any]] = []
    for raw in body["results"]:
        if not isinstance(raw, dict) or raw.get("conversionId") is None:
            raise ValidationError("HubSpot form submission missing conversionId")
        conversion_id = str(raw["conversionId"])
        values: list[dict[str, Any]] = []
        for raw_value in raw.get("values") or []:
            if not isinstance(raw_value, dict):
                continue
            field_name = str(raw_value.get("name") or "").strip()
            if not field_name:
                continue
            field_ref = refs.upsert(
                credential=credential,
                object_type="form-field",
                provider_object_id=json.dumps(
                    [form.provider_object_id, field_name],
                    separators=(",", ":"),
                ),
                display_name=field_name,
                metadata_json={"form_ref": form.safe_ref},
            )
            values.append({"field_ref": field_ref, "value": raw_value.get("value")})
        submission: dict[str, Any] = {
            "submission_ref": refs.upsert(
                credential=credential,
                object_type="form-submission",
                provider_object_id=json.dumps(
                    [form.provider_object_id, conversion_id],
                    separators=(",", ":"),
                ),
                display_name="HubSpot form submission",
                metadata_json={"form_ref": form.safe_ref},
            ),
            "submitted_at": raw.get("submittedAt"),
            "values": values,
        }
        page_url = _safe_page_url(raw.get("pageUrl"))
        if page_url is not None:
            submission["page_url"] = page_url
        submissions.append(submission)
    result_value = _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body=body,
        headers=headers,
        results=submissions,
        extra={"form_ref": form.safe_ref},
    )
    result_value.metadata_json = {
        **(result_value.metadata_json or {}),
        "sensitive_data": True,
    }
    return result_value


def _segment_search_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    if not isinstance(body, dict) or not isinstance(body.get("lists"), list):
        raise ValidationError("HubSpot segment search response missing lists")
    refs, credential = _ref_context(request)
    segments: list[dict[str, Any]] = []
    for raw in body["lists"]:
        if not isinstance(raw, dict) or raw.get("listId") is None:
            continue
        object_type_id = str(raw.get("objectTypeId") or "")
        if object_type_id != _CONTACT_OBJECT_TYPE_ID:
            raise ValidationError("HubSpot segment search returned a non-contact list")
        processing_type = str(raw.get("processingType") or "").upper()
        processing_status = str(raw.get("processingStatus") or "")
        name = str(raw.get("name") or "HubSpot segment")
        additional = raw.get("additionalProperties")
        size: Any = raw.get("size")
        if size is None and isinstance(additional, dict):
            size = additional.get("hs_list_size")
        try:
            normalized_size = int(size) if size is not None else None
        except (TypeError, ValueError):
            normalized_size = None
        segment_ref = refs.upsert(
            credential=credential,
            object_type="segment",
            provider_object_id=raw["listId"],
            display_name=name,
            metadata_json={
                "object_type_id": object_type_id,
                "processing_type": processing_type,
                "processing_status": processing_status,
            },
        )
        segments.append(
            {
                "segment_ref": segment_ref,
                "name": name,
                "object_type": "contact",
                "processing_type": processing_type,
                "processing_status": processing_status,
                "size": normalized_size,
                "created_at": raw.get("createdAt"),
                "updated_at": raw.get("updatedAt"),
            }
        )
    extra: dict[str, Any] = {}
    if isinstance(body.get("total"), int):
        extra["total"] = body["total"]
    if body.get("hasMore") and body.get("offset") is not None:
        extra["paging"] = {"offset": int(body["offset"])}
    return _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body={},
        headers=headers,
        results=segments,
        extra=extra,
    )


def _resolved_segment(
    request: ActionConnectorRequest,
    *,
    require_mutable: bool,
) -> Any:
    refs, credential = _ref_context(request)
    segment = refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "segment_ref"),
        expected_object_type="segment",
    )
    metadata = segment.metadata_json or {}
    if metadata.get("object_type_id") != _CONTACT_OBJECT_TYPE_ID:
        raise ValidationError("HubSpot segment must be verified as a contact segment")
    processing_type = str(metadata.get("processing_type") or "").upper()
    if require_mutable and processing_type not in {"MANUAL", "SNAPSHOT"}:
        raise ValidationError("HubSpot membership changes require a MANUAL or SNAPSHOT segment")
    return segment


def _resolved_contact_refs(
    request: ActionConnectorRequest,
) -> tuple[list[str], dict[str, str]]:
    raw_refs = request.input_json.get("contact_refs")
    if not isinstance(raw_refs, list):
        raise ValidationError("HubSpot contact_refs must be a list")
    refs, credential = _ref_context(request)
    provider_ids: list[str] = []
    safe_by_provider_id: dict[str, str] = {}
    for safe_ref in raw_refs:
        contact = refs.resolve(
            credential=credential,
            safe_ref=safe_ref,
            expected_object_type="contact",
        )
        provider_ids.append(contact.provider_object_id)
        safe_by_provider_id[contact.provider_object_id] = contact.safe_ref
    return provider_ids, safe_by_provider_id


def _segment_membership_result(
    request: ActionConnectorRequest,
    *,
    segment: Any,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValidationError("HubSpot segment memberships response missing results")
    refs, credential = _ref_context(request)
    memberships: list[dict[str, Any]] = []
    for raw in body["results"]:
        if not isinstance(raw, dict) or raw.get("recordId") is None:
            continue
        memberships.append(
            {
                "contact_ref": refs.upsert(
                    credential=credential,
                    object_type="contact",
                    provider_object_id=raw["recordId"],
                    display_name="HubSpot contact",
                ),
                "membership_timestamp": raw.get("membershipTimestamp"),
            }
        )
    extra: dict[str, Any] = {"segment_ref": segment.safe_ref}
    if isinstance(body.get("total"), int):
        extra["total"] = body["total"]
    return _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body=body,
        headers=headers,
        results=memberships,
        extra=extra,
    )


def _segment_mutation_result(
    request: ActionConnectorRequest,
    *,
    segment: Any,
    safe_by_provider_id: dict[str, str],
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    if not isinstance(body, dict):
        raise ValidationError("HubSpot segment mutation response must be an object")
    refs, credential = _ref_context(request)

    def safe_refs(raw_values: Any) -> list[str]:
        if not isinstance(raw_values, list):
            return []
        normalized: list[str] = []
        for raw_id in raw_values:
            provider_id = str(raw_id)
            normalized.append(
                safe_by_provider_id.get(provider_id)
                or refs.upsert(
                    credential=credential,
                    object_type="contact",
                    provider_object_id=provider_id,
                    display_name="HubSpot contact",
                )
            )
        return normalized

    added = safe_refs(body.get("recordsIdsAdded") or body.get("recordIdsAdded"))
    removed = safe_refs(body.get("recordIdsRemoved"))
    missing = safe_refs(body.get("recordIdsMissing"))
    request_id = _request_id(headers, body)
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": "partial" if missing else "success",
        "segment_ref": segment.safe_ref,
        "added_contact_refs": added,
        "removed_contact_refs": removed,
        "missing_contact_refs": missing,
        "success_count": len(added) + len(removed),
        "missing_count": len(missing),
    }
    if request_id:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-provider-refs-v1",
        "partial_failure": bool(missing),
    }
    if request_id:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _safe_metric_map(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict) or depth > 2:
        return {}
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        compact_key = key.lower().replace("_", "")
        if compact_key.endswith("id") or compact_key.endswith("ids"):
            continue
        if isinstance(raw_value, (bool, int, float)):
            normalized[key] = raw_value
        elif isinstance(raw_value, dict):
            nested = _safe_metric_map(raw_value, depth=depth + 1)
            if nested:
                normalized[key] = nested
    return normalized


def _brand_ref(
    request: ActionConnectorRequest,
    *,
    provider_id: Any,
) -> str | None:
    if provider_id is None:
        return None
    refs, credential = _ref_context(request)
    return refs.upsert(
        credential=credential,
        object_type="brand",
        provider_object_id=provider_id,
        display_name="HubSpot brand",
    )


def _campaign_assets(
    request: ActionConnectorRequest,
    *,
    raw_assets: Any,
) -> list[dict[str, Any]]:
    if not isinstance(raw_assets, dict):
        return []
    refs, credential = _ref_context(request)
    normalized: list[dict[str, Any]] = []
    for raw_asset_type, raw_group in raw_assets.items():
        if not isinstance(raw_group, dict):
            continue
        asset_type = str(raw_asset_type).upper()
        raw_results = raw_group.get("results")
        paging = raw_group.get("paging")
        if not isinstance(raw_results, list) and isinstance(paging, dict):
            raw_results = paging.get("results")
        if not isinstance(raw_results, list):
            continue
        object_type = _CAMPAIGN_ASSET_TYPES.get(asset_type, "campaign-asset")
        for raw in raw_results:
            if not isinstance(raw, dict) or raw.get("id") is None:
                continue
            provider_id: Any = raw["id"]
            if object_type == "campaign-asset":
                provider_id = json.dumps(
                    [asset_type, str(raw["id"])],
                    separators=(",", ":"),
                )
            item: dict[str, Any] = {
                "asset_ref": refs.upsert(
                    credential=credential,
                    object_type=object_type,
                    provider_object_id=provider_id,
                    display_name=str(raw.get("name") or f"HubSpot {asset_type} asset"),
                    metadata_json={"campaign_asset_type": asset_type},
                ),
                "asset_type": asset_type.lower(),
                "name": raw.get("name"),
            }
            metrics = _safe_metric_map(raw.get("metrics"))
            if metrics:
                item["metrics"] = metrics
            normalized.append(item)
    return normalized


def _campaign_item(
    request: ActionConnectorRequest,
    *,
    raw: Any,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("id") is None:
        raise ValidationError("HubSpot campaign response missing id")
    refs, credential = _ref_context(request)
    properties = raw.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    name = str(properties.get("hs_name") or "HubSpot campaign")
    metadata_json: dict[str, Any] = {
        "status": properties.get("hs_campaign_status"),
    }
    campaign_ref = refs.upsert(
        credential=credential,
        object_type="campaign",
        provider_object_id=raw["id"],
        display_name=name,
        metadata_json=metadata_json,
    )
    item: dict[str, Any] = {
        "campaign_ref": campaign_ref,
        "name": name,
        "created_at": raw.get("createdAt"),
        "updated_at": raw.get("updatedAt"),
    }
    for output_key, provider_key in _CAMPAIGN_PROPERTIES.items():
        if output_key == "name":
            continue
        if properties.get(provider_key) is not None:
            item[output_key] = properties[provider_key]
    owner_id = properties.get("hs_owner")
    if owner_id is not None:
        item["owner_ref"] = refs.upsert(
            credential=credential,
            object_type="owner",
            provider_object_id=owner_id,
            display_name="HubSpot owner",
        )
    for provider_key, output_key in (
        ("hs_budget_items_sum_amount", "budget_total"),
        ("hs_spend_items_sum_amount", "spend_total"),
    ):
        if properties.get(provider_key) is not None:
            item[output_key] = properties[provider_key]
    brand_refs = [
        ref
        for unit in raw.get("businessUnits") or []
        if isinstance(unit, dict)
        for ref in [_brand_ref(request, provider_id=unit.get("id"))]
        if ref is not None
    ]
    if brand_refs:
        item["brand_refs"] = brand_refs
    assets = _campaign_assets(request, raw_assets=raw.get("assets"))
    if assets:
        item["assets"] = assets
    return item


def _campaign_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    raw_results = body.get("results") if isinstance(body, dict) else None
    if isinstance(raw_results, list):
        results = [_campaign_item(request, raw=raw) for raw in raw_results]
    else:
        results = [_campaign_item(request, raw=body)]
    extra: dict[str, Any] = {}
    if isinstance(body, dict) and isinstance(body.get("total"), int):
        extra["total"] = body["total"]
    return _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body=body,
        headers=headers,
        results=results,
        extra=extra,
    )


def _resolved_campaign(request: ActionConnectorRequest) -> Any:
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "campaign_ref"),
        expected_object_type="campaign",
    )


def _validated_date(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"HubSpot {field} must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"HubSpot {field} must use YYYY-MM-DD") from exc
    return value


def _campaign_write_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for input_key, provider_key in _CAMPAIGN_PROPERTIES.items():
        if payload.get(input_key) is not None:
            properties[provider_key] = payload[input_key]
    for date_key in ("start_date", "end_date"):
        if date_key in payload:
            properties[_CAMPAIGN_PROPERTIES[date_key]] = _validated_date(
                payload.get(date_key),
                field=date_key,
            )
    status = payload.get("status")
    if status is not None and status not in _CAMPAIGN_STATUSES:
        raise ValidationError("HubSpot campaign status is not supported")
    currency = payload.get("currency_code")
    if currency is not None:
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
            raise ValidationError("HubSpot campaign currency_code must be a 3-letter code")
        properties["hs_currency_code"] = currency.upper()
    start_date = properties.get("hs_start_date")
    end_date = properties.get("hs_end_date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValidationError("HubSpot campaign start_date must not follow end_date")
    if not properties:
        raise ValidationError("HubSpot campaign update requires at least one field")
    return {"properties": properties}


def _resolved_optional_ref(
    request: ActionConnectorRequest,
    *,
    input_key: str,
    object_type: str,
) -> Any | None:
    safe_ref = request.input_json.get(input_key)
    if safe_ref is None:
        return None
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=safe_ref,
        expected_object_type=object_type,
    )


def _email_template_path(request: ActionConnectorRequest) -> str:
    template_ref = _resolved_optional_ref(
        request,
        input_key="template_ref",
        object_type="email-template",
    )
    raw_path = request.input_json.get("template_path")
    if template_ref is not None and raw_path is not None:
        raise ValidationError("HubSpot email accepts template_ref or template_path, not both")
    path = template_ref.provider_object_id if template_ref is not None else raw_path
    if not isinstance(path, str) or not path.strip():
        raise ValidationError("HubSpot email template_ref or template_path is required")
    normalized = path.strip()
    if (
        len(normalized) > 500
        or normalized.startswith("/")
        or "://" in normalized
        or ".." in normalized.split("/")
        or not normalized.endswith(".html")
    ):
        raise ValidationError("HubSpot email template_path must be a safe .html asset path")
    return normalized


def _email_write_body(
    request: ActionConnectorRequest,
    *,
    create: bool,
) -> dict[str, Any]:
    payload = request.input_json
    body: dict[str, Any] = {}
    for input_key, provider_key in (("name", "name"), ("subject", "subject")):
        if payload.get(input_key) is not None:
            body[provider_key] = payload[input_key]
    if create:
        body["templatePath"] = _email_template_path(request)
    campaign = _resolved_optional_ref(
        request,
        input_key="campaign_ref",
        object_type="campaign",
    )
    if campaign is not None:
        body["campaign"] = campaign.provider_object_id
    brand = _resolved_optional_ref(
        request,
        input_key="brand_ref",
        object_type="brand",
    )
    if brand is not None:
        try:
            body["businessUnitId"] = int(brand.provider_object_id)
        except ValueError as exc:
            raise ValidationError("HubSpot brand ref does not contain a numeric id") from exc
    if not body:
        raise ValidationError("HubSpot email update requires at least one field")
    return body


def _email_item(
    request: ActionConnectorRequest,
    *,
    raw: Any,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("id") is None:
        raise ValidationError("HubSpot marketing email response missing id")
    refs, credential = _ref_context(request)
    name = str(raw.get("name") or "HubSpot marketing email")
    email_ref = refs.upsert(
        credential=credential,
        object_type="marketing-email",
        provider_object_id=raw["id"],
        display_name=name,
        metadata_json={
            "is_published": bool(raw.get("isPublished")),
            "is_transactional": raw.get("isTransactional") is True,
            "provider_state": raw.get("state"),
            "publish_date": raw.get("publishDate"),
            "archived": bool(raw.get("archived")),
            "email_type": raw.get("type"),
        },
    )
    lifecycle_state = (
        "archived" if raw.get("archived") else ("published" if raw.get("isPublished") else "draft")
    )
    item: dict[str, Any] = {
        "email_ref": email_ref,
        "name": name,
        "subject": raw.get("subject"),
        "email_type": raw.get("type"),
        "subcategory": raw.get("subcategory"),
        "lifecycle_state": lifecycle_state,
        "is_transactional": bool(raw.get("isTransactional")),
        "created_at": raw.get("createdAt"),
        "updated_at": raw.get("updatedAt"),
        "published_at": raw.get("publishedAt"),
        "publish_date": raw.get("publishDate"),
        "recipient_configuration_present": isinstance(raw.get("to"), dict),
    }
    campaign_id = raw.get("campaign")
    if campaign_id:
        item["campaign_ref"] = refs.upsert(
            credential=credential,
            object_type="campaign",
            provider_object_id=campaign_id,
            display_name=str(raw.get("campaignName") or "HubSpot campaign"),
        )
    brand = _brand_ref(request, provider_id=raw.get("businessUnitId"))
    if brand is not None:
        item["brand_ref"] = brand
    folder_id = raw.get("folderIdV2")
    if folder_id is not None:
        item["folder_ref"] = refs.upsert(
            credential=credential,
            object_type="marketing-email-folder",
            provider_object_id=folder_id,
            display_name="HubSpot marketing email folder",
        )
    content = raw.get("content")
    template_path = content.get("templatePath") if isinstance(content, dict) else None
    if template_path:
        item["template_ref"] = refs.upsert(
            credential=credential,
            object_type="email-template",
            provider_object_id=template_path,
            display_name=str(template_path),
        )
    subscription_details = raw.get("subscriptionDetails")
    if (
        isinstance(subscription_details, dict)
        and subscription_details.get("subscriptionId") is not None
    ):
        item["subscription_type_ref"] = refs.upsert(
            credential=credential,
            object_type="subscription-type",
            provider_object_id=subscription_details["subscriptionId"],
            display_name=str(
                subscription_details.get("subscriptionName") or "HubSpot subscription type"
            ),
        )
    stats = _safe_metric_map(raw.get("stats"))
    if stats:
        item["statistics"] = stats
    return item


def _email_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    raw_results = body.get("results") if isinstance(body, dict) else None
    if isinstance(raw_results, list):
        results = [_email_item(request, raw=raw) for raw in raw_results]
    else:
        results = [_email_item(request, raw=body)]
    extra: dict[str, Any] = {}
    if isinstance(body, dict) and isinstance(body.get("total"), int):
        extra["total"] = body["total"]
    return _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body=body,
        headers=headers,
        results=results,
        extra=extra,
    )


def _resolved_email(request: ActionConnectorRequest) -> Any:
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "email_ref"),
        expected_object_type="marketing-email",
    )


def _resolved_transactional_email(request: ActionConnectorRequest) -> Any:
    email = _resolved_email(request)
    metadata = dict(email.metadata_json or {})
    if metadata.get("is_transactional") is not True:
        raise ValidationError(
            "HubSpot transactional send requires a provider-verified transactional email ref"
        )
    return email


def _transactional_custom_properties(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("custom_properties")
    if not isinstance(raw, dict) or len(raw) > 100:
        raise ValidationError("HubSpot custom_properties must be an object with at most 100 values")
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise ValidationError(
                "HubSpot custom property names must be non-empty strings up to 200 characters"
            )
        if value is None or not isinstance(value, (str, int, float, bool)):
            raise ValidationError("HubSpot custom property values must be JSON scalars")
        normalized[key.strip()] = value
    return normalized


def _safe_transactional_text(value: Any, *, redact_values: tuple[str, ...]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    redacted = redact_secret_values(value.strip(), redact_values)
    return redact_secret_text(str(redacted))[:2000]


def _transactional_email_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    contact: Any,
    contact_email: str,
    email: Any,
) -> ActionConnectorResult:
    raw = body if isinstance(body, dict) else {}
    provider_state = str(raw.get("status") or "UNKNOWN").strip().upper()
    state = {
        "CANCELED": "canceled",
        "COMPLETE": "complete",
        "PENDING": "pending",
        "PROCESSING": "processing",
    }.get(provider_state, "unknown")
    refs, credential = _ref_context(request)
    send_id = str(request.input_json["send_id"])
    local_digest = hashlib.sha256(send_id.encode("utf-8")).hexdigest()[:24]
    message_ref = f"hubspot-transactional-message:{local_digest}"
    status_id = raw.get("statusId")
    if status_id is not None and str(status_id).strip():
        message_ref = refs.upsert(
            credential=credential,
            object_type="transactional-email-send",
            provider_object_id=status_id,
            display_name="HubSpot transactional email send",
            metadata_json={"provider_status": provider_state},
        )
    event_ref = None
    raw_event = raw.get("eventId")
    event_id = None
    event_created = None
    if isinstance(raw_event, dict):
        candidate_event_id = raw_event.get("id")
        if candidate_event_id is not None and str(candidate_event_id).strip():
            event_id = str(candidate_event_id).strip()
            event_created = raw_event.get("created")
    if event_id is not None:
        event_ref = refs.upsert(
            credential=credential,
            object_type="transactional-email-event",
            provider_object_id=event_id,
            display_name="HubSpot transactional email event",
            metadata_json=({"created": event_created} if event_created is not None else None),
        )
    dynamic_redactions = tuple(
        value
        for value in (
            contact_email,
            str(contact.provider_object_id),
            str(email.provider_object_id),
            str(status_id or ""),
            str(event_id or ""),
        )
        if value
    )
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": state,
        "provider_status": provider_state,
        "message_ref": message_ref,
        "contact_ref": contact.safe_ref,
        "email_ref": email.safe_ref,
        "communication_target_ref": request.input_json.get("communication_target_ref"),
        "profile_ref": request.input_json.get("profile_ref"),
        "marketing_contact_state": request.input_json["marketing_contact_state"],
        "transactional_use_confirmed": True,
        "consent_or_relationship_confirmed": True,
        "contact_properties_updated": False,
        "marketing_contact_state_changed": False,
        "response_complete": (
            provider_state != "UNKNOWN" and status_id is not None and event_id is not None
        ),
    }
    if event_ref is not None:
        output["event_ref"] = event_ref
    for provider_key, output_key in (
        ("requestedAt", "requested_at"),
        ("startedAt", "started_at"),
        ("completedAt", "completed_at"),
    ):
        if raw.get(provider_key) is not None:
            output[output_key] = raw[provider_key]
    send_result = _safe_transactional_text(
        raw.get("sendResult"),
        redact_values=dynamic_redactions,
    )
    if send_result is not None:
        output["send_result"] = send_result
    provider_message = _safe_transactional_text(
        raw.get("message"),
        redact_values=dynamic_redactions,
    )
    if provider_message is not None:
        output["provider_message"] = provider_message
    request_id = _request_id(headers, raw)
    if request_id is not None:
        output["request_id"] = request_id

    if request.session is not None:
        ResourceRepository(request.session).upsert_record(
            project_id=request.project_id,
            plugin_slug="communications",
            resource_key="communication-message",
            external_id=message_ref,
            title="HubSpot transactional email",
            data_json={
                "provider_key": "hubspot",
                "direction": "outbound",
                "surface_ref": contact.safe_ref,
                "message_ref": message_ref,
                "contact_ref": contact.safe_ref,
                "email_ref": email.safe_ref,
                "communication_target_ref": request.input_json.get("communication_target_ref"),
                "profile_ref": request.input_json.get("profile_ref"),
                "content_type": "provider-template",
                "template_property_keys": sorted(
                    str(key) for key in request.input_json["custom_properties"]
                ),
                "transport_status": state,
                "attention_status": (
                    "sent"
                    if state == "complete"
                    else ("rejected" if state == "canceled" else "pending")
                ),
                "marketing_contact_state": request.input_json["marketing_contact_state"],
                "marketing_contact_state_changed": False,
                "legal_basis": request.input_json["legal_basis"],
                "legal_basis_explanation": request.input_json["legal_basis_explanation"],
                "source_agent_request_id": request.input_json.get("source_agent_request_id"),
                "action_ref": request.action_ref,
            },
            provenance_json={"source": "hubspot-action"},
        )

    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-transactional-send-v1",
        "provider_state": provider_state,
        "provider_response_incomplete": not output["response_complete"],
    }
    if request_id is not None:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _subscription_type_results(
    request: ActionConnectorRequest,
    *,
    body: Any,
) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValidationError("HubSpot subscription definition response missing results")
    refs, credential = _ref_context(request)
    normalized: list[dict[str, Any]] = []
    for raw in body["results"]:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        brand = _brand_ref(request, provider_id=raw.get("businessUnitId"))
        subscription_ref = refs.upsert(
            credential=credential,
            object_type="subscription-type",
            provider_object_id=raw["id"],
            display_name=str(raw.get("name") or "HubSpot subscription type"),
            metadata_json={
                "brand_ref": brand,
                "communication_method": raw.get("communicationMethod"),
                "is_active": bool(raw.get("isActive")),
            },
        )
        item: dict[str, Any] = {
            "subscription_type_ref": subscription_ref,
            "name": raw.get("name"),
            "description": raw.get("description"),
            "purpose": raw.get("purpose"),
            "communication_method": raw.get("communicationMethod"),
            "is_active": bool(raw.get("isActive")),
            "is_default": bool(raw.get("isDefault")),
            "is_internal": bool(raw.get("isInternal")),
            "created_at": raw.get("createdAt"),
            "updated_at": raw.get("updatedAt"),
        }
        if brand is not None:
            item["brand_ref"] = brand
        normalized.append(item)
    return normalized


async def _resolved_contact_email(
    request: ActionConnectorRequest,
    *,
    headers: Mapping[str, str],
) -> tuple[Any, str]:
    refs, credential = _ref_context(request)
    contact = refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "contact_ref"),
        expected_object_type="contact",
    )
    _status, body, _response_headers = await send_json(
        method="GET",
        url=_object_api_url("contacts", f"/{q(contact.provider_object_id)}"),
        headers=headers,
        params={"properties": "email", "archived": "false"},
        redact_values=(str(contact.provider_object_id),),
    )
    properties = body.get("properties") if isinstance(body, dict) else None
    email = properties.get("email") if isinstance(properties, dict) else None
    if not isinstance(email, str) or "@" not in email or len(email) > 320:
        raise ValidationError("HubSpot contact has no usable primary email")
    return contact, email.strip().lower()


def _preference_results(
    request: ActionConnectorRequest,
    *,
    body: Any,
    contact: Any,
    expected_email: str,
    expected_subscription: Any | None = None,
) -> list[dict[str, Any]]:
    raw_results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(raw_results, list):
        raw_results = [body] if isinstance(body, dict) and body.get("subscriptionId") else []
    if not raw_results:
        raise ValidationError("HubSpot preference response missing results")
    refs, credential = _ref_context(request)
    normalized: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict) or raw.get("subscriptionId") is None:
            continue
        subscriber = raw.get("subscriberIdString")
        if isinstance(subscriber, str) and subscriber.strip().lower() != expected_email:
            raise ValidationError("HubSpot preference response returned another subscriber")
        subscription_id = str(raw["subscriptionId"])
        if (
            expected_subscription is not None
            and subscription_id != expected_subscription.provider_object_id
        ):
            raise ValidationError("HubSpot preference response returned another subscription")
        brand = _brand_ref(request, provider_id=raw.get("businessUnitId"))
        subscription_ref = refs.upsert(
            credential=credential,
            object_type="subscription-type",
            provider_object_id=subscription_id,
            display_name="HubSpot subscription type",
            metadata_json={"brand_ref": brand},
        )
        item: dict[str, Any] = {
            "contact_ref": contact.safe_ref,
            "subscription_type_ref": subscription_ref,
            "channel": "EMAIL",
            "status": raw.get("status") or raw.get("statusState"),
            "source": raw.get("source"),
            "legal_basis": raw.get("legalBasis"),
            "legal_basis_explanation": raw.get("legalBasisExplanation"),
            "status_reason": raw.get("setStatusSuccessReason"),
            "timestamp": raw.get("timestamp"),
        }
        if brand is not None:
            item["brand_ref"] = brand
        normalized.append(item)
    if not normalized:
        raise ValidationError("HubSpot preference response contained no usable results")
    return normalized


def _preference_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    contact: Any,
    expected_email: str,
    expected_subscription: Any | None = None,
) -> ActionConnectorResult:
    result_value = _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body=body,
        headers=headers,
        results=_preference_results(
            request,
            body=body,
            contact=contact,
            expected_email=expected_email,
            expected_subscription=expected_subscription,
        ),
        extra={"contact_ref": contact.safe_ref, "channel": "EMAIL"},
    )
    result_value.metadata_json = {
        **(result_value.metadata_json or {}),
        "sensitive_data": True,
        "consent_audit": True,
    }
    return result_value


def _event_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    rendered = value.strip()
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _event_timestamp_issue(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    required: bool = False,
) -> None:
    value = payload.get(key)
    if value is None and not required:
        return
    if _event_timestamp(value) is None:
        issues.append(
            ActionValidationIssue(
                path=f"$.{key}",
                message=f"{key} must be an ISO 8601 timestamp with a timezone",
                code="format",
            )
        )


def _event_property_value_issue(
    *,
    value: Any,
    path: str,
    issues: list[ActionValidationIssue],
    strings_only: bool,
) -> None:
    if strings_only:
        valid = isinstance(value, str) and len(value) <= 1024
    else:
        valid = isinstance(value, (str, int, float, bool)) and not (
            isinstance(value, str) and len(value) > 1024
        )
        if isinstance(value, float) and not math.isfinite(value):
            valid = False
    if not valid:
        issues.append(
            ActionValidationIssue(
                path=path,
                message=(
                    "event property values must be strings up to 1024 characters"
                    if strings_only
                    else "event property values must be finite JSON scalars"
                ),
                code="type_error",
            )
        )


def _marketing_event_write_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "externalAccountId": payload["external_account_key"],
        "externalEventId": payload["external_event_key"],
        "eventName": payload["name"],
        "eventOrganizer": payload["organizer"],
    }
    for input_key, provider_key in (
        ("event_type", "eventType"),
        ("description", "eventDescription"),
        ("event_url", "eventUrl"),
        ("start_at", "startDateTime"),
        ("end_at", "endDateTime"),
        ("event_cancelled", "eventCancelled"),
        ("event_completed", "eventCompleted"),
    ):
        if payload.get(input_key) is not None:
            body[provider_key] = payload[input_key]
    custom_properties = payload.get("custom_properties")
    if isinstance(custom_properties, dict):
        body["customProperties"] = [
            {"name": str(name), "value": value} for name, value in sorted(custom_properties.items())
        ]
    return {"inputs": [body]}


def _marketing_event_item(
    request: ActionConnectorRequest,
    *,
    raw: Any,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("HubSpot marketing event response item must be an object")
    provider_id = raw.get("objectId") or raw.get("id")
    if provider_id is None or not str(provider_id).strip():
        raise ValidationError("HubSpot marketing event response missing object id")
    refs, credential = _ref_context(request)
    name = str(raw.get("eventName") or "HubSpot marketing event")
    event_ref = refs.upsert(
        credential=credential,
        object_type="marketing-event",
        provider_object_id=provider_id,
        display_name=name,
        metadata_json={
            "external_event_key": raw.get("externalEventId"),
            "provider_status": raw.get("eventStatusV2") or raw.get("eventStatus"),
        },
    )
    item: dict[str, Any] = {
        "event_ref": event_ref,
        "name": name,
        "organizer": raw.get("eventOrganizer"),
        "event_type": raw.get("eventType"),
        "status": raw.get("eventStatusV2") or raw.get("eventStatus"),
        "start_at": raw.get("startDateTime"),
        "end_at": raw.get("endDateTime"),
        "event_cancelled": bool(raw.get("eventCancelled")),
        "event_completed": bool(raw.get("eventCompleted")),
        "description": raw.get("eventDescription"),
        "created_at": raw.get("createdAt"),
        "updated_at": raw.get("updatedAt"),
    }
    safe_url = _safe_page_url(raw.get("eventUrl"))
    if safe_url is not None:
        item["event_url"] = safe_url
    app_info = raw.get("appInfo")
    if isinstance(app_info, dict) and app_info.get("id") is not None:
        item["app_ref"] = refs.upsert(
            credential=credential,
            object_type="hubspot-app",
            provider_object_id=app_info["id"],
            display_name=str(app_info.get("name") or "HubSpot app"),
        )
    custom_property_names = sorted(
        {
            str(raw_property["name"])
            for raw_property in raw.get("customProperties") or []
            if isinstance(raw_property, dict)
            and raw_property.get("name") is not None
            and str(raw_property["name"]).strip()
        }
    )
    if custom_property_names:
        item["custom_property_names"] = custom_property_names
    return item


def _marketing_event_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValidationError("HubSpot marketing event response missing results")
    results = [
        _marketing_event_item(request, raw=raw) for raw in body["results"] if isinstance(raw, dict)
    ]
    if request.operation == "marketing.events.list":
        return _safe_metadata_result(
            operation=request.operation,
            status_code=status_code,
            body=body,
            headers=headers,
            results=results,
        )

    dynamic_redactions = tuple(
        str(request.input_json.get(key) or "")
        for key in ("external_account_key", "external_event_key")
        if request.input_json.get(key)
    )
    failures: list[dict[str, Any]] = []
    for raw_error in body.get("errors") or []:
        if not isinstance(raw_error, dict):
            continue
        nested_errors = raw_error.get("errors")
        error_codes = [
            str(nested.get("code"))
            for nested in nested_errors or []
            if isinstance(nested, dict) and nested.get("code") is not None
        ]
        failure: dict[str, Any] = {
            "category": raw_error.get("category"),
            "message": _safe_transactional_text(
                raw_error.get("message") or "Marketing event row failed",
                redact_values=dynamic_redactions,
            ),
        }
        if error_codes:
            failure["codes"] = error_codes
        failures.append(failure)
    provider_status = str(body.get("status") or "COMPLETE").upper()
    if failures and results:
        state = "partial"
    elif failures:
        state = "failed"
    else:
        state = {
            "CANCELED": "canceled",
            "PENDING": "pending",
            "PROCESSING": "processing",
        }.get(provider_status, "success")
    request_id = _request_id(headers, body)
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": state,
        "provider_status": provider_status,
        "success_count": len(results),
        "failure_count": len(failures),
        "results": results,
        "failures": failures,
        "started_at": body.get("startedAt"),
        "completed_at": body.get("completedAt"),
        "response_complete": provider_status in {"COMPLETE", "CANCELED"},
    }
    if request_id is not None:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-provider-refs-v1",
        "partial_failure": bool(failures),
        "provider_response_incomplete": not output["response_complete"],
    }
    if request_id is not None:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _behavioral_target_object_type(raw: Mapping[str, Any]) -> str:
    candidates = (
        raw.get("primaryObject"),
        raw.get("objectTypeId"),
        raw.get("primaryObjectId"),
    )
    for value in candidates:
        normalized = str(value or "").strip().lower().replace("_", "-")
        if normalized in {"contact", "contacts", "0-1"}:
            return "contact"
        if normalized in {"company", "companies", "0-2"}:
            return "company"
    return "unknown"


def _behavioral_definition_item(
    request: ActionConnectorRequest,
    *,
    raw: Any,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("HubSpot event definition response item must be an object")
    fully_qualified_name = str(raw.get("fullyQualifiedName") or "").strip()
    if not _BEHAVIORAL_EVENT_NAME_RE.fullmatch(fully_qualified_name):
        raise ValidationError("HubSpot event definition missing a valid fully qualified name")
    refs, credential = _ref_context(request)
    raw_labels = raw.get("labels")
    labels: dict[str, Any] = dict(raw_labels) if isinstance(raw_labels, dict) else {}
    label = str(labels.get("singular") or raw.get("name") or "HubSpot behavioral event")
    target_object_type = _behavioral_target_object_type(raw)
    definition_ref = refs.upsert(
        credential=credential,
        object_type="behavioral-event-definition",
        provider_object_id=fully_qualified_name,
        display_name=label,
        metadata_json={
            "target_object_type": target_object_type,
            "archived": bool(raw.get("archived")),
        },
    )
    item: dict[str, Any] = {
        "definition_ref": definition_ref,
        "label": label,
        "plural_label": labels.get("plural"),
        "description": raw.get("description"),
        "target_object_type": target_object_type,
        "archived": bool(raw.get("archived")),
        "created_at": raw.get("createdAt"),
        "updated_at": raw.get("updatedAt"),
    }
    properties: list[dict[str, Any]] = []
    for raw_property in raw.get("properties") or []:
        if not isinstance(raw_property, dict):
            continue
        property_name = str(raw_property.get("name") or "").strip()
        if not _EVENT_PROPERTY_NAME_RE.fullmatch(property_name):
            continue
        property_ref = refs.upsert(
            credential=credential,
            object_type="behavioral-event-property",
            provider_object_id=json.dumps(
                [fully_qualified_name, property_name],
                separators=(",", ":"),
            ),
            display_name=str(raw_property.get("label") or property_name),
            metadata_json={"definition_ref": definition_ref},
        )
        properties.append(
            {
                "property_ref": property_ref,
                "label": raw_property.get("label") or property_name,
                "description": raw_property.get("description"),
                "type": raw_property.get("type"),
                "field_type": raw_property.get("fieldType"),
            }
        )
    if properties:
        item["properties"] = properties
    return item


def _behavioral_definition_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
) -> ActionConnectorResult:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValidationError("HubSpot event definition response missing results")
    extra: dict[str, Any] = {}
    if isinstance(body.get("total"), int):
        extra["total"] = body["total"]
    return _safe_metadata_result(
        operation=request.operation,
        status_code=status_code,
        body=body,
        headers=headers,
        results=[
            _behavioral_definition_item(request, raw=raw)
            for raw in body["results"]
            if isinstance(raw, dict)
        ],
        extra=extra,
    )


def _resolved_behavioral_definition(request: ActionConnectorRequest) -> Any:
    refs, credential = _ref_context(request)
    definition = refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "definition_ref"),
        expected_object_type="behavioral-event-definition",
    )
    if (definition.metadata_json or {}).get("target_object_type") != "contact":
        raise ValidationError(
            "HubSpot behavioral event send requires a provider-verified contact definition"
        )
    if not _BEHAVIORAL_EVENT_NAME_RE.fullmatch(definition.provider_object_id):
        raise ValidationError("HubSpot behavioral event definition ref is invalid")
    return definition


def _resolved_behavioral_contact(request: ActionConnectorRequest) -> Any:
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "contact_ref"),
        expected_object_type="contact",
    )


def _behavioral_event_properties(
    request: ActionConnectorRequest,
    *,
    definition: Any,
) -> dict[str, Any]:
    raw_values = request.input_json.get("property_values")
    if not isinstance(raw_values, list) or len(raw_values) > 50:
        raise ValidationError("HubSpot behavioral event property_values must be a bounded list")
    refs, credential = _ref_context(request)
    normalized: dict[str, Any] = {}
    for raw_value in raw_values:
        if not isinstance(raw_value, dict):
            raise ValidationError("HubSpot behavioral event property values must be objects")
        resolved = refs.resolve(
            credential=credential,
            safe_ref=_required_safe_ref(raw_value, "property_ref"),
            expected_object_type="behavioral-event-property",
        )
        try:
            identity = json.loads(resolved.provider_object_id)
        except json.JSONDecodeError as exc:
            raise ValidationError("HubSpot behavioral event property ref is invalid") from exc
        if (
            not isinstance(identity, list)
            or len(identity) != 2
            or identity[0] != definition.provider_object_id
            or not isinstance(identity[1], str)
            or not _EVENT_PROPERTY_NAME_RE.fullmatch(identity[1])
            or (resolved.metadata_json or {}).get("definition_ref") != definition.safe_ref
        ):
            raise ValidationError(
                "HubSpot behavioral event property ref does not belong to the definition"
            )
        property_name = identity[1]
        if property_name in normalized:
            raise ValidationError("HubSpot behavioral event properties must be unique")
        value = raw_value.get("value")
        if value is None or not isinstance(value, (str, int, float, bool)):
            raise ValidationError("HubSpot behavioral event property value is not a JSON scalar")
        if isinstance(value, str) and len(value) > 1024:
            raise ValidationError("HubSpot behavioral event property value is too long")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError("HubSpot behavioral event property value must be finite")
        normalized[property_name] = value
    return normalized


def _behavioral_occurrence_uuid(
    *,
    definition: Any,
    contact: Any,
    occurrence_key: str,
) -> str:
    stable_name = "\x00".join(
        (
            contact.provider_account_id,
            definition.provider_object_id,
            occurrence_key,
        )
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable_name))


def _behavioral_event_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    definition: Any,
    contact: Any,
    provider_uuid: str,
) -> ActionConnectorResult:
    refs, credential = _ref_context(request)
    occurrence_ref = refs.upsert(
        credential=credential,
        object_type="behavioral-event-occurrence",
        provider_object_id=provider_uuid,
        display_name="HubSpot behavioral event occurrence",
        metadata_json={
            "definition_ref": definition.safe_ref,
            "contact_ref": contact.safe_ref,
            "occurred_at": request.input_json["occurred_at"],
        },
    )
    request_id = _request_id(headers, body)
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": "accepted",
        "occurrence_ref": occurrence_ref,
        "definition_ref": definition.safe_ref,
        "contact_ref": contact.safe_ref,
        "occurred_at": request.input_json["occurred_at"],
        "tracking_authority_confirmed": True,
        "deduplicated_by": "provider_uuid",
    }
    if request_id is not None:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-event-occurrence-v1",
        "event_audit": True,
        "provider_deduplication": True,
    }
    if request_id is not None:
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _bulk_export_job(request: ActionConnectorRequest) -> Any:
    refs, credential = _ref_context(request)
    return refs.resolve(
        credential=credential,
        safe_ref=_required_safe_ref(request.input_json, "job_ref"),
        expected_object_type="bulk-export-job",
    )


def _bulk_export_job_metadata(job: Any) -> dict[str, Any]:
    metadata = dict(job.metadata_json or {})
    object_type = metadata.get("object_type")
    property_refs = metadata.get("property_refs")
    provider_property_names = metadata.get("provider_property_names")
    associated_object_types = metadata.get("associated_object_types")
    export_format = metadata.get("format")
    export_name = metadata.get("export_name")
    if object_type not in _BULK_EXPORT_OBJECT_NAMES:
        raise ValidationError("HubSpot export job ref has invalid object metadata")
    if (
        not isinstance(property_refs, list)
        or not all(isinstance(item, str) for item in property_refs)
        or not isinstance(provider_property_names, list)
        or not all(isinstance(item, str) for item in provider_property_names)
        or len(property_refs) != len(provider_property_names)
    ):
        raise ValidationError("HubSpot export job ref has invalid property metadata")
    if not isinstance(associated_object_types, list) or not all(
        item in _BULK_EXPORT_ASSOCIATION_OBJECTS for item in associated_object_types
    ):
        raise ValidationError("HubSpot export job ref has invalid association metadata")
    if export_format not in _BULK_EXPORT_FORMATS:
        raise ValidationError("HubSpot export job ref has invalid format metadata")
    if not isinstance(export_name, str) or not export_name.strip():
        raise ValidationError("HubSpot export job ref has invalid name metadata")
    return metadata


def _bulk_export_create_body(
    request: ActionConnectorRequest,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = request.input_json
    if payload.get("export_authorized") is not True:
        raise ValidationError("HubSpot export requires explicit export authority")
    object_type = payload.get("object_type")
    if object_type not in _BULK_EXPORT_OBJECT_NAMES:
        raise ValidationError("HubSpot export object_type is not supported")
    raw_property_refs = payload.get("property_refs")
    if (
        not isinstance(raw_property_refs, list)
        or not raw_property_refs
        or len(raw_property_refs) > 100
        or not all(isinstance(item, str) for item in raw_property_refs)
    ):
        raise ValidationError("HubSpot export property_refs must contain 1 to 100 safe refs")
    property_refs = list(raw_property_refs)
    if len(set(property_refs)) != len(property_refs):
        raise ValidationError("HubSpot export property_refs must be unique")
    provider_property_names = [
        _resolved_property_id(
            request,
            object_type=str(object_type),
            property_ref=property_ref,
        )
        for property_ref in property_refs
    ]
    if len(set(provider_property_names)) != len(provider_property_names):
        raise ValidationError("HubSpot export properties resolve to duplicate provider fields")
    raw_associations = payload.get("associated_object_types") or []
    if (
        not isinstance(raw_associations, list)
        or len(raw_associations) > 4
        or not all(item in _BULK_EXPORT_ASSOCIATION_OBJECTS for item in raw_associations)
    ):
        raise ValidationError(
            "HubSpot export associated_object_types must contain at most four supported types"
        )
    associated_object_types = list(raw_associations)
    if len(set(associated_object_types)) != len(associated_object_types):
        raise ValidationError("HubSpot export associated_object_types must be unique")
    if object_type in associated_object_types:
        raise ValidationError("HubSpot export cannot associate its primary object type to itself")
    export_format = payload.get("format")
    if export_format not in _BULK_EXPORT_FORMATS:
        raise ValidationError("HubSpot export format is not supported")
    language = payload.get("language") or "EN"
    if language not in _BULK_EXPORT_LANGUAGES:
        raise ValidationError("HubSpot export language is not supported")
    export_name = str(payload.get("export_name") or "").strip()
    if not export_name or len(export_name) > 255:
        raise ValidationError("HubSpot export_name must contain 1 to 255 characters")
    internal_options: list[str] = []
    if payload.get("include_internal_property_names") is True:
        internal_options.append("NAMES")
    if payload.get("include_internal_property_values") is True:
        internal_options.append("VALUES")
    body: dict[str, Any] = {
        "exportType": "VIEW",
        "exportName": export_name,
        "format": export_format,
        "language": language,
        "objectType": _BULK_EXPORT_OBJECT_NAMES[str(object_type)],
        "objectProperties": provider_property_names,
        "associatedObjectType": [
            _BULK_EXPORT_OBJECT_NAMES[item] for item in associated_object_types
        ],
        "includePrimaryDisplayPropertyForAssociatedObjects": (
            payload.get("include_primary_display_properties") is True
        ),
        "includeLabeledAssociations": payload.get("include_labeled_associations") is True,
        "exportInternalValuesOptions": internal_options,
        "overrideAssociatedObjectsPerDefinitionPerRowLimit": (
            payload.get("override_association_limit") is True
        ),
    }
    metadata = {
        "object_type": object_type,
        "property_refs": property_refs,
        "provider_property_names": provider_property_names,
        "associated_object_types": associated_object_types,
        "format": export_format,
        "export_name": export_name,
    }
    return body, metadata


def _bulk_export_create_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    job_metadata: dict[str, Any],
) -> ActionConnectorResult:
    if not isinstance(body, dict):
        raise ValidationError("HubSpot export create response must be an object")
    provider_job_id = str(body.get("id") or "").strip()
    if not provider_job_id.isdigit():
        raise ValidationError("HubSpot export create response missing a valid job id")
    refs, credential = _ref_context(request)
    job_ref = refs.upsert(
        credential=credential,
        object_type="bulk-export-job",
        provider_object_id=provider_job_id,
        display_name=str(job_metadata["export_name"]),
        metadata_json=job_metadata,
    )
    request_id = _request_id(headers, body)
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": "accepted",
        "job_ref": job_ref,
        "object_type": job_metadata["object_type"],
        "property_refs": job_metadata["property_refs"],
        "associated_object_types": job_metadata["associated_object_types"],
        "format": job_metadata["format"],
        "export_name": job_metadata["export_name"],
    }
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-async-export-job-v1",
        "async_provider_job": True,
        "provider_called": True,
    }
    if request_id is not None:
        output["request_id"] = request_id
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _normalized_bulk_export_state(value: Any) -> tuple[str, str]:
    provider_status = str(value or "").strip().upper()
    if (
        not provider_status
        or len(provider_status) > 50
        or not re.fullmatch(r"[A-Z][A-Z0-9_]*", provider_status)
    ):
        raise ValidationError("HubSpot export response missing a valid state")
    return _BULK_EXPORT_STATES.get(provider_status, "unknown"), provider_status


def _safe_provider_timestamp(body: Mapping[str, Any], key: str) -> str | None:
    value = body.get(key)
    if isinstance(value, str) and value.strip() and len(value) <= 100:
        return value.strip()
    return None


def _bulk_export_status_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    job: Any,
    job_metadata: dict[str, Any],
) -> ActionConnectorResult:
    if not isinstance(body, dict):
        raise ValidationError("HubSpot export status response must be an object")
    response_id = body.get("id")
    if response_id is not None and str(response_id) != job.provider_object_id:
        raise ValidationError("HubSpot export status returned a mismatched job")
    state, provider_status = _normalized_bulk_export_state(body.get("exportState"))
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": state,
        "provider_status": provider_status,
        "job_ref": job.safe_ref,
        "object_type": job_metadata["object_type"],
        "property_refs": job_metadata["property_refs"],
        "associated_object_types": job_metadata["associated_object_types"],
        "format": job_metadata["format"],
        "export_name": job_metadata["export_name"],
    }
    record_count = body.get("recordCount")
    if isinstance(record_count, int) and not isinstance(record_count, bool) and record_count >= 0:
        output["record_count"] = record_count
    for provider_key, output_key in (
        ("createdAt", "created_at"),
        ("updatedAt", "updated_at"),
    ):
        timestamp = _safe_provider_timestamp(body, provider_key)
        if timestamp is not None:
            output[output_key] = timestamp
    request_id = _request_id(headers, body)
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-async-export-status-v1",
        "provider_status": provider_status,
    }
    if request_id is not None:
        output["request_id"] = request_id
        metadata["request_id"] = request_id
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _safe_bulk_export_errors(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_errors = body.get("errors")
    if not isinstance(raw_errors, list):
        return []
    summaries: list[dict[str, Any]] = []
    for raw_error in raw_errors[:20]:
        if not isinstance(raw_error, dict):
            continue
        summary: dict[str, Any] = {}
        category = raw_error.get("category")
        if isinstance(category, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", category):
            summary["category"] = category
        codes: list[str] = []
        nested_errors = raw_error.get("errors")
        if isinstance(nested_errors, list):
            for nested in nested_errors[:20]:
                if not isinstance(nested, dict):
                    continue
                code = nested.get("code")
                if (
                    isinstance(code, str)
                    and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", code)
                    and code not in codes
                ):
                    codes.append(code)
        if codes:
            summary["codes"] = codes
        if summary:
            summaries.append(summary)
    return summaries


def _safe_export_download_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("HubSpot completed export did not return a download URL")
    url = value.strip()
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValidationError("HubSpot export download URL failed safety validation")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValidationError("HubSpot export download URL cannot target a local host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValidationError("HubSpot export download URL cannot target a private network")
    return url


def _bulk_export_filename(
    *,
    content_disposition: str | None,
    export_name: str,
    export_format: str,
    mime_type: str,
    digest: str,
) -> str:
    candidate = ""
    if content_disposition:
        encoded = re.search(
            r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)",
            content_disposition,
            flags=re.IGNORECASE,
        )
        plain = re.search(
            r'filename\s*=\s*"?([^";]+)',
            content_disposition,
            flags=re.IGNORECASE,
        )
        match = encoded or plain
        if match is not None:
            candidate = unquote(match.group(1).strip().strip('"'))
    candidate = Path(candidate).name
    candidate = _SAFE_EXPORT_FILENAME_RE.sub("-", candidate).strip(".-")[:180]
    if not candidate:
        candidate = _SAFE_EXPORT_FILENAME_RE.sub("-", export_name).strip(".-")[:120]
    if not candidate:
        candidate = "hubspot-export"
    if "." not in candidate:
        extension = mimetypes.guess_extension(mime_type) or f".{export_format.lower()}"
        candidate = f"{candidate}-{digest[:8]}{extension}"
    return candidate


def _bulk_download_error(
    *,
    job_ref: str,
    detail: str,
    status_code: int | None = None,
    retryable: bool,
) -> ActionConnectorError:
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": "bulk.exports.result",
        "status": "download_failed",
        "job_ref": job_ref,
        "retryable": retryable,
    }
    if status_code is not None:
        output["download_status_code"] = status_code
    if retryable:
        output["next_action"] = (
            "Run hubspot.bulk.exports.result again to obtain a fresh HubSpot download URL."
        )
    else:
        output["next_action"] = (
            "Increase max_bytes deliberately or create a narrower HubSpot export."
        )
    return ActionConnectorError(
        detail,
        provider_status_code=(
            status_code if status_code is not None and status_code >= 400 else None
        ),
        output_json=output,
        metadata_json={
            "vendor": "hubspot",
            "operation": "bulk.exports.result",
            "download_attempted": True,
            "signed_url_stored": False,
        },
    )


async def _download_bulk_export_artifact(
    request: ActionConnectorRequest,
    *,
    job: Any,
    job_metadata: dict[str, Any],
    result_url: Any,
) -> dict[str, Any]:
    current_url = _safe_export_download_url(result_url)
    max_bytes = int(request.input_json.get("max_bytes") or _DEFAULT_BULK_EXPORT_MAX_BYTES)
    root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    partial_dir = (root / "hubspot-exports" / ".partial").resolve()
    if root != partial_dir and root not in partial_dir.parents:
        raise ValidationError("HubSpot export artifact path must stay inside generated assets")
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_path = partial_dir / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size_bytes = 0
    download_status_code: int | None = None
    response_headers: Mapping[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as http:
            for redirect_count in range(6):
                try:
                    async with http.stream("GET", current_url, follow_redirects=False) as response:
                        download_status_code = response.status_code
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location or redirect_count == 5:
                                raise _bulk_download_error(
                                    job_ref=job.safe_ref,
                                    detail="HubSpot export download returned an invalid redirect",
                                    status_code=response.status_code,
                                    retryable=True,
                                )
                            current_url = _safe_export_download_url(urljoin(current_url, location))
                            continue
                        if response.status_code >= 400:
                            raise _bulk_download_error(
                                job_ref=job.safe_ref,
                                detail=(
                                    "HubSpot export download failed; refresh the result URL "
                                    "and retry"
                                ),
                                status_code=response.status_code,
                                retryable=response.status_code in {401, 403, 408, 409, 429}
                                or response.status_code >= 500,
                            )
                        content_length = response.headers.get("content-length")
                        if (
                            content_length
                            and content_length.isdigit()
                            and int(content_length) > max_bytes
                        ):
                            raise _bulk_download_error(
                                job_ref=job.safe_ref,
                                detail="HubSpot export exceeds the configured max_bytes limit",
                                status_code=response.status_code,
                                retryable=False,
                            )
                        response_headers = dict(response.headers)
                        with partial_path.open("wb") as file_obj:
                            async for chunk in response.aiter_bytes():
                                size_bytes += len(chunk)
                                if size_bytes > max_bytes:
                                    raise _bulk_download_error(
                                        job_ref=job.safe_ref,
                                        detail=(
                                            "HubSpot export exceeds the configured max_bytes limit"
                                        ),
                                        status_code=response.status_code,
                                        retryable=False,
                                    )
                                digest.update(chunk)
                                file_obj.write(chunk)
                        break
                except httpx.HTTPError:
                    raise _bulk_download_error(
                        job_ref=job.safe_ref,
                        detail="HubSpot export download failed due to a network error",
                        retryable=True,
                    ) from None
            else:
                raise _bulk_download_error(
                    job_ref=job.safe_ref,
                    detail="HubSpot export download exceeded the redirect limit",
                    retryable=True,
                )
        digest_hex = digest.hexdigest()
        mime_type = str(response_headers.get("content-type") or "").split(";", 1)[0].strip()
        if not mime_type:
            mime_type = "application/octet-stream"
        filename = _bulk_export_filename(
            content_disposition=response_headers.get("content-disposition"),
            export_name=str(job_metadata["export_name"]),
            export_format=str(job_metadata["format"]),
            mime_type=mime_type,
            digest=digest_hex,
        )
        relative = Path("hubspot-exports") / digest_hex[:16] / filename
        final_path = (root / relative).resolve()
        if root != final_path and root not in final_path.parents:
            raise ValidationError("HubSpot export artifact path must stay inside generated assets")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            partial_path.unlink(missing_ok=True)
        else:
            partial_path.replace(final_path)
        artifact_ref = f"/generated-assets/{relative.as_posix()}"
        artifact_id: int | None = None
        if request.session is not None:
            artifact = (
                ArtifactRepository(request.session)
                .create(
                    project_id=request.project_id,
                    plugin_slug="gtm",
                    kind="hubspot-export",
                    uri=artifact_ref,
                    status="draft",
                    name=filename,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                    metadata_json={
                        "provider_key": "hubspot",
                        "operation": request.operation,
                        "job_ref": job.safe_ref,
                        "object_type": job_metadata["object_type"],
                        "property_refs": job_metadata["property_refs"],
                        "associated_object_types": job_metadata["associated_object_types"],
                        "sha256": digest_hex,
                    },
                    provenance_json={"source": "hubspot-export-action"},
                )
                .data
            )
            artifact_id = artifact.id
        return {
            "artifact_ref": artifact_ref,
            "artifact_id": artifact_id,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "sha256": digest_hex,
            "download_status_code": download_status_code,
        }
    finally:
        partial_path.unlink(missing_ok=True)


async def _bulk_export_result(
    request: ActionConnectorRequest,
    *,
    status_code: int,
    body: Any,
    headers: Mapping[str, str],
    job: Any,
    job_metadata: dict[str, Any],
) -> ActionConnectorResult:
    if not isinstance(body, dict):
        raise ValidationError("HubSpot export result response must be an object")
    state, provider_status = _normalized_bulk_export_state(body.get("status"))
    output: dict[str, Any] = {
        "provider": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "status": state,
        "provider_status": provider_status,
        "job_ref": job.safe_ref,
        "result_available": False,
    }
    error_count = body.get("numErrors")
    if isinstance(error_count, int) and not isinstance(error_count, bool) and error_count >= 0:
        output["error_count"] = error_count
    error_summary = _safe_bulk_export_errors(body)
    if error_summary:
        output["error_summary"] = error_summary
    for provider_key, output_key in (
        ("requestedAt", "requested_at"),
        ("startedAt", "started_at"),
        ("completedAt", "completed_at"),
    ):
        timestamp = _safe_provider_timestamp(body, provider_key)
        if timestamp is not None:
            output[output_key] = timestamp
    request_id = _request_id(headers, body)
    if request_id is not None:
        output["request_id"] = request_id
    metadata: dict[str, Any] = {
        "vendor": "hubspot",
        "operation": request.operation,
        "status_code": status_code,
        "response_contract": "safe-async-export-result-v1",
        "provider_status": provider_status,
        "signed_url_returned": False,
        "signed_url_stored": False,
    }
    if request_id is not None:
        metadata["request_id"] = request_id
    if provider_status != "COMPLETE":
        return ActionConnectorResult(output_json=output, metadata_json=metadata)
    result_url = body.get("result")
    if not isinstance(result_url, str) or not result_url.strip():
        output["response_complete"] = False
        output["next_action"] = "Run hubspot.bulk.exports.result again to refresh the result."
        metadata["provider_response_incomplete"] = True
        return ActionConnectorResult(output_json=output, metadata_json=metadata)
    artifact = await _download_bulk_export_artifact(
        request,
        job=job,
        job_metadata=job_metadata,
        result_url=result_url,
    )
    output.update(artifact)
    output["result_available"] = True
    metadata.update(
        {
            "artifact_created": artifact["artifact_id"] is not None,
            "download_status_code": artifact["download_status_code"],
            "content_length": artifact["size_bytes"],
        }
    )
    return ActionConnectorResult(output_json=output, metadata_json=metadata)


def _search_body(
    request: ActionConnectorRequest,
    *,
    object_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if payload.get("query") is not None:
        body["query"] = payload["query"]
    filter_groups: list[dict[str, Any]] = []
    filter_count = 0
    for raw_group in payload.get("filter_groups") or []:
        if not isinstance(raw_group, dict):
            raise ValidationError("HubSpot filter groups must be objects")
        filters: list[dict[str, Any]] = []
        for raw_filter in raw_group.get("filters") or []:
            if not isinstance(raw_filter, dict):
                raise ValidationError("HubSpot filters must be objects")
            operator = str(raw_filter.get("operator") or "")
            rendered_filter: dict[str, Any] = {
                "propertyName": _resolved_property_id(
                    request,
                    object_type=object_type,
                    property_ref=raw_filter.get("property_ref"),
                ),
                "operator": operator,
            }
            if operator in {"IN", "NOT_IN"}:
                values = raw_filter.get("values")
                if not isinstance(values, list) or not values:
                    raise ValidationError(f"HubSpot {operator} filters require values")
                rendered_filter["values"] = values
            elif operator == "BETWEEN":
                if raw_filter.get("value") is None or raw_filter.get("high_value") is None:
                    raise ValidationError("HubSpot BETWEEN filters require value and high_value")
                rendered_filter["value"] = raw_filter["value"]
                rendered_filter["highValue"] = raw_filter["high_value"]
            elif operator not in {"HAS_PROPERTY", "NOT_HAS_PROPERTY"}:
                if raw_filter.get("value") is None:
                    raise ValidationError(f"HubSpot {operator} filters require value")
                rendered_filter["value"] = raw_filter["value"]
            filters.append(rendered_filter)
            filter_count += 1
        filter_groups.append({"filters": filters})
    if filter_count > 18:
        raise ValidationError("HubSpot search supports at most 18 filters")
    if filter_groups:
        body["filterGroups"] = filter_groups
    sort = payload.get("sort")
    if isinstance(sort, dict):
        body["sorts"] = [
            {
                "propertyName": _resolved_property_id(
                    request,
                    object_type=object_type,
                    property_ref=sort.get("property_ref"),
                ),
                "direction": sort.get("direction"),
            }
        ]
    property_refs = payload.get("property_refs")
    if isinstance(property_refs, list):
        body["properties"] = [
            _resolved_property_id(request, object_type=object_type, property_ref=item)
            for item in property_refs
        ]
    if payload.get("after") is not None:
        body["after"] = payload["after"]
    limit = payload.get("limit")
    if isinstance(limit, int):
        body["limit"] = limit
    if len(json.dumps(body, separators=(",", ":"))) > 3000:
        raise ValidationError("HubSpot search request exceeds the 3000-character limit")
    return body


class HubSpotActionConnector:
    """Decision-free adapter for HubSpot CRM APIs."""

    key = "hubspot"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case (
                "crm.contacts.properties.list"
                | "crm.companies.properties.list"
                | "crm.deals.properties.list"
                | "crm.leads.properties.list"
                | "crm.deals.pipelines.list"
                | "crm.contact_company.labels.list"
                | "crm.contact_deal.labels.list"
                | "crm.company_deal.labels.list"
                | "sales.products.properties.list"
                | "sales.line_items.properties.list"
                | "sales.quotes.properties.list"
                | "sales.goal_targets.properties.list"
            ):
                pass
            case "crm.owners.list":
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
                archived = payload.get("archived")
                if archived is not None and not isinstance(archived, bool):
                    issues.append(
                        ActionValidationIssue(
                            path="$.archived",
                            message="archived must be a boolean",
                            code="type_error",
                        )
                    )
            case (
                "crm.contact_company.associate"
                | "crm.contact_company.dissociate"
                | "crm.contact_deal.associate"
                | "crm.contact_deal.dissociate"
                | "crm.company_deal.associate"
                | "crm.company_deal.dissociate"
            ):
                required_str(payload, "from_ref", issues)
                required_str(payload, "to_ref", issues)
                required_str(payload, "association_label_ref", issues)
            case "sales.line_items.associate_deal" | "sales.line_items.dissociate_deal":
                required_str(payload, "line_item_ref", issues)
                required_str(payload, "deal_ref", issues)
            case (
                "crm.companies.batch_upsert"
                | "crm.contacts.batch_upsert"
                | "crm.deals.batch_upsert"
                | "crm.leads.batch_upsert"
                | "sales.products.batch_upsert"
                | "sales.line_items.batch_upsert"
            ):
                required_str(payload, "id_property_ref", issues)
                list_field(payload, "inputs", issues, required=True, max_items=100)
            case "crm.notes.create":
                _activity_timestamp_issue(payload, "timestamp", issues)
                required_str(payload, "body", issues)
                optional_str(payload, "owner_ref", issues)
                list_field(payload, "associations", issues, max_items=20)
            case "crm.tasks.create":
                _activity_timestamp_issue(payload, "due_at", issues)
                required_str(payload, "title", issues)
                optional_str(payload, "owner_ref", issues)
                list_field(payload, "associations", issues, max_items=20)
            case "crm.calls.create":
                _activity_timestamp_issue(payload, "timestamp", issues)
                required_str(payload, "direction", issues)
                optional_str(payload, "owner_ref", issues)
                list_field(payload, "associations", issues, max_items=20)
            case "crm.meetings.create":
                _activity_timestamp_issue(payload, "timestamp", issues)
                required_str(payload, "title", issues)
                optional_str(payload, "owner_ref", issues)
                list_field(payload, "associations", issues, max_items=20)
            case (
                "crm.contacts.search"
                | "crm.companies.search"
                | "crm.deals.search"
                | "crm.leads.search"
                | "sales.products.search"
                | "sales.line_items.search"
                | "sales.quotes.search"
            ):
                optional_str(payload, "query", issues)
                list_field(payload, "filter_groups", issues, max_items=5)
                dict_field(payload, "sort", issues)
                list_field(payload, "property_refs", issues, max_items=100)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=200)
            case "crm.deals.list":
                list_field(payload, "property_refs", issues, max_items=100)
                list_field(payload, "property_history_refs", issues, max_items=50)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
                archived = payload.get("archived")
                if archived is not None and not isinstance(archived, bool):
                    issues.append(
                        ActionValidationIssue(
                            path="$.archived",
                            message="archived must be a boolean",
                            code="type_error",
                        )
                    )
            case "sales.goal_targets.list":
                list_field(payload, "property_refs", issues, max_items=100)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
                archived = payload.get("archived")
                if archived is not None and not isinstance(archived, bool):
                    issues.append(
                        ActionValidationIssue(
                            path="$.archived",
                            message="archived must be a boolean",
                            code="type_error",
                        )
                    )
            case "marketing.forms.list":
                list_field(payload, "form_types", issues, max_items=4)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
                archived = payload.get("archived")
                if archived is not None and not isinstance(archived, bool):
                    issues.append(
                        ActionValidationIssue(
                            path="$.archived",
                            message="archived must be a boolean",
                            code="type_error",
                        )
                    )
            case "marketing.forms.submissions.list":
                required_str(payload, "form_ref", issues)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=50)
            case "marketing.segments.list":
                optional_str(payload, "query", issues)
                list_field(payload, "processing_types", issues, max_items=3)
                int_range(payload, "offset", issues, minimum=0, maximum=1_000_000)
                int_range(payload, "count", issues, minimum=1, maximum=500)
            case "marketing.segments.memberships.list":
                required_str(payload, "segment_ref", issues)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
            case "marketing.segments.memberships.add" | "marketing.segments.memberships.remove":
                required_str(payload, "segment_ref", issues)
                list_field(
                    payload,
                    "contact_refs",
                    issues,
                    required=True,
                    max_items=100,
                )
            case "marketing.campaigns.list":
                optional_str(payload, "name", issues)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
            case "marketing.campaigns.get":
                required_str(payload, "campaign_ref", issues)
                optional_str(payload, "start_date", issues)
                optional_str(payload, "end_date", issues)
                if bool(payload.get("start_date")) != bool(payload.get("end_date")):
                    issues.append(
                        ActionValidationIssue(
                            path="$.start_date",
                            message="start_date and end_date must be provided together",
                            code="required_together",
                        )
                    )
            case "marketing.campaigns.create":
                required_str(payload, "name", issues)
                for key in (
                    "start_date",
                    "end_date",
                    "notes",
                    "audience",
                    "currency_code",
                    "status",
                    "utm",
                ):
                    optional_str(payload, key, issues)
            case "marketing.campaigns.update":
                required_str(payload, "campaign_ref", issues)
                for key in _CAMPAIGN_PROPERTIES:
                    optional_str(payload, key, issues)
                if not any(key in payload for key in _CAMPAIGN_PROPERTIES):
                    issues.append(
                        ActionValidationIssue(
                            path="$",
                            message="campaign update requires at least one changed field",
                            code="required",
                        )
                    )
            case "marketing.emails.list":
                optional_str(payload, "after", issues)
                optional_str(payload, "email_type", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
                for key in ("archived", "is_published", "include_stats"):
                    value = payload.get(key)
                    if value is not None and not isinstance(value, bool):
                        issues.append(
                            ActionValidationIssue(
                                path=f"$.{key}",
                                message=f"{key} must be a boolean",
                                code="type_error",
                            )
                        )
            case "marketing.emails.get":
                required_str(payload, "email_ref", issues)
            case "marketing.emails.create":
                required_str(payload, "name", issues)
                required_str(payload, "subject", issues)
                optional_str(payload, "template_ref", issues)
                optional_str(payload, "template_path", issues)
                optional_str(payload, "campaign_ref", issues)
                optional_str(payload, "brand_ref", issues)
                if bool(payload.get("template_ref")) == bool(payload.get("template_path")):
                    issues.append(
                        ActionValidationIssue(
                            path="$.template_ref",
                            message="provide exactly one of template_ref or template_path",
                            code="one_of",
                        )
                    )
            case "marketing.emails.update":
                required_str(payload, "email_ref", issues)
                for key in ("name", "subject", "campaign_ref", "brand_ref"):
                    optional_str(payload, key, issues)
                if not any(
                    key in payload for key in ("name", "subject", "campaign_ref", "brand_ref")
                ):
                    issues.append(
                        ActionValidationIssue(
                            path="$",
                            message="email update requires at least one changed field",
                            code="required",
                        )
                    )
            case "transactional.single_email.send":
                for key in (
                    "contact_ref",
                    "email_ref",
                    "send_id",
                    "legal_basis",
                    "legal_basis_explanation",
                    "marketing_contact_state",
                ):
                    required_str(payload, key, issues)
                dict_field(payload, "custom_properties", issues, required=True)
                for key in (
                    "entitlement_confirmed",
                    "transactional_use_confirmed",
                    "consent_or_relationship_confirmed",
                ):
                    if payload.get(key) is not True:
                        issues.append(
                            ActionValidationIssue(
                                path=f"$.{key}",
                                message=f"{key} must be true",
                                code="confirmation_required",
                            )
                        )
                send_id = payload.get("send_id")
                if isinstance(send_id, str) and (
                    len(send_id) > 100
                    or not all(character.isalnum() or character in "._:-" for character in send_id)
                ):
                    issues.append(
                        ActionValidationIssue(
                            path="$.send_id",
                            message=(
                                "send_id must contain at most 100 letters, digits, '.', '_', "
                                "':' or '-'"
                            ),
                            code="format",
                        )
                    )
                if payload.get("marketing_contact_state") not in {
                    "marketing",
                    "non-marketing",
                }:
                    issues.append(
                        ActionValidationIssue(
                            path="$.marketing_contact_state",
                            message="marketing_contact_state must be marketing or non-marketing",
                            code="enum_mismatch",
                        )
                    )
                custom_properties = payload.get("custom_properties")
                if isinstance(custom_properties, dict):
                    if len(custom_properties) > 100:
                        issues.append(
                            ActionValidationIssue(
                                path="$.custom_properties",
                                message="custom_properties must contain at most 100 values",
                                code="length",
                            )
                        )
                    for key, value in custom_properties.items():
                        if not isinstance(key, str) or not key.strip() or len(key) > 200:
                            issues.append(
                                ActionValidationIssue(
                                    path="$.custom_properties",
                                    message=(
                                        "custom property names must be non-empty strings up to "
                                        "200 characters"
                                    ),
                                    code="format",
                                )
                            )
                            break
                        if value is None or not isinstance(value, (str, int, float, bool)):
                            issues.append(
                                ActionValidationIssue(
                                    path=f"$.custom_properties.{key}",
                                    message="custom property values must be JSON scalars",
                                    code="type_error",
                                )
                            )
                            break
            case "marketing.subscription_types.list":
                optional_str(payload, "brand_ref", issues)
                include_translations = payload.get("include_translations")
                if include_translations is not None and not isinstance(include_translations, bool):
                    issues.append(
                        ActionValidationIssue(
                            path="$.include_translations",
                            message="include_translations must be a boolean",
                            code="type_error",
                        )
                    )
            case "marketing.contact_preferences.get":
                required_str(payload, "contact_ref", issues)
            case "marketing.contact_preferences.update":
                for key in (
                    "contact_ref",
                    "subscription_type_ref",
                    "status",
                    "legal_basis",
                    "legal_basis_explanation",
                ):
                    required_str(payload, key, issues)
                if payload.get("legal_change_confirmed") is not True:
                    issues.append(
                        ActionValidationIssue(
                            path="$.legal_change_confirmed",
                            message="legal_change_confirmed must be true",
                            code="confirmation_required",
                        )
                    )
                if (
                    payload.get("status") == "SUBSCRIBED"
                    and payload.get("consent_obtained") is not True
                ):
                    issues.append(
                        ActionValidationIssue(
                            path="$.consent_obtained",
                            message="subscribing requires consent_obtained=true",
                            code="consent_required",
                        )
                    )
            case "marketing.events.list":
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
            case "marketing.events.upsert":
                for key in (
                    "external_account_key",
                    "external_event_key",
                    "name",
                    "organizer",
                ):
                    required_str(payload, key, issues)
                for key in ("event_type", "description", "event_url"):
                    optional_str(payload, key, issues)
                for key in ("event_cancelled", "event_completed"):
                    value = payload.get(key)
                    if value is not None and not isinstance(value, bool):
                        issues.append(
                            ActionValidationIssue(
                                path=f"$.{key}",
                                message=f"{key} must be a boolean",
                                code="type_error",
                            )
                        )
                _event_timestamp_issue(payload, "start_at", issues)
                _event_timestamp_issue(payload, "end_at", issues)
                start_at = _event_timestamp(payload.get("start_at"))
                end_at = _event_timestamp(payload.get("end_at"))
                if start_at is not None and end_at is not None and end_at < start_at:
                    issues.append(
                        ActionValidationIssue(
                            path="$.end_at",
                            message="end_at must not be before start_at",
                            code="range",
                        )
                    )
                event_url = payload.get("event_url")
                if isinstance(event_url, str):
                    parsed_url = urlsplit(event_url)
                    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                        issues.append(
                            ActionValidationIssue(
                                path="$.event_url",
                                message="event_url must be an absolute HTTP or HTTPS URL",
                                code="format",
                            )
                        )
                dict_field(payload, "custom_properties", issues)
                custom_properties = payload.get("custom_properties")
                if isinstance(custom_properties, dict):
                    if len(custom_properties) > 50:
                        issues.append(
                            ActionValidationIssue(
                                path="$.custom_properties",
                                message="custom_properties must contain at most 50 values",
                                code="length",
                            )
                        )
                    for property_name, value in custom_properties.items():
                        if not isinstance(property_name, str) or not (
                            _EVENT_PROPERTY_NAME_RE.fullmatch(property_name)
                        ):
                            issues.append(
                                ActionValidationIssue(
                                    path="$.custom_properties",
                                    message="custom property names must use lowercase API names",
                                    code="format",
                                )
                            )
                            break
                        _event_property_value_issue(
                            value=value,
                            path=f"$.custom_properties.{property_name}",
                            issues=issues,
                            strings_only=True,
                        )
            case "marketing.behavioral_events.definitions.list":
                optional_str(payload, "search_string", issues)
                optional_str(payload, "after", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=100)
                include_properties = payload.get("include_properties")
                if include_properties is not None and not isinstance(include_properties, bool):
                    issues.append(
                        ActionValidationIssue(
                            path="$.include_properties",
                            message="include_properties must be a boolean",
                            code="type_error",
                        )
                    )
            case "marketing.behavioral_events.send":
                for key in (
                    "definition_ref",
                    "contact_ref",
                    "occurrence_key",
                    "legal_basis",
                    "legal_basis_explanation",
                ):
                    required_str(payload, key, issues)
                _event_timestamp_issue(payload, "occurred_at", issues, required=True)
                list_field(
                    payload,
                    "property_values",
                    issues,
                    required=True,
                    max_items=50,
                )
                if payload.get("tracking_authority_confirmed") is not True:
                    issues.append(
                        ActionValidationIssue(
                            path="$.tracking_authority_confirmed",
                            message="tracking_authority_confirmed must be true",
                            code="confirmation_required",
                        )
                    )
                occurrence_key = payload.get("occurrence_key")
                if isinstance(occurrence_key, str) and (
                    len(occurrence_key) > 200
                    or not occurrence_key
                    or not all(
                        character.isalnum() or character in "._:-" for character in occurrence_key
                    )
                ):
                    issues.append(
                        ActionValidationIssue(
                            path="$.occurrence_key",
                            message=(
                                "occurrence_key must contain at most 200 letters, digits, '.', "
                                "'_', ':' or '-'"
                            ),
                            code="format",
                        )
                    )
                property_values = payload.get("property_values")
                if isinstance(property_values, list):
                    for index, raw_value in enumerate(property_values):
                        if not isinstance(raw_value, dict):
                            issues.append(
                                ActionValidationIssue(
                                    path=f"$.property_values[{index}]",
                                    message="property values must be objects",
                                    code="type_error",
                                )
                            )
                            continue
                        required_str(raw_value, "property_ref", issues)
                        if "value" not in raw_value:
                            issues.append(
                                ActionValidationIssue(
                                    path=f"$.property_values[{index}].value",
                                    message="value is required",
                                    code="required",
                                )
                            )
                            continue
                        _event_property_value_issue(
                            value=raw_value["value"],
                            path=f"$.property_values[{index}].value",
                            issues=issues,
                            strings_only=False,
                        )
            case "bulk.exports.create":
                for key in ("export_name", "object_type", "format"):
                    required_str(payload, key, issues)
                list_field(
                    payload,
                    "property_refs",
                    issues,
                    required=True,
                    max_items=100,
                )
                list_field(payload, "associated_object_types", issues, max_items=4)
                optional_str(payload, "language", issues)
                for key in (
                    "include_internal_property_names",
                    "include_internal_property_values",
                    "include_primary_display_properties",
                    "include_labeled_associations",
                    "override_association_limit",
                ):
                    value = payload.get(key)
                    if value is not None and not isinstance(value, bool):
                        issues.append(
                            ActionValidationIssue(
                                path=f"$.{key}",
                                message=f"{key} must be a boolean",
                                code="type_error",
                            )
                        )
                if payload.get("export_authorized") is not True:
                    issues.append(
                        ActionValidationIssue(
                            path="$.export_authorized",
                            message="export_authorized must be true",
                            code="confirmation_required",
                        )
                    )
            case "bulk.exports.status":
                required_str(payload, "job_ref", issues)
            case "bulk.exports.result":
                required_str(payload, "job_ref", issues)
                int_range(
                    payload,
                    "max_bytes",
                    issues,
                    minimum=1,
                    maximum=1_073_741_824,
                )
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        headers = bearer_headers(request, "access_token")
        payload = request.input_json
        match request.operation:
            case "bulk.exports.create":
                request_body, job_metadata = _bulk_export_create_body(request)
                redactions = tuple(
                    str(value)
                    for value in (
                        job_metadata["export_name"],
                        *job_metadata["provider_property_names"],
                    )
                    if value
                )
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url("/crm/exports/2026-03/export/async"),
                    headers=headers,
                    json_body=request_body,
                    redact_values=redactions,
                )
                return _bulk_export_create_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    job_metadata=job_metadata,
                )
            case "bulk.exports.status":
                job = _bulk_export_job(request)
                job_metadata = _bulk_export_job_metadata(job)
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(f"/crm/exports/2026-03/export/{q(job.provider_object_id)}"),
                    headers=headers,
                    redact_values=(
                        job.provider_object_id,
                        *tuple(job_metadata["provider_property_names"]),
                    ),
                )
                return _bulk_export_status_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    job=job,
                    job_metadata=job_metadata,
                )
            case "bulk.exports.result":
                job = _bulk_export_job(request)
                job_metadata = _bulk_export_job_metadata(job)
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(
                        "/crm/exports/2026-03/export/async/tasks/"
                        f"{q(job.provider_object_id)}/status"
                    ),
                    headers=headers,
                    redact_values=(
                        job.provider_object_id,
                        *tuple(job_metadata["provider_property_names"]),
                    ),
                )
                return await _bulk_export_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    job=job,
                    job_metadata=job_metadata,
                )
            case (
                "crm.contacts.properties.list"
                | "crm.companies.properties.list"
                | "crm.deals.properties.list"
                | "crm.leads.properties.list"
                | "sales.products.properties.list"
                | "sales.line_items.properties.list"
                | "sales.quotes.properties.list"
                | "sales.goal_targets.properties.list"
            ):
                object_type = request.operation.split(".")[1]
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(f"/crm/properties/2026-03/{object_type}"),
                    headers=headers,
                )
                properties, lifecycle_stages = _property_metadata(
                    request,
                    object_type=object_type,
                    body=body,
                )
                extra = (
                    {"lifecycle_stages": lifecycle_stages} if object_type == "contacts" else None
                )
                return _safe_metadata_result(
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    results=properties,
                    extra=extra,
                )
            case "crm.owners.list":
                owner_params: dict[str, Any] = {}
                if isinstance(payload.get("limit"), int):
                    owner_params["limit"] = payload["limit"]
                if payload.get("after"):
                    owner_params["after"] = str(payload["after"])
                if isinstance(payload.get("archived"), bool):
                    owner_params["archived"] = "true" if payload["archived"] else "false"
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/crm/owners/2026-03"),
                    headers=headers,
                    params=owner_params,
                )
                return _safe_metadata_result(
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    results=_owner_metadata(request, body),
                )
            case "crm.deals.pipelines.list":
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/crm/pipelines/2026-03/deals"),
                    headers=headers,
                )
                return _safe_metadata_result(
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    results=_pipeline_metadata(request, body),
                )
            case (
                "crm.contact_company.labels.list"
                | "crm.contact_deal.labels.list"
                | "crm.company_deal.labels.list"
            ):
                relationship, from_type, to_type = {
                    "crm.contact_company.labels.list": (
                        "contact-company",
                        "contacts",
                        "companies",
                    ),
                    "crm.contact_deal.labels.list": (
                        "contact-deal",
                        "contacts",
                        "deals",
                    ),
                    "crm.company_deal.labels.list": (
                        "company-deal",
                        "companies",
                        "deals",
                    ),
                }[request.operation]
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(f"/crm/associations/2026-03/{from_type}/{to_type}/labels"),
                    headers=headers,
                )
                return _safe_metadata_result(
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    results=_association_label_metadata(
                        request,
                        relationship=relationship,
                        body=body,
                    ),
                )
            case (
                "crm.contact_company.associate"
                | "crm.contact_company.dissociate"
                | "crm.contact_deal.associate"
                | "crm.contact_deal.dissociate"
                | "crm.company_deal.associate"
                | "crm.company_deal.dissociate"
            ):
                (
                    from_record,
                    to_record,
                    _label,
                    category,
                    type_id,
                    from_type,
                    to_type,
                    from_plural,
                    to_plural,
                ) = _resolved_association(request)
                association_type = {
                    "associationCategory": category,
                    "associationTypeId": type_id,
                }
                if request.operation.endswith(".associate"):
                    status, body, response_headers = await send_json(
                        method="PUT",
                        url=_metadata_url(
                            "/crm/objects/2026-03/"
                            f"{from_type}/{q(from_record.provider_object_id)}/associations/"
                            f"{to_type}/{q(to_record.provider_object_id)}"
                        ),
                        headers=headers,
                        json_body=[association_type],
                    )
                    relationship_state = "associated"
                else:
                    status, body, response_headers = await send_json(
                        method="POST",
                        url=_metadata_url(
                            "/crm/associations/2026-03/"
                            f"{from_plural}/{to_plural}/batch/labels/archive"
                        ),
                        headers=headers,
                        json_body={
                            "inputs": [
                                {
                                    "types": [association_type],
                                    "from": {"id": from_record.provider_object_id},
                                    "to": {"id": to_record.provider_object_id},
                                }
                            ]
                        },
                    )
                    relationship_state = "label_removed"
                return _association_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    relationship_state=relationship_state,
                )
            case "sales.line_items.associate_deal" | "sales.line_items.dissociate_deal":
                line_item, deal = _resolved_line_item_deal(request)
                if request.operation.endswith(".associate_deal"):
                    status, body, response_headers = await send_json(
                        method="PUT",
                        url=_metadata_url(
                            "/crm/objects/2026-03/line_items/"
                            f"{q(line_item.provider_object_id)}/associations/default/deals/"
                            f"{q(deal.provider_object_id)}"
                        ),
                        headers=headers,
                    )
                    relationship_state = "associated"
                else:
                    status, body, response_headers = await send_json(
                        method="DELETE",
                        url=_metadata_url(
                            "/crm/objects/2026-03/line_items/"
                            f"{q(line_item.provider_object_id)}/associations/deals/"
                            f"{q(deal.provider_object_id)}"
                        ),
                        headers=headers,
                    )
                    relationship_state = "dissociated"
                return _line_item_deal_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    relationship_state=relationship_state,
                )
            case (
                "crm.companies.batch_upsert"
                | "crm.contacts.batch_upsert"
                | "crm.deals.batch_upsert"
                | "crm.leads.batch_upsert"
                | "sales.products.batch_upsert"
                | "sales.line_items.batch_upsert"
            ):
                object_type = request.operation.split(".")[1]
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_object_api_url(object_type, "/batch/upsert"),
                    headers=headers,
                    json_body={
                        "inputs": _upsert_inputs(
                            request,
                            object_type=object_type,
                            payload=payload,
                        )
                    },
                )
                return _batch_result(
                    request,
                    object_type=object_type,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case (
                "crm.notes.create" | "crm.tasks.create" | "crm.calls.create" | "crm.meetings.create"
            ):
                activity_type = request.operation.split(".")[1]
                request_body, safe_associations = _activity_body(
                    request,
                    activity_type=activity_type,
                    payload=payload,
                )
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_object_api_url(activity_type),
                    headers=headers,
                    json_body=request_body,
                )
                return _activity_result(
                    request,
                    activity_type=activity_type,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    safe_associations=safe_associations,
                )
            case (
                "crm.contacts.search"
                | "crm.companies.search"
                | "crm.deals.search"
                | "crm.leads.search"
                | "sales.products.search"
                | "sales.line_items.search"
                | "sales.quotes.search"
            ):
                object_type = request.operation.split(".")[1]
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_object_api_url(object_type, "/search"),
                    headers=headers,
                    json_body=_search_body(
                        request,
                        object_type=object_type,
                        payload=payload,
                    ),
                )
                return _record_result(
                    request,
                    object_type=object_type,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "sales.goal_targets.list":
                goal_params: dict[str, Any] = {}
                if isinstance(payload.get("limit"), int):
                    goal_params["limit"] = payload["limit"]
                fields = payload.get("property_refs")
                if isinstance(fields, list) and fields:
                    goal_params["properties"] = ",".join(
                        _resolved_property_id(
                            request,
                            object_type="goal_targets",
                            property_ref=field,
                        )
                        for field in fields
                    )
                after = payload.get("after")
                if after:
                    goal_params["after"] = str(after)
                if isinstance(payload.get("archived"), bool):
                    goal_params["archived"] = "true" if payload["archived"] else "false"
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_object_api_url("goal_targets"),
                    headers=headers,
                    params=goal_params,
                )
                return _record_result(
                    request,
                    object_type="goal_targets",
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.forms.list":
                form_list_params: dict[str, Any] = {}
                if isinstance(payload.get("limit"), int):
                    form_list_params["limit"] = payload["limit"]
                if payload.get("after"):
                    form_list_params["after"] = str(payload["after"])
                if isinstance(payload.get("archived"), bool):
                    form_list_params["archived"] = "true" if payload["archived"] else "false"
                if isinstance(payload.get("form_types"), list):
                    form_list_params["formTypes"] = payload["form_types"]
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/marketing/v3/forms"),
                    headers=headers,
                    params=form_list_params,
                )
                return _safe_metadata_result(
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    results=_form_results(request, body=body),
                )
            case "marketing.forms.submissions.list":
                form = _resolved_form(request)
                form_submission_params: dict[str, Any] = {}
                if isinstance(payload.get("limit"), int):
                    form_submission_params["limit"] = payload["limit"]
                if payload.get("after"):
                    form_submission_params["after"] = str(payload["after"])
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(
                        f"/form-integrations/v1/submissions/forms/{q(form.provider_object_id)}"
                    ),
                    headers=headers,
                    params=form_submission_params,
                )
                return _form_submission_result(
                    request,
                    form=form,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.segments.list":
                search_body: dict[str, Any] = {
                    "objectTypeId": _CONTACT_OBJECT_TYPE_ID,
                }
                for input_key, provider_key in (
                    ("query", "query"),
                    ("processing_types", "processingTypes"),
                    ("offset", "offset"),
                    ("count", "count"),
                ):
                    if payload.get(input_key) is not None:
                        search_body[provider_key] = payload[input_key]
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url("/crm/lists/2026-03/search"),
                    headers=headers,
                    json_body=search_body,
                )
                return _segment_search_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.segments.memberships.list":
                segment = _resolved_segment(request, require_mutable=False)
                membership_params: dict[str, Any] = {}
                if isinstance(payload.get("limit"), int):
                    membership_params["limit"] = payload["limit"]
                if payload.get("after"):
                    membership_params["after"] = str(payload["after"])
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(
                        f"/crm/lists/2026-03/{q(segment.provider_object_id)}/memberships"
                    ),
                    headers=headers,
                    params=membership_params,
                )
                return _segment_membership_result(
                    request,
                    segment=segment,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.segments.memberships.add" | "marketing.segments.memberships.remove":
                segment = _resolved_segment(request, require_mutable=True)
                provider_ids, safe_by_provider_id = _resolved_contact_refs(request)
                mutation = request.operation.rsplit(".", 1)[1]
                status, body, response_headers = await send_json(
                    method="PUT",
                    url=_metadata_url(
                        f"/crm/lists/2026-03/{q(segment.provider_object_id)}/memberships/{mutation}"
                    ),
                    headers=headers,
                    json_body=provider_ids,
                )
                return _segment_mutation_result(
                    request,
                    segment=segment,
                    safe_by_provider_id=safe_by_provider_id,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.campaigns.list":
                campaign_list_params: dict[str, Any] = {
                    "properties": ",".join(
                        (
                            *_CAMPAIGN_PROPERTIES.values(),
                            "hs_owner",
                            "hs_budget_items_sum_amount",
                            "hs_spend_items_sum_amount",
                        )
                    )
                }
                if payload.get("name"):
                    campaign_list_params["name"] = payload["name"]
                if isinstance(payload.get("limit"), int):
                    campaign_list_params["limit"] = payload["limit"]
                if payload.get("after"):
                    campaign_list_params["after"] = payload["after"]
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/marketing/campaigns/2026-03"),
                    headers=headers,
                    params=campaign_list_params,
                )
                return _campaign_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.campaigns.get":
                campaign = _resolved_campaign(request)
                campaign_get_params: dict[str, Any] = {
                    "properties": ",".join(
                        (
                            *_CAMPAIGN_PROPERTIES.values(),
                            "hs_owner",
                            "hs_budget_items_sum_amount",
                            "hs_spend_items_sum_amount",
                        )
                    )
                }
                for key, provider_key in (
                    ("start_date", "startDate"),
                    ("end_date", "endDate"),
                ):
                    value = _validated_date(payload.get(key), field=key)
                    if value is not None:
                        campaign_get_params[provider_key] = value
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(
                        f"/marketing/campaigns/2026-03/{q(campaign.provider_object_id)}"
                    ),
                    headers=headers,
                    params=campaign_get_params,
                )
                return _campaign_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.campaigns.create":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url("/marketing/campaigns/2026-03"),
                    headers=headers,
                    json_body=_campaign_write_body(payload),
                )
                return _campaign_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.campaigns.update":
                campaign = _resolved_campaign(request)
                status, body, response_headers = await send_json(
                    method="PATCH",
                    url=_metadata_url(
                        f"/marketing/campaigns/2026-03/{q(campaign.provider_object_id)}"
                    ),
                    headers=headers,
                    json_body=_campaign_write_body(payload),
                )
                return _campaign_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.emails.list":
                email_list_params: dict[str, Any] = {}
                for input_key, provider_key in (
                    ("after", "after"),
                    ("limit", "limit"),
                    ("email_type", "type"),
                ):
                    if payload.get(input_key) is not None:
                        email_list_params[provider_key] = payload[input_key]
                for input_key, provider_key in (
                    ("archived", "archived"),
                    ("is_published", "isPublished"),
                    ("include_stats", "includeStats"),
                ):
                    if isinstance(payload.get(input_key), bool):
                        email_list_params[provider_key] = "true" if payload[input_key] else "false"
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/marketing/emails/2026-03"),
                    headers=headers,
                    params=email_list_params,
                )
                return _email_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.emails.get":
                email = _resolved_email(request)
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(f"/marketing/emails/2026-03/{q(email.provider_object_id)}"),
                    headers=headers,
                )
                return _email_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.emails.create":
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url("/marketing/emails/2026-03"),
                    headers=headers,
                    json_body=_email_write_body(request, create=True),
                )
                return _email_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.emails.update":
                email = _resolved_email(request)
                _current_status, current, _current_headers = await send_json(
                    method="GET",
                    url=_metadata_url(f"/marketing/emails/2026-03/{q(email.provider_object_id)}"),
                    headers=headers,
                )
                current_state = (
                    str(current.get("state") or "").strip().upper()
                    if isinstance(current, dict)
                    else ""
                )
                if (
                    not isinstance(current, dict)
                    or current.get("isPublished") is not False
                    or current_state != "DRAFT"
                ):
                    raise ValidationError(
                        "HubSpot marketing email updates require a provider-verified draft"
                    )
                status, body, response_headers = await send_json(
                    method="PATCH",
                    url=_metadata_url(f"/marketing/emails/2026-03/{q(email.provider_object_id)}"),
                    headers=headers,
                    json_body=_email_write_body(request, create=False),
                )
                return _email_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "transactional.single_email.send":
                if (
                    credential_config(request).get("transactional_email_entitlement_confirmed")
                    is not True
                ):
                    raise ActionConnectorError(
                        "HubSpot transactional email add-on is not confirmed for this connection",
                        output_json={
                            "provider": "hubspot",
                            "operation": request.operation,
                            "status": "blocked",
                            "reason": "transactional_email_entitlement_not_confirmed",
                            "next_action": (
                                "Verify the add-on on the connected portal and update the "
                                "connection entitlement confirmation."
                            ),
                        },
                        metadata_json={
                            "vendor": "hubspot",
                            "operation": request.operation,
                            "provider_called": False,
                        },
                    )
                email = _resolved_transactional_email(request)
                try:
                    email_id = int(email.provider_object_id)
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        "HubSpot transactional email ref does not contain a numeric id"
                    ) from exc
                custom_properties = _transactional_custom_properties(payload)
                contact, contact_email = await _resolved_contact_email(
                    request,
                    headers=headers,
                )
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url("/marketing/transactional/2026-03/single-email/send"),
                    headers=headers,
                    json_body={
                        "emailId": email_id,
                        "message": {
                            "to": contact_email,
                            "sendId": payload["send_id"],
                        },
                        "contactProperties": {},
                        "customProperties": custom_properties,
                    },
                    redact_values=(
                        contact_email,
                        str(contact.provider_object_id),
                        str(email.provider_object_id),
                    ),
                )
                return _transactional_email_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    contact=contact,
                    contact_email=contact_email,
                    email=email,
                )
            case "marketing.subscription_types.list":
                subscription_params: dict[str, Any] = {}
                brand = _resolved_optional_ref(
                    request,
                    input_key="brand_ref",
                    object_type="brand",
                )
                if brand is not None:
                    subscription_params["businessUnitId"] = brand.provider_object_id
                if isinstance(payload.get("include_translations"), bool):
                    subscription_params["includeTranslations"] = (
                        "true" if payload["include_translations"] else "false"
                    )
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/communication-preferences/2026-03/definitions"),
                    headers=headers,
                    params=subscription_params,
                )
                return _safe_metadata_result(
                    operation=request.operation,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    results=_subscription_type_results(request, body=body),
                )
            case "marketing.contact_preferences.get":
                contact, contact_email = await _resolved_contact_email(
                    request,
                    headers=headers,
                )
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url(
                        f"/communication-preferences/2026-03/statuses/{q(contact_email)}"
                    ),
                    headers=headers,
                    params={"channel": "EMAIL"},
                    redact_values=(contact_email,),
                )
                return _preference_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    contact=contact,
                    expected_email=contact_email,
                )
            case "marketing.contact_preferences.update":
                contact, contact_email = await _resolved_contact_email(
                    request,
                    headers=headers,
                )
                subscription = _resolved_optional_ref(
                    request,
                    input_key="subscription_type_ref",
                    object_type="subscription-type",
                )
                if subscription is None:
                    raise ValidationError("HubSpot subscription_type_ref is required")
                status_state = payload.get("status")
                if status_state not in {"SUBSCRIBED", "UNSUBSCRIBED", "NOT_SPECIFIED"}:
                    raise ValidationError("HubSpot preference status is not supported")
                if payload.get("legal_change_confirmed") is not True:
                    raise ValidationError(
                        "HubSpot preference update requires legal_change_confirmed=true"
                    )
                if status_state == "SUBSCRIBED" and payload.get("consent_obtained") is not True:
                    raise ValidationError("HubSpot subscribing requires consent_obtained=true")
                try:
                    subscription_id = int(subscription.provider_object_id)
                except ValueError as exc:
                    raise ValidationError(
                        "HubSpot subscription ref does not contain a numeric id"
                    ) from exc
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url(
                        f"/communication-preferences/2026-03/statuses/{q(contact_email)}"
                    ),
                    headers=headers,
                    json_body={
                        "subscriptionId": subscription_id,
                        "statusState": status_state,
                        "legalBasis": payload["legal_basis"],
                        "legalBasisExplanation": payload["legal_basis_explanation"],
                        "channel": "EMAIL",
                    },
                    redact_values=(contact_email,),
                )
                return _preference_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    contact=contact,
                    expected_email=contact_email,
                    expected_subscription=subscription,
                )
            case "marketing.events.list":
                marketing_event_params: dict[str, Any] = {}
                if payload.get("after"):
                    marketing_event_params["after"] = payload["after"]
                if isinstance(payload.get("limit"), int):
                    marketing_event_params["limit"] = payload["limit"]
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/marketing/marketing-events/2026-03"),
                    headers=headers,
                    params=marketing_event_params,
                )
                return _marketing_event_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.events.upsert":
                dynamic_redactions = tuple(
                    str(payload.get(key) or "")
                    for key in ("external_account_key", "external_event_key")
                    if payload.get(key)
                )
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url("/marketing/marketing-events/2026-03/events/upsert"),
                    headers=headers,
                    json_body=_marketing_event_write_body(payload),
                    redact_values=dynamic_redactions,
                )
                return _marketing_event_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.behavioral_events.definitions.list":
                event_definition_params: dict[str, Any] = {}
                for input_key, provider_key in (
                    ("search_string", "searchString"),
                    ("after", "after"),
                    ("limit", "limit"),
                ):
                    if payload.get(input_key) is not None:
                        event_definition_params[provider_key] = payload[input_key]
                if isinstance(payload.get("include_properties"), bool):
                    event_definition_params["includeProperties"] = (
                        "true" if payload["include_properties"] else "false"
                    )
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_metadata_url("/events/2026-03/event-definitions"),
                    headers=headers,
                    params=event_definition_params,
                )
                return _behavioral_definition_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case "marketing.behavioral_events.send":
                if payload.get("tracking_authority_confirmed") is not True:
                    raise ValidationError(
                        "HubSpot behavioral event send requires tracking authority"
                    )
                if (
                    not str(payload.get("legal_basis") or "").strip()
                    or not str(payload.get("legal_basis_explanation") or "").strip()
                ):
                    raise ValidationError(
                        "HubSpot behavioral event send requires legal basis context"
                    )
                definition = _resolved_behavioral_definition(request)
                contact = _resolved_behavioral_contact(request)
                event_properties = _behavioral_event_properties(
                    request,
                    definition=definition,
                )
                provider_uuid = _behavioral_occurrence_uuid(
                    definition=definition,
                    contact=contact,
                    occurrence_key=str(payload["occurrence_key"]),
                )
                status, body, response_headers = await send_json(
                    method="POST",
                    url=_metadata_url("/events/2026-03/send"),
                    headers=headers,
                    json_body={
                        "eventName": definition.provider_object_id,
                        "objectId": contact.provider_object_id,
                        "occurredAt": payload["occurred_at"],
                        "uuid": provider_uuid,
                        "properties": event_properties,
                    },
                    redact_values=(
                        definition.provider_object_id,
                        contact.provider_object_id,
                        provider_uuid,
                    ),
                )
                return _behavioral_event_result(
                    request,
                    status_code=status,
                    body=body,
                    headers=response_headers,
                    definition=definition,
                    contact=contact,
                    provider_uuid=provider_uuid,
                )
            case "crm.deals.list":
                deal_list_params: dict[str, Any] = {}
                if isinstance(payload.get("limit"), int):
                    deal_list_params["limit"] = payload["limit"]
                fields = payload.get("property_refs")
                if isinstance(fields, list) and fields:
                    deal_list_params["properties"] = ",".join(
                        _resolved_property_id(
                            request,
                            object_type="deals",
                            property_ref=field,
                        )
                        for field in fields
                    )
                history_fields = payload.get("property_history_refs")
                if isinstance(history_fields, list) and history_fields:
                    deal_list_params["propertiesWithHistory"] = ",".join(
                        _resolved_property_id(
                            request,
                            object_type="deals",
                            property_ref=field,
                        )
                        for field in history_fields
                    )
                after = payload.get("after")
                if after:
                    deal_list_params["after"] = str(after)
                if isinstance(payload.get("archived"), bool):
                    deal_list_params["archived"] = "true" if payload["archived"] else "false"
                status, body, response_headers = await send_json(
                    method="GET",
                    url=_object_api_url("deals"),
                    headers=headers,
                    params=deal_list_params,
                )
                return _record_result(
                    request,
                    object_type="deals",
                    status_code=status,
                    body=body,
                    headers=response_headers,
                )
            case _:
                raise ValidationError(f"unsupported HubSpot operation {request.operation!r}")


__all__ = ["HubSpotActionConnector"]
