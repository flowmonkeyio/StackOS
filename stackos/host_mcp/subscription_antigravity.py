"""Read native quotas and optional status-line plan metadata without identity.

Source: https://antigravity.google/docs/cli/commands/usage/ and sanitized
agy 1.5.13 SUCCESS capture (2026-11-21), retained in tests/fixtures.
Plan: https://antigravity.google/docs/cli/statusline/ (agy 1.5.14).
"""

from __future__ import annotations

import json
import os
import select
import shlex
import sys
import tempfile
from pathlib import Path
from time import monotonic

from . import subscription_native as native
from .agent_execution import ManagedProcessControl

# The native callback receives account and session data. Retain only the plan;
# run this with the packaged Python and session-only settings, never install it.
_PLAN_CALLBACK = """import json, sys
from pathlib import Path
data = json.loads(sys.stdin.read(1048577))
plan = data.get('plan_tier') if isinstance(data, dict) else None
if isinstance(plan, str) and 0 < len(plan.strip()) <= 120 and plan.isprintable():
    path = Path(sys.argv[1])
    path.with_suffix('.tmp').write_text(json.dumps(plan.strip()))
    path.with_suffix('.tmp').replace(path)
"""


def _read_plan(binary: str, *, deadline: float) -> str | None:
    # Status-line callbacks are TUI-only. No terminal input is sent: this reads
    # initialization metadata, without a model turn or persistent setting change.
    if os.name != "posix" or monotonic() >= deadline:
        return None
    control = ManagedProcessControl()
    process = None
    master = slave = None
    plan = None
    retired = False
    capture_directory = None
    try:
        capture_directory = tempfile.TemporaryDirectory(prefix="stackos-subscription-plan-")
        directory = capture_directory.name
        try:
            path = Path(directory) / "plan.json"
            settings = {
                "statusLine": {
                    "type": "command",
                    "enabled": True,
                    "command": shlex.join([sys.executable, "-c", _PLAN_CALLBACK, str(path)]),
                }
            }
            master, slave = os.openpty()
            env = native.subprocess_env_for_cli(binary)
            env["TERM"] = "xterm-256color"
            process = control.spawn(
                [binary, "--mode", "plan", "--settings", json.dumps(settings)],
                cwd=directory,
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
            )
            os.close(slave)
            slave = None
            output_bytes = 0
            while monotonic() < deadline:
                if path.exists():
                    with path.open() as stream:
                        candidate = json.loads(stream.read(1024))
                    if (
                        isinstance(candidate, str)
                        and 0 < len(candidate) <= 120
                        and candidate.isprintable()
                    ):
                        plan = candidate
                    break
                if process.poll() is not None:
                    break
                if select.select([master], [], [], min(0.05, max(0, deadline - monotonic())))[0]:
                    chunk = os.read(master, 16_384)
                    output_bytes += len(chunk)
                    if not chunk or output_bytes > 1_048_576:
                        break
        finally:
            # Hang up the temporary terminal before confirming group retirement.
            if master is not None:
                os.close(master)
                master = None
            if process is not None:
                retired = control.stop(timeout=0.2) == "stopped"
    except (OSError, ValueError, RecursionError):
        pass
    finally:
        for descriptor in (master, slave):
            if descriptor is not None:
                os.close(descriptor)
        if capture_directory is not None:
            capture_directory.cleanup()
    return plan if retired else None


def read_antigravity(binary: str) -> dict | None:
    deadline = monotonic() + 12
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
    if not meters:
        return None
    try:
        plan = _read_plan(binary, deadline=deadline)
    except OSError:
        # Even a failed temporary-file cleanup must not discard valid quotas.
        plan = None
    return {"plan": plan, "meters": meters}
