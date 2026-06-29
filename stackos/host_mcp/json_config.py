"""Atomic JSON helpers for desktop MCP host configuration files."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class JsonConfigRead:
    ok: bool
    data: dict[str, Any]
    error: str | None = None


def read_json_object(path: Path) -> JsonConfigRead:
    if not path.exists():
        return JsonConfigRead(ok=True, data={})
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return JsonConfigRead(ok=False, data={}, error=f"{type(exc).__name__}: {exc}")
    if not isinstance(parsed, dict):
        return JsonConfigRead(ok=False, data={}, error="config root must be a JSON object")
    return JsonConfigRead(ok=True, data=parsed)


def upsert_mcp_server(path: Path, name: str, server: dict[str, Any]) -> tuple[bool, str | None]:
    loaded = read_json_object(path)
    if not loaded.ok:
        return False, loaded.error
    data = dict(loaded.data)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        return False, "mcpServers must be a JSON object"
    current = servers.get(name)
    if current == server:
        return True, None
    servers = dict(servers)
    servers[name] = server
    data["mcpServers"] = servers
    _atomic_write_json(path, data)
    return True, None


def remove_mcp_server(path: Path, name: str) -> tuple[bool, str | None, bool]:
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
    servers = dict(servers)
    del servers[name]
    data["mcpServers"] = servers
    _atomic_write_json(path, data)
    return True, None, True


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
