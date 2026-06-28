"""Claude Code MCP registration contract tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from stackos import claude_mcp


def _write_fake_claude(bin_dir: Path, state_path: Path) -> Path:
    script = bin_dir / "claude"
    script.write_text(
        f"""#!{sys.executable}
from __future__ import annotations

import json
import os
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
    if os.environ.get("FAKE_CLAUDE_FAIL") == "1":
        save(data)
        print("fake claude failure", file=sys.stderr)
        return 42
    if argv[:2] != ["mcp", argv[1] if len(argv) > 1 else ""]:
        save(data)
        print("unsupported", file=sys.stderr)
        return 2
    command = argv[1]
    servers = data.setdefault("servers", {{}})
    if command == "add":
        scope = "local"
        transport = "stdio"
        idx = 2
        while idx < len(argv) and argv[idx].startswith("-"):
            flag = argv[idx]
            if flag in ("--scope", "-s"):
                scope = argv[idx + 1]
                idx += 2
            elif flag in ("--transport", "-t"):
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
        print(f"Added {{name}}")
        return 0
    if command == "remove":
        scope = None
        names = []
        idx = 2
        while idx < len(argv):
            if argv[idx] in ("--scope", "-s"):
                scope = argv[idx + 1]
                idx += 2
            else:
                names.append(argv[idx])
                idx += 1
        name = names[0]
        row = servers.get(name)
        if row is not None and (scope is None or row.get("scope") == scope):
            del servers[name]
        save(data)
        print(f"Removed {{name}}")
        return 0
    if command == "get":
        name = argv[2]
        row = servers.get(name)
        save(data)
        if row is None:
            print(f'No MCP server named "{{name}}".', file=sys.stderr)
            return 1
        print(f"{{name}}:")
        scope = row.get("scope", "user")
        print(f"  Scope: {{scope.title()}} config")
        print("  Status: ✓ Connected")
        print(f"  Type: {{row.get('type', 'stdio')}}")
        print(f"  Command: {{row.get('command', '')}}")
        print("  Args: " + " ".join(row.get("args", [])))
        print(f"To remove this server, run: claude mcp remove {{name}} -s {{scope}}")
        return 0
    if command == "list":
        for name, row in servers.items():
            print(f"{{name}}: {{row.get('command', '')}} - ✓ Connected")
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


def _fake_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"servers": {}, "calls": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_token(home: Path) -> None:
    token = home / ".local" / "state" / "stackos" / "auth.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("unit-test-token\n", encoding="utf-8")
    token.chmod(0o600)


