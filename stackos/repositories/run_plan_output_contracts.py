"""Pure validation for frozen run-plan step output contracts."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from stackos.db.models import RunPlanStep
from stackos.repositories.base import ValidationError


def _output_schema(contract: dict[str, Any]) -> dict[str, Any]:
    raw_schema = contract.get("schema_json")
    schema = dict(raw_schema) if isinstance(raw_schema, dict) else {}
    output_type = contract.get("type")
    if "type" not in schema and isinstance(output_type, str) and output_type:
        schema["type"] = output_type
    return schema


def _schema_error_path(output_key: str, error: Any) -> str:
    path = f"$.{output_key}"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    if (
        error.validator == "required"
        and isinstance(error.instance, dict)
        and isinstance(error.validator_value, list)
    ):
        missing = [
            key
            for key in error.validator_value
            if isinstance(key, str) and key not in error.instance
        ]
        if len(missing) == 1:
            path += f".{missing[0]}"
    return path


def _schema_error_message(error: Any) -> str:
    validator = str(error.validator or "schema")
    if validator == "required":
        return "required field is missing"
    if validator == "type":
        return f"expected type {error.validator_value!r}"
    if validator == "enum":
        return "value is not one of the allowed enum values"
    if validator == "const":
        return "value does not match the required constant"
    return f"value does not satisfy {validator}"


def validate_step_expected_outputs(
    step: RunPlanStep,
    result_json: dict[str, Any] | None,
) -> None:
    """Validate a step result against its frozen expected-output contracts."""

    contracts = step.expected_outputs_json or {}
    if not contracts:
        return
    result = result_json or {}
    issues: list[dict[str, str]] = []
    required_output_keys: list[str] = []
    for output_key, raw_contract in contracts.items():
        if not isinstance(output_key, str) or not isinstance(raw_contract, dict):
            issues.append(
                {
                    "path": f"$.{output_key}",
                    "code": "invalid_output_contract",
                    "message": "frozen output contract is invalid",
                }
            )
            continue
        required = raw_contract.get("required") is True
        if required:
            required_output_keys.append(output_key)
        if output_key not in result:
            if required:
                issues.append(
                    {
                        "path": f"$.{output_key}",
                        "code": "required_output",
                        "message": "required output is missing",
                    }
                )
            continue
        schema = _output_schema(raw_contract)
        if not schema:
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            issues.append(
                {
                    "path": f"$.{output_key}",
                    "code": "invalid_output_schema",
                    "message": "frozen output schema is invalid",
                }
            )
            continue
        validator = Draft202012Validator(schema)
        schema_errors = sorted(
            validator.iter_errors(result[output_key]),
            key=lambda item: (_schema_error_path(output_key, item), str(item.validator)),
        )
        for schema_error in schema_errors:
            issues.append(
                {
                    "path": _schema_error_path(output_key, schema_error),
                    "code": f"schema_{schema_error.validator or 'validation'}",
                    "message": _schema_error_message(schema_error),
                }
            )
            if len(issues) >= 20:
                break
        if len(issues) >= 20:
            break
    if issues:
        raise ValidationError(
            "run plan step result does not satisfy expected outputs",
            data={
                "run_plan_id": step.run_plan_id,
                "step_id": step.step_id,
                "issues": issues,
                "required_output_keys": sorted(required_output_keys),
                "next_operations": ["runPlan.getStep", "runPlan.recordStep"],
            },
        )


__all__ = ["validate_step_expected_outputs"]
