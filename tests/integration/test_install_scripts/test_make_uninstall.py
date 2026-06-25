"""Top-level uninstall target preserves user-owned state."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def _install_stubs(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    codex_state = tmp_path / "codex-registered"
    codex_state.write_text("registered\n", encoding="utf-8")
    codex_log = tmp_path / "codex.log"
    _write_executable(
        bin_dir / "codex",
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{codex_log}"\n'
        f'STATE="{codex_state}"\n'
        'case "$1 $2" in\n'
        '  "mcp list") if [[ -f "$STATE" ]]; then '
        'echo "stackos /tmp/python -m stackos mcp-bridge"; fi ;;\n'
        '  "mcp remove") rm -f "$STATE" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
    )

    launchctl_log = tmp_path / "launchctl.log"
    _write_executable(
        bin_dir / "launchctl",
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{launchctl_log}"\n'
        'case "$1" in\n'
        "  print) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
    )
    return bin_dir


def test_make_uninstall_removes_integrations_and_preserves_local_state(
    sandbox_home: Path,
    repo_root: Path,
    tmp_path: Path,
) -> None:
    bin_dir = _install_stubs(tmp_path)

    data_dir = sandbox_home / ".local" / "share" / "stackos"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "stackos.db"
    db_path.write_text("db stays\n", encoding="utf-8")

    state_dir = sandbox_home / ".local" / "state" / "stackos"
    seed_path = state_dir / "seed.bin"
    token_path = state_dir / "auth.token"
    seed_path.write_bytes(b"seed stays")
    token_before = token_path.read_text(encoding="utf-8")

    (sandbox_home / ".codex" / "skills" / "stackos").mkdir(parents=True)
    (sandbox_home / ".claude" / "skills" / "stackos").mkdir(parents=True)
    plugin_root = sandbox_home / ".codex" / "plugins" / "stackos"
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    cache_root = sandbox_home / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos"
    (cache_root / "0.1.0" / ".codex-plugin").mkdir(parents=True)
    (cache_root / "0.1.0" / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

    marketplace = sandbox_home / ".agents" / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps(
            {
                "name": "local-stackos",
                "plugins": [
                    {"name": "stackos", "source": {"path": "./.codex/plugins/stackos"}},
                    {"name": "other", "source": {"path": "./other"}},
                ],
            }
        ),
        encoding="utf-8",
    )

    claude_mcp = sandbox_home / ".claude" / "mcp.json"
    claude_mcp.parent.mkdir(parents=True, exist_ok=True)
    claude_mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stackos": {"transport": "stdio"},
                    "other-server": {"transport": "stdio"},
                }
            }
        ),
        encoding="utf-8",
    )

    plist = sandbox_home / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist><dict /></plist>\n", encoding="utf-8")

    env = {
        **os.environ,
        "HOME": str(sandbox_home),
        "STACKOS_HOME": str(sandbox_home),
        "STACKOS_MCP_TARGET": str(claude_mcp),
        "STACKOS_PLUGIN_PYTHON": sys.executable,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["make", "uninstall"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "DB" in result.stdout
    assert "preserved" in result.stdout
    assert not plist.exists()
    assert not (sandbox_home / ".codex" / "skills" / "stackos").exists()
    assert not (sandbox_home / ".claude" / "skills" / "stackos").exists()
    assert not plugin_root.exists()
    assert not cache_root.exists()

    marketplace_payload = json.loads(marketplace.read_text(encoding="utf-8"))
    assert [item["name"] for item in marketplace_payload["plugins"]] == ["other"]
    claude_payload = json.loads(claude_mcp.read_text(encoding="utf-8"))
    assert "stackos" not in claude_payload["mcpServers"]
    assert "other-server" in claude_payload["mcpServers"]

    assert db_path.read_text(encoding="utf-8") == "db stays\n"
    assert seed_path.read_bytes() == b"seed stays"
    assert token_path.read_text(encoding="utf-8") == token_before


def test_make_uninstall_fails_before_claiming_completion_on_launchd_failure(
    sandbox_home: Path,
    repo_root: Path,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl = bin_dir / "launchctl"
    launchctl.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  print) exit 1 ;;\n"
        "  unload) echo unload failed >&2; exit 44 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)

    plist = sandbox_home / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist><dict /></plist>\n", encoding="utf-8")

    result = subprocess.run(
        ["make", "uninstall"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(sandbox_home),
            "STACKOS_HOME": str(sandbox_home),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert result.returncode != 0
    assert "failed to unload launchd job" in result.stderr
    assert "unload failed" in result.stderr
    assert "uninstall complete" not in result.stdout
    assert plist.exists()
