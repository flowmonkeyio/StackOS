"""Ahrefs action connector."""

from __future__ import annotations

from dataclasses import replace

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.vendor_utils import (
    int_range,
    issue,
    optional_str,
    required_str,
    unknown_operation,
)
from stackos.repositories.base import ValidationError

AHREFS_ACTION_MAX_ROWS = 1000
AHREFS_DEFAULT_ROWS = 100
AHREFS_MODE_VALUES = ("exact", "prefix", "domain", "subdomains")
_AHREFS_OPERATIONS = {
    "competitor.keywords",
    "keywords_for_site",
    "backlink.research",
    "top_backlinks",
}
_AHREFS_PLAN_ROW_LIMITS = {
    "lite": 100,
    "standard": 250,
    "advanced": 500,
}
_AHREFS_NO_DIRECT_API_PLANS = {"starter", "free"}


class AhrefsActionConnector:
    """Decision-free adapter for Ahrefs SEO actions."""

    key = "ahrefs"

    def __init__(self, *, client=None, options=None) -> None:
        self._client = client
        self._options = options

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "competitor.keywords" | "keywords_for_site":
                required_str(payload, "target", issues)
                optional_str(payload, "country", issues)
                optional_str(payload, "date", issues)
                int_range(payload, "limit", issues, minimum=1, maximum=AHREFS_ACTION_MAX_ROWS)
            case "backlink.research" | "top_backlinks":
                required_str(payload, "target", issues)
                optional_str(payload, "mode", issues)
                if (
                    isinstance(payload.get("mode"), str)
                    and payload["mode"] not in AHREFS_MODE_VALUES
                ):
                    issues.append(
                        issue(
                            "$.mode",
                            "mode must be one of exact, prefix, domain, subdomains",
                            "enum_mismatch",
                        )
                    )
                int_range(payload, "limit", issues, minimum=1, maximum=AHREFS_ACTION_MAX_ROWS)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation not in _AHREFS_OPERATIONS:
            raise ValidationError(f"unsupported Ahrefs operation {request.operation!r}")
        from stackos_connectors import CallOptions, ConnectorClient
        from stackos_connectors.catalog import load_registry
        from stackos_connectors.connectors.ahrefs.integration import AhrefsIntegration

        from stackos.actions.package_bridge import PackageActionConnector
        from stackos.integrations._rate_limit import get_bucket

        if self._client is None:
            self._client = ConnectorClient(registry=load_registry("connectors/ahrefs/catalog.json"))
        options = self._options or CallOptions()
        limiter = (
            options.rate_limiter
            if options.rate_limiter is not None
            else get_bucket(
                project_id=request.project_id, kind="ahrefs", qps=AhrefsIntegration.default_qps
            )
        )
        # A single caller-owned transport is shared by admission and the paid call.
        from contextlib import nullcontext

        async with (
            nullcontext(options.http)
            if options.http is not None
            else httpx.AsyncClient(timeout=60.0)
        ) as http:
            adapter = PackageActionConnector(
                "ahrefs",
                client=self._client,
                options=replace(options, http=http, rate_limiter=limiter),
            )
            limits_result = await adapter.execute_native(
                replace(
                    request,
                    action_key="limits_and_usage",
                    operation="limits_and_usage",
                    input_json={},
                )
            )
            limits_info = _limits_info(limits_result.output_json)
            row_limit, row_limit_source = row_limit_for_subscription(
                limits_info.get("subscription")
            )
            requested_limit = int(request.input_json.get("limit", AHREFS_DEFAULT_ROWS))
            try:
                _enforce_plan_row_limit(
                    requested_limit=requested_limit,
                    row_limit=row_limit,
                    subscription=limits_info.get("subscription"),
                )
            except ValidationError as exc:
                raise ActionConnectorError(
                    exc.detail,
                    provider_error=exc.data,
                    output_json={"status": "failed", "data": exc.data},
                    metadata_json={
                        "vendor": "ahrefs",
                        "operation": request.operation,
                        "provider_executed": True,
                        "primary_request_executed": False,
                        "preflight_requests": ["limits_and_usage"],
                        "retry_safe": True,
                    },
                ) from None
            action_key = (
                "competitor.keywords"
                if request.operation in {"competitor.keywords", "keywords_for_site"}
                else "backlink.research"
            )
            try:
                call_result = await adapter.execute_native(
                    replace(request, action_key=action_key, operation=action_key)
                )
            except ActionConnectorError as exc:
                exc.metadata_json = {
                    **exc.metadata_json,
                    "provider_executed": True,
                    "preflight_requests": ["limits_and_usage"],
                }
                raise
        return ActionConnectorResult(
            output_json=call_result.output_json,
            metadata_json=_metadata(
                request.operation,
                limits_info=limits_info,
                row_limit=row_limit,
                row_limit_source=row_limit_source,
                api_units=(call_result.metadata_json or {}).get("api_units"),
            ),
            cost_cents=0,
        )


