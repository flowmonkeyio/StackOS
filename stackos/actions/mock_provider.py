"""Local mock provider connector for end-to-end StackOS integration tests."""

from __future__ import annotations

from typing import Any

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.repositories.base import ValidationError

_SCENARIOS = {
    "success",
    "partial_success",
    "provider_error",
    "invalid_credentials",
    "rate_limit",
    "timeout",
}


def _issue(path: str, message: str, code: str = "validation_error") -> ActionValidationIssue:
    return ActionValidationIssue(path=path, message=message, code=code)


def _scenario(payload: dict[str, Any]) -> str:
    raw = payload.get("scenario", "success")
    return raw if isinstance(raw, str) else ""


def _cost_cents(payload: dict[str, Any]) -> int:
    raw = payload.get("cost_cents", 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return max(0, raw)


class MockProviderActionConnector:
    """Decision-free fake provider that still requires real StackOS auth/grants."""

    key = "mock-provider"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        if request.operation != "echo":
            issues.append(
                _issue(
                    "$.operation",
                    f"unsupported operation {request.operation!r}",
                    "enum_mismatch",
                )
            )
        message = request.input_json.get("message")
        if not isinstance(message, str) or not message.strip():
            issues.append(_issue("$.message", "message is required", "required"))
        scenario = _scenario(request.input_json)
        if scenario not in _SCENARIOS:
            issues.append(
                _issue(
                    "$.scenario",
                    f"scenario must be one of {sorted(_SCENARIOS)}",
                    "enum_mismatch",
                )
            )
        echo = request.input_json.get("echo")
        if echo is not None and not isinstance(echo, dict):
            issues.append(_issue("$.echo", "echo must be an object", "type_mismatch"))
        raw_cost = request.input_json.get("cost_cents", 0)
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, int) or raw_cost < 0:
            issues.append(_issue("$.cost_cents", "cost_cents must be a non-negative integer"))
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        return _cost_cents(request.input_json)

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("mock-provider action requires a resolved credential")
        secret = request.credential.secret_payload.decode("utf-8").strip()
        if not secret:
            raise ValidationError("mock-provider credential payload is empty")

        scenario = _scenario(request.input_json)
        if scenario == "invalid_credentials":
            raise ValidationError("mock provider rejected credential authorization=Bearer bad")
        if scenario == "rate_limit":
            raise ActionConnectorError(
                "mock provider rate limited request",
                provider_status_code=429,
                provider_error={
                    "code": "mock_rate_limit",
                    "message": "mock provider rate limited request",
                    "retry_after_ms": 60000,
                },
                metadata_json={"vendor": "mock-provider", "scenario": scenario},
            )
        if scenario == "timeout":
            raise TimeoutError("mock provider timeout after 30s")
        if scenario == "provider_error":
            raise ActionConnectorError(
                "mock provider returned status 500",
                provider_status_code=500,
                provider_error={
                    "code": "mock_provider_error",
                    "message": "mock provider returned status 500",
                },
                metadata_json={"vendor": "mock-provider", "scenario": scenario},
            )

        status = "partial_success" if scenario == "partial_success" else "success"
        return ActionConnectorResult(
            output_json={
                "provider": "mock-provider",
                "operation": request.operation,
                "status": status,
                "message": request.input_json["message"],
                "echo": request.input_json.get("echo", {}),
                "credential_ref": request.credential.credential_ref,
                "leak_check": {
                    "authorization": f"Bearer {secret}",
                    "api_key": secret,
                },
            },
            metadata_json={
                "vendor": "mock-provider",
                "scenario": scenario,
                "access_token": secret,
            },
            cost_cents=_cost_cents(request.input_json),
        )


__all__ = ["MockProviderActionConnector"]
