"""Pure validation policy for stored execution contexts."""

from __future__ import annotations

import re
from typing import Any

from stackos.actions.manifest import ExecutableActionManifest
from stackos.actions.repository.utils import _schema_issues
from stackos.artifacts import redact_secrets
from stackos.repositories.base import ValidationError
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

_VALID_STORED_OUTPUT_MODES = {"inline", "file_if_large", "always_file"}
_VALID_REQUEST_BUDGET_FIELDS = {
    "max_parallel",
    "max_calls",
    "max_calls_per_run",
    "window_seconds",
    "notes",
}


def derive_execution_context_scope(
    *,
    action_manifest: ExecutableActionManifest | None,
    plugin_slug: str | None,
    provider_key: str | None,
) -> tuple[str | None, str | None]:
    """Resolve stored context scope without performing repository lookups."""

    if action_manifest is None:
        return plugin_slug, provider_key
    if plugin_slug is not None and plugin_slug != action_manifest.plugin_slug:
        raise ValidationError(
            "plugin_slug does not match action_ref",
            data={"plugin_slug": plugin_slug, "action_ref": action_manifest.action_ref},
        )
    if provider_key is not None and provider_key != action_manifest.provider_key:
        raise ValidationError(
            "provider_key does not match action_ref",
            data={"provider_key": provider_key, "action_ref": action_manifest.action_ref},
        )
    return action_manifest.plugin_slug, action_manifest.provider_key


def validate_execution_context_provider(
    action_manifest: ExecutableActionManifest | None,
    provider_context: dict[str, Any],
) -> None:
    """Validate stored provider context against its selected action contract."""

    issues = execution_context_provider_issues(action_manifest, provider_context)
    if issues:
        raise ValidationError(
            "provider context is invalid",
            data={"issues": issues},
        )


def execution_context_provider_issues(
    action_manifest: ExecutableActionManifest | None,
    provider_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return public validation issues for stored provider context."""

    if not provider_context:
        return []
    if action_manifest is None:
        return [
            {
                "path": "$.provider_context_json",
                "code": "provider_context_schema_required",
                "message": "provider context requires an action_ref with provider_context_schema",
            }
        ]
    if not action_manifest.provider_context_schema_json:
        return [
            {
                "path": "$.provider_context_json",
                "code": "provider_context_not_allowed",
                "message": "provider context is not allowed for this action",
                "data": {"action_ref": action_manifest.action_ref},
            }
        ]
    return [
        issue.model_dump(mode="json")
        for issue in _schema_issues(
            action_manifest.provider_context_schema_json,
            provider_context,
            path="$.provider_context_json",
        )
    ]


def clean_execution_context_object(
    value: dict[str, Any] | None,
    field: str,
) -> dict[str, Any]:
    """Validate, reject secrets from, and redact one stored context object."""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")
    _reject_execution_context_secrets(value, field=field)
    return redact_secrets(dict(value))


def clean_optional_execution_context_object(
    value: dict[str, Any] | None,
    field: str,
) -> dict[str, Any] | None:
    """Clean an optional stored context object while preserving ``None``."""

    if value is None:
        return None
    return clean_execution_context_object(value, field)


def clean_execution_context_string_list(
    value: list[str] | None,
    field: str,
) -> list[str]:
    """Validate and stably deduplicate one stored context string list."""

    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError(f"{field} must be a string array")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def validate_execution_context_locked_fields(fields: list[str]) -> None:
    """Require locked provider-context fields to be top-level keys."""

    unsupported = [field for field in fields if _locked_field_key(field) is None]
    if unsupported:
        raise ValidationError(
            "provider_context_locked_fields_json supports top-level provider context fields only",
            data={"fields": unsupported},
        )


def validate_stored_execution_context_output_policy(policy: dict[str, Any]) -> None:
    """Validate durable context defaults, distinct from per-action runtime policy."""

    if not policy:
        return
    allowed = {"mode", "max_inline_bytes", "semantic_name", "content_type"}
    unknown = sorted(set(policy) - allowed)
    if unknown:
        raise ValidationError("unsupported output_policy_json fields", data={"fields": unknown})
    mode = str(policy.get("mode") or "inline")
    if mode not in _VALID_STORED_OUTPUT_MODES:
        raise ValidationError(
            "invalid output policy mode",
            data={"mode": mode, "accepted": sorted(_VALID_STORED_OUTPUT_MODES)},
        )
    max_inline_bytes = policy.get("max_inline_bytes")
    if max_inline_bytes is not None and (
        not isinstance(max_inline_bytes, int)
        or isinstance(max_inline_bytes, bool)
        or max_inline_bytes < 1
    ):
        raise ValidationError("output_policy_json.max_inline_bytes must be a positive integer")
    for field in ("semantic_name", "content_type"):
        value = policy.get(field)
        if value is not None and not isinstance(value, str):
            raise ValidationError(f"output_policy_json.{field} must be a string")
    content_type = policy.get("content_type")
    if content_type is not None and content_type != "application/json":
        raise ValidationError(
            "output_policy_json.content_type must be application/json for file-backed outputs",
            data={"content_type": content_type},
        )


def validate_execution_context_request_budget(budget: dict[str, Any]) -> None:
    """Validate durable request-budget defaults stored on an execution context."""

    if not budget:
        return
    unknown = sorted(set(budget) - _VALID_REQUEST_BUDGET_FIELDS)
    if unknown:
        raise ValidationError(
            "unsupported request_budget_json fields",
            data={"fields": unknown},
        )
    for field in ("max_parallel", "max_calls", "max_calls_per_run", "window_seconds"):
        value = budget.get(field)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationError(f"request_budget_json.{field} must be a positive integer")
    notes = budget.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValidationError("request_budget_json.notes must be a string")


def _locked_field_key(value: str) -> str | None:
    field = value.strip()
    if not field:
        return None
    if field.startswith("$."):
        field = field[2:]
    if field.startswith("."):
        field = field[1:]
    if "." in field or "[" in field or "]" in field:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:-]*", field):
        return None
    return field


def _reject_execution_context_secrets(value: dict[str, Any], *, field: str) -> None:
    secret_paths = find_run_plan_secret_paths(value)
    if secret_paths:
        raise ValidationError(
            f"{field} must not contain secrets; use opaque credential_ref values",
            data={"paths": secret_paths[:8]},
        )


__all__ = [
    "clean_execution_context_object",
    "clean_execution_context_string_list",
    "clean_optional_execution_context_object",
    "derive_execution_context_scope",
    "execution_context_provider_issues",
    "validate_execution_context_locked_fields",
    "validate_execution_context_provider",
    "validate_execution_context_request_budget",
    "validate_stored_execution_context_output_policy",
]
