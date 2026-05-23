"""`scripts/doctor.sh --json` emits the documented JSON envelope.

Per audit P-G3 / A-MINOR-29, the schema is `{ok, code, checks, info}`
where `code` is the documented exit code (PLAN.md L1271) and `ok` is
True iff `code == 0`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_doctor_json_emits_documented_schema(
    sandbox_home: Path, scripts_dir: Path, repo_root: Path
) -> None:
    # Point the daemon's settings at the sandbox so doctor probes the
    # tmp seed/auth/db rather than the real `~/.local/...`.
    env = {
        **os.environ,
        "STACKOS_HOME": str(sandbox_home),
        "HOME": str(sandbox_home),
        "STACKOS_DATA_DIR": str(sandbox_home / ".local" / "share" / "stackos"),
        "STACKOS_STATE_DIR": str(sandbox_home / ".local" / "state" / "stackos"),
        # Pin the seed to a value matching the conftest auth.token. The
        # doctor refuses to start if it sees a token with the wrong mode,
        # but the conftest already wrote 0600 — so just give it dirs.
    }
    # Remove env vars that might shadow our overrides during a test run.
    env.pop("STACKOS_PORT", None)

    result = subprocess.run(
        ["bash", str(scripts_dir / "doctor.sh"), "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )
    # Doctor will exit non-zero (daemon down, seed missing, etc.) — that's fine,
    # we only need to verify the JSON envelope.
    payload_line = result.stdout.strip().splitlines()[-1]
    payload = json.loads(payload_line)

    assert set(payload.keys()) >= {"ok", "code", "checks", "info"}
    assert isinstance(payload["code"], int)
    assert isinstance(payload["ok"], bool)
    assert payload["ok"] is (payload["code"] == 0)
    assert isinstance(payload["checks"], dict)

    # All check rows must surface as bools.
    for name, val in payload["checks"].items():
        assert isinstance(val, bool), f"check {name!r} value {val!r} is not a bool"

    # The documented info keys per PLAN.md.
    info = payload["info"]
    for k in (
        "host",
        "port",
        "data_dir",
        "state_dir",
        "version",
        "milestone",
        "install_checks",
        "codex_mcp",
        "claude_mcp",
        "launchd",
    ):
        assert k in info, f"missing info key: {k}"

    for k in (
        "codex_mcp_registered",
        "claude_mcp_registered",
        "launchd_plist_present",
        "plugins_installed",
        "plugin_marketplace_registered",
    ):
        assert k in payload["checks"], f"missing optional check: {k}"
