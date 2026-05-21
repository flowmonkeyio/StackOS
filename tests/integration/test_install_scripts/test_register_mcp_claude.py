"""`scripts/register-mcp-claude.sh` upserts via atomic JSON merge.

Per audit B-24 the script must:
  - Create the file when absent.
  - Preserve any sibling MCP servers when present.
  - No-op (idempotent) when content-stack is already registered with
    the same payload.
  - Atomic write via tempfile + rename — never leave a half-written
    `mcp.json` on a crash.
  - Backup any existing file to `.bak`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _run(
    scripts_dir: Path, home: Path, *args: str, target: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CONTENT_STACK_HOME": str(home)}
    env.pop("CONTENT_STACK_PORT", None)
    if target is not None:
        env["CONTENT_STACK_MCP_TARGET"] = str(target)
    return subprocess.run(
        ["bash", str(scripts_dir / "register-mcp-claude.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_creates_file_when_absent(sandbox_home: Path, scripts_dir: Path) -> None:
    target = sandbox_home / ".claude" / "mcp.json"
    assert not target.exists()
    result = _run(scripts_dir, sandbox_home, target=target)
    assert result.returncode == 0, result.stderr
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "mcpServers" in payload
    cs = payload["mcpServers"]["content-stack"]
    assert cs["transport"] == "stdio"
    assert cs["args"] == ["-m", "content_stack", "mcp-bridge"]
    assert "headers" not in cs


def test_preserves_existing_other_server(sandbox_home: Path, scripts_dir: Path) -> None:
    target = sandbox_home / ".claude" / "mcp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other-server": {
                        "transport": "stdio",
                        "command": "/usr/local/bin/other-server",
                    }
                },
                "extraTopLevelKey": "preserved",
            }
        ),
        encoding="utf-8",
    )

    result = _run(scripts_dir, sandbox_home, target=target)
    assert result.returncode == 0, result.stderr
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert "other-server" in payload["mcpServers"], "sibling server lost"
    assert payload["mcpServers"]["other-server"]["command"] == "/usr/local/bin/other-server"
    assert payload["mcpServers"]["content-stack"]["transport"] == "stdio"
    assert payload["extraTopLevelKey"] == "preserved", "top-level keys not preserved"


def test_idempotent_payload(sandbox_home: Path, scripts_dir: Path) -> None:
    target = sandbox_home / ".claude" / "mcp.json"
    _run(scripts_dir, sandbox_home, target=target)
    first = target.read_text(encoding="utf-8")
    _run(scripts_dir, sandbox_home, target=target)
    second = target.read_text(encoding="utf-8")
    assert first == second


def test_creates_bak_on_existing(sandbox_home: Path, scripts_dir: Path) -> None:
    target = sandbox_home / ".claude" / "mcp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    _run(scripts_dir, sandbox_home, target=target)
    assert (target.parent / "mcp.json.bak").is_file()


def test_remove_flag(sandbox_home: Path, scripts_dir: Path) -> None:
    target = sandbox_home / ".claude" / "mcp.json"
    _run(scripts_dir, sandbox_home, target=target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "content-stack" in payload["mcpServers"]

    result = _run(scripts_dir, sandbox_home, "--remove", target=target)
    assert result.returncode == 0, result.stderr
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "content-stack" not in payload["mcpServers"]


def test_atomic_write_uses_tempfile_in_same_dir(sandbox_home: Path, scripts_dir: Path) -> None:
    """No leftover temp files in the target dir after a successful merge."""
    target = sandbox_home / ".claude" / "mcp.json"
    _run(scripts_dir, sandbox_home, target=target)
    leftovers = [p.name for p in target.parent.glob(".mcp.*")]
    assert leftovers == [], f"unexpected temp leftovers: {leftovers}"


def test_per_project_target_via_env(sandbox_home: Path, scripts_dir: Path, tmp_path: Path) -> None:
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    target = project_dir / ".mcp.json"

    result = _run(scripts_dir, sandbox_home, target=target)
    assert result.returncode == 0, result.stderr
    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "content-stack" in payload["mcpServers"]


def test_rejects_non_json(sandbox_home: Path, scripts_dir: Path) -> None:
    target = sandbox_home / ".claude" / "mcp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not-json-at-all", encoding="utf-8")
    result = _run(scripts_dir, sandbox_home, target=target)
    assert result.returncode != 0
    assert "not valid JSON" in result.stderr
