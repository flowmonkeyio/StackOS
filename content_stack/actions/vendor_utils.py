"""Shared helpers for provider-specific action connectors."""

from __future__ import annotations

import math
from typing import Any

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.repositories.base import ValidationError


def issue(path: str, message: str, code: str = "validation_error") -> ActionValidationIssue:
    return ActionValidationIssue(path=path, message=message, code=code)


def required_str(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))


def optional_str(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        issues.append(issue(f"$.{key}", f"{key} must be a string", "type_mismatch"))


def int_range(
    payload: dict[str, Any],
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
            issue(
                f"$.{key}",
                f"{key} must be an integer between {minimum} and {maximum}",
                "range",
            )
        )


def float_range(
    payload: dict[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: float,
    maximum: float,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        issues.append(
            issue(
                f"$.{key}",
                f"{key} must be a number between {minimum:g} and {maximum:g}",
                "range",
            )
        )


def bool_field(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        issues.append(issue(f"$.{key}", f"{key} must be a boolean", "type_mismatch"))


def str_list(
    payload: dict[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    required: bool = False,
    length: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        issues.append(issue(f"$.{key}", f"{key} must be an array of strings", "type_mismatch"))
        return
    if length is not None and len(value) != length:
        issues.append(issue(f"$.{key}", f"{key} must contain {length} items", "length"))


def dict_field(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, dict):
        issues.append(issue(f"$.{key}", f"{key} must be an object", "type_mismatch"))


def required_dict(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    if key not in payload:
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    dict_field(payload, key, issues)


def unknown_operation(request: ActionConnectorRequest) -> list[ActionValidationIssue]:
    return [issue("$.operation", f"unsupported operation {request.operation!r}", "enum_mismatch")]


def result(vendor: str, operation: str, result_data: Any, cost_usd: float) -> ActionConnectorResult:
    output = result_data if isinstance(result_data, dict) else {"data": result_data}
    return ActionConnectorResult(
        output_json=output,
        metadata_json={"vendor": vendor, "operation": operation},
        cost_cents=cost_cents(cost_usd),
    )


def cost_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, math.ceil(cost_usd * 100))


def credential_payload(request: ActionConnectorRequest, *, required: bool = True) -> bytes:
    if request.credential is None:
        if required:
            target = request.provider_key or request.action_ref
            raise ValidationError(f"{target} requires a credential")
        return b""
    return request.credential.plaintext_payload


def credential_config_str(
    request: ActionConnectorRequest,
    *keys: str,
    label: str,
) -> str:
    config = request.credential.config_json if request.credential is not None else None
    target = request.provider_key or request.action_ref
    if not isinstance(config, dict):
        raise ValidationError(f"{target} credential missing {label}")
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValidationError(f"{target} credential missing {label}")


__all__ = [
    "bool_field",
    "cost_cents",
    "credential_config_str",
    "credential_payload",
    "dict_field",
    "float_range",
    "int_range",
    "issue",
    "optional_str",
    "required_dict",
    "required_str",
    "result",
    "str_list",
    "unknown_operation",
]
