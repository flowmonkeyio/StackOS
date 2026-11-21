"""Atomic JSON helpers for desktop MCP host configuration files."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_UNSET = object()
CONFIG_CHANGED = "The JSON configuration changed since inspection; inspect it again."


@dataclass(frozen=True)
class JsonConfigRead:
    ok: bool
    data: dict[str, Any]
    error: str | None = None
    original: bytes | None = None


def read_json_object(path: Path) -> JsonConfigRead:
    if not path.exists():
        return JsonConfigRead(ok=True, data={})
    try:
        original = path.read_bytes()
        parsed = json.loads(original.decode("utf-8"))
    except Exception as exc:
        return JsonConfigRead(ok=False, data={}, error=f"{type(exc).__name__}: {exc}")
    if not isinstance(parsed, dict):
        return JsonConfigRead(ok=False, data={}, error="config root must be a JSON object")
    return JsonConfigRead(ok=True, data=parsed, original=original)


def upsert_mcp_server(
    path: Path, name: str, server: dict[str, Any], *, expected_entry: Any = _UNSET
) -> tuple[bool, str | None]:
    loaded = read_json_object(path)
    if not loaded.ok:
        return False, loaded.error
    data = dict(loaded.data)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        return False, "mcpServers must be a JSON object"
    if expected_entry is not _UNSET and not _entry_matches(servers, name, expected_entry):
        return False, CONFIG_CHANGED
    current = servers.get(name)
    if current == server:
        return True, None
    servers = dict(servers)
    servers[name] = server
    data["mcpServers"] = servers
    if expected_entry is _UNSET:
        _atomic_write_json(path, data)
    elif not _atomic_write_json(path, data, expected_bytes=loaded.original):
        return False, CONFIG_CHANGED
    return True, None


def remove_mcp_server(
    path: Path, name: str, *, expected_entry: Any = _UNSET
) -> tuple[bool, str | None, bool]:
    loaded = read_json_object(path)
    if not loaded.ok:
        return False, loaded.error, False
    data = dict(loaded.data)
    servers = data.get("mcpServers")
    if servers is None:
        return True, None, False
    if not isinstance(servers, dict):
        return False, "mcpServers must be a JSON object", False
    if name not in servers:
        return True, None, False
    if expected_entry is not _UNSET and not _entry_matches(servers, name, expected_entry):
        return False, CONFIG_CHANGED, False
    servers = dict(servers)
    del servers[name]
    data["mcpServers"] = servers
    if expected_entry is _UNSET:
        _atomic_write_json(path, data)
    elif not _atomic_write_json(path, data, expected_bytes=loaded.original):
        return False, CONFIG_CHANGED, False
    return True, None, True


def _entry_matches(servers: dict[str, Any], name: str, expected: Any) -> bool:
    if expected is None:
        return name not in servers
    # JSON comparison distinguishes booleans from integers, unlike Python ==.
    return name in servers and json.dumps(servers[name], sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )


def _atomic_write_json(path: Path, data: dict[str, Any], *, expected_bytes: Any = _UNSET) -> bool:
    """Refuse observed edits during preparation for expectation-bound callers.

    As with the TOML owner, the final read detects changes before replacement;
    it is not an OS-level compare-and-swap against noncooperating writers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if expected_bytes is not _UNSET:
            current: bytes | None
            try:
                current = path.read_bytes()
            except FileNotFoundError:
                current = None
            if current != expected_bytes:
                return False
        os.replace(tmp, path)
        return True
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
