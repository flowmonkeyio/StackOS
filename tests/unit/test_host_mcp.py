from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

import stackos.host_mcp.service as host_mcp_service
from stackos.host_mcp.adapters import claude_desktop, codex, gemini_cli, hermes
from stackos.host_mcp.bridge import resolve_bridge_command
from stackos.host_mcp.restart_state import state_path
from stackos.host_mcp.result import HostMcpResult
from stackos.host_mcp.service import (
    HostMcpDefinition,
    HostMcpPolicy,
    normalize_result,
    repair_all,
)


def _write_token(home: Path) -> None:
    token = home / ".local" / "state" / "stackos" / "auth.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("unit-test-token\n", encoding="utf-8")
    token.chmod(0o600)


def _write_fake_cli(bin_dir: Path, name: str, log: Path, *, list_output: str = "") -> Path:
    script = bin_dir / name
    script.write_text(
        f"""#!{sys.executable}
from __future__ import annotations

import sys
from pathlib import Path

LOG = Path({str(log)!r})
LIST_OUTPUT = {list_output!r}


def main(argv):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(" ".join(argv) + "\\n")
    if argv[:2] == ["mcp", "list"]:
        print(LIST_OUTPUT, end="")
        return 0
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
    monkeypatch.setattr(codex, "MACOS_CHATGPT_BUNDLE_CANDIDATES", ())

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
    monkeypatch.setattr(codex, "MACOS_CHATGPT_BUNDLE_CANDIDATES", ())
    monkeypatch.setattr(codex, "MACOS_CODEX_APP_BUNDLE_CANDIDATES", (str(app_cli),))

    assert codex.resolve_codex_bin() == str(nvm_cli)

    nvm_cli.unlink()
    assert codex.resolve_codex_bin() == str(app_cli)


def test_codex_candidates_prefer_chatgpt_and_keep_standalone_fallbacks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    chatgpt_cli = tmp_path / "ChatGPT.app" / "Contents" / "Resources" / "codex"
    chatgpt_cli.parent.mkdir(parents=True)
    chatgpt_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    chatgpt_cli.chmod(0o755)
    codex_app_cli = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    codex_app_cli.parent.mkdir(parents=True)
    codex_app_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    codex_app_cli.chmod(0o755)
    standalone_cli = tmp_path / ".local" / "bin" / "codex"
    standalone_cli.parent.mkdir(parents=True)
    standalone_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    standalone_cli.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(
        codex,
        "MACOS_CHATGPT_BUNDLE_CANDIDATES",
        (str(chatgpt_cli),),
    )
    monkeypatch.setattr(
        codex,
        "MACOS_CODEX_APP_BUNDLE_CANDIDATES",
        (str(codex_app_cli),),
    )

    assert codex.resolve_codex_bins()[:3] == [
        str(chatgpt_cli),
        str(standalone_cli),
        str(codex_app_cli),
    ]


def test_host_lifecycle_contract_keeps_available_optional_hosts_nonblocking() -> None:
    policy = HostMcpPolicy(
        host_key="codex",
        display_name="ChatGPT / Codex",
        setup_policy="automatic",
    )
    result = normalize_result(
        HostMcpResult(
            host_key="codex",
            surface="shared-config",
            status="available_unregistered",
            message="StackOS is not registered with Codex.",
            ok=False,
            available=True,
            blocking=True,
        ),
        policy,
    )

    assert result.connection_state == "available"
    assert result.status_label == "Available"
    assert result.selected is False
    assert result.managed is False
    assert result.repairable is False
    assert result.ok is True
    assert result.blocking is False
    assert result.to_info()["display_name"] == "ChatGPT / Codex"


def test_host_lifecycle_contract_distinguishes_repair_from_review() -> None:
    policy = HostMcpPolicy(
        host_key="codex",
        display_name="ChatGPT / Codex",
        setup_policy="automatic",
    )
    managed = normalize_result(
        HostMcpResult(
            host_key="codex",
            surface="shared-config",
            status="registered_stale",
            message="Saved StackOS bridge is stale.",
            ok=False,
            available=True,
            blocking=True,
            managed=True,
            selected=True,
        ),
        policy,
    )
    unmanaged = normalize_result(
        HostMcpResult(
            host_key="codex",
            surface="shared-config",
            status="registered_unmanaged",
            message="The stackos name belongs to another connection.",
            ok=False,
            available=True,
            blocking=True,
            managed=False,
            selected=True,
        ),
        policy,
    )

    assert managed.connection_state == "repair_needed"
    assert managed.repairable is True
    assert unmanaged.connection_state == "review_required"
    assert unmanaged.repairable is False


def test_unselected_unsupported_host_is_advisory_not_attention() -> None:
    result = normalize_result(
        HostMcpResult(
            host_key="claude-code",
            surface="cli",
            status="unsupported_host_version",
            message="This CLI version cannot be inspected.",
            ok=False,
            available=True,
            blocking=True,
        ),
        HostMcpPolicy("claude-code", "Claude Code", "automatic"),
    )

    assert result.connection_state == "update_required"
    assert result.selected is False
    assert result.blocking is False
    assert result.advisory is True


def test_codex_structured_inspection_distinguishes_owned_stale_from_unmanaged() -> None:
    expected = [
        "/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos",
        "mcp-bridge",
        "--runtime",
        "codex",
    ]
    managed = codex._json_details_result(
        json.dumps(
            {
                "name": "stackos",
                "enabled": True,
                "transport": {
                    "type": "stdio",
                    "command": (
                        "/Applications/OldStackOS.app/Contents/Resources/stackos/bin/stackos"
                    ),
                    "args": ["mcp-bridge", "--runtime", "codex"],
                },
            }
        ),
        expected,
        "stackos",
    )
    unmanaged = codex._json_details_result(
        json.dumps(
            {
                "name": "stackos",
                "enabled": True,
                "transport": {
                    "type": "stdio",
                    "command": "other-tool",
                    "args": ["serve"],
                },
            }
        ),
        expected,
        "stackos",
    )

    assert managed is not None
    assert managed.status == "registered_stale"
    assert managed.managed is True
    assert unmanaged is not None
    assert unmanaged.status == "registered_unmanaged"
    assert unmanaged.managed is False


def test_codex_register_never_removes_unmanaged_same_name_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(codex, "resolve_codex_bins", lambda _value=None: ["/fake/codex"])
    monkeypatch.setattr(
        codex,
        "inspect",
        lambda _home, server_name="stackos": HostMcpResult(
            host_key="codex",
            surface="shared-config",
            status="registered_unmanaged",
            message="Another connection owns this name.",
            ok=False,
            available=True,
            blocking=True,
            selected=True,
            managed=False,
        ),
    )
    monkeypatch.setattr(
        codex,
        "_run_codex",
        lambda _binary, args: (
            calls.append(list(args)) or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )

    result = codex.register(tmp_path)

    assert result.status == "registered_unmanaged"
    assert not any(args[:2] in (["mcp", "remove"], ["mcp", "add"]) for args in calls)


def test_codex_register_falls_back_when_chatgpt_bundled_cli_lacks_mcp_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        codex,
        "resolve_codex_bins",
        lambda _value=None: ["/chatgpt/codex", "/standalone/codex"],
    )

    def fake_run(binary: str, args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append((binary, list(args)))
        if binary == "/chatgpt/codex":
            return subprocess.CompletedProcess(args, 2, "", "unsupported")
        if args[:2] == ["mcp", "get"]:
            return subprocess.CompletedProcess(args, 2, "", "not configured")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(codex, "_run_codex", fake_run)

    result = codex.register(tmp_path)

    assert result.status == "registered"
    assert ("/standalone/codex", ["mcp", "add", "stackos", "--", *result.command]) in calls
    assert not any(
        binary == "/chatgpt/codex" and args[:2] == ["mcp", "add"] for binary, args in calls
    )


def test_hermes_discovers_default_and_named_profiles_without_persisted_selection(
    tmp_path: Path,
) -> None:
    (tmp_path / ".hermes" / "profiles" / "work").mkdir(parents=True)
    (tmp_path / ".hermes" / "profiles" / "empty").mkdir(parents=True)

    assert hermes.discover_profile_names(tmp_path) == [None, "empty", "work"]


def test_hermes_profile_environment_resolves_back_to_profile_registry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile_home = tmp_path / ".hermes" / "profiles" / "work"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    assert hermes._hermes_root(tmp_path) == tmp_path / ".hermes"
    assert hermes._profile_home(tmp_path, "work") == profile_home


def test_hermes_profile_inspection_fails_closed_on_unmanaged_same_name_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile_home = tmp_path / ".hermes" / "profiles" / "work"
    profile_home.mkdir(parents=True)
    config_path = profile_home / "config.yaml"
    config_path.write_text(
        json.dumps(
            {
                "unrelated": {"preserve": True},
                "mcp_servers": {
                    "stackos": {
                        "command": "other-tool",
                        "args": ["serve"],
                        "custom": "keep",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda _value=None: "/fake/hermes")
    monkeypatch.setattr(
        hermes,
        "_hermes_config_path",
        lambda _home, profile=None: config_path if profile == "work" else tmp_path / "unused",
    )

    result = hermes.inspect_profile(tmp_path, profile="work")

    assert result.status == "registered_unmanaged"
    assert result.managed is False
    assert result.target == {"kind": "profile", "profile": "work"}
    assert json.loads(config_path.read_text(encoding="utf-8"))["unrelated"] == {"preserve": True}


def test_hermes_register_never_creates_a_missing_named_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda _value=None: "/fake/hermes")

    result = hermes.register(tmp_path, profile="missing")

    assert result.status == "register_failed"
    assert "does not exist" in result.message
    assert not (tmp_path / ".hermes").exists()


def test_hermes_registers_mcp_and_only_the_stackos_skill_in_existing_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    profile_home = tmp_path / ".hermes" / "profiles" / "work"
    config_path = profile_home / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("unrelated:\n  preserve: true\n", encoding="utf-8")
    sibling_skill = profile_home / "skills" / "research" / "SKILL.md"
    sibling_skill.parent.mkdir(parents=True)
    sibling_skill.write_text("keep me\n", encoding="utf-8")
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda _value=None: "/fake/hermes")
    monkeypatch.setattr(hermes, "daemon_preflight", lambda: None)

    def fake_run(
        _binary: str,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del input_text
        if "add" in args:
            command_index = args.index("--command") + 1
            args_index = args.index("--args") + 1
            config_path.write_text(
                (
                    "unrelated:\n"
                    "  preserve: true\n"
                    "mcp_servers:\n"
                    "  stackos:\n"
                    f"    command: {args[command_index]}\n"
                    "    args:\n" + "".join(f"      - {value}\n" for value in args[args_index:])
                ),
                encoding="utf-8",
            )
        elif "remove" in args:
            config_path.write_text(
                "unrelated:\n  preserve: true\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(hermes, "_run_hermes", fake_run)

    result = hermes.register(tmp_path, profile="work")

    assert result.status == "registered"
    assert result.target == {"kind": "profile", "profile": "work"}
    assert (profile_home / "skills" / "stackos" / "SKILL.md").is_file()
    assert sibling_skill.read_text(encoding="utf-8") == "keep me\n"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["unrelated"] == {
        "preserve": True
    }

    removed = hermes.remove(tmp_path, profile="work")

    assert removed.status == "removed"
    assert not (profile_home / "skills" / "stackos").exists()
    assert sibling_skill.read_text(encoding="utf-8") == "keep me\n"


def test_repair_all_connects_automatic_hosts_but_not_explicit_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    calls: list[str] = []

    class FakeAdapter:
        def __init__(self, key: str) -> None:
            self.HOST_KEY = key

        def inspect(self, _home: Path) -> HostMcpResult:
            return HostMcpResult(
                host_key=self.HOST_KEY,
                surface="test",
                status="available_unregistered",
                message="Available.",
                ok=True,
                available=True,
            )

        def register(self, _home: Path) -> HostMcpResult:
            calls.append(self.HOST_KEY)
            return HostMcpResult(
                host_key=self.HOST_KEY,
                surface="test",
                status="registered",
                message="Connected.",
                ok=True,
                available=True,
                selected=True,
                managed=True,
            )

        def remove(self, _home: Path) -> HostMcpResult:
            raise AssertionError("remove should not run")

    monkeypatch.setattr(
        host_mcp_service,
        "_definitions",
        lambda: (
            HostMcpDefinition(
                FakeAdapter("automatic"),
                HostMcpPolicy("automatic", "Automatic", "automatic"),
            ),
            HostMcpDefinition(
                FakeAdapter("explicit"),
                HostMcpPolicy("explicit", "Explicit", "explicit"),
            ),
        ),
    )

    aggregate = repair_all(home=tmp_path)

    assert aggregate.ok is True
    assert calls == ["automatic"]
    assert [result.connection_state for result in aggregate.results] == [
        "connected",
        "available",
    ]


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
    monkeypatch.setattr(codex, "MACOS_CHATGPT_BUNDLE_CANDIDATES", ())

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


def test_claude_desktop_never_overwrites_unmanaged_same_name_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    config = tmp_path / "claude_desktop_config.json"
    original = {
        "mcpServers": {
            "stackos": {
                "command": "other-tool",
                "args": ["serve"],
            }
        }
    }
    config.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setenv("STACKOS_CLAUDE_DESKTOP_CONFIG", str(config))

    inspected = claude_desktop.inspect(tmp_path)
    registered = claude_desktop.register(tmp_path)
    removed = claude_desktop.remove(tmp_path)

    assert inspected.status == "registered_unmanaged"
    assert inspected.managed is False
    assert registered.status == "registered_unmanaged"
    assert removed.status == "registered_unmanaged"
    assert json.loads(config.read_text(encoding="utf-8")) == original


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


def test_gemini_never_mutates_unmanaged_same_name_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_token(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(gemini_cli, "resolve_gemini_bin", lambda _value=None: "/fake/gemini")
    monkeypatch.setattr(
        gemini_cli,
        "_run_gemini",
        lambda _binary, args: (
            calls.append(list(args))
            or subprocess.CompletedProcess(
                args,
                0,
                "stackos other-tool serve\n" if args[:2] == ["mcp", "list"] else "",
                "",
            )
        ),
    )

    inspected = gemini_cli.inspect(tmp_path)
    registered = gemini_cli.register(tmp_path)
    removed = gemini_cli.remove(tmp_path)

    assert inspected.status == "registered_unmanaged"
    assert inspected.managed is False
    assert registered.status == "registered_unmanaged"
    assert removed.status == "registered_unmanaged"
    assert not any(args[:2] in (["mcp", "add"], ["mcp", "remove"]) for args in calls)


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
    monkeypatch.setattr(codex, "MACOS_CHATGPT_BUNDLE_CANDIDATES", ())

    result = codex.register(tmp_path / "other-home")

    assert result.ok is True
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(f"--state-dir {configured_state}" in call for call in calls)
