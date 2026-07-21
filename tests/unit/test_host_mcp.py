from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from stackos.host_mcp.adapters import claude_desktop, codex, gemini_cli, hermes
from stackos.host_mcp.bridge import resolve_bridge_command
from stackos.host_mcp.restart_state import state_path


def _write_token(home: Path) -> None:
    token = home / ".local" / "state" / "stackos" / "auth.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("unit-test-token\n", encoding="utf-8")
    token.chmod(0o600)


def _write_hermes_config(path: Path, stackos: dict[str, object] | None = None) -> None:
    servers: dict[str, object] = {}
    if stackos is not None:
        servers["stackos"] = stackos
    path.write_text(json.dumps({"mcp_servers": servers}), encoding="utf-8")


def _write_fake_cli(
    bin_dir: Path,
    name: str,
    log: Path,
    *,
    list_output: str = "",
    get_output: str = "",
    get_returncode: int = 2,
) -> Path:
    script = bin_dir / name
    script.write_text(
        f"""#!{sys.executable}
from __future__ import annotations

import sys
from pathlib import Path

LOG = Path({str(log)!r})
LIST_OUTPUT = {list_output!r}
GET_OUTPUT = {get_output!r}
GET_RETURNCODE = {get_returncode!r}


def main(argv):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(" ".join(argv) + "\\n")
    if argv[:2] == ["mcp", "list"]:
        print(LIST_OUTPUT, end="")
        return 0
    if argv[:3] == ["mcp", "get", "stackos"]:
        print(GET_OUTPUT, end="")
        return GET_RETURNCODE
    if argv[:2] in (["mcp", "add"], ["mcp", "remove"]):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_codex_adapter_uses_packaged_bridge_command(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "codex.log"
    _write_fake_cli(bin_dir, "codex", log)
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))

    result = codex.register(tmp_path)

    assert result.ok is True
    assert result.command == [str(packaged), "mcp-bridge", "--runtime", "codex"]
    calls = log.read_text(encoding="utf-8").splitlines()
    assert f"mcp add stackos -- {packaged} mcp-bridge --runtime codex" in calls


def test_codex_adapter_finds_gui_app_and_node_manager_installs(tmp_path: Path, monkeypatch) -> None:
    app_cli = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    app_cli.parent.mkdir(parents=True)
    app_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    app_cli.chmod(0o755)
    nvm_cli = tmp_path / ".nvm" / "versions" / "node" / "v20.19.2" / "bin" / "codex"
    nvm_cli.parent.mkdir(parents=True)
    nvm_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    nvm_cli.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(codex, "MACOS_CODEX_APP_BUNDLE_CANDIDATES", (str(app_cli),))

    assert codex.resolve_codex_bin() == str(nvm_cli)

    nvm_cli.unlink()
    assert codex.resolve_codex_bin() == str(app_cli)


def test_codex_adapter_recognizes_chatgpt_bundled_codex_cli() -> None:
    assert (
        "/Applications/ChatGPT.app/Contents/Resources/codex"
        in codex.MACOS_CODEX_APP_BUNDLE_CANDIDATES
    )


def test_codex_adapter_recognizes_json_bridge_with_space_in_app_path(
    tmp_path: Path, monkeypatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "codex.log"
    packaged = (
        tmp_path / "StackOS Local.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    )
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    _write_fake_cli(
        bin_dir,
        "codex",
        log,
        get_output=json.dumps(
            {
                "name": "stackos",
                "transport": {
                    "type": "stdio",
                    "command": str(packaged),
                    "args": ["mcp-bridge", "--runtime", "codex"],
                    "env": None,
                    "env_vars": [],
                    "cwd": None,
                },
                "enabled": True,
            }
        ),
        get_returncode=0,
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))

    result = codex.inspect(tmp_path)

    assert result.ok is True
    assert result.status == "registered_current"
    assert result.command == [str(packaged), "mcp-bridge", "--runtime", "codex"]


def test_codex_adapter_does_not_replace_unsafe_json_entry(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "codex.log"
    _write_fake_cli(
        bin_dir,
        "codex",
        log,
        get_output=json.dumps(
            {
                "name": "stackos",
                "transport": {
                    "type": "sse",
                    "url": "https://remote.example.test/mcp",
                },
                "enabled": True,
            }
        ),
        get_returncode=0,
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = codex.register(tmp_path)

    assert result.ok is False
    assert result.status == "registered_unsafe"
    calls = log.read_text(encoding="utf-8").splitlines()
    assert not any(call.startswith("mcp remove") for call in calls)


def test_codex_adapter_force_replaces_current_json_entry(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "codex.log"
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    _write_fake_cli(
        bin_dir,
        "codex",
        log,
        get_output=json.dumps(
            {
                "name": "stackos",
                "transport": {
                    "type": "stdio",
                    "command": str(packaged),
                    "args": ["mcp-bridge", "--runtime", "codex"],
                    "env": None,
                    "env_vars": [],
                    "cwd": None,
                },
                "enabled": True,
            }
        ),
        get_returncode=0,
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))

    result = codex.register(tmp_path, force=True)

    assert result.ok is True
    calls = log.read_text(encoding="utf-8").splitlines()
    assert "mcp remove stackos" in calls
    assert f"mcp add stackos -- {packaged} mcp-bridge --runtime codex" in calls


def test_hermes_adapter_registers_local_bridge_after_daemon_is_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    hermes_cli = tmp_path / "bin" / "hermes"
    hermes_cli.parent.mkdir()
    hermes_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_cli.chmod(0o755)
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    config = tmp_path / "hermes-config.yaml"
    _write_hermes_config(config)
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(_binary: str, args: list[str], *, input_text: str | None = None):
        calls.append((args, input_text))
        if args == ["config", "path"]:
            return subprocess.CompletedProcess(args, 0, f"{config}\n", "")
        if args[:2] == ["mcp", "add"]:
            _write_hermes_config(
                config,
                {
                    "command": str(packaged),
                    "args": ["mcp-bridge", "--runtime", "hermes"],
                    "enabled": True,
                },
            )
            return subprocess.CompletedProcess(args, 0, "Saved 'stackos' to config.yaml\n", "")
        raise AssertionError(f"unexpected Hermes command: {args}")

    monkeypatch.setenv("STACKOS_HERMES_BIN", str(hermes_cli))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(hermes, "_daemon_is_ready", lambda: True)
    monkeypatch.setattr(hermes, "_run_hermes", fake_run)

    result = hermes.register(tmp_path)

    assert result.ok is True
    assert result.command == [str(packaged), "mcp-bridge", "--runtime", "hermes"]
    assert (
        [
            "mcp",
            "add",
            "stackos",
            "--command",
            str(packaged),
            "--args",
            "mcp-bridge",
            "--runtime",
            "hermes",
        ],
        "y\n",
    ) in calls


def test_hermes_adapter_refuses_registration_while_daemon_is_not_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    hermes_cli = tmp_path / "bin" / "hermes"
    hermes_cli.parent.mkdir()
    hermes_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_cli.chmod(0o755)
    monkeypatch.setenv("STACKOS_HERMES_BIN", str(hermes_cli))
    monkeypatch.setattr(hermes, "_daemon_is_ready", lambda: False)
    monkeypatch.setattr(
        hermes,
        "_run_hermes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not invoke Hermes")),
    )

    result = hermes.register(tmp_path)

    assert result.ok is False
    assert result.status == "register_failed"
    assert "running" in result.message.lower()


def test_hermes_adapter_does_not_probe_a_remote_stackos_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    hermes_cli = tmp_path / "bin" / "hermes"
    hermes_cli.parent.mkdir()
    hermes_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_cli.chmod(0o755)
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    config = tmp_path / "hermes-config.yaml"
    _write_hermes_config(config, {"url": "https://remote.example.test/mcp", "enabled": True})
    calls: list[list[str]] = []

    def fake_run(_binary: str, args: list[str], *, input_text: str | None = None):
        _ = input_text
        calls.append(args)
        if args == ["config", "path"]:
            return subprocess.CompletedProcess(args, 0, f"{config}\n", "")
        raise AssertionError(f"unexpected Hermes command: {args}")

    monkeypatch.setenv("STACKOS_HERMES_BIN", str(hermes_cli))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(hermes, "_run_hermes", fake_run)

    result = hermes.inspect(tmp_path)

    assert result.status == "registered_unsafe"
    assert calls == [["config", "path"]]


def test_hermes_adapter_recognizes_its_expected_local_bridge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    hermes_cli = tmp_path / "bin" / "hermes"
    hermes_cli.parent.mkdir()
    hermes_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_cli.chmod(0o755)
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    config = tmp_path / "hermes-config.yaml"
    _write_hermes_config(
        config,
        {
            "command": str(packaged),
            "args": ["mcp-bridge", "--runtime", "hermes"],
            "enabled": True,
        },
    )
    calls: list[list[str]] = []

    def fake_run(_binary: str, args: list[str], *, input_text: str | None = None):
        _ = input_text
        calls.append(args)
        if args == ["config", "path"]:
            return subprocess.CompletedProcess(args, 0, f"{config}\n", "")
        raise AssertionError(f"unexpected Hermes command: {args}")

    monkeypatch.setenv("STACKOS_HERMES_BIN", str(hermes_cli))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(hermes, "_run_hermes", fake_run)

    result = hermes.inspect(tmp_path)

    assert result.ok is True
    assert result.status == "registered_current"
    assert calls == [["config", "path"]]


def test_hermes_adapter_rejects_wrong_runtime_from_exact_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    hermes_cli = tmp_path / "bin" / "hermes"
    hermes_cli.parent.mkdir()
    hermes_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_cli.chmod(0o755)
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    config = tmp_path / "hermes-config.yaml"
    _write_hermes_config(
        config,
        {
            "command": str(packaged),
            "args": ["mcp-bridge", "--runtime", "codex"],
            "enabled": True,
        },
    )

    def fake_run(_binary: str, args: list[str], *, input_text: str | None = None):
        _ = input_text
        if args == ["config", "path"]:
            return subprocess.CompletedProcess(args, 0, f"{config}\n", "")
        raise AssertionError(f"unexpected Hermes command: {args}")

    monkeypatch.setenv("STACKOS_HERMES_BIN", str(hermes_cli))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(hermes, "_run_hermes", fake_run)

    result = hermes.inspect(tmp_path)

    assert result.ok is False
    assert result.status == "registered_stale"


def test_hermes_adapter_removes_disabled_registration_after_failed_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    hermes_cli = tmp_path / "bin" / "hermes"
    hermes_cli.parent.mkdir()
    hermes_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_cli.chmod(0o755)
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    config = tmp_path / "hermes-config.yaml"
    _write_hermes_config(config)
    calls: list[list[str]] = []

    def fake_run(_binary: str, args: list[str], *, input_text: str | None = None):
        _ = input_text
        calls.append(args)
        if args == ["config", "path"]:
            return subprocess.CompletedProcess(args, 0, f"{config}\n", "")
        if args[:2] == ["mcp", "add"]:
            _write_hermes_config(
                config,
                {
                    "command": str(packaged),
                    "args": ["mcp-bridge", "--runtime", "hermes"],
                    "enabled": False,
                },
            )
            return subprocess.CompletedProcess(
                args,
                0,
                "Saved 'stackos' to config (disabled)\n",
                "",
            )
        if args == ["mcp", "remove", "stackos"]:
            _write_hermes_config(config)
            return subprocess.CompletedProcess(args, 0, "Removed 'stackos' from config\n", "")
        raise AssertionError(f"unexpected Hermes command: {args}")

    monkeypatch.setenv("STACKOS_HERMES_BIN", str(hermes_cli))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(hermes, "_daemon_is_ready", lambda: True)
    monkeypatch.setattr(hermes, "_run_hermes", fake_run)

    result = hermes.register(tmp_path)

    assert result.ok is False
    assert result.status == "register_failed"
    assert ["mcp", "remove", "stackos"] in calls
    assert json.loads(config.read_text(encoding="utf-8"))["mcp_servers"] == {}


def test_resolve_bridge_command_accepts_workspace_root(tmp_path: Path, monkeypatch) -> None:
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    workspace_root = tmp_path / "client-workspace"
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))

    command = resolve_bridge_command(runtime="claude-desktop", workspace_root=workspace_root)

    assert command == [
        str(packaged),
        "mcp-bridge",
        "--workspace-root",
        str(workspace_root),
        "--runtime",
        "claude-desktop",
    ]


def test_resolve_bridge_command_uses_workspace_root_env(tmp_path: Path, monkeypatch) -> None:
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    workspace_root = tmp_path / "operator-selected"
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setenv("STACKOS_WORKSPACE_ROOT", str(workspace_root))

    command = resolve_bridge_command(runtime="gemini-cli")

    assert "--workspace-root" in command
    assert str(workspace_root) in command
    assert command[-2:] == ["--runtime", "gemini-cli"]


def test_codex_adapter_rejects_stale_packaged_bridge_command(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "codex.log"
    _write_fake_cli(
        bin_dir,
        "codex",
        log,
        list_output=(
            "stackos /Applications/OldStackOS.app/Contents/Resources/stackos/bin/stackos "
            "mcp-bridge --runtime codex\n"
        ),
    )
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))

    result = codex.inspect(tmp_path)

    assert result.ok is False
    assert result.status == "registered_stale"


def test_claude_desktop_adapter_preserves_sibling_servers_and_unknown_fields(
    tmp_path: Path, monkeypatch
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps(
            {
                "unrelated": True,
                "mcpServers": {"other": {"command": "other", "args": ["serve"]}},
            }
        ),
        encoding="utf-8",
    )
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "running")

    registered = claude_desktop.register(tmp_path)
    registered_payload = json.loads(config.read_text(encoding="utf-8"))
    removed = claude_desktop.remove(tmp_path)
    removed_payload = json.loads(config.read_text(encoding="utf-8"))

    assert registered.ok is True
    assert registered.needs_restart is True
    assert registered_payload["mcpServers"]["stackos"] == {
        "command": str(packaged),
        "args": ["mcp-bridge", "--runtime", "claude-desktop"],
    }
    assert removed_payload["unrelated"] is True
    assert removed_payload["mcpServers"]["other"] == {"command": "other", "args": ["serve"]}
    assert "stackos" not in removed_payload["mcpServers"]
    assert removed.ok is True


def test_claude_desktop_inspect_surfaces_pending_restart(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "running")

    registered = claude_desktop.register(tmp_path)
    inspected = claude_desktop.inspect(tmp_path)

    assert registered.status == "restart_required"
    assert inspected.ok is True
    assert inspected.status == "restart_required"
    assert inspected.needs_restart is True


def test_claude_desktop_register_is_noop_when_connection_is_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stackos": {
                        "command": str(packaged),
                        "args": ["mcp-bridge", "--runtime", "claude-desktop"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "running")

    registered = claude_desktop.register(tmp_path)

    assert registered.ok is True
    assert registered.status == "registered_current"
    assert registered.needs_restart is False
    assert not state_path(tmp_path).exists()


def test_claude_desktop_inspect_clears_restart_when_app_is_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "running")

    registered = claude_desktop.register(tmp_path)
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "not_running")
    inspected = claude_desktop.inspect(tmp_path)

    assert registered.status == "restart_required"
    assert inspected.ok is True
    assert inspected.status == "registered_current"
    assert inspected.needs_restart is False
    assert not state_path(tmp_path).exists() or "claude-desktop" not in json.loads(
        state_path(tmp_path).read_text(encoding="utf-8")
    )


def test_claude_desktop_inspect_clears_restart_after_app_relaunch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "running")

    registered = claude_desktop.register(tmp_path)
    monkeypatch.setattr(
        claude_desktop,
        "_claude_desktop_started_after",
        lambda _marked_at: True,
    )
    inspected = claude_desktop.inspect(tmp_path)

    assert registered.status == "restart_required"
    assert inspected.ok is True
    assert inspected.status == "registered_current"
    assert inspected.needs_restart is False
    assert not state_path(tmp_path).exists() or "claude-desktop" not in json.loads(
        state_path(tmp_path).read_text(encoding="utf-8")
    )


def test_claude_desktop_register_does_not_require_restart_when_app_is_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "not_running")

    registered = claude_desktop.register(tmp_path)
    inspected = claude_desktop.inspect(tmp_path)

    assert registered.ok is True
    assert registered.status == "registered_current"
    assert registered.needs_restart is False
    assert inspected.status == "registered_current"
    assert not state_path(tmp_path).exists()


def test_claude_desktop_restart_hint_expires(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    packaged = tmp_path / "stackos"
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stackos": {
                        "command": str(packaged),
                        "args": ["mcp-bridge", "--runtime", "claude-desktop"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    marker = state_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "claude-desktop": {
                    "surface": "desktop-json",
                    "config_path": str(config),
                    "command": [str(packaged), "mcp-bridge", "--runtime", "claude-desktop"],
                    "marked_at": time.time() - 7200,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))

    inspected = claude_desktop.inspect(tmp_path)

    assert inspected.ok is True
    assert inspected.status == "registered_current"
    assert inspected.needs_restart is False


def test_claude_desktop_adapter_does_not_overwrite_invalid_json(
    tmp_path: Path, monkeypatch
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    original = "{not json"
    config.write_text(original, encoding="utf-8")
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))

    result = claude_desktop.register(tmp_path)

    assert result.ok is False
    assert result.status == "config_unreadable"
    assert config.read_text(encoding="utf-8") == original


def test_gemini_adapter_registers_without_project_config(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gemini.log"
    _write_fake_cli(bin_dir, "gemini", log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = gemini_cli.register(tmp_path)

    assert result.ok is True
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(call.startswith("mcp add --scope user stackos ") for call in calls)
    assert not (tmp_path / ".gemini").exists()


def test_gemini_adapter_inspects_user_config_when_cli_list_is_silent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gemini.log"
    _write_fake_cli(bin_dir, "gemini", log)
    packaged = tmp_path / "StackOS.app" / "Contents" / "Resources" / "stackos" / "bin" / "stackos"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("#!/bin/sh\n", encoding="utf-8")
    packaged.chmod(0o755)
    settings = tmp_path / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stackos": {
                        "command": str(packaged),
                        "args": ["mcp-bridge", "--runtime", "gemini-cli"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(packaged))

    result = gemini_cli.inspect(tmp_path)

    assert result.ok is True
    assert result.status == "registered_current"
    assert result.command == [str(packaged), "mcp-bridge", "--runtime", "gemini-cli"]


def test_gemini_adapter_matches_exact_stackos_server_row(tmp_path: Path, monkeypatch) -> None:
    _write_token(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gemini.log"
    _write_fake_cli(
        bin_dir,
        "gemini",
        log,
        list_output="my-stackos-dev /tmp/stackos mcp-bridge --runtime gemini-cli\n",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = gemini_cli.inspect(tmp_path)

    assert result.ok is True
    assert result.advisory is True
    assert result.blocking is False
    assert result.status == "available_unregistered"


def test_gemini_unsupported_mcp_is_advisory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gemini.log"
    _write_fake_cli(bin_dir, "gemini", log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(
        gemini_cli,
        "_run_gemini",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["gemini", "mcp", "list"],
            returncode=2,
            stdout="",
            stderr="unknown command",
        ),
    )

    result = gemini_cli.inspect(tmp_path)

    assert result.ok is True
    assert result.advisory is True
    assert result.blocking is False
    assert result.status == "unsupported_host_version"


def test_host_mcp_token_preflight_respects_configured_state_dir(
    tmp_path: Path, monkeypatch
) -> None:
    configured_state = tmp_path / "configured-state"
    configured_state.mkdir()
    token = configured_state / "auth.token"
    token.write_text("unit-test-token\n", encoding="utf-8")
    token.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "codex.log"
    _write_fake_cli(bin_dir, "codex", log)
    monkeypatch.setenv("STACKOS_STATE_DIR", str(configured_state))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = codex.register(tmp_path / "other-home")

    assert result.ok is True
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(f"--state-dir {configured_state}" in call for call in calls)
