"""Pure composition and override policy for project workflow extensions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from stackos.workflows.template_schema import (
    WorkflowTemplateIssue,
    WorkflowTemplateSpec,
    WorkflowTemplateValidationOut,
    validate_workflow_template_obj,
)

EXTENSION_JSON_FIELD_DEFAULTS: dict[str, Any] = {
    "input_defaults_json": {},
    "selected_context_json": {},
    "required_input_keys_json": [],
    "guardrails_json": {},
    "step_overrides_json": {},
    "template_overrides_json": {},
    "metadata_json": {},
}
EXTENSION_JSON_FIELDS = frozenset(EXTENSION_JSON_FIELD_DEFAULTS)
EXTENSION_UPDATE_MODES = frozenset({"merge", "replace"})

_TEMPLATE_OVERRIDE_ALIASES = {
    "metadata": "metadata_json",
    "extensions": "extensions_json",
    "ui": "ui_json",
}


def extension_update_issues(
    *,
    update_mode: str,
    clear_fields_json: list[str] | None,
) -> list[WorkflowTemplateIssue]:
    """Return validation issues for extension update/clear semantics."""
    issues: list[WorkflowTemplateIssue] = []
    if update_mode not in EXTENSION_UPDATE_MODES:
        issues.append(
            WorkflowTemplateIssue(
                path="update_mode",
                code="invalid_update_mode",
                message=(f"update_mode must be one of {', '.join(sorted(EXTENSION_UPDATE_MODES))}"),
            )
        )
    if update_mode == "replace" and clear_fields_json:
        issues.append(
            WorkflowTemplateIssue(
                path="clear_fields_json",
                code="clear_fields_requires_merge",
                message="clear_fields_json is only valid with update_mode='merge'",
            )
        )
    invalid_clear_fields = [
        field
        for field in list(dict.fromkeys(clear_fields_json or []))
        if field not in EXTENSION_JSON_FIELDS
    ]
    if invalid_clear_fields:
        issues.append(
            WorkflowTemplateIssue(
                path="clear_fields_json",
                code="unknown_extension_field",
                message=(
                    "clear_fields_json contains unknown extension fields: "
                    f"{', '.join(invalid_clear_fields)}"
                ),
            )
        )
    return issues


def compose_extension_update_values(
    *,
    current: dict[str, Any],
    enabled: bool | None,
    provided_json: dict[str, Any],
    update_mode: str,
    clear_fields_json: list[str] | None,
) -> dict[str, Any]:
    """Compose the desired extension values without reading or writing state."""
    if update_mode == "replace":
        return {
            "enabled": True if enabled is None else enabled,
            **{
                field: deepcopy(provided_json[field])
                if field in provided_json
                else deepcopy(default)
                for field, default in EXTENSION_JSON_FIELD_DEFAULTS.items()
            },
        }
    desired = deepcopy(current)
    if enabled is not None:
        desired["enabled"] = enabled
    for field, value in provided_json.items():
        desired[field] = deepcopy(value)
    for field in dict.fromkeys(clear_fields_json or []):
        desired[field] = deepcopy(EXTENSION_JSON_FIELD_DEFAULTS[field])
    return desired


def template_override_data(
    spec: WorkflowTemplateSpec,
    template_overrides_json: dict[str, Any],
) -> dict[str, Any]:
    """Layer atomic top-level overrides onto a serialized workflow template."""
    data = spec.model_dump(mode="json")
    for key, value in template_overrides_json.items():
        target_key = _TEMPLATE_OVERRIDE_ALIASES.get(key, key)
        if target_key != key and target_key in data:
            data.pop(key, None)
        data[target_key] = value
    return data


def validate_template_overrides(
    spec: WorkflowTemplateSpec,
    *,
    workflow_key: str,
    template_overrides_json: dict[str, Any],
) -> WorkflowTemplateValidationOut:
    """Validate overrides and preserve extension-scoped issue paths."""
    validation = validate_workflow_template_obj(
        template_override_data(spec, template_overrides_json)
    )
    if not validation.valid or validation.template is None:

        def _issue_path(issue: WorkflowTemplateIssue) -> str:
            return (
                "template_overrides_json"
                if issue.path == "$"
                else f"template_overrides_json.{issue.path}"
            )

        return WorkflowTemplateValidationOut(
            valid=False,
            errors=[
                WorkflowTemplateIssue(
                    path=_issue_path(issue),
                    code=issue.code,
                    message=issue.message,
                )
                for issue in validation.errors
            ],
            warnings=validation.warnings,
        )
    if validation.template.key != workflow_key:
        return WorkflowTemplateValidationOut(
            valid=False,
            errors=[
                WorkflowTemplateIssue(
                    path="template_overrides_json.key",
                    code="workflow_key_mismatch",
                    message=(
                        "template_overrides_json.key must match workflow_key; "
                        "use workflowTemplate.fork for a new workflow identity"
                    ),
                )
            ],
        )
    return validation


def apply_template_overrides(
    spec: WorkflowTemplateSpec,
    *,
    workflow_key: str,
    template_overrides_json: dict[str, Any],
) -> WorkflowTemplateSpec:
    """Apply validated workflow overrides or raise the legacy value error."""
    validation = validate_template_overrides(
        spec,
        workflow_key=workflow_key,
        template_overrides_json=template_overrides_json,
    )
    if not validation.valid or validation.template is None:
        messages = "; ".join(item.message for item in validation.errors) or "invalid override"
        raise ValueError(messages)
    return validation.template


__all__ = [
    "EXTENSION_JSON_FIELDS",
    "EXTENSION_JSON_FIELD_DEFAULTS",
    "EXTENSION_UPDATE_MODES",
    "apply_template_overrides",
    "compose_extension_update_values",
    "extension_update_issues",
    "template_override_data",
    "validate_template_overrides",
]
