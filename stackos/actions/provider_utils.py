"""Shared no-secret helpers for provider-specific action connectors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

JsonObject = dict[str, Any]


def issue(path: str, message: str, code: str = "validation_error") -> ActionValidationIssue:
    return ActionValidationIssue(path=path, message=message, code=code)


def unknown_operation(request: ActionConnectorRequest) -> list[ActionValidationIssue]:
    return [issue("$.operation", f"unsupported operation {request.operation!r}", "enum_mismatch")]


def required_str(payload: Mapping[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))


def optional_str(payload: Mapping[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        issues.append(issue(f"$.{key}", f"{key} must be a string", "type_error"))


def dict_field(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    required: bool = False,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, dict):
        issues.append(issue(f"$.{key}", f"{key} must be an object", "type_error"))


def list_field(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    required: bool = False,
    max_items: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, list):
        issues.append(issue(f"$.{key}", f"{key} must be an array", "type_error"))
        return
    if max_items is not None and len(value) > max_items:
        issues.append(issue(f"$.{key}", f"{key} must contain at most {max_items} items", "length"))


def int_range(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: int,
    maximum: int,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        issues.append(
            issue(f"$.{key}", f"{key} must be an integer between {minimum} and {maximum}", "range")
        )


def credential_payload(request: ActionConnectorRequest) -> JsonObject:
    if request.credential is None:
        raise ValidationError(f"{request.provider_key or request.action_ref} requires a credential")
    text = request.credential.secret_payload.decode("utf-8").strip()
    if not text:
        raise ValidationError(f"{request.provider_key or request.action_ref} credential is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"value": text}
    if isinstance(parsed, dict):
        return parsed
    return {"value": text}


def credential_config(request: ActionConnectorRequest) -> JsonObject:
    if request.credential is None or request.credential.config_json is None:
        return {}
    return dict(request.credential.config_json)


def credential_value(request: ActionConnectorRequest, *keys: str) -> str:
    payload = credential_payload(request)
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    value = payload.get("value")
    if value is not None and str(value).strip():
        return str(value).strip()
    raise ValidationError(f"{request.provider_key or request.action_ref} credential missing token")


def bearer_headers(request: ActionConnectorRequest, *keys: str) -> dict[str, str]:
    token_keys = keys or ("access_token", "api_key", "bearer_token", "token")
    return {
        "Authorization": f"Bearer {credential_value(request, *token_keys)}",
        "Content-Type": "application/json",
    }


def connector_error_from_integration(
    exc: IntegrationDownError | RateLimitedError,
    *,
    provider: str,
    operation: str,
) -> ActionConnectorError:
    """Translate integration-wrapper failures into action audit output."""
    data = exc.data if isinstance(exc.data, dict) else {}
    status = data.get("status")
    try:
        provider_status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        provider_status_code = None
    provider_error = data.get("provider_error")
    if provider_error is None:
        provider_error = {"message": redact_secret_text(exc.detail)}
    metadata: JsonObject = {"vendor": provider, "operation": operation}
    if provider_status_code is not None:
        metadata["status_code"] = provider_status_code
    retry_after = data.get("retry_after")
    if retry_after is not None:
        metadata["retry_after"] = retry_after
    return ActionConnectorError(
        redact_secret_text(exc.detail),
        provider_status_code=provider_status_code,
        provider_error=redact_secrets(provider_error),
        metadata_json=metadata,
    )


def config_str(
    request: ActionConnectorRequest,
    key: str,
    *,
    default: str | None = None,
    required: bool = False,
) -> str | None:
    value = credential_config(request).get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        target = request.provider_key or request.action_ref
        raise ValidationError(f"{target} credential missing {key}")
    return default


def resolve_ref(request: ActionConnectorRequest, value: Any, *map_keys: str) -> Any:
    if not isinstance(value, str) or not value:
        return value
    config = credential_config(request)
    for map_key in map_keys:
        mapping = config.get(map_key)
        if isinstance(mapping, Mapping):
            mapped = mapping.get(value)
            if mapped is not None:
                return mapped
    refs = config.get("refs")
    if isinstance(refs, Mapping):
        mapped = refs.get(value)
        if mapped is not None:
            return mapped
    return value


def nested_value(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            return None
    return current


def clean_customer_id(value: Any) -> str:
    return str(value).replace("-", "").strip()


def q(value: Any) -> str:
    return quote(str(value), safe="")


def result(
    *,
    provider: str,
    operation: str,
    status_code: int,
    body: Any,
    headers: Mapping[str, str] | None = None,
    metadata: JsonObject | None = None,
) -> ActionConnectorResult:
    meta: JsonObject = {"vendor": provider, "operation": operation, "status_code": status_code}
    if headers is not None:
        request_id = (
            headers.get("request-id")
            or headers.get("x-hubspot-correlation-id")
            or headers.get("google-ads-request-id")
            or headers.get("x-request-id")
            or headers.get("fbtrace_id")
        )
        if request_id:
            meta["request_id"] = request_id
    if metadata:
        meta.update(metadata)
    return ActionConnectorResult(
        output_json={
            "provider": provider,
            "operation": operation,
            "status_code": status_code,
            "body": body,
        },
        metadata_json=meta,
    )


__all__ = [
    "JsonObject",
    "bearer_headers",
    "clean_customer_id",
    "config_str",
    "credential_config",
    "credential_payload",
    "credential_value",
    "dict_field",
    "int_range",
    "issue",
    "list_field",
    "nested_value",
    "optional_str",
    "q",
    "required_str",
    "resolve_ref",
    "result",
    "unknown_operation",
]
