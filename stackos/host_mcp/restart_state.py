"""Pending restart markers for host apps that load MCP config at launch."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from stackos.host_mcp.bridge import default_home

STATE_FILE = "host-mcp-restarts.json"
DEFAULT_RESTART_HINT_SECONDS = 3600


def mark_restart_required(
    host_key: str,
    *,
    surface: str,
    config_path: str,
    command: list[str],
    home: Path | None = None,
) -> None:
    state = _read_state(home=home)
    state[host_key] = {
        "surface": surface,
        "config_path": config_path,
        "command": command,
        "marked_at": time.time(),
    }
    _write_state(state, home=home)


def pending_restart(
    host_key: str,
    *,
    config_path: str | None = None,
    command: list[str] | None = None,
    home: Path | None = None,
    max_age_seconds: int = DEFAULT_RESTART_HINT_SECONDS,
) -> dict[str, object] | None:
    value = _read_state(home=home).get(host_key)
    if not isinstance(value, dict):
        return None
    if config_path is not None and value.get("config_path") != config_path:
        return None
    if command is not None and value.get("command") != command:
        return None
    marked_at = value.get("marked_at")
    if isinstance(marked_at, int | float) and time.time() - marked_at > max_age_seconds:
        clear_restart_required(host_key, home=home)
        return None
    return value


def clear_restart_required(host_key: str, *, home: Path | None = None) -> None:
    state = _read_state(home=home)
    if host_key not in state:
        return
    del state[host_key]
    _write_state(state, home=home)


def state_path(home: Path | None = None) -> Path:
    if os.environ.get("STACKOS_STATE_DIR"):
        try:
            from stackos.config import get_settings

            return get_settings().state_dir / STATE_FILE
        except Exception:
            pass
    return (home or default_home()) / ".local" / "state" / "stackos" / STATE_FILE


def _read_state(*, home: Path | None = None) -> dict[str, object]:
    path = state_path(home)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(payload: dict[str, object], *, home: Path | None = None) -> None:
    path = state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".host-mcp-restarts.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
