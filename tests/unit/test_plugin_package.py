"""Plugin package smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "stackos"


def test_codex_plugin_manifest_points_to_local_assets() -> None:
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "stackos"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"


def test_plugin_mcp_uses_local_bridge_not_project_env() -> None:
    config = json.loads((PLUGIN / ".mcp.json").read_text())
    server = config["mcpServers"]["stackos"]

    assert server == {"command": "stackos", "args": ["mcp-bridge"]}


def test_plugin_skill_preserves_repo_local_files_by_default() -> None:
    text = (PLUGIN / "skills" / "stackos" / "SKILL.md").read_text()

    assert "Do not create `.env`, `.mcp.json`, `AGENTS.md`, `CLAUDE.md`" in text
    assert "workspace.connect" in text
