"""Read Claude Code's subscription and its synthetic /usage report."""

import json
import re
from datetime import UTC, datetime
from typing import Any

from .accounts import HostAccountError, ResolvedAccountSelection
from .adapters.claude_accounts import ClaudeAccountError, selected_profile
from .subscription_native import run_capture

# The CLI added auth status in 2.1.41 and structured /usage in 2.1.273.
_VERSION = re.compile(r"2\.1\.(\d{1,6}) \(Claude Code\)")
_PLANS = {
    tier.lower(): f"Claude {tier}" for tier in ("Free", "Pro", "Max", "Ultra", "Team", "Enterprise")
}
_USAGE_ARGS = [
    "--safe-mode",
    "--strict-mcp-config",
    "--permission-mode",
    "dontAsk",
    "--tools",
    "",
    "--max-turns",
    "0",
    "--no-session-persistence",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "/usage",
]


def _meter(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    used = row.get("percent")
    if isinstance(used, bool) or not isinstance(used, int | float) or not 0 <= used <= 100:
        return None
    kind = row.get("kind")
    if kind == "session":
        label = "5-hour"
    elif kind == "weekly_all":
        label = "All models"
    elif kind == "weekly_scoped" and isinstance(scope := row.get("scope"), dict):
        labels = []
        for field in ("model", "surface"):
            item = scope.get(field)
            name = item.get("display_name") if isinstance(item, dict) else None
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9 .-]{0,39}", name):
                labels.append(name)
        if not labels:
            return None
        label = " · ".join(labels)
    else:
        return None
    reset = row.get("resets_at")
    if reset is not None:
        if not isinstance(reset, str):
            return None
        try:
            when = datetime.fromisoformat(reset)
            if when.tzinfo is None or when <= datetime.now(UTC):
                return None
            reset = when.astimezone(UTC).isoformat()
        except (ValueError, OverflowError):
            return None
    return {"scope": label, "remaining_percent": 100 - used, "resets_at": reset}


def _meters(stdout: str) -> list[dict[str, Any]]:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        report = event.get("usage_report")
        limits = report.get("rate_limits") if isinstance(report, dict) else None
        rows = limits.get("limits") if isinstance(limits, dict) else None
        if isinstance(rows, list):
            return [meter for row in rows if (meter := _meter(row)) is not None]
    return []


def read_claude(
    binary: str,
    selection: ResolvedAccountSelection | None = None,
) -> dict[str, Any] | None:
    env = None
    if selection is not None and selection.desired.account_ref is not None:
        try:
            env, _ = selected_profile(binary, selection)
        except (ClaudeAccountError, HostAccountError):
            return None
    version = run_capture(binary, ["--version"], env=env)
    if (
        version is None
        or version.returncode
        or not (match := _VERSION.fullmatch(version.stdout.strip()))
    ):
        return None
    patch = int(match[1])
    if patch < 41:
        return None
    auth = run_capture(binary, ["auth", "status", "--json"], env=env)
    if auth is None or auth.returncode:
        return None
    try:
        status = json.loads(auth.stdout)
    except (ValueError, RecursionError):
        return None
    if (
        not isinstance(status, dict)
        or status.get("loggedIn") is not True
        or status.get("authMethod") not in ("claude.ai", "oauth_token")
    ):
        return None
    tier = status.get("subscriptionType")
    plan = _PLANS.get(tier.lower()) if isinstance(tier, str) else None
    meters = []
    if patch >= 273:
        result = run_capture(binary, _USAGE_ARGS, env=env)
        if result is not None and result.returncode == 0:
            meters = _meters(result.stdout)
    return {"plan": plan, "meters": meters} if plan or meters else None
