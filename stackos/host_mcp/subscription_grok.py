"""Read Grok subscription billing and entitlement without exposing identity."""

from __future__ import annotations

from . import subscription_native as native


def read_grok(binary: str) -> dict | None:
    responses = native.read_rpc(
        binary,
        ["--no-auto-update", "agent", "stdio"],
        [
            (
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "stackos_subscription", "version": "1"},
                },
            ),
            ("_x.ai/billing", {}),
            ("_x.ai/auth/check_subscription", {}),
        ],
    )
    billing, auth = native.rpc_result(responses, 1), native.rpc_result(responses, 2)
    if auth.get("authenticated") is False:
        return None
    meta = auth.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    tier = billing.get("subscription_tier") or meta.get("subscription_tier")
    plans = {"SuperGrok", "SuperGrok Heavy", "X Premium", "X Premium+", "X Premium Plus"}
    plan = tier if isinstance(tier, str) and tier in plans else None
    config = billing.get("config")
    config = config if isinstance(config, dict) else {}
    period = config.get("currentPeriod")
    period = period if isinstance(period, dict) else {}
    period_type = period.get("type")
    scope = {"USAGE_PERIOD_TYPE_WEEKLY": "Weekly", "USAGE_PERIOD_TYPE_MONTHLY": "Monthly"}.get(
        period_type if isinstance(period_type, str) else "", "Current limit"
    )
    if config.get("isUnifiedBillingUser") is True:
        scope = "Shared " + scope.lower()
    meter = native.quota_meter(scope, config.get("creditUsagePercent"), period.get("end"))
    return {"plan": plan, "meters": [meter] if meter else []} if plan or meter else None
