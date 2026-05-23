"""`scripts/install-plugins.sh` mirrors plugin packages into a sandbox HOME."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(script: Path, home: Path, *args: str) -> str:
    result = subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "STACKOS_HOME": str(home),
            "STACKOS_PLUGIN_PYTHON": sys.executable,
        },
        check=True,
    )
    return result.stdout


def _snapshot(target: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(target)): p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()
    }


def test_install_plugins_creates_plugin_and_marketplace(
    sandbox_home: Path, scripts_dir: Path
) -> None:
    output = _run(scripts_dir / "install-plugins.sh", sandbox_home)
    plugin_root = sandbox_home / ".codex" / "plugins" / "stackos"
    marketplace = sandbox_home / ".agents" / "plugins" / "marketplace.json"
    payload = json.loads(marketplace.read_text(encoding="utf-8"))

    assert "Installed 1 plugins" in output
    assert (plugin_root / ".codex-plugin" / "plugin.json").is_file()
    mcp = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["stackos"] == {
        "command": sys.executable,
        "args": ["-m", "stackos", "mcp-bridge"],
    }
    assert (plugin_root / "skills" / "stackos" / "SKILL.md").is_file()
    assert not (plugin_root / "skills" / "catalog").exists()
    assert any(
        p["name"] == "stackos" and p["source"]["path"] == "./.codex/plugins/stackos"
        for p in payload["plugins"]
    )


def test_install_plugins_idempotent(sandbox_home: Path, scripts_dir: Path) -> None:
    _run(scripts_dir / "install-plugins.sh", sandbox_home)
    target = sandbox_home / ".codex" / "plugins"
    snap1 = _snapshot(target)

    _run(scripts_dir / "install-plugins.sh", sandbox_home)
    snap2 = _snapshot(target)

    assert snap1 == snap2


def test_install_plugins_remove_preserves_marketplace_file(
    sandbox_home: Path, scripts_dir: Path
) -> None:
    _run(scripts_dir / "install-plugins.sh", sandbox_home)

    _run(scripts_dir / "install-plugins.sh", sandbox_home, "--remove")

    plugin_root = sandbox_home / ".codex" / "plugins" / "stackos"
    marketplace = sandbox_home / ".agents" / "plugins" / "marketplace.json"
    payload = json.loads(marketplace.read_text(encoding="utf-8"))
    assert not plugin_root.exists()
    assert all(p["name"] != "stackos" for p in payload["plugins"])
