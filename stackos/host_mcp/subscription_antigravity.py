"""Read the bounded native /usage contract, without inferring plan or identity.

Source: https://antigravity.google/docs/cli/commands/usage/ and sanitized
agy 1.5.13 SUCCESS capture (2026-11-21), retained in tests/fixtures.
"""

from __future__ import annotations

import json

from . import subscription_native as native


def read_antigravity(binary: str) -> dict | None:
    result = native.run_capture(
        binary,
        ["--print", "/usage", "--output-format", "json"],
        timeout=12,
        max_output_bytes=1_048_576,
    )
    if result is None or result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS" or payload.get("error"):
        return None
    command = payload.get("command")
    if not isinstance(command, dict) or command.get("name") != "usage":
        return None
    data = command.get("data")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return None
    meters = []
    seen = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        name, buckets = group.get("name"), group.get("buckets")
        if not isinstance(name, str) or not 0 < len(name) <= 120 or not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            identity, label, window = (bucket.get(key) for key in ("id", "name", "window"))
            if not all(
                isinstance(value, str) and 0 < len(value) <= 120 for value in (identity, label)
            ):
                continue
            # Only the observed weekly window is established. New native window
            # contracts stay unknown instead of becoming an invented allowance.
            if window != "weekly" or (name, identity) in seen:
                continue
            fraction = bucket.get("remaining_fraction")
            if (
                isinstance(fraction, bool)
                or not isinstance(fraction, (int, float))
                or not 0 <= fraction <= 1
            ):
                continue
            scope = f"{name} · Weekly"
            if len(buckets) > 1:
                scope += f" · {label} ({identity})"
            meter = native.quota_meter(scope, (1 - fraction) * 100, bucket.get("reset_time"))
            if meter is not None:
                meters.append(meter)
                seen.add((name, identity))
    return {"plan": None, "meters": meters} if meters else None
