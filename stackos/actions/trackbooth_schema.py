"""Pure Trackbooth action manifest and validation schema builders."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest, ActionValidationIssue
from stackos.actions.provider_utils import issue
from stackos.actions.trackbooth_assets import _schema_descriptor
from stackos.actions.trackbooth_contract import (
    ACTION_SLUG_RE as _ACTION_SLUG_RE,
)
from stackos.actions.trackbooth_contract import (
    READ_METHODS as _READ_METHODS,
)
from stackos.actions.trackbooth_contract import (
    RUNTIME_INVENTORY_SOURCE as _RUNTIME_INVENTORY_SOURCE,
)
from stackos.actions.trackbooth_contract import (
    TRACKBOOTH_PROVIDER_CONTEXT_SCHEMA as _TRACKBOOTH_PROVIDER_CONTEXT_SCHEMA,
)
from stackos.actions.trackbooth_contract import (
    TRACKBOOTH_PROVIDER_KEY as _TRACKBOOTH_PROVIDER_KEY,
)
from stackos.actions.trackbooth_contract import (
    JsonObject,
)


def _runtime_endpoint_checksum(detail: Mapping[str, Any]) -> str | None:
    checksum = detail.get("checksum")
    return checksum.strip() if isinstance(checksum, str) and checksum.strip() else None


def _runtime_action_manifest(
    *,
    action_key: str,
    public_action_key: str,
    detail: Mapping[str, Any],
    base_url: str,
    inventory_scope: Mapping[str, Any],
    inventory_scope_key: str,
    catalog_hash: str | None,
    synced_at: datetime,
) -> JsonObject:
    method = str(detail.get("method") or "").upper()
    path = str(detail.get("path") or "")
    operation_id = str(detail.get("operation_id") or "")
    title = str(detail.get("title") or operation_id)
    request_body = detail.get("request_body")
    query_params = detail.get("query_params")
    path_params = detail.get("path_params")
    return {
        "key": action_key,
        "name": _runtime_action_name(title),
        "description": _runtime_action_description(detail=detail, method=method, path=path),
        "provider": _TRACKBOOTH_PROVIDER_KEY,
        "capability": "agent-api",
        "risk_level": _runtime_risk_level(detail=detail, method=method, path=path),
        "input_schema": _runtime_input_schema(detail),
        "output_schema": _runtime_output_schema(detail),
        "config": {
            "schema_version": "stackos.action.v1",
            "public_action_key": public_action_key,
            "connector": "trackbooth",
            "operation": "operation.execute",
            "requires_credential": True,
            "trackbooth_operation_id": operation_id,
            "method": method,
            "path": path,
            "path_param_names": [
                str(item.get("name"))
                for item in path_params
                if isinstance(item, Mapping) and item.get("name")
            ]
            if isinstance(path_params, list)
            else [],
            "body_required_fields": _schema_required_fields(request_body),
            "has_query": isinstance(query_params, Mapping),
            "has_body": isinstance(request_body, Mapping),
            "category": detail.get("category"),
            "tags": detail.get("tags") or [],
            "permissions": detail.get("permissions") or [],
            "feature_requirements": detail.get("feature_requirements") or [],
            "field_groups": detail.get("field_groups") or [],
            "inventory_source": _RUNTIME_INVENTORY_SOURCE,
            "inventory_state": "active",
            "inventory_scope_key": inventory_scope_key,
            "inventory_project_id": inventory_scope["project_id"],
            "inventory_credential_ref": inventory_scope["credential_ref"],
            "inventory_api_base_url": base_url,
            "inventory_catalog_hash": catalog_hash,
            "inventory_endpoint_checksum": _runtime_endpoint_checksum(detail),
            "inventory_synced_at": synced_at.isoformat(),
            "provider_context_schema": _TRACKBOOTH_PROVIDER_CONTEXT_SCHEMA,
        },
    }


def _runtime_action_name(title: str) -> str:
    return f"Trackbooth: {title}".strip()[:200]


def _runtime_action_description(*, detail: Mapping[str, Any], method: str, path: str) -> str:
    del method, path
    summary = str(detail.get("description") or detail.get("subtitle") or "").strip()
    category = detail.get("category")
    tags = detail.get("tags") or []
    parts: list[str] = []
    if category:
        parts.append(f"category: {category}")
    if tags:
        parts.append(f"tags: {', '.join(map(str, tags[:8]))}")
    if summary:
        parts.append(summary)
    return " | ".join(parts) or "Generated Trackbooth operation from the live catalog."


def _runtime_risk_level(*, detail: Mapping[str, Any], method: str, path: str) -> str:
    explicit = _explicit_risk_level(detail)
    if explicit is not None:
        return explicit
    if method in _READ_METHODS:
        return "read"
    if _looks_like_read_semantics(detail=detail, path=path):
        return "read"
    return "write"


def _explicit_risk_level(detail: Mapping[str, Any]) -> str | None:
    candidates: list[tuple[str, Any]] = [
        ("risk_level", detail.get("risk_level")),
        ("risk", detail.get("risk")),
        ("side_effect", detail.get("side_effect")),
        ("side_effects", detail.get("side_effects")),
        ("read_only", detail.get("read_only")),
        ("readonly", detail.get("readonly")),
        ("readOnly", detail.get("readOnly")),
    ]
    context = detail.get("context")
    if isinstance(context, Mapping):
        candidates.extend(
            [
                ("risk_level", context.get("risk_level")),
                ("risk", context.get("risk")),
                ("side_effect", context.get("side_effect")),
                ("side_effects", context.get("side_effects")),
                ("read_only", context.get("read_only")),
                ("readonly", context.get("readonly")),
                ("readOnly", context.get("readOnly")),
            ]
        )
    for key, candidate in candidates:
        if isinstance(candidate, str):
            normalized_key = key.lower()
            normalized = candidate.strip().lower().replace("-", "_")
            if normalized in {"read", "readonly", "read_only", "low", "safe", "none"}:
                return "read"
            if normalized in {"write", "mutation", "mutating", "high", "unsafe", "side_effect"}:
                return "write"
            if normalized in {"true", "yes"}:
                if normalized_key in {"read_only", "readonly"}:
                    return "read"
                if normalized_key in {"side_effect", "side_effects"}:
                    return "write"
            if normalized in {"false", "no"}:
                if normalized_key in {"read_only", "readonly"}:
                    return "write"
                if normalized_key in {"side_effect", "side_effects"}:
                    return "read"
        elif isinstance(candidate, bool):
            normalized_key = key.lower()
            if normalized_key in {"read_only", "readonly"}:
                return "read" if candidate else "write"
            if normalized_key in {"side_effect", "side_effects"}:
                return "write" if candidate else "read"
            if not candidate:
                return "read"
            if candidate:
                continue
    return None


def _looks_like_read_semantics(*, detail: Mapping[str, Any], path: str) -> bool:
    values = [
        detail.get("operation_id"),
        detail.get("name"),
        detail.get("title"),
        detail.get("subtitle"),
        detail.get("description"),
        detail.get("category"),
        path,
    ]
    context = detail.get("context")
    if isinstance(context, Mapping):
        values.extend(
            [
                context.get("title"),
                context.get("subtitle"),
                context.get("category"),
                " ".join(map(str, context.get("tags") or []))
                if isinstance(context.get("tags"), list)
                else None,
            ]
        )
    if isinstance(detail.get("tags"), list):
        values.append(" ".join(map(str, detail.get("tags") or [])))
    text = " ".join(str(value or "") for value in values).lower().replace("_", " ")
    read_domains = ("reporting", "analytics", "dashboard", "report", "metric", "metrics")
    read_verbs = (
        "aggregate",
        "catalog",
        "compare",
        "comparison",
        "dashboard",
        "export",
        "get",
        "kpi",
        "list",
        "metric",
        "record",
        "report",
        "search",
        "summary",
        "top",
        "view",
    )
    write_verbs = (
        "approve",
        "create",
        "delete",
        "duplicate",
        "generate",
        "invite",
        "pause",
        "reject",
        "remove",
        "reveal",
        "revoke",
        "send",
        "sync",
        "terminate",
        "update",
        "upsert",
    )
    return (
        any(domain in text for domain in read_domains)
        and any(verb in text for verb in read_verbs)
        and not any(verb in text for verb in write_verbs)
    )


def _runtime_input_schema(detail: Mapping[str, Any]) -> JsonObject:
    properties: JsonObject = {}
    required: list[str] = []
    path_params = detail.get("path_params")
    if isinstance(path_params, list) and path_params:
        path_properties = {
            str(item.get("name")): {"type": "string"}
            for item in path_params
            if isinstance(item, Mapping) and item.get("name")
        }
        if path_properties:
            properties["path_params"] = {
                "type": "object",
                "additionalProperties": False,
                "required": list(path_properties),
                "properties": path_properties,
            }
            required.append("path_params")

    query_schema = _schema_property(detail.get("query_params"))
    if query_schema is not None:
        properties["query"] = query_schema
        if query_schema.get("required"):
            required.append("query")

    body_schema = _schema_property(detail.get("request_body"))
    if body_schema is not None:
        properties["body"] = body_schema
        if body_schema.get("required"):
            required.append("body")

    schema: JsonObject = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _runtime_output_schema(detail: Mapping[str, Any]) -> JsonObject:
    response = _schema_property(detail.get("response"))
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "operation_id": {"type": "string"},
            "status_code": {"type": "integer"},
            "data": response or {"type": "object", "additionalProperties": True},
        },
    }


def _schema_property(raw: Any) -> JsonObject | None:
    if not isinstance(raw, Mapping):
        return None
    schema: JsonObject = {
        "type": "object",
        "additionalProperties": True,
    }
    properties = raw.get("properties")
    if isinstance(properties, Mapping) and properties:
        schema["properties"] = dict(properties)
    required = raw.get("required")
    if isinstance(required, list) and required:
        schema["required"] = [str(item) for item in required if isinstance(item, str)]
    if raw.get("weak"):
        schema["x_trackbooth_schema_warning"] = raw.get("warning") or "weak live schema"
    if raw.get("type_script"):
        schema["x_trackbooth_type_script"] = raw["type_script"]
    return schema


def _schema_required_fields(raw: Any) -> list[str]:
    if not isinstance(raw, Mapping):
        return []
    required = raw.get("required")
    if isinstance(required, list):
        return [str(item) for item in required if isinstance(item, str)]
    properties = raw.get("properties")
    if isinstance(properties, Mapping):
        return [
            str(name)
            for name, prop in properties.items()
            if isinstance(name, str) and isinstance(prop, Mapping) and prop.get("required") is True
        ]
    return []


def _operation_action_slug(detail: Mapping[str, Any]) -> str:
    raw_name = detail.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        slug = _normalize_action_slug(raw_name)
        if slug:
            return slug[:150]
    operation_id = str(detail.get("operation_id") or "")
    if "." in operation_id:
        controller, method = operation_id.split(".", 1)
        controller = controller.removesuffix("Controller")
        return _normalize_action_slug(f"{controller}_{method}")[:150]
    return _normalize_action_slug(operation_id)[:150] or "operation"


def _normalize_action_slug(value: str) -> str:
    slug = _ACTION_SLUG_RE.sub("_", value.lower()).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


def _configured_endpoint(config: Mapping[str, Any], operation_id: str) -> JsonObject | None:
    configured_operation_id = config.get("trackbooth_operation_id")
    if not isinstance(configured_operation_id, str) or configured_operation_id != operation_id:
        return None
    method = config.get("method")
    path = config.get("path")
    if not isinstance(method, str) or not isinstance(path, str) or not method or not path:
        return None
    path_param_names = config.get("path_param_names")
    if not isinstance(path_param_names, list):
        path_param_names = []
    body_required_fields = config.get("body_required_fields")
    if not isinstance(body_required_fields, list):
        body_required_fields = []
    body_fields = [
        {"name": str(item), "type": "unknown", "required": True}
        for item in body_required_fields
        if isinstance(item, str)
    ]
    return {
        "operation_id": operation_id,
        "method": method.upper(),
        "path": path,
        "path_params": [{"name": str(item)} for item in path_param_names if isinstance(item, str)],
        "body_schema": {"details": {"type": "object", "fields": body_fields}}
        if config.get("has_body") is True
        else None,
        "query_schema": {"details": {"type": "object", "fields": []}}
        if config.get("has_query") is True
        else None,
        "category": config.get("category"),
        "tags": config.get("tags") or [],
        "permissions": config.get("permissions") or [],
        "feature_requirements": config.get("feature_requirements") or [],
        "field_groups": config.get("field_groups") or [],
    }


def _operation_accepts_body(
    request: ActionConnectorRequest,
    endpoint: Mapping[str, Any],
    *,
    body_schema: Mapping[str, Any] | None,
) -> bool:
    if request.config_json.get("has_body") is True:
        return True
    if body_schema is not None:
        return True
    return _schema_descriptor(endpoint, "body_schema") is not None


def _schema_value_issues(
    schema: Mapping[str, Any] | None,
    value: Mapping[str, Any],
    path: str,
) -> list[ActionValidationIssue]:
    if not schema:
        return []
    issues: list[ActionValidationIssue] = []
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                issues.append(issue(f"{path}.{key}", "required field is missing", "required"))
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for key, prop in properties.items():
            if key not in value or not isinstance(prop, Mapping):
                continue
            prop_type = prop.get("type")
            if prop_type == "array":
                raw_items = prop.get("items")
                item_schema = raw_items if isinstance(raw_items, Mapping) else {}
                if not isinstance(value[key], list):
                    issues.append(
                        issue(
                            f"{path}.{key}",
                            "value must be an array",
                            "type_mismatch",
                        )
                    )
                    continue
                item_enum_values = item_schema.get("enum")
                if isinstance(item_enum_values, list):
                    for index, item in enumerate(value[key]):
                        if item not in item_enum_values:
                            issues.append(
                                issue(
                                    f"{path}.{key}[{index}]",
                                    "value must be one of: "
                                    f"{', '.join(map(str, item_enum_values))}",
                                    "enum_mismatch",
                                )
                            )
                continue
            enum_values = prop.get("enum")
            if isinstance(enum_values, list) and value[key] not in enum_values:
                issues.append(
                    issue(
                        f"{path}.{key}",
                        f"value must be one of: {', '.join(map(str, enum_values))}",
                        "enum_mismatch",
                    )
                )
    return issues


def _missing_required_body_issues(schema: Mapping[str, Any]) -> list[ActionValidationIssue]:
    issues: list[ActionValidationIssue] = []
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str):
                issues.append(issue(f"$.body.{key}", "required field is missing", "required"))
    return issues


def _dedupe_issues(issues: Iterable[ActionValidationIssue]) -> list[ActionValidationIssue]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ActionValidationIssue] = []
    for item in issues:
        key = (item.path, item.message, item.code)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
