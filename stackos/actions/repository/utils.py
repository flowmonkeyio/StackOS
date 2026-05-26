"""Shared action repository helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from stackos.actions.connectors import ActionValidationIssue
from stackos.artifacts import redact_secret_text


def utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


_TEXT_REDACT_KEYS = frozenset(
    {
        "auth_ref",
        "credential_ref",
    }
)
_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _TEXT_REDACT_KEYS or normalized.endswith("_credential_ref"):
        return False
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact_for_audit(value: Any) -> Any:
    """Return a deep-redacted copy for agent-visible output and audit."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            redacted[key] = "[redacted]" if _is_secret_key(key) else _redact_for_audit(raw_value)
        return redacted
    if isinstance(value, list):
        return [_redact_for_audit(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_for_audit(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _schema_type_matches(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _schema_issues(
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: str = "$",
) -> list[ActionValidationIssue]:
    """Small JSON-schema validator for the manifest subset StackOS owns.

    It intentionally handles the common schema fields in current manifests
    without adding executor decisions or provider logic.
    """
    issues: list[ActionValidationIssue] = []
    raw_type = schema.get("type")
    expected_types: list[str] = []
    if isinstance(raw_type, str):
        expected_types = [raw_type]
    elif isinstance(raw_type, list):
        expected_types = [str(item) for item in raw_type if isinstance(item, str)]
    if expected_types and not any(
        _schema_type_matches(expected, value) for expected in expected_types
    ):
        issues.append(
            ActionValidationIssue(
                path=path,
                message=f"expected {' or '.join(expected_types)}, got {_json_type_name(value)}",
                code="type_mismatch",
            )
        )
        return issues

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        issues.append(
            ActionValidationIssue(
                path=path,
                message="value is not one of the allowed enum values",
                code="enum_mismatch",
            )
        )

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    issues.append(
                        ActionValidationIssue(
                            path=f"{path}.{key}",
                            message="required field is missing",
                            code="required",
                        )
                    )
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    issues.extend(_schema_issues(child_schema, value[key], path=f"{path}.{key}"))
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            allowed = {str(key) for key in properties}
            for key in value:
                if str(key) not in allowed:
                    issues.append(
                        ActionValidationIssue(
                            path=f"{path}.{key}",
                            message="additional property is not allowed",
                            code="additional_property",
                        )
                    )

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(_schema_issues(item_schema, item, path=f"{path}[{index}]"))
    return issues