def test_register_uses_claude_cli_user_scope_without_secrets(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    _write_fake_claude(bin_dir, state_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = claude_mcp.register(
        home=tmp_path,
        bridge_command=[
            "/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos",
            "mcp-bridge",
        ],
    )

    assert result.status == "registered"
    state = _fake_state(state_path)
    server = state["servers"]["stackos"]
    assert server["scope"] == "user"
    assert server["type"] == "stdio"
    assert server["command"].endswith("/stackos")
    assert server["args"] == ["mcp-bridge"]
    assert "token" not in json.dumps(server).lower()
    assert ["mcp", "remove", "stackos", "--scope", "user"] in state["calls"]
    assert ["mcp", "add", "--scope", "user", "--transport", "stdio"] in [
        call[:6] for call in state["calls"]
    ]


def test_register_skips_when_claude_cli_absent(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("STACKOS_CLAUDE_BIN", str(tmp_path / "missing-claude"))

    result = claude_mcp.register(home=tmp_path, bridge_command=["stackos", "mcp-bridge"])

    assert result.status == "claude_absent"
    assert result.ok is True
    assert not (tmp_path / ".claude" / "mcp.json").exists()


def test_register_fails_before_claude_mutation_when_token_missing(
    tmp_path: Path, monkeypatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    _write_fake_claude(bin_dir, state_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = claude_mcp.register(home=tmp_path, bridge_command=["stackos", "mcp-bridge"])

    assert result.status == "token_missing"
    assert result.ok is False
    assert _fake_state(state_path)["calls"] == []


def test_inspect_does_not_treat_legacy_json_as_healthy(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / ".claude" / "mcp.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stackos": {
                        "transport": "stdio",
                        "command": "python3",
                        "args": ["-m", "stackos", "mcp-bridge"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("STACKOS_CLAUDE_BIN", str(tmp_path / "missing-claude"))

    state = claude_mcp.inspect(home=tmp_path, expected_command=["stackos", "mcp-bridge"])

    assert state.status == "claude_absent"
    assert state.legacy_json_present is True
    assert state.ok is True


def test_inspect_stale_project_scope_gives_scope_specific_repair(
    tmp_path: Path, monkeypatch
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
                        "scope": "project",
                        "type": "stdio",
                        "command": "stackos",
                        "args": ["mcp-bridge"],
                    }
                },
                "calls": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    state = claude_mcp.inspect(home=tmp_path, expected_command=["stackos", "mcp-bridge"])

    assert state.status == "stale"
    assert state.scope == "project"
    assert "claude mcp remove stackos --scope project" in (state.repair or "")


def test_register_discovers_gui_claude_candidate_when_path_is_empty(
    tmp_path: Path, monkeypatch
) -> None:
    _write_token(tmp_path)
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    app_bin = tmp_path / "Applications" / "cmux.app" / "Contents" / "Resources" / "bin"
    app_bin.mkdir(parents=True)
    state_path = tmp_path / "claude-state.json"
    fake_claude = _write_fake_claude(app_bin, state_path)
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("STACKOS_HOME", str(tmp_path))
    monkeypatch.delenv("STACKOS_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(claude_mcp.sys, "platform", "darwin")
    monkeypatch.setattr(claude_mcp, "COMMON_CLAUDE_CLI_CANDIDATES", ())
    monkeypatch.setattr(claude_mcp, "MACOS_CLAUDE_APP_BUNDLE_CANDIDATES", (str(fake_claude),))

    result = claude_mcp.register(home=tmp_path, bridge_command=["stackos", "mcp-bridge"])

    assert result.status == "registered"
    assert result.claude_bin == str(fake_claude)
    state = _fake_state(state_path)
    assert state["servers"]["stackos"]["command"] == "stackos"


def test_register_discovers_cli_install_outside_app_bundle(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    cli_bin = tmp_path / ".local" / "bin"
    cli_bin.mkdir(parents=True)
    state_path = tmp_path / "claude-state.json"
    fake_claude = _write_fake_claude(cli_bin, state_path)
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("STACKOS_HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", str(tmp_path / "missing-shell"))
    monkeypatch.delenv("STACKOS_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(claude_mcp, "COMMON_CLAUDE_CLI_CANDIDATES", ("~/.local/bin/claude",))
    monkeypatch.setattr(claude_mcp, "MACOS_CLAUDE_APP_BUNDLE_CANDIDATES", ())

    result = claude_mcp.register(home=tmp_path, bridge_command=["stackos", "mcp-bridge"])

    assert result.status == "registered"
    assert result.claude_bin == str(fake_claude)
    state = _fake_state(state_path)
    assert state["servers"]["stackos"]["command"] == "stackos"


def test_register_discovers_claude_from_user_login_shell(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    shell_dir = tmp_path / "shell-bin"
    shell_dir.mkdir()
    claude_dir = tmp_path / "custom-claude-bin"
    claude_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    fake_claude = _write_fake_claude(claude_dir, state_path)
    fake_shell = shell_dir / "zsh"
    fake_shell.write_text(
        f"#!{sys.executable}\nprint({str(fake_claude)!r})\n",
        encoding="utf-8",
    )
    fake_shell.chmod(0o755)
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("STACKOS_HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", str(fake_shell))
    monkeypatch.delenv("STACKOS_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(claude_mcp, "COMMON_CLAUDE_CLI_CANDIDATES", ())
    monkeypatch.setattr(claude_mcp, "MACOS_CLAUDE_APP_BUNDLE_CANDIDATES", ())

    result = claude_mcp.register(home=tmp_path, bridge_command=["stackos", "mcp-bridge"])

    assert result.status == "registered"
    assert result.claude_bin == str(fake_claude)


def test_register_runs_app_wrapper_with_augmented_cli_path(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    real_bin = tmp_path / ".local" / "bin"
    real_bin.mkdir(parents=True)
    wrapper_bin = tmp_path / "Applications" / "cmux.app" / "Contents" / "Resources" / "bin"
    wrapper_bin.mkdir(parents=True)
    state_path = tmp_path / "claude-state.json"
    real_claude = _write_fake_claude(real_bin, state_path)
    wrapper = wrapper_bin / "claude"
    wrapper.write_text(
        f"""#!{sys.executable}
from __future__ import annotations

import os
import sys
from pathlib import Path

self_dir = Path(__file__).parent.resolve()
for raw in os.environ.get("PATH", "").split(os.pathsep):
    if not raw:
        continue
    candidate = (Path(raw).expanduser() / "claude").resolve()
    if candidate.parent == self_dir:
        continue
    if candidate == Path({str(real_claude)!r}).resolve() and os.access(candidate, os.X_OK):
        os.execv(candidate, [str(candidate), *sys.argv[1:]])
print("Error: claude not found in PATH", file=sys.stderr)
raise SystemExit(127)
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("STACKOS_HOME", str(tmp_path))
    monkeypatch.setenv("STACKOS_CLAUDE_BIN", str(wrapper))
    monkeypatch.setenv("SHELL", str(tmp_path / "missing-shell"))
    monkeypatch.setattr(claude_mcp, "COMMON_CLAUDE_CLI_CANDIDATES", ("~/.local/bin/claude",))

    result = claude_mcp.register(home=tmp_path, bridge_command=["stackos", "mcp-bridge"])

    assert result.status == "registered"
    assert result.claude_bin == str(wrapper)
    state = _fake_state(state_path)
    assert state["servers"]["stackos"]["args"] == ["mcp-bridge"]


def test_result_info_redacts_secret_like_commands() -> None:
    result = claude_mcp.ClaudeMcpResult(
        ok=False,
        status="stale",
        message="stale",
        command=["curl", "-H", "Authorization: Bearer secret"],
    )

    assert result.to_info()["command"] == ["<redacted: secret-like MCP command>"]


def test_remove_uses_user_scope_and_cleans_only_legacy_stackos_entry(
    tmp_path: Path, monkeypatch
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
    legacy = tmp_path / ".claude" / "mcp.json"
    legacy.parent.mkdir(parents=True)
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
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = claude_mcp.remove(home=tmp_path)

    assert result.status == "removed"
    state = _fake_state(state_path)
    assert "stackos" not in state["servers"]
    assert "other" in state["servers"]
    legacy_payload = json.loads(legacy.read_text(encoding="utf-8"))
    assert "stackos" not in legacy_payload["mcpServers"]
    assert "other" in legacy_payload["mcpServers"]


def test_resolve_bridge_command_prefers_packaged_launcher(tmp_path: Path, monkeypatch) -> None:
    launcher = tmp_path / "stackos"
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(launcher))

    assert claude_mcp.resolve_bridge_command() == [str(launcher), "mcp-bridge"]
