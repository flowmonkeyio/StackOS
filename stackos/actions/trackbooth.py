"""Trackbooth Agent API REST action connector."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from functools import cached_property
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from sqlmodel import col, select

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    credential_config,
    issue,
    list_field,
    optional_str,
    required_str,
)
from stackos.artifacts import redact_secret_text
from stackos.db.models import Action, ActionVersion, Plugin, Provider
from stackos.integrations.trackbooth import (
    TRACKBOOTH_DEFAULT_API_BASE_URL,
    TrackboothConfigError,
    normalize_trackbooth_base_url,
    parse_trackbooth_api_key,
    trackbooth_headers,
)
from stackos.repositories.base import ValidationError

JsonObject = dict[str, Any]

_TRACKBOOTH_PLUGIN_SLUG = "trackbooth"
_TRACKBOOTH_PROVIDER_KEY = "trackbooth"
_READ_METHODS = {"GET"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_RUNTIME_INVENTORY_SOURCE = "trackbooth.catalog.sync"
_RUNTIME_ACTION_KEY_PREFIX = "api."
_BLOCKED_OPERATION_MESSAGE = "Trackbooth API-key reveal/generate operations are not executable"
_BLOCKED_OPERATION_IDS = {
    "AccountApiKeyController.revealApiKey",
    "AccountApiKeyController.generateApiKey",
}
_TRACKBOOTH_PROVIDER_CONTEXT_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "acting_as_account": {
            "type": "string",
            "description": (
                "Optional managed Trackbooth account id. StackOS sends it as "
                "X-Acting-As-Account."
            ),
        }
    },
}
_PATH_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENUM_VALUE_RE = re.compile(r"'([^']*)'")
_ACTION_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


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
        tools = self.stackos_tools.get("tools") if isinstance(self.stackos_tools, dict) else []
        return {
            str(tool.get("operation_id")): tool
            for tool in tools
            if isinstance(tool, dict) and tool.get("operation_id")
        }

    @cached_property
    def catalog_by_operation_id(self) -> dict[str, JsonObject]:
        endpoints = self.catalog.get("endpoints") if isinstance(self.catalog, dict) else []
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
        context = endpoint.get("context") if isinstance(endpoint.get("context"), dict) else {}
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


class TrackboothActionConnector:
    """Decision-free adapter for Trackbooth Agent API REST actions."""

    key = "trackbooth"

    def __init__(self, assets: TrackboothAssets | None = None) -> None:
        self._assets = assets or TrackboothAssets()

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        if request.operation == "catalog.sync":
            return self._validate_catalog_sync(payload)
        if request.operation == "catalog.search":
            return self._validate_catalog_search(payload)
        if request.operation == "operation.describe":
            issues: list[ActionValidationIssue] = []
            required_str(payload, "operation_id", issues)
            return issues
        if request.operation in {"rest.read", "rest.write"}:
            return self._validate_rest(request)
        if self._configured_operation_id(request) is not None:
            issues = _runtime_inventory_input_issues(request)
            if issues:
                return issues
            return self._validate_rest(
                request,
                operation_id_override=self._configured_operation_id(request),
            )
        return [
            issue(
                "$.operation",
                f"unsupported operation {request.operation!r}",
                "enum_mismatch",
            )
        ]

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        api_key = self._api_key(request)
        base_url = self._base_url(request)
        acting_as_account = _effective_acting_as_account(request)
        headers = trackbooth_headers(api_key, acting_as_account=acting_as_account)
        self._enforce_runtime_inventory_scope(
            request=request,
            base_url=base_url,
        )

        if request.operation == "catalog.sync":
            live_items = await self._live_catalog(base_url=base_url, headers=headers)
            return await self._sync_catalog_actions(
                request=request,
                base_url=base_url,
                headers=headers,
                live_items=live_items,
            )

        if request.operation == "catalog.search":
            live_items = await self._live_catalog(base_url=base_url, headers=headers)
            filtered = self._filter_catalog(live_items, request.input_json)
            return ActionConnectorResult(
                output_json={
                    "data": filtered,
                    "count": len(filtered),
                    "limit": _limit(request.input_json),
                    "tool_count": len(live_items),
                    "api_base_url": base_url,
                },
                metadata_json={"vendor": "trackbooth", "operation": request.operation},
            )

        if request.operation == "operation.describe":
            operation_id = str(request.input_json["operation_id"]).strip()
            live = await self._live_operation(
                base_url=base_url,
                headers=headers,
                operation_id=operation_id,
            )
            return ActionConnectorResult(
                output_json={
                    "data": _detail_from_endpoint(
                        live,
                        openapi_schemas=self._assets.openapi_schemas,
                    )
                },
                metadata_json={"vendor": "trackbooth", "operation": request.operation},
            )

        if request.operation in {"rest.read", "rest.write"}:
            return await self._execute_rest(
                request=request,
                base_url=base_url,
                headers=headers,
            )

        operation_id = self._configured_operation_id(request)
        if operation_id is not None:
            return await self._execute_rest(
                request=request,
                base_url=base_url,
                headers=headers,
                operation_id_override=operation_id,
                live_visibility_check=False,
            )

        raise ValidationError(f"unsupported Trackbooth operation {request.operation!r}")

    def _validate_catalog_sync(self, payload: JsonObject) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        operation_ids = payload.get("operation_ids")
        if operation_ids is not None:
            if not isinstance(operation_ids, list):
                issues.append(
                    issue("$.operation_ids", "operation_ids must be an array", "type_error")
                )
            else:
                for index, operation_id in enumerate(operation_ids):
                    if not isinstance(operation_id, str) or not operation_id.strip():
                        issues.append(
                            issue(
                                f"$.operation_ids[{index}]",
                                "operation id must be a non-empty string",
                                "type_error",
                            )
                        )
        limit = payload.get("limit")
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 1000
        ):
            issues.append(issue("$.limit", "limit must be an integer between 1 and 1000", "range"))
        return issues

    def _validate_catalog_search(self, payload: JsonObject) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        for key in ("query", "category", "path", "operation_id"):
            optional_str(payload, key, issues)
        list_field(payload, "tags", issues)
        value = payload.get("limit")
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 100
        ):
            issues.append(issue("$.limit", "limit must be an integer between 1 and 100", "range"))
        return issues

    def _validate_rest(
        self,
        request: ActionConnectorRequest,
        *,
        operation_id_override: str | None = None,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if operation_id_override is None:
            required_str(payload, "operation_id", issues)
        for key in ("path_params", "query", "body"):
            value = payload.get(key)
            if value is not None and not isinstance(value, dict):
                issues.append(issue(f"$.{key}", f"{key} must be an object", "type_error"))
        operation_id = operation_id_override or payload.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            return issues
        operation_id = operation_id.strip()
        if self._assets.is_blocked(operation_id):
            issues.append(
                issue(
                    "$.operation_id",
                    _BLOCKED_OPERATION_MESSAGE,
                    "blocked_operation",
                )
            )
        try:
            endpoint = self._endpoint_for_request(request, operation_id)
        except KeyError:
            if operation_id_override is not None:
                issues.append(
                    issue(
                        "$.operation_id",
                        f"unknown Trackbooth operation {operation_id}",
                        "not_found",
                    )
                )
            return _dedupe_issues(issues)
        method = str(endpoint.get("method") or "").upper()
        if request.operation == "rest.read" and method not in _READ_METHODS:
            issues.append(
                issue("$.operation_id", "rest.read can execute only GET operations", "method")
            )
        if request.operation == "rest.write" and method not in _WRITE_METHODS:
            issues.append(
                issue(
                    "$.operation_id",
                    "rest.write can execute only POST, PUT, PATCH, or DELETE operations",
                    "method",
                )
            )
        if self._assets.is_blocked(endpoint):
            issues.append(
                issue(
                    "$.operation_id",
                    _BLOCKED_OPERATION_MESSAGE,
                    "blocked_operation",
                )
            )
        path_params = (
            payload.get("path_params")
            if isinstance(payload.get("path_params"), dict)
            else {}
        )
        for name in _path_param_names(str(endpoint.get("path") or "")):
            if name not in path_params:
                issues.append(
                    issue(f"$.path_params.{name}", "path parameter is required", "required")
                )
        query_schema = self._assets.expand_schema(_schema_descriptor(endpoint, "query_schema"))
        if isinstance(payload.get("query"), dict):
            issues.extend(_schema_value_issues(query_schema, payload["query"], "$.query"))
        body_schema = self._assets.expand_schema(_schema_descriptor(endpoint, "body_schema"))
        body_allowed = _operation_accepts_body(request, endpoint, body_schema=body_schema)
        body = payload.get("body")
        if body is not None and body_schema is None and not body_allowed:
            issues.append(
                issue(
                    "$.body",
                    "selected operation does not accept a request body",
                    "body_not_allowed",
                )
            )
        body_requires_validation = (
            request.operation == "rest.write" or method in _WRITE_METHODS
        )
        if body_requires_validation and body_schema is not None:
            if body is None:
                issues.extend(_missing_required_body_issues(body_schema))
            elif isinstance(body, dict):
                issues.extend(_schema_value_issues(body_schema, body, "$.body"))
        return _dedupe_issues(issues)

    def _endpoint_for_request(
        self,
        request: ActionConnectorRequest,
        operation_id: str,
    ) -> JsonObject:
        configured = _configured_endpoint(request.config_json, operation_id)
        if configured is not None:
            return configured
        return self._assets.operation(operation_id)

    async def _execute_rest(
        self,
        *,
        request: ActionConnectorRequest,
        base_url: str,
        headers: Mapping[str, str],
        operation_id_override: str | None = None,
        live_visibility_check: bool = True,
    ) -> ActionConnectorResult:
        payload = request.input_json
        operation_id = str(operation_id_override or payload["operation_id"]).strip()
        if self._assets.is_blocked(operation_id):
            raise ValidationError(_BLOCKED_OPERATION_MESSAGE)
        if live_visibility_check:
            endpoint = await self._live_operation(
                base_url=base_url,
                headers=headers,
                operation_id=operation_id,
            )
        else:
            endpoint = self._endpoint_for_request(request, operation_id)
        if self._assets.is_blocked(endpoint):
            raise ValidationError(_BLOCKED_OPERATION_MESSAGE)
        method = str(endpoint.get("method") or "").upper()
        if request.operation == "rest.read" and method not in _READ_METHODS:
            raise ValidationError("rest.read can execute only Trackbooth GET operations")
        if request.operation == "rest.write" and method not in _WRITE_METHODS:
            raise ValidationError("rest.write can execute only Trackbooth write operations")

        path = str(endpoint.get("path") or "")
        url = f"{base_url}{_substitute_path_params(path, payload.get('path_params'))}"
        raw_query = payload.get("query") if isinstance(payload.get("query"), dict) else None
        query = _serialize_query(raw_query)
        body_schema = self._assets.expand_schema(_schema_descriptor(endpoint, "body_schema"))
        body_allowed = _operation_accepts_body(request, endpoint, body_schema=body_schema)
        body = payload.get("body") if isinstance(payload.get("body"), dict) else None
        kwargs: dict[str, Any] = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "params": query,
        }
        if body_allowed and body is not None:
            kwargs["json"] = body
        if not body_allowed and payload.get("body") is not None:
            raise ValidationError("selected Trackbooth operation does not accept a request body")

        status_code, response_body = await self._request_json(**kwargs)
        generated_action = request.config_json.get("inventory_source") == _RUNTIME_INVENTORY_SOURCE
        output_json: JsonObject = {
            "operation_id": operation_id,
            "status_code": status_code,
            "data": response_body,
        }
        if not generated_action:
            output_json.update(
                {
                    "method": method,
                    "path": path,
                    "response_schema": self._assets.expand_schema(
                        _schema_descriptor(endpoint, "response_schema")
                    ),
                }
            )
        metadata_json: JsonObject = {
            "vendor": "trackbooth",
            "operation": request.operation,
            "operation_id": operation_id,
            "status_code": status_code,
        }
        if generated_action:
            scope_key = request.config_json.get("inventory_scope_key")
            if isinstance(scope_key, str):
                metadata_json["inventory_scope_key"] = scope_key
        else:
            metadata_json["method"] = method
            metadata_json["path"] = path
        return ActionConnectorResult(
            output_json=output_json,
            metadata_json=metadata_json,
        )

    async def _sync_catalog_actions(
        self,
        *,
        request: ActionConnectorRequest,
        base_url: str,
        headers: Mapping[str, str],
        live_items: Sequence[Mapping[str, Any]],
    ) -> ActionConnectorResult:
        if request.session is None:
            raise ValidationError("Trackbooth catalog sync requires a database session")
        requested_ids = _requested_operation_ids(request.input_json)
        limit = _sync_limit(request.input_json)
        selected_items = _select_sync_items(live_items, requested_ids=requested_ids, limit=limit)
        selected_ids = {str(item.get("operation_id")) for item in selected_items}
        missing_ids = sorted(requested_ids - selected_ids)

        details: list[JsonObject] = []
        warnings: list[JsonObject] = []
        for item in selected_items:
            operation_id = str(item.get("operation_id") or "").strip()
            if not operation_id:
                continue
            live_detail = await self._live_operation(
                base_url=base_url,
                headers=headers,
                operation_id=operation_id,
            )
            endpoint: JsonObject = dict(item)
            endpoint.update({key: value for key, value in live_detail.items() if value is not None})
            detail = _detail_from_endpoint(
                endpoint,
                openapi_schemas=self._assets.openapi_schemas,
            )
            if detail.get("schema_warnings"):
                warnings.append(
                    {
                        "operation_id": operation_id,
                        "schema_warnings": detail["schema_warnings"],
                    }
                )
            details.append(detail)

        sync_result = _upsert_runtime_actions(
            session=request.session,
            request=request,
            details=details,
            base_url=base_url,
            prune_missing=not requested_ids and limit is None,
        )
        return ActionConnectorResult(
            output_json={
                "synced": sync_result["synced"],
                "created": sync_result["created"],
                "updated": sync_result["updated"],
                "pruned": sync_result["pruned"],
                "action_refs": sync_result["action_refs"],
                "operation_ids": sync_result["operation_ids"],
                "blocked_operation_ids": sync_result["blocked_operation_ids"],
                "missing_operation_ids": missing_ids,
                "warnings": warnings,
                "api_base_url": base_url,
                "inventory_scope_key": sync_result["inventory_scope_key"],
                "manual_sync": True,
            },
            metadata_json={
                "vendor": "trackbooth",
                "operation": request.operation,
                "synced": sync_result["synced"],
                "api_base_url": base_url,
            },
        )

    def _configured_operation_id(self, request: ActionConnectorRequest) -> str | None:
        value = request.config_json.get("trackbooth_operation_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _enforce_runtime_inventory_scope(
        self,
        *,
        request: ActionConnectorRequest,
        base_url: str,
    ) -> None:
        if self._configured_operation_id(request) is None:
            return
        config = request.config_json
        if config.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
            return
        if config.get("inventory_state") == "retired":
            raise ValidationError(
                "Trackbooth generated action is retired; rerun trackbooth.catalog.sync"
            )
        expected_project_id = config.get("inventory_project_id")
        if isinstance(expected_project_id, int) and expected_project_id != request.project_id:
            raise ValidationError(
                "Trackbooth generated action belongs to a different StackOS project"
            )
        expected_credential_ref = config.get("inventory_credential_ref")
        credential_ref = (
            request.credential.credential_ref if request.credential is not None else None
        )
        if isinstance(expected_credential_ref, str) and expected_credential_ref != credential_ref:
            raise ValidationError(
                "Trackbooth generated action requires the credential used for catalog sync",
                data={"expected_credential_ref": expected_credential_ref},
            )
        expected_base_url = config.get("inventory_api_base_url")
        if isinstance(expected_base_url, str) and expected_base_url != base_url:
            raise ValidationError(
                "Trackbooth generated action was synced for a different API URL; "
                "rerun trackbooth.catalog.sync for the current credential"
            )
        if request.credential is not None:
            actual_scope_key = _runtime_inventory_scope_key(
                _runtime_inventory_scope(
                    request=request,
                    base_url=base_url,
                )
            )
            expected_scope_key = config.get("inventory_scope_key")
            if isinstance(expected_scope_key, str) and expected_scope_key != actual_scope_key:
                raise ValidationError(
                    "Trackbooth generated action scope does not match the current execution context"
                )

    def _api_key(self, request: ActionConnectorRequest) -> str:
        if request.credential is None:
            raise ValidationError("Trackbooth actions require a credential")
        try:
            return parse_trackbooth_api_key(request.credential.secret_payload)
        except TrackboothConfigError as exc:
            raise ValidationError(redact_secret_text(str(exc))) from exc

    def _base_url(self, request: ActionConnectorRequest) -> str:
        config = credential_config(request)
        try:
            return normalize_trackbooth_base_url(
                str(config.get("api_base_url") or TRACKBOOTH_DEFAULT_API_BASE_URL)
            )
        except TrackboothConfigError as exc:
            raise ValidationError(redact_secret_text(str(exc))) from exc

    async def _live_catalog(self, *, base_url: str, headers: Mapping[str, str]) -> list[JsonObject]:
        status_code, body = await self._request_json(
            method="GET",
            url=f"{base_url}/api/agent-api/catalog",
            headers=headers,
            params=[],
        )
        del status_code
        items = _extract_catalog_items(body)
        return [dict(item) for item in items]

    async def _live_operation(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str],
        operation_id: str,
    ) -> JsonObject:
        status_code, body = await self._request_json(
            method="GET",
            url=f"{base_url}/api/agent-api/catalog/{quote(operation_id, safe='')}",
            headers=headers,
            params=[],
        )
        del status_code
        item = _extract_operation_detail(body)
        if not item.get("operation_id"):
            item["operation_id"] = operation_id
        return item

    async def _request_json(self, **kwargs: Any) -> tuple[int, Any]:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as http:
            try:
                response = await http.request(**kwargs)
            except httpx.HTTPError as exc:
                raise ValidationError(
                    redact_secret_text(f"Trackbooth request failed: {exc}")
                ) from exc
        if response.status_code >= 400:
            raise ValidationError(
                redact_secret_text(
                    f"Trackbooth returned {response.status_code}: {response.text[:1000]}"
                ),
                data={"status_code": response.status_code},
            )
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body

    def _filter_catalog(
        self,
        items: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any],
    ) -> list[JsonObject]:
        query = _optional_clean_str(payload.get("query"))
        category = _optional_clean_str(payload.get("category"))
        method = _optional_clean_str(payload.get("method"))
        path = _optional_clean_str(payload.get("path"))
        operation_id = _optional_clean_str(payload.get("operation_id"))
        tags = [str(tag).lower() for tag in payload.get("tags", []) if isinstance(tag, str)]
        limit = _limit(payload)
        summaries: list[JsonObject] = []
        for item in items:
            summary = self._assets.summary(item)
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
    context = endpoint.get("context") if isinstance(endpoint.get("context"), Mapping) else {}
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


def _requested_operation_ids(payload: Mapping[str, Any]) -> set[str]:
    operation_ids = payload.get("operation_ids")
    if not isinstance(operation_ids, list):
        return set()
    return {item.strip() for item in operation_ids if isinstance(item, str) and item.strip()}


def _sync_limit(payload: Mapping[str, Any]) -> int | None:
    raw = payload.get("limit")
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 1000:
        return raw
    return None


def _select_sync_items(
    items: Sequence[Mapping[str, Any]],
    *,
    requested_ids: set[str],
    limit: int | None,
) -> list[JsonObject]:
    selected: list[JsonObject] = []
    for item in items:
        operation_id = str(item.get("operation_id") or "").strip()
        if not operation_id:
            continue
        if requested_ids and operation_id not in requested_ids:
            continue
        selected.append(dict(item))
        if limit is not None and len(selected) >= limit:
            break
    return selected


def _runtime_inventory_scope(
    *,
    request: ActionConnectorRequest,
    base_url: str,
) -> JsonObject:
    if request.credential is None:
        raise ValidationError("Trackbooth catalog sync requires a credential")
    return {
        "project_id": request.project_id,
        "credential_ref": request.credential.credential_ref,
        "api_base_url": base_url,
    }


def _runtime_inventory_scope_key(scope: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "project_id": scope.get("project_id"),
            "credential_ref": scope.get("credential_ref"),
            "api_base_url": scope.get("api_base_url"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"ctx_{_short_hash(payload, length=12)}"


def _runtime_action_key(
    *,
    scope_key: str,
    detail: Mapping[str, Any],
    suffix: str | None = None,
) -> str:
    prefix = f"{_RUNTIME_ACTION_KEY_PREFIX}{scope_key}."
    max_slug_length = max(1, 160 - len(prefix))
    slug = _operation_action_slug(detail)
    if suffix:
        suffix_text = f"_{suffix}"
        slug = f"{slug[: max(1, max_slug_length - len(suffix_text))]}{suffix_text}"
    else:
        slug = slug[:max_slug_length]
    return f"{prefix}{slug}"


def _short_hash(value: str, *, length: int = 10) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _retire_runtime_action(row: Action, *, now: datetime) -> None:
    config = dict(row.config_json or {})
    if config.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
        return
    config.pop("connector", None)
    config["execution_mode"] = "deferred.retired"
    config["deferred_reason"] = (
        "Generated Trackbooth action was not returned by the latest catalog sync "
        "for this inventory scope."
    )
    config["inventory_state"] = "retired"
    config["inventory_retired_at"] = now.isoformat()
    row.config_json = config
    row.updated_at = now


def _upsert_runtime_actions(
    *,
    session: Any,
    request: ActionConnectorRequest,
    details: Sequence[Mapping[str, Any]],
    base_url: str,
    prune_missing: bool,
) -> JsonObject:
    plugin = session.exec(
        select(Plugin).where(col(Plugin.slug) == _TRACKBOOTH_PLUGIN_SLUG)
    ).first()
    if plugin is None or plugin.id is None:
        raise ValidationError("Trackbooth plugin must be synced before catalog sync")
    provider = session.exec(
        select(Provider).where(
            col(Provider.plugin_id) == plugin.id,
            col(Provider.key) == _TRACKBOOTH_PROVIDER_KEY,
        )
    ).first()
    if provider is None or provider.id is None:
        raise ValidationError("Trackbooth provider must be synced before catalog sync")

    now = _utcnow()
    scope = _runtime_inventory_scope(
        request=request,
        base_url=base_url,
    )
    scope_key = _runtime_inventory_scope_key(scope)
    created = 0
    updated = 0
    pruned = 0
    action_refs: list[str] = []
    action_keys: set[str] = set()
    action_key_operations: dict[str, str] = {}
    operation_ids: list[str] = []
    blocked_operation_ids: list[str] = []
    for detail in details:
        operation_id = str(detail.get("operation_id") or "").strip()
        method = str(detail.get("method") or "").upper()
        path = str(detail.get("path") or "").strip()
        if not operation_id or not method or not path:
            continue
        if _is_blocked_endpoint(detail):
            blocked_operation_ids.append(operation_id)
            continue
        action_key = _runtime_action_key(scope_key=scope_key, detail=detail)
        prior_operation_id = action_key_operations.get(action_key)
        if prior_operation_id is not None and prior_operation_id != operation_id:
            action_key = _runtime_action_key(
                scope_key=scope_key,
                detail=detail,
                suffix=_short_hash(operation_id),
            )
        action_key_operations[action_key] = operation_id
        row = session.exec(
            select(Action).where(col(Action.plugin_id) == plugin.id, col(Action.key) == action_key)
        ).first()
        manifest_json = _runtime_action_manifest(
            action_key=action_key,
            detail=detail,
            base_url=base_url,
            inventory_scope=scope,
            inventory_scope_key=scope_key,
            synced_at=now,
        )
        if row is None:
            row = Action(
                plugin_id=plugin.id,
                provider_id=provider.id,
                key=action_key,
                name=manifest_json["name"],
                description=manifest_json["description"],
                capability_key=manifest_json["capability"],
                risk_level=manifest_json["risk_level"],
                input_schema_json=manifest_json["input_schema"],
                output_schema_json=manifest_json["output_schema"],
                config_json=manifest_json["config"],
            )
            created += 1
        else:
            row.provider_id = provider.id
            row.name = manifest_json["name"]
            row.description = manifest_json["description"]
            row.capability_key = manifest_json["capability"]
            row.risk_level = manifest_json["risk_level"]
            row.input_schema_json = manifest_json["input_schema"]
            row.output_schema_json = manifest_json["output_schema"]
            row.config_json = manifest_json["config"]
            row.updated_at = now
            updated += 1
        session.add(row)
        session.flush()
        if row.id is None:
            raise RuntimeError("expected persisted Trackbooth action id")
        version = session.exec(
            select(ActionVersion).where(
                col(ActionVersion.action_id) == row.id,
                col(ActionVersion.version) == "live-catalog",
            )
        ).first()
        if version is None:
            version = ActionVersion(
                action_id=row.id,
                version="live-catalog",
                manifest_json=manifest_json,
            )
        else:
            version.manifest_json = manifest_json
        session.add(version)
        action_refs.append(f"{_TRACKBOOTH_PLUGIN_SLUG}.{action_key}")
        action_keys.add(action_key)
        operation_ids.append(operation_id)
    if prune_missing:
        stale_rows = session.exec(
            select(Action).where(
                col(Action.plugin_id) == plugin.id,
                col(Action.key).like(f"{_RUNTIME_ACTION_KEY_PREFIX}{scope_key}.%"),
            )
        ).all()
        for row in stale_rows:
            if row.key in action_keys:
                continue
            _retire_runtime_action(row, now=now)
            session.add(row)
            pruned += 1
    session.commit()
    return {
        "synced": len(action_refs),
        "created": created,
        "updated": updated,
        "pruned": pruned,
        "action_refs": action_refs,
        "operation_ids": operation_ids,
        "blocked_operation_ids": blocked_operation_ids,
        "inventory_scope_key": scope_key,
    }


def _runtime_action_manifest(
    *,
    action_key: str,
    detail: Mapping[str, Any],
    base_url: str,
    inventory_scope: Mapping[str, Any],
    inventory_scope_key: str,
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
        "risk_level": "read" if method == "GET" else "write",
        "input_schema": _runtime_input_schema(detail),
        "output_schema": _runtime_output_schema(detail),
        "config": {
            "schema_version": "stackos.action.v1",
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
        "path_params": [
            {"name": str(item)}
            for item in path_param_names
            if isinstance(item, str)
        ],
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


def _substitute_path_params(path: str, raw_params: Any) -> str:
    params = raw_params if isinstance(raw_params, Mapping) else {}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if not name or name not in params or params[name] in {None, ""}:
            raise ValidationError(f"missing Trackbooth path parameter {name}")
        return quote(str(params[name]), safe="")

    resolved = _PATH_PARAM_RE.sub(replace, path)
    if not resolved.startswith("/"):
        resolved = f"/{resolved}"
    return resolved


def _field_to_schema(field: Mapping[str, Any]) -> JsonObject:
    raw_type = str(field.get("type") or "object")
    enum_values = _enum_values(field)
    schema: JsonObject
    if raw_type.endswith("[]"):
        schema = {"type": "array", "items": _simple_type_schema(raw_type[:-2])}
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


def _simple_type_schema(raw_type: str) -> JsonObject:
    normalized = raw_type.strip()
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


def _serialize_query(query: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    if not query:
        return []
    params: list[tuple[str, str]] = []
    for key, value in query.items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if item is not None:
                    params.append((str(key), _query_value(item)))
        else:
            params.append((str(key), _query_value(value)))
    return params


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping | list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _extract_catalog_items(body: Any) -> list[JsonObject]:
    if isinstance(body, list):
        raw_items = body
    elif isinstance(body, Mapping):
        raw_data = body.get("data")
        if isinstance(raw_data, list):
            raw_items = raw_data
        elif isinstance(raw_data, Mapping):
            raw_items = raw_data.get("endpoints") or raw_data.get("tools") or []
        else:
            raw_items = body.get("endpoints") or body.get("tools") or []
    else:
        raw_items = []
    items = [
        dict(item)
        for item in raw_items
        if isinstance(item, Mapping) and item.get("operation_id")
    ]
    return items


def _extract_operation_detail(body: Any) -> JsonObject:
    if isinstance(body, Mapping):
        raw_data = body.get("data")
        if isinstance(raw_data, Mapping):
            return dict(raw_data)
        raw_operation = body.get("operation") or body.get("endpoint") or body.get("tool")
        if isinstance(raw_operation, Mapping):
            return dict(raw_operation)
        if body.get("operation_id"):
            return dict(body)
    raise ValidationError("Trackbooth operation detail response did not include an operation")


def _effective_acting_as_account(request: ActionConnectorRequest) -> str | None:
    return _optional_clean_str(request.provider_context_json.get("acting_as_account"))


def _runtime_inventory_input_issues(
    request: ActionConnectorRequest,
) -> list[ActionValidationIssue]:
    config = request.config_json
    if config.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
        return []
    if config.get("inventory_state") == "retired":
        return [
            issue(
                "$.action",
                "generated Trackbooth action is retired; rerun trackbooth.catalog.sync",
                "retired_action",
            )
        ]
    issues: list[ActionValidationIssue] = []
    expected_project_id = config.get("inventory_project_id")
    if (
        isinstance(expected_project_id, int)
        and request.project_id
        and expected_project_id != request.project_id
    ):
        issues.append(
            issue(
                "$.project_id",
                "generated Trackbooth action belongs to a different StackOS project",
                "scope_mismatch",
            )
        )
    return issues


def trackbooth_generated_action_visible_for_project(
    *,
    plugin_slug: str,
    config_json: Mapping[str, Any] | None,
    project_id: int | None,
) -> bool:
    """Return whether a Trackbooth action belongs in project discovery."""
    if plugin_slug != _TRACKBOOTH_PLUGIN_SLUG:
        return True
    if not isinstance(config_json, Mapping):
        return True
    if config_json.get("operation") in {"rest.read", "rest.write"}:
        return False
    if config_json.get("trackbooth_removed_action") is True:
        return False
    if config_json.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
        return True
    if config_json.get("inventory_state") != "active":
        return False
    if project_id is None:
        return False
    inventory_project_id = config_json.get("inventory_project_id")
    return isinstance(inventory_project_id, int) and inventory_project_id == project_id


def retire_removed_trackbooth_actions(
    *,
    session: Any,
    plugin_id: int,
    now: datetime,
) -> None:
    """Mark removed first-pass Trackbooth generic actions as non-executable."""
    for action_key in ("rest.read", "rest.write"):
        row = session.exec(
            select(Action).where(col(Action.plugin_id) == plugin_id, col(Action.key) == action_key)
        ).first()
        if row is None:
            continue
        config = dict(row.config_json or {})
        if (
            config.get("trackbooth_removed_action") is True
            and config.get("execution_mode") == "deferred.removed"
        ):
            continue
        config.pop("connector", None)
        config["execution_mode"] = "deferred.removed"
        config["deferred_reason"] = (
            "Trackbooth operation execution is exposed through generated actions from "
            "trackbooth.catalog.sync."
        )
        config["trackbooth_removed_action"] = True
        row.config_json = config
        row.updated_at = now
        session.add(row)


def _optional_clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _limit(payload: Mapping[str, Any]) -> int:
    raw = payload.get("limit")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return min(raw, 100)
    return 25


__all__ = [
    "TrackboothActionConnector",
    "TrackboothAssets",
    "retire_removed_trackbooth_actions",
    "trackbooth_generated_action_visible_for_project",
]
