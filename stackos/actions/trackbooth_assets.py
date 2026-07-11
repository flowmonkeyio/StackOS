"""Trackbooth bundled assets and live catalog schema projection."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cached_property
from importlib import resources
from pathlib import Path
from typing import Any

from stackos.actions.trackbooth_contract import (
    BLOCKED_OPERATION_IDS as _BLOCKED_OPERATION_IDS,
)
from stackos.actions.trackbooth_contract import (
    ENUM_VALUE_RE as _ENUM_VALUE_RE,
)
from stackos.actions.trackbooth_contract import (
    PATH_PARAM_RE as _PATH_PARAM_RE,
)
from stackos.actions.trackbooth_contract import (
    JsonObject,
)
from stackos.actions.trackbooth_transport import _limit, _optional_clean_str


class TrackboothAssets:
    """Local Trackbooth API asset loader and schema resolver."""

    def __init__(self) -> None:
        self.stackos_tools = self._read_json("stackos-tools.json")
        self.openapi = self._read_json("openapi.json")
        self.catalog = self._read_json("catalog.json")
        self.schema_audit = self._read_json("schema-constraints-audit.json")

    @staticmethod
    def _repo_asset_path(name: str) -> Path:
        return Path(__file__).resolve().parents[2] / "plugins" / "trackbooth" / "agent-api" / name

    def _read_json(self, name: str) -> Any:
        path = self._repo_asset_path(name)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        node = (
            resources.files("stackos")
            .joinpath("_assets")
            .joinpath("plugins")
            .joinpath("trackbooth")
            .joinpath("agent-api")
            .joinpath(name)
        )
        return json.loads(node.read_text(encoding="utf-8"))

    @cached_property
    def tools_by_operation_id(self) -> dict[str, JsonObject]:
        tools_raw = (
            self.stackos_tools.get("tools") if isinstance(self.stackos_tools, dict) else None
        )
        tools = tools_raw if isinstance(tools_raw, list) else []
        return {
            str(tool.get("operation_id")): tool
            for tool in tools
            if isinstance(tool, dict) and tool.get("operation_id")
        }

    @cached_property
    def catalog_by_operation_id(self) -> dict[str, JsonObject]:
        endpoints_raw = self.catalog.get("endpoints") if isinstance(self.catalog, dict) else None
        endpoints = endpoints_raw if isinstance(endpoints_raw, list) else []
        return {
            str(endpoint.get("operation_id")): endpoint
            for endpoint in endpoints
            if isinstance(endpoint, dict) and endpoint.get("operation_id")
        }

    @cached_property
    def openapi_schemas(self) -> dict[str, JsonObject]:
        if not isinstance(self.openapi, dict):
            return {}
        schemas = self.openapi.get("components", {}).get("schemas", {})
        return schemas if isinstance(schemas, dict) else {}

    def operation(self, operation_id: str, live: Mapping[str, Any] | None = None) -> JsonObject:
        static_tool = self.tools_by_operation_id.get(operation_id) or {}
        static_catalog = self.catalog_by_operation_id.get(operation_id) or {}
        if not static_tool and not static_catalog and live is None:
            raise KeyError(operation_id)
        merged: JsonObject = {}
        merged.update(static_catalog)
        merged.update(static_tool)
        if live is not None:
            for key, value in dict(live).items():
                if value is not None:
                    merged[key] = value
        context = _merge_context(static_catalog, static_tool, live)
        if context:
            merged["context"] = context
        return merged

    def summary(self, endpoint: Mapping[str, Any]) -> JsonObject:
        operation_id = str(endpoint.get("operation_id") or "")
        context_raw = endpoint.get("context")
        context: Mapping[str, Any] = context_raw if isinstance(context_raw, Mapping) else {}
        return {
            "operation_id": operation_id,
            "title": endpoint.get("title") or context.get("title") or operation_id,
            "subtitle": context.get("subtitle") or endpoint.get("subtitle"),
            "description": endpoint.get("description") or context.get("subtitle") or "",
            "category": endpoint.get("category") or context.get("category"),
            "tags": endpoint.get("tags") or context.get("tags") or [],
            "method": endpoint.get("method"),
            "path": endpoint.get("path"),
            "permissions": endpoint.get("permissions") or [],
            "roles": endpoint.get("roles") or [],
            "feature_requirements": endpoint.get("feature_requirements") or [],
            "field_groups": endpoint.get("field_groups") or [],
            "execution_blocked": self.is_blocked(endpoint),
        }

    def detail(self, operation_id: str, live: Mapping[str, Any] | None = None) -> JsonObject:
        endpoint = self.operation(operation_id, live=live)
        summary = self.summary(endpoint)
        path_params = _path_param_details(endpoint)
        query_schema = self.expand_schema(_schema_descriptor(endpoint, "query_schema"))
        body_schema = self.expand_schema(_schema_descriptor(endpoint, "body_schema"))
        response_schema = self.expand_schema(_schema_descriptor(endpoint, "response_schema"))
        weak: list[str] = []
        for label, schema in (
            ("query", query_schema),
            ("body", body_schema),
            ("response", response_schema),
        ):
            if schema and schema.get("weak"):
                weak.append(label)
        return {
            **summary,
            "path_params": path_params,
            "query_params": query_schema,
            "request_body": body_schema,
            "response": response_schema,
            "schema_warnings": weak,
            "source": {
                "bootstrap_manifest": bool(self.tools_by_operation_id.get(operation_id)),
                "static_catalog": bool(self.catalog_by_operation_id.get(operation_id)),
                "live_catalog": live is not None,
            },
        }

    def is_blocked(self, endpoint: Mapping[str, Any] | str) -> bool:
        return _is_blocked_endpoint(endpoint)

    def expand_schema(self, descriptor: Any) -> JsonObject | None:
        return _expand_schema_descriptor(descriptor, self.openapi_schemas)

    def filter_catalog(
        self,
        items: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any],
    ) -> list[JsonObject]:
        """Project live catalog rows into bounded summaries for discovery."""
        query = _optional_clean_str(payload.get("query"))
        category = _optional_clean_str(payload.get("category"))
        method = _optional_clean_str(payload.get("method"))
        path = _optional_clean_str(payload.get("path"))
        operation_id = _optional_clean_str(payload.get("operation_id"))
        tags = [str(tag).lower() for tag in payload.get("tags", []) if isinstance(tag, str)]
        limit = _limit(payload)
        summaries: list[JsonObject] = []
        for item in items:
            summary = self.summary(item)
            if operation_id and operation_id.lower() not in str(summary["operation_id"]).lower():
                continue
            if method and str(summary.get("method") or "").upper() != method.upper():
                continue
            if category and category.lower() not in str(summary.get("category") or "").lower():
                continue
            if path and path.lower() not in str(summary.get("path") or "").lower():
                continue
            item_tags = [str(tag).lower() for tag in summary.get("tags") or []]
            if tags and not all(tag in item_tags for tag in tags):
                continue
            if query:
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        summary.get("operation_id"),
                        summary.get("title"),
                        summary.get("subtitle"),
                        summary.get("description"),
                        summary.get("category"),
                        summary.get("method"),
                        summary.get("path"),
                        " ".join(item_tags),
                    )
                ).lower()
                if query.lower() not in haystack:
                    continue
            summaries.append(summary)
            if len(summaries) >= limit:
                break
        return summaries


def _merge_context(
    *sources: Mapping[str, Any] | None,
) -> JsonObject:
    context: JsonObject = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        raw = source.get("context")
        if isinstance(raw, Mapping):
            context.update(dict(raw))
        for key in ("title", "subtitle", "category", "tags"):
            if key in source and source[key] is not None:
                context[key] = source[key]
    return context


def _is_blocked_endpoint(endpoint: Mapping[str, Any] | str) -> bool:
    if isinstance(endpoint, str):
        operation_id = endpoint
        path = ""
    else:
        operation_id = str(endpoint.get("operation_id") or "")
        path = str(endpoint.get("path") or "")
    return operation_id in _BLOCKED_OPERATION_IDS or "/api-key" in path


def _detail_from_endpoint(
    endpoint: Mapping[str, Any],
    *,
    openapi_schemas: Mapping[str, Any] | None = None,
) -> JsonObject:
    schemas = openapi_schemas or {}
    operation_id = str(endpoint.get("operation_id") or "")
    context_raw = endpoint.get("context")
    context: Mapping[str, Any] = context_raw if isinstance(context_raw, Mapping) else {}
    query_schema = _expand_schema_descriptor(_schema_descriptor(endpoint, "query_schema"), schemas)
    body_schema = _expand_schema_descriptor(_schema_descriptor(endpoint, "body_schema"), schemas)
    response_schema = _expand_schema_descriptor(
        _schema_descriptor(endpoint, "response_schema"),
        schemas,
    )
    schema_warnings: list[str] = []
    for label, schema in (
        ("query", query_schema),
        ("body", body_schema),
        ("response", response_schema),
    ):
        if schema and schema.get("weak"):
            schema_warnings.append(label)
    return {
        "operation_id": operation_id,
        "checksum": endpoint.get("checksum") if isinstance(endpoint.get("checksum"), str) else None,
        "name": endpoint.get("name"),
        "title": endpoint.get("title") or context.get("title") or operation_id,
        "subtitle": context.get("subtitle") or endpoint.get("subtitle"),
        "description": endpoint.get("description") or context.get("subtitle") or "",
        "category": endpoint.get("category") or context.get("category"),
        "tags": endpoint.get("tags") or context.get("tags") or [],
        "method": str(endpoint.get("method") or "").upper(),
        "path": endpoint.get("path"),
        "permissions": endpoint.get("permissions") or [],
        "roles": endpoint.get("roles") or [],
        "feature_requirements": endpoint.get("feature_requirements") or [],
        "field_groups": endpoint.get("field_groups") or [],
        "execution_blocked": _is_blocked_endpoint(endpoint),
        **_risk_metadata(endpoint, context),
        "path_params": _path_param_details(endpoint),
        "query_params": query_schema,
        "request_body": body_schema,
        "response": response_schema,
        "schema_warnings": schema_warnings,
        "source": {
            "live_catalog": True,
            "bootstrap_manifest": False,
            "static_catalog": False,
        },
    }


def _risk_metadata(
    endpoint: Mapping[str, Any],
    context: Mapping[str, Any],
) -> JsonObject:
    metadata: JsonObject = {}
    for key in (
        "risk_level",
        "risk",
        "side_effect",
        "side_effects",
        "read_only",
        "readonly",
        "readOnly",
        "idempotent",
    ):
        if key in endpoint and endpoint[key] is not None:
            metadata[key] = endpoint[key]
        elif key in context and context[key] is not None:
            metadata[key] = context[key]
    return metadata


def _expand_schema_descriptor(
    descriptor: Any,
    openapi_schemas: Mapping[str, Any],
) -> JsonObject | None:
    if not isinstance(descriptor, Mapping):
        return None
    component_name = str(descriptor.get("component_name") or "")
    openapi_component = openapi_schemas.get(component_name)
    if not isinstance(openapi_component, Mapping):
        openapi_component = {}
    details = descriptor.get("details")
    if not isinstance(details, Mapping):
        details = {}
    fields = details.get("fields")
    field_list = (
        [field for field in fields if isinstance(field, Mapping)]
        if isinstance(fields, list)
        else []
    )
    properties: JsonObject = {}
    required: list[str] = []
    for field in field_list:
        name = field.get("name")
        if not isinstance(name, str) or not name:
            continue
        properties[name] = _field_to_schema(field)
        if field.get("required") is True:
            required.append(name)
    if not properties:
        openapi_properties = openapi_component.get("properties")
        if isinstance(openapi_properties, Mapping):
            for raw_name, raw_property in openapi_properties.items():
                if not isinstance(raw_name, str):
                    continue
                properties[raw_name] = (
                    dict(raw_property)
                    if isinstance(raw_property, Mapping)
                    else {"type": "object", "x_trackbooth_schema": raw_property}
                )
            openapi_required = openapi_component.get("required")
            if isinstance(openapi_required, list):
                required = [str(item) for item in openapi_required if isinstance(item, str)]

    constraints = _constraints(descriptor, details, openapi_component)
    schema_type = details.get("type") or openapi_component.get("type") or "object"
    weak = not properties and schema_type == "object"
    result: JsonObject = {
        "name": descriptor.get("name"),
        "component_name": component_name or None,
        "source": descriptor.get("source"),
        "file": descriptor.get("file"),
        "type": schema_type,
        "properties": properties,
        "required": required,
        "constraints": constraints,
        "modifiers": details.get("modifiers") or [],
        "spreads": details.get("spreads") or [],
        "weak": weak,
    }
    if details.get("label"):
        result["label"] = details["label"]
    if details.get("type_script"):
        result["type_script"] = details["type_script"]
    description = openapi_component.get("description")
    if isinstance(description, str):
        result["description"] = description
    enum_values = _enum_values_from_schema(result)
    if enum_values:
        result["enum_values"] = enum_values
    if weak:
        result["warning"] = "schema has no expanded properties in the live catalog detail"
    return result


def _schema_descriptor(endpoint: Mapping[str, Any], key: str) -> Any:
    if key in endpoint and endpoint[key] is not None:
        return endpoint[key]
    input_obj = endpoint.get("input")
    if isinstance(input_obj, Mapping):
        mapped_key = "response_schema" if key == "response_schema" else key
        if mapped_key in input_obj and input_obj[mapped_key] is not None:
            return input_obj[mapped_key]
    if key == "response_schema":
        value = endpoint.get("output_schema")
        if value is not None:
            return value
    return None


def _path_param_details(endpoint: Mapping[str, Any]) -> list[JsonObject]:
    raw = endpoint.get("path_params")
    if raw is None:
        input_obj = endpoint.get("input")
        if isinstance(input_obj, Mapping):
            raw = input_obj.get("path_params")
    details: list[JsonObject] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                name = item.get("name")
                if isinstance(name, str):
                    details.append({"name": name, **{k: v for k, v in item.items() if k != "name"}})
            elif isinstance(item, str):
                details.append({"name": item})
    known = {item["name"] for item in details}
    for name in _path_param_names(str(endpoint.get("path") or "")):
        if name not in known:
            details.append({"name": name, "source": "path"})
    return details


def _path_param_names(path: str) -> list[str]:
    names: list[str] = []
    for match in _PATH_PARAM_RE.finditer(path):
        name = match.group(1) or match.group(2)
        if name and name not in names:
            names.append(name)
    return names


def _field_to_schema(field: Mapping[str, Any]) -> JsonObject:
    raw_type = str(field.get("type") or "object").strip()
    enum_values = _enum_values(field)
    schema: JsonObject
    array_item_type = _array_item_type(raw_type)
    if array_item_type is not None:
        items = _simple_type_schema(array_item_type)
        if enum_values:
            items = {"type": "string", "enum": enum_values}
        schema = {"type": "array", "items": items}
    elif enum_values:
        schema = {"type": "string", "enum": enum_values}
    else:
        schema = _simple_type_schema(raw_type)
    schema["required"] = bool(field.get("required"))
    schema["nullable"] = bool(field.get("nullable"))
    validations = field.get("validations")
    if isinstance(validations, list):
        schema["validations"] = validations
    constraints = field.get("constraints")
    if isinstance(constraints, list):
        schema["constraints"] = constraints
    default_value = field.get("default_value")
    if default_value is not None:
        schema["default"] = default_value
    return schema


def _array_item_type(raw_type: str) -> str | None:
    normalized = raw_type.strip()
    while normalized.startswith("readonly "):
        normalized = normalized.removeprefix("readonly ").strip()
    if normalized.endswith("[]"):
        return normalized[:-2].strip()
    if normalized.startswith("Array<") and normalized.endswith(">"):
        return normalized[len("Array<") : -1].strip()
    if normalized.startswith("ReadonlyArray<") and normalized.endswith(">"):
        return normalized[len("ReadonlyArray<") : -1].strip()
    return None


def _simple_type_schema(raw_type: str) -> JsonObject:
    normalized = raw_type.strip()
    while normalized.startswith("readonly "):
        normalized = normalized.removeprefix("readonly ").strip()
    if normalized in {"string", "z.string"}:
        return {"type": "string"}
    if normalized in {"number", "z.number"}:
        return {"type": "number"}
    if normalized in {"integer", "int"}:
        return {"type": "integer"}
    if normalized in {"boolean", "bool", "z.boolean"}:
        return {"type": "boolean"}
    if normalized in {"record", "Record<string, string>", "Record<string, unknown>"}:
        return {"type": "object", "additionalProperties": True}
    if normalized in {"object", "unknown"}:
        return {"type": "object", "additionalProperties": True}
    enum_values = _enum_values_from_type(normalized)
    if enum_values:
        return {"type": "string", "enum": enum_values}
    return {"type": "object", "x_trackbooth_type": normalized}


def _enum_values(field: Mapping[str, Any]) -> list[Any]:
    explicit = field.get("enum_values")
    if isinstance(explicit, list):
        return explicit
    raw_type = field.get("type")
    if isinstance(raw_type, str):
        return _enum_values_from_type(raw_type)
    return []


def _enum_values_from_type(raw_type: str) -> list[str]:
    values = _ENUM_VALUE_RE.findall(raw_type)
    return values if len(values) >= 2 else []


def _enum_values_from_schema(schema: Mapping[str, Any]) -> list[Any]:
    values: list[Any] = []
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return values
    for prop in properties.values():
        if isinstance(prop, Mapping) and isinstance(prop.get("enum"), list):
            values.extend(prop["enum"])
    return values


def _constraints(*sources: Any) -> list[Any]:
    out: list[Any] = []
    for source in sources:
        if isinstance(source, Mapping) and isinstance(source.get("constraints"), list):
            out.extend(source["constraints"])
        if isinstance(source, Mapping) and isinstance(
            source.get("x-flowfilliates-constraints"),
            list,
        ):
            out.extend(source["x-flowfilliates-constraints"])
    return out
