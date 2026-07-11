"""Trackbooth Agent API REST action connector public facade."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from time import perf_counter
from typing import Any
from urllib.parse import quote

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
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
from stackos.actions.trackbooth_assets import (
    TrackboothAssets,
    _detail_from_endpoint,
    _path_param_names,
    _schema_descriptor,
)
from stackos.actions.trackbooth_contract import (
    BLOCKED_OPERATION_MESSAGE as _BLOCKED_OPERATION_MESSAGE,
)
from stackos.actions.trackbooth_contract import (
    READ_METHODS as _READ_METHODS,
)
from stackos.actions.trackbooth_contract import (
    RUNTIME_INVENTORY_SOURCE as _RUNTIME_INVENTORY_SOURCE,
)
from stackos.actions.trackbooth_contract import (
    WRITE_METHODS as _WRITE_METHODS,
)
from stackos.actions.trackbooth_contract import (
    JsonObject,
)
from stackos.actions.trackbooth_inventory import (
    _requested_operation_ids,
    _runtime_inventory_input_issues,
    _runtime_inventory_scope,
    _runtime_inventory_scope_key,
    _select_sync_items,
    _sync_limit,
    _upsert_runtime_actions,
    retire_removed_trackbooth_actions,
    retire_superseded_trackbooth_inventory_scopes,
)
from stackos.actions.trackbooth_schema import (
    _configured_endpoint,
    _dedupe_issues,
    _missing_required_body_issues,
    _operation_accepts_body,
    _schema_value_issues,
)
from stackos.actions.trackbooth_transport import (
    _effective_acting_as_account,
    _elapsed_ms,
    _extract_catalog_export,
    _extract_catalog_items,
    _extract_operation_detail,
    _limit,
    _serialize_query,
    _substitute_path_params,
)
from stackos.artifacts import redact_secret_text
from stackos.integrations.trackbooth import (
    TRACKBOOTH_DEFAULT_API_BASE_URL,
    TrackboothConfigError,
    normalize_trackbooth_base_url,
    parse_trackbooth_api_key,
    trackbooth_headers,
)
from stackos.repositories.base import ValidationError


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
            source_start = perf_counter()
            live_export = await self._live_catalog_export(base_url=base_url, headers=headers)
            return await self._sync_catalog_actions(
                request=request,
                base_url=base_url,
                live_items=live_export["endpoints"],
                source_fetch_ms=_elapsed_ms(source_start),
                endpoint_count=live_export.get("endpoint_count"),
                catalog_hash=live_export.get("catalog_hash"),
                catalog_version=live_export.get("version"),
                catalog_generated_at=live_export.get("generated_at"),
            )

        if request.operation == "catalog.search":
            live_items = await self._live_catalog(base_url=base_url, headers=headers)
            filtered = self._assets.filter_catalog(live_items, request.input_json)
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

        configured_operation_id = self._configured_operation_id(request)
        if configured_operation_id is not None:
            return await self._execute_rest(
                request=request,
                base_url=base_url,
                headers=headers,
                operation_id_override=configured_operation_id,
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
        path_params_raw = payload.get("path_params")
        path_params: Mapping[str, Any] = (
            path_params_raw if isinstance(path_params_raw, Mapping) else {}
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
        body_requires_validation = request.operation == "rest.write" or method in _WRITE_METHODS
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
        live_items: Sequence[Mapping[str, Any]],
        source_fetch_ms: int | None = None,
        endpoint_count: Any = None,
        catalog_hash: Any = None,
        catalog_version: Any = None,
        catalog_generated_at: Any = None,
    ) -> ActionConnectorResult:
        if request.session is None:
            raise ValidationError("Trackbooth catalog sync requires a database session")
        requested_ids = _requested_operation_ids(request.input_json)
        limit = _sync_limit(request.input_json)
        selected_items = _select_sync_items(live_items, requested_ids=requested_ids, limit=limit)
        selected_ids = {str(item.get("operation_id")) for item in selected_items}
        missing_ids = sorted(requested_ids - selected_ids)

        started = perf_counter()
        details: list[JsonObject] = []
        warnings: list[JsonObject] = []
        for item in selected_items:
            operation_id = str(item.get("operation_id") or "").strip()
            if not operation_id:
                continue
            endpoint: JsonObject = dict(item)
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
            catalog_hash=catalog_hash if isinstance(catalog_hash, str) else None,
            prune_missing=not requested_ids and limit is None,
        )
        write_ms = sync_result["write_ms"]
        total_ms = int((perf_counter() - started) * 1000) + (
            source_fetch_ms if isinstance(source_fetch_ms, int) else 0
        )
        return ActionConnectorResult(
            output_json={
                "synced": sync_result["synced"],
                "created": sync_result["created"],
                "updated": sync_result["updated"],
                "skipped": sync_result["skipped"],
                "pruned": sync_result["pruned"],
                "retired": sync_result["retired"],
                "action_refs": sync_result["action_refs"],
                "operation_ids": sync_result["operation_ids"],
                "blocked_operation_ids": sync_result["blocked_operation_ids"],
                "missing_operation_ids": missing_ids,
                "warnings": warnings,
                "api_base_url": base_url,
                "inventory_scope_key": sync_result["inventory_scope_key"],
                "source_endpoint": "/api/agent-api/catalog/export",
                "source_fetch_ms": source_fetch_ms,
                "write_ms": write_ms,
                "total_ms": total_ms,
                "endpoint_count": endpoint_count
                if isinstance(endpoint_count, int)
                else len(live_items),
                "detail_fetch_count": 0,
                "catalog_hash": catalog_hash if isinstance(catalog_hash, str) else None,
                "catalog_version": catalog_version if isinstance(catalog_version, int) else None,
                "catalog_generated_at": catalog_generated_at
                if isinstance(catalog_generated_at, str)
                else None,
                "manual_sync": True,
            },
            metadata_json={
                "vendor": "trackbooth",
                "operation": request.operation,
                "synced": sync_result["synced"],
                "api_base_url": base_url,
                "catalog_hash": catalog_hash if isinstance(catalog_hash, str) else None,
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

    async def _live_catalog_export(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str],
    ) -> JsonObject:
        status_code, body = await self._request_json(
            method="GET",
            url=f"{base_url}/api/agent-api/catalog/export",
            headers=headers,
            params=[],
        )
        del status_code
        return _extract_catalog_export(body)

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
            try:
                provider_error: Any = response.json()
            except ValueError:
                provider_error = {"message": response.text[:1000]}
            raise ActionConnectorError(
                f"Trackbooth returned status {response.status_code}",
                provider_status_code=response.status_code,
                provider_error=provider_error,
                metadata_json={
                    "vendor": "trackbooth",
                    "status_code": response.status_code,
                },
            )
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body


__all__ = [
    "TrackboothActionConnector",
    "TrackboothAssets",
    "retire_removed_trackbooth_actions",
    "retire_superseded_trackbooth_inventory_scopes",
]