def _subscription_key(subscription: object) -> str | None:
    if not isinstance(subscription, str):
        return None
    normalized = subscription.strip().lower()
    if not normalized:
        return None
    for key in ("enterprise", "advanced", "standard", "lite", "starter", "free"):
        if key in normalized:
            return key
    return normalized


def row_limit_for_subscription(subscription: object) -> tuple[int | None, str]:
    """Return the documented direct-API row cap for an Ahrefs subscription."""
    key = _subscription_key(subscription)
    if key == "enterprise":
        return None, "enterprise_unlimited"
    if key in _AHREFS_PLAN_ROW_LIMITS:
        return _AHREFS_PLAN_ROW_LIMITS[key], "documented_plan_cap"
    if key in _AHREFS_NO_DIRECT_API_PLANS:
        return 0, "no_direct_api"
    return AHREFS_DEFAULT_ROWS, "unknown_subscription_safe_default"


def _effective_row_limit(row_limit: int | None) -> int:
    if row_limit is None:
        return AHREFS_ACTION_MAX_ROWS
    return min(row_limit, AHREFS_ACTION_MAX_ROWS)


def _enforce_plan_row_limit(
    *,
    requested_limit: int,
    row_limit: int | None,
    subscription: object,
) -> None:
    if row_limit == 0:
        raise ValidationError(
            "Ahrefs subscription does not include Direct API access",
            data={
                "vendor": "ahrefs",
                "subscription": subscription,
                "documented_access": "Direct API requires an eligible paid API plan.",
            },
        )
    effective_limit = _effective_row_limit(row_limit)
    if requested_limit > effective_limit:
        raise ValidationError(
            "Ahrefs request limit exceeds the subscription row cap",
            data={
                "vendor": "ahrefs",
                "subscription": subscription,
                "requested_limit": requested_limit,
                "effective_row_limit": effective_limit,
                "documented_limits": {
                    "lite": 100,
                    "standard": 250,
                    "advanced": 500,
                    "enterprise": "unlimited; StackOS action cap is 1000",
                },
            },
        )


def _limits_info(data: object) -> dict[str, object]:
    if isinstance(data, dict) and isinstance(data.get("limits_and_usage"), dict):
        return data["limits_and_usage"]
    return data if isinstance(data, dict) else {}


def _metadata(
    operation: str,
    *,
    limits_info: dict[str, object],
    row_limit: int | None,
    row_limit_source: str,
    api_units: object,
) -> dict[str, object]:
    return {
        "vendor": "ahrefs",
        "operation": operation,
        "ahrefs": {
            "subscription": limits_info.get("subscription"),
            "row_limit": row_limit,
            "row_limit_source": row_limit_source,
            "effective_row_limit": _effective_row_limit(row_limit),
            "action_row_cap": AHREFS_ACTION_MAX_ROWS,
            "units_limit_api_key": limits_info.get("units_limit_api_key"),
            "units_limit_workspace": limits_info.get("units_limit_workspace"),
            "units_usage_api_key": limits_info.get("units_usage_api_key"),
            "units_usage_workspace": limits_info.get("units_usage_workspace"),
            "usage_reset_date": limits_info.get("usage_reset_date"),
            "api_key_expiration_date": limits_info.get("api_key_expiration_date"),
            "api_units": api_units,
        },
    }


__all__ = [
    "AHREFS_ACTION_MAX_ROWS",
    "AHREFS_DEFAULT_ROWS",
    "AHREFS_MODE_VALUES",
    "AhrefsActionConnector",
    "row_limit_for_subscription",
]
