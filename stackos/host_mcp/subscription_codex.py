"""Read Codex subscription allowances without exposing account identity."""

from __future__ import annotations

from . import subscription_native as native


def _window_name(minutes: object, fallback: str) -> str:
    if type(minutes) is not int or not 0 < minutes <= 527_040:
        return fallback
    if minutes in {1440, 10080, 43200}:
        return {1440: "Daily", 10080: "Weekly", 43200: "Monthly"}[minutes]
    return f"{minutes // 60}-hour" if minutes % 60 == 0 else f"{minutes}-minute"


def read_codex(binary: str) -> dict | None:
    responses = native.read_rpc(
        binary,
        ["app-server", "--listen", "stdio://"],
        [
            ("initialize", {"clientInfo": {"name": "stackos_subscription", "version": "1"}}),
            ("account/read", {"refreshToken": False}),
            ("account/rateLimits/read", None),
        ],
    )
    account = native.rpc_result(responses, 1).get("account")
    account = account if isinstance(account, dict) else {}
    if account.get("type") == "apiKey":
        return None
    limits = native.rpc_result(responses, 2)
    buckets = limits.get("rateLimitsByLimitId")
    buckets = dict(buckets) if isinstance(buckets, dict) else {}
    default = limits.get("rateLimits")
    if isinstance(default, dict):
        key = default.get("limitId")
        key = key if isinstance(key, str) else "codex"
        buckets.setdefault(key, default)
    plans = {
        "free": "Free",
        "go": "Go",
        "plus": "Plus",
        "pro": "Pro",
        "team": "Team",
        "business": "Business",
        "enterprise": "Enterprise",
        "edu": "Education",
    }
    plan_type = account.get("planType")
    plan = plans.get(plan_type) if isinstance(plan_type, str) else None
    meters = []
    for index, (key, bucket) in enumerate(buckets.items(), 1):
        if not isinstance(bucket, dict):
            continue
        if plan is None and isinstance(bucket.get("planType"), str):
            plan = plans.get(bucket["planType"])
        for field in ("primary", "secondary"):
            window = bucket.get(field)
            if not isinstance(window, dict):
                continue
            label = _window_name(window.get("windowDurationMins"), field.title() + " limit")
            scope = label if key == "codex" else f"Additional limit {index}: {label}"
            meter = native.quota_meter(scope, window.get("usedPercent"), window.get("resetsAt"))
            if meter is not None:
                meters.append(meter)
    return (
        {"plan": "ChatGPT " + plan if plan else None, "meters": meters} if plan or meters else None
    )
