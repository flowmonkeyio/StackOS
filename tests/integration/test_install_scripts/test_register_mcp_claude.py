"""`scripts/register-mcp-claude.sh` delegates to Claude Code's MCP CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _write_fake_claude(bin_dir: Path, state_path: Path) -> Path:
    script = bin_dir / "claude"
    script.write_text(
        f"""#!{sys.executable}
from __future__ import annotations

import json
import sys
from pathlib import Path

STATE = Path({str(state_path)!r})


def load():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {{"servers": {{}}, "calls": []}}


def save(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def main(argv):
    data = load()
    data.setdefault("calls", []).append(argv)
    servers = data.setdefault("servers", {{}})
    if argv[:2] != ["mcp", argv[1] if len(argv) > 1 else ""]:
        save(data)
        return 2
    command = argv[1]
    if command == "add":
        scope = "local"
        transport = "stdio"
        idx = 2
        while idx < len(argv) and argv[idx].startswith("-"):
            if argv[idx] in ("--scope", "-s"):
                scope = argv[idx + 1]
                idx += 2
            elif argv[idx] in ("--transport", "-t"):
                transport = argv[idx + 1]
                idx += 2
            else:
                idx += 1
        name = argv[idx]
        idx += 1
        if idx < len(argv) and argv[idx] == "--":
            idx += 1
        servers[name] = {{
            "scope": scope,
            "type": transport,
            "command": argv[idx] if idx < len(argv) else "",
            "args": argv[idx + 1:],
        }}
        save(data)
        return 0
    if command == "get":
        name = argv[2]
        row = servers.get(name)
        save(data)
        if row is None:
            print(f'No MCP server named "{{name}}".', file=sys.stderr)
            return 1
        print(f"{{name}}:")
        print(f"  Scope: {{row.get('scope', 'user').title()}} config")
        print("  Status: ✓ Connected")
        print(f"  Type: {{row.get('type', 'stdio')}}")
        print(f"  Command: {{row.get('command', '')}}")
        print("  Args: " + " ".join(row.get("args", [])))
        return 0
    if command == "remove":
        idx = 2
        names = []
        scope = None
        while idx < len(argv):
            if argv[idx] in ("--scope", "-s"):
                scope = argv[idx + 1]
                idx += 2
            else:
                names.append(argv[idx])
                idx += 1
        row = servers.get(names[0])
        if row is not None and (scope is None or row.get("scope") == scope):
            del servers[names[0]]
        save(data)
        return 0
    save(data)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run(
    scripts_dir: Path,
    home: Path,
    *args: str,
    bin_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "STACKOS_HOME": str(home)}
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}/usr/bin:/bin:/usr/sbin:/sbin"
    if extra_env:
        env.update(extra_env)
    env.pop("STACKOS_PORT", None)
    env.pop("STACKOS_MCP_TARGET", None)
    return subprocess.run(
        ["/bin/bash", str(scripts_dir / "register-mcp-claude.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _state(path: Path) -> dict[str, object]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"servers": {}, "calls": []}


def test_skips_when_claude_cli_absent(
    sandbox_home: Path, scripts_dir: Path, tmp_path: Path
) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()

    result = _run(
        scripts_dir,
        sandbox_home,
        bin_dir=empty_bin,
        extra_env={"STACKOS_CLAUDE_BIN": str(tmp_path / "missing-claude")},
    )

    assert result.returncode == 0, result.stderr
    assert "Claude Code CLI not found" in result.stdout
    assert not (sandbox_home / ".claude" / "mcp.json").exists()


def test_register_fails_when_token_missing(
    sandbox_home: Path, scripts_dir: Path, tmp_path: Path
) -> None:
    (sandbox_home / ".local" / "state" / "stackos" / "auth.token").unlink()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    _write_fake_claude(bin_dir, state_path)

    result = _run(scripts_dir, sandbox_home, bin_dir=bin_dir)

    assert result.returncode == 1
    assert "auth token missing" in result.stderr
    assert _state(state_path)["calls"] == []


def test_register_uses_stackos_home_when_home_differs(
    sandbox_home: Path, scripts_dir: Path, tmp_path: Path
) -> None:
    real_home = tmp_path / "real-home-without-token"
    real_home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    _write_fake_claude(bin_dir, state_path)

    result = _run(
        scripts_dir,
        sandbox_home,
        bin_dir=bin_dir,
        extra_env={"HOME": str(real_home), "STACKOS_MCP_BRIDGE_COMMAND": "stackos:mcp-bridge"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _state(state_path)
    assert ["mcp", "add", "--scope", "user", "--transport", "stdio"] in [
        call[:6] for call in payload["calls"]
    ]


def test_registers_user_scope_stdio_with_claude_cli(
    sandbox_home: Path, scripts_dir: Path, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    _write_fake_claude(bin_dir, state_path)

    result = _run(
        scripts_dir,
        sandbox_home,
        bin_dir=bin_dir,
        extra_env={"STACKOS_MCP_BRIDGE_COMMAND": "stackos:mcp-bridge"},
    )

    assert result.returncode == 0, result.stderr
    payload = _state(state_path)
    server = payload["servers"]["stackos"]
    assert server["scope"] == "user"
    assert server["type"] == "stdio"
    assert server["command"] == "stackos"
    assert server["args"] == ["mcp-bridge", "--runtime", "claude-code"]
    assert ["mcp", "remove", "stackos", "--scope", "user"] in payload["calls"]
    assert ["mcp", "add", "--scope", "user", "--transport", "stdio"] in [
        call[:6] for call in payload["calls"]
    ]
    assert not (sandbox_home / ".claude" / "mcp.json").exists()


def test_remove_uses_user_scope_and_cleans_only_legacy_stackos_entry(
    sandbox_home: Path, scripts_dir: Path, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    _write_fake_claude(bin_dir, state_path)
    state_path.write_text(
        json.dumps(
            {
                "servers": {
                    "stackos": {
                        "scope": "user",
                        "type": "stdio",
                        "command": "stackos",
                        "args": ["mcp-bridge"],
                    },
                    "other": {"scope": "user", "type": "stdio", "command": "other", "args": []},
                },
                "calls": [],
            }
        ),
        encoding="utf-8",
    )
    legacy = sandbox_home / ".claude" / "mcp.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stackos": {"transport": "stdio", "command": "python3"},
                    "other": {"transport": "stdio", "command": "other"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = _run(scripts_dir, sandbox_home, "--remove", bin_dir=bin_dir)

    assert result.returncode == 0, result.stderr
    payload = _state(state_path)
    assert payload["calls"][0] == ["mcp", "remove", "stackos", "--scope", "user"]
    assert "stackos" not in payload["servers"]
    assert "other" in payload["servers"]
    legacy_payload = json.loads(legacy.read_text(encoding="utf-8"))
    assert "stackos" not in legacy_payload["mcpServers"]
    assert "other" in legacy_payload["mcpServers"]
