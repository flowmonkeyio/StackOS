"""Tests for the `stackos install` Typer subcommand and its primitives.

Mirrors the bash-script behavior (see
`tests/integration/test_install_scripts/`) but exercises the Python
code paths in isolation so they stay green on platforms that lack
`bash` or `rsync`.
"""

from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select
from typer.testing import CliRunner

import stackos.cli.daemon_commands as daemon_cli
import stackos.cli.daemon_processes as daemon_processes
import stackos.cli.doctor_commands as doctor_cli
import stackos.cli.launchd as launchd_cli
import stackos.cli.local_commands as local_cli
import stackos.db.migrate as migrate_module
import stackos.db.models  # noqa: F401  (populate SQLModel metadata)
import stackos.host_mcp.adapters.claude_code as claude_code_adapter
from stackos import claude_mcp
from stackos import install as installer
from stackos.auth_providers import AuthRepository
from stackos.cli import app
from stackos.config import Settings
from stackos.crypto.aes_gcm import configure_seed_path
from stackos.crypto.seed import ensure_seed_file
from stackos.db.connection import make_engine
from stackos.db.migrate import current_alembic_version, upgrade_to_head
from stackos.db.models import PayloadSecret, Project
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.secrets import PayloadSecretRepository

HEAD_REVISION = "0024_provider_object_references"


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox HOME with a token in place."""
    home = tmp_path / "home"
    home.mkdir()
    state = home / ".local" / "state" / "stackos"
    state.mkdir(parents=True)
    token = state / "auth.token"
    token.write_text("unit-test-token\n", encoding="utf-8")
    os.chmod(token, 0o600)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STACKOS_HOME", str(home))
    monkeypatch.setenv("STACKOS_DATA_DIR", str(home / ".local" / "share" / "stackos"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(state))
    return home


def _write_fake_claude_cli(bin_dir: Path, state_path: Path) -> Path:
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
    if argv[:1] != ["mcp"] or len(argv) < 2:
        save(data)
        return 2
    command = argv[1]
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
        row = servers.get(names[0])
        if row is not None and (scope is None or row.get("scope") == scope):
            del servers[names[0]]
        save(data)
        return 0
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
    save(data)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _fake_claude_state(path: Path) -> dict[str, object]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"servers": {}, "calls": []}


def test_detect_mode_clone(sandbox: Path) -> None:
    """In dev (running from the repo) we resolve `clone` mode."""
    assert installer.detect_mode() == "clone"


def test_copy_skills_clone_mode(sandbox: Path) -> None:
    target, count = installer.copy_skills("codex", home=sandbox)
    assert target == sandbox / ".codex" / "skills" / "stackos"
    assert target.is_dir()
    assert count == 1
    assert (target / "SKILL.md").is_file()


def test_copy_plugins_hydrates_catalogs(sandbox: Path) -> None:
    target, count = installer.copy_plugins(home=sandbox)

    assert target == sandbox / ".codex" / "plugins" / "stackos"
    assert count == 1
    assert (target / ".codex-plugin" / "plugin.json").is_file()
    mcp = json.loads((target / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["stackos"] == {
        "command": sys.executable,
        "args": ["-m", "stackos", "mcp-bridge"],
    }
    assert (target / "skills" / "stackos" / "SKILL.md").is_file()
    assert not (target / "skills" / "catalog").exists()


def test_copy_plugins_refreshes_existing_codex_cache(sandbox: Path) -> None:
    cache = sandbox / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos" / "0.1.0"
    (cache / ".codex-plugin").mkdir(parents=True)
    (cache / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (cache / ".mcp.json").write_text(
        '{"mcpServers":{"stackos":{"command":"stackos","args":["mcp-bridge"]}}}',
        encoding="utf-8",
    )
    stale_skill = cache / "skills" / "stackos" / "SKILL.md"
    stale_skill.parent.mkdir(parents=True)
    stale_skill.write_text("old skill\n", encoding="utf-8")
    stale_file = cache / "skills" / "legacy" / "stale.md"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text("remove me\n", encoding="utf-8")

    target, _count = installer.copy_plugins(home=sandbox)

    assert stale_skill.read_bytes() == (target / "skills" / "stackos" / "SKILL.md").read_bytes()
    assert not stale_file.exists()
    mcp = json.loads((cache / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["stackos"] == {
        "command": sys.executable,
        "args": ["-m", "stackos", "mcp-bridge"],
    }


def test_remove_plugins_removes_source_and_codex_cache(sandbox: Path) -> None:
    target, _count = installer.copy_plugins(home=sandbox)
    cache = sandbox / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos" / "0.1.0"
    (cache / ".codex-plugin").mkdir(parents=True)
    (cache / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

    removed_target, removed_cache = installer.remove_plugins(home=sandbox)

    assert removed_target == target
    assert removed_cache == sandbox / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos"
    assert not target.exists()
    assert not removed_cache.exists()


def test_doctor_plugin_count_ignores_other_codex_plugins(sandbox: Path) -> None:
    stackos_manifest = sandbox / ".codex" / "plugins" / "stackos" / ".codex-plugin"
    stackos_manifest.mkdir(parents=True)
    (stackos_manifest / "plugin.json").write_text("{}", encoding="utf-8")
    other_manifest = sandbox / ".codex" / "plugins" / "other" / ".codex-plugin"
    other_manifest.mkdir(parents=True)
    (other_manifest / "plugin.json").write_text("{}", encoding="utf-8")

    checks, details = doctor_cli._check_installed_assets(sandbox)

    assert doctor_cli._installed_plugin_count(sandbox) == 1
    assert details["plugins_count"] == 1
    assert checks["plugins_installed"] is True


def test_doctor_reports_managed_stackos_plugin_skill_current(sandbox: Path) -> None:
    cache = sandbox / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos" / "0.1.0"
    (cache / ".codex-plugin").mkdir(parents=True)
    (cache / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (cache / "skills" / "stackos").mkdir(parents=True)
    (cache / "skills" / "stackos" / "SKILL.md").write_text("old skill\n", encoding="utf-8")

    installer.copy_plugins(home=sandbox)
    installer.register_plugin_marketplace(home=sandbox)

    checks, details = doctor_cli._check_installed_assets(sandbox)
    skill = details["stackos_plugin_skill"]

    assert checks["stackos_plugin_skill_current"] is True
    assert isinstance(skill, dict)
    assert skill["cache_count"] == 1
    assert all(row["ok"] for row in skill["caches"])


def test_doctor_reports_stale_stackos_plugin_skill_cache(sandbox: Path) -> None:
    installer.copy_plugins(home=sandbox)
    installer.register_plugin_marketplace(home=sandbox)
    cache = sandbox / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos" / "0.1.0"
    (cache / ".codex-plugin").mkdir(parents=True)
    (cache / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (cache / "skills" / "stackos").mkdir(parents=True)
    (cache / "skills" / "stackos" / "SKILL.md").write_text("old skill\n", encoding="utf-8")

    checks, details = doctor_cli._check_installed_assets(sandbox)
    skill = details["stackos_plugin_skill"]

    assert checks["stackos_plugin_skill_current"] is False
    assert isinstance(skill, dict)
    assert skill["cache_count"] == 1
    assert skill["caches"][0]["ok"] is False
    assert "stackos install" in skill["repair"]


def test_ensure_playwright_browser_reports_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer.importlib.util, "find_spec", lambda name: None)

    ok, message = installer.ensure_playwright_browser()

    assert ok is False
    assert "not importable" in message


def test_ensure_playwright_browser_hides_existing_browser_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        installer,
        "playwright_chromium_executable_path",
        lambda **_kwargs: "/private/playwright/chromium",
    )

    ok, message = installer.ensure_playwright_browser()

    assert ok is True
    assert message == "Playwright Chromium browser present."
    assert "/private" not in message


def test_ensure_playwright_browser_installs_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(installer.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(installer, "playwright_chromium_executable_path", lambda **_kwargs: None)
    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    ok, message = installer.ensure_playwright_browser()

    assert ok is True
    assert message == "Playwright Chromium browser installed."
    assert calls == [[sys.executable, "-m", "playwright", "install", "chromium"]]


def test_ensure_playwright_browser_reports_install_failure_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        return subprocess.CompletedProcess(
            args,
            2,
            "",
            "failed at /private/playwright secret-token",
        )

    monkeypatch.setattr(installer.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(installer, "playwright_chromium_executable_path", lambda **_kwargs: None)
    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    ok, message = installer.ensure_playwright_browser()

    assert ok is False
    assert "Playwright Chromium install failed: exit_code=2" in message
    assert "output_sha256=" in message
    assert "/private" not in message
    assert "secret-token" not in message


def test_ensure_playwright_browser_reports_install_timeout_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        raise subprocess.TimeoutExpired(args, timeout=1, output="secret")

    monkeypatch.setattr(installer.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(installer, "playwright_chromium_executable_path", lambda **_kwargs: None)
    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    ok, message = installer.ensure_playwright_browser()

    assert ok is False
    assert "Playwright Chromium install failed: error_type=TimeoutExpired" in message
    assert "message_sha256=" in message
    assert "secret" not in message


def test_doctor_browser_runtime_hides_existing_browser_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_cli.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        doctor_cli,
        "playwright_chromium_executable_path",
        lambda **_kwargs: "/private/playwright/chromium",
    )

    ok, details = doctor_cli._check_browser_runtime()

    assert ok is True
    assert details["browser_downloaded"] is True
    assert details["browser_path_present"] is True
    assert "executable_path" not in details


def test_doctor_exits_9_for_stale_stackos_plugin_skill_cache(sandbox: Path) -> None:
    seed = sandbox / ".local" / "state" / "stackos" / "seed.bin"
    seed.write_bytes(b"0" * 32)
    os.chmod(seed, 0o600)
    installer.copy_plugins(home=sandbox)
    installer.register_plugin_marketplace(home=sandbox)
    cache = sandbox / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos" / "0.1.0"
    (cache / ".codex-plugin").mkdir(parents=True)
    (cache / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (cache / "skills" / "stackos").mkdir(parents=True)
    (cache / "skills" / "stackos" / "SKILL.md").write_text("old skill\n", encoding="utf-8")

    runner = CliRunner()
    json_result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(json_result.stdout)

    assert json_result.exit_code == 9
    assert payload["code"] == 9
    assert payload["checks"]["stackos_plugin_skill_current"] is False
    assert "stackos install" in payload["info"]["install_checks"]["stackos_plugin_skill"]["repair"]

    plain_result = runner.invoke(app, ["doctor"])

    assert plain_result.exit_code == 9
    assert "installed StackOS plugin or skill assets are stale" in plain_result.stdout
    assert "stackos install" in plain_result.stdout


def test_doctor_provider_readiness_reports_missing_connections(sandbox: Path) -> None:
    settings = Settings()
    settings.ensure_dirs()
    upgrade_to_head(settings)

    ok, details = doctor_cli._check_provider_readiness(settings, db_present=True)

    assert ok is True
    assert details["status"] == "needs_connections"
    assert details["providers_count"] > 0
    assert details["connected_count"] == 0
    assert details["setup_required_count"] == details["providers_count"]
    assert "connections" in str(details["connections_url"])
    assert "auth.status" in str(details["repair"])


def test_doctor_provider_readiness_does_not_sync_catalog(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    settings.ensure_dirs()
    upgrade_to_head(settings)

    def fail_sync(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor provider readiness must stay read-only")

    monkeypatch.setattr(PluginRepository, "sync_builtin_plugins", fail_sync)

    ok, details = doctor_cli._check_provider_readiness(settings, db_present=True)

    assert ok is True
    assert details["providers_count"] > 0
    assert details["status"] == "needs_connections"


def test_doctor_provider_readiness_summarizes_connections_without_secrets(
    sandbox: Path,
) -> None:
    settings = Settings()
    init_result = CliRunner().invoke(app, ["init"], catch_exceptions=False)
    assert init_result.exit_code == 0, init_result.stdout
    configure_seed_path(settings.seed_path)
    upgrade_to_head(settings)

    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            PluginRepository(session).sync_builtin_plugins()
            project = Project(
                slug="provider-readiness",
                name="Provider Readiness",
                domain="example.test",
                locale="en",
                is_active=True,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            assert project.id is not None
            AuthRepository(session).store_credential(
                project_id=project.id,
                provider_key="firecrawl",
                auth_method_key="api_key",
                profile_key="primary",
                label="Primary Firecrawl",
                fields={"api_key": "fc-secret"},
            )
    finally:
        engine.dispose()

    ok, details = doctor_cli._check_provider_readiness(settings, db_present=True)

    assert ok is True
    assert details["status"] in {"partial", "ready"}
    assert "firecrawl" in details["connected_provider_keys"]
    rendered = json.dumps(details)
    assert "fc-secret" not in rendered
    assert "encrypted_payload" not in rendered


def test_bridge_autostart_spawns_loopback_daemon(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "stackos",
        state_dir=sandbox / ".local" / "state" / "stackos",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        pid = 12345

        def poll(self) -> None:
            return None

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda *args, **kwargs: False)
    monkeypatch.setattr(daemon_processes, "_wait_for_daemon", lambda *args, **kwargs: True)
    monkeypatch.setattr(daemon_processes.subprocess, "Popen", fake_popen)

    ok, message = daemon_cli._autostart_bridge_daemon(settings, "127.0.0.1", 5180)

    assert ok is True
    assert "auto-started daemon" in message
    assert calls
    args, kwargs = calls[0]
    assert args == [
        sys.executable,
        "-m",
        "stackos",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "5180",
        "--log-level",
        settings.log_level,
    ]
    assert kwargs["stdin"] is daemon_processes.subprocess.DEVNULL
    assert kwargs["stderr"] is daemon_processes.subprocess.STDOUT
    assert kwargs["start_new_session"] is True


def test_bridge_autostart_rejects_non_loopback(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "stackos",
        state_dir=sandbox / ".local" / "state" / "stackos",
    )

    def fail_popen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Popen should not be called for non-loopback hosts")

    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda *args, **kwargs: False)
    monkeypatch.setattr(daemon_processes.subprocess, "Popen", fail_popen)

    ok, message = daemon_cli._autostart_bridge_daemon(settings, "0.0.0.0", 5180)

    assert ok is False
    assert "non-loopback" in message


def test_copy_skills_idempotent(sandbox: Path) -> None:
    installer.copy_skills("codex", home=sandbox)
    target = sandbox / ".codex" / "skills" / "stackos"
    snap1 = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    installer.copy_skills("codex", home=sandbox)
    snap2 = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert snap1 == snap2


def test_copy_skills_deletes_stale(sandbox: Path) -> None:
    installer.copy_skills("codex", home=sandbox)
    target = sandbox / ".codex" / "skills" / "stackos"
    stale = target / "legacy-seo-skill" / "stale.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("not in source\n", encoding="utf-8")
    installer.copy_skills("codex", home=sandbox)
    assert not stale.exists()


def test_register_mcp_claude_uses_shared_contract(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = sandbox / ".claude" / "mcp.json"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        claude_code_adapter,
        "resolve_bridge_command",
        lambda **_: [
            "/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos",
            "mcp-bridge",
        ],
    )

    def fake_register(**kwargs: object) -> claude_mcp.ClaudeMcpResult:
        captured.update(kwargs)
        return claude_mcp.ClaudeMcpResult(
            ok=True,
            status="registered",
            message="Registered MCP 'stackos' with Claude Code using user scope.",
            scope="user",
            transport="stdio",
            command=list(kwargs["bridge_command"]),  # type: ignore[index]
        )

    monkeypatch.setattr(claude_mcp, "register", fake_register)

    msg = installer.register_mcp_claude(home=sandbox, target=target)

    assert "Registered MCP 'stackos'" in msg
    assert captured["home"] == sandbox
    assert captured["bridge_command"] == [
        "/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos",
        "mcp-bridge",
    ]
    assert not target.exists()


def test_register_mcp_claude_skips_when_claude_absent(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = sandbox / ".claude" / "mcp.json"
    monkeypatch.setattr(
        claude_mcp,
        "register",
        lambda **_: claude_mcp.ClaudeMcpResult(
            ok=True,
            status="claude_absent",
            message="Claude Code CLI not found; skipped Claude MCP registration.",
        ),
    )

    msg = installer.register_mcp_claude(home=sandbox, target=target)

    assert "Claude Code CLI not found" in msg
    assert not target.exists()


def test_register_mcp_claude_remove_uses_shared_contract(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = sandbox / ".claude" / "mcp.json"
    captured: dict[str, object] = {}

    def fake_remove(**kwargs: object) -> claude_mcp.ClaudeMcpResult:
        captured.update(kwargs)
        return claude_mcp.ClaudeMcpResult(
            ok=True,
            status="removed",
            message="Removed MCP 'stackos' from Claude Code user scope.",
        )

    monkeypatch.setattr(claude_mcp, "remove", fake_remove)

    msg = installer.register_mcp_claude(home=sandbox, target=target, remove=True)

    assert "Removed MCP 'stackos'" in msg
    assert captured["home"] == sandbox
    assert not target.exists()


def test_register_mcp_claude_returns_repair_message_on_failure(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_mcp,
        "register",
        lambda **_: claude_mcp.ClaudeMcpResult(
            ok=False,
            status="registration_failed",
            message="Claude Code MCP registration failed.",
            repair="Check `claude mcp add --help`, then rerun `stackos install --mcp-only`.",
        ),
    )

    msg = installer.register_mcp_claude(home=sandbox)

    assert "registration failed" in msg
    assert "claude mcp add --help" in msg


def test_register_mcp_codex_not_detected(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When Codex cannot be found anywhere, the helper returns a friendly notice."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("STACKOS_CODEX_BIN", str(empty / "codex"))
    msg = installer.register_mcp_codex(home=sandbox, port=5180)
    assert "was not detected" in msg


def test_cli_install_skills_only_subcommand(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["install", "--skills-only", "--skip-doctor"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert (sandbox / ".codex" / "skills" / "stackos" / "SKILL.md").is_file()
    assert (sandbox / ".claude" / "skills" / "stackos" / "SKILL.md").is_file()
    assert not (sandbox / ".codex" / "plugins" / "stackos").exists()


def test_cli_install_default_installs_plugin_and_skill_mirrors(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer, "ensure_playwright_browser", lambda: (True, "browser ok"))
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda home: (True, ["codex ok", "claude ok"]),
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["install", "--skip-doctor"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert (sandbox / ".codex" / "plugins" / "stackos" / ".codex-plugin" / "plugin.json").is_file()
    assert (sandbox / ".codex" / "skills" / "stackos" / "SKILL.md").is_file()
    assert (sandbox / ".claude" / "skills" / "stackos" / "SKILL.md").is_file()
    assert (sandbox / ".local" / "share" / "stackos" / "stackos.db").is_file()
    assert current_alembic_version(Settings()) == HEAD_REVISION


def test_cli_install_preserves_seed_and_token_on_rerun(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer, "ensure_playwright_browser", lambda: (True, "browser ok"))
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda home: (True, ["codex ok", "claude ok"]),
    )
    monkeypatch.setattr(installer, "copy_skills", lambda runtime, home: (home / runtime, 1))
    monkeypatch.setattr(
        installer,
        "copy_plugins",
        lambda home: (home / ".codex/plugins/stackos", 1),
    )
    monkeypatch.setattr(installer, "register_plugin_marketplace", lambda home: "marketplace ok")
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda home: (True, ["codex ok", "claude ok"]),
    )
    monkeypatch.setattr(local_cli, "doctor", lambda json_output=False: None)

    runner = CliRunner()
    first = runner.invoke(app, ["install"], catch_exceptions=False)
    assert first.exit_code == 0, first.stdout
    state = sandbox / ".local" / "state" / "stackos"
    seed_bytes = (state / "seed.bin").read_bytes()
    token_text = (state / "auth.token").read_text(encoding="utf-8")

    second = runner.invoke(app, ["install"], catch_exceptions=False)

    assert second.exit_code == 0, second.stdout
    assert (state / "seed.bin").read_bytes() == seed_bytes
    assert (state / "auth.token").read_text(encoding="utf-8") == token_text


def test_cli_install_mcp_only_registers_claude_with_fake_cli(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "claude-state.json"
    _write_fake_claude_cli(bin_dir, state_path)
    monkeypatch.setenv("PATH", str(bin_dir))

    result = CliRunner().invoke(
        app,
        ["install", "--mcp-only", "--skip-doctor"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    state = _fake_claude_state(state_path)
    assert ["mcp", "remove", "stackos", "--scope", "user"] in state["calls"]
    assert ["mcp", "add", "--scope", "user", "--transport", "stdio"] in [
        call[:6] for call in state["calls"]
    ]
    assert not (sandbox / ".claude" / "mcp.json").exists()


def test_cli_uninstall_removes_integrations_and_preserves_state(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl = bin_dir / "launchctl"
    launchctl.write_text(
        '#!/bin/sh\ncase "$1" in\n  print) exit 1 ;;\n  *) exit 0 ;;\nesac\n',
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    claude_state = tmp_path / "claude-state.json"
    _write_fake_claude_cli(bin_dir, claude_state)
    claude_state.write_text(
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
    monkeypatch.setenv("PATH", str(bin_dir))

    data_dir = sandbox / ".local" / "share" / "stackos"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "stackos.db"
    db_path.write_text("db stays\n", encoding="utf-8")
    state_dir = sandbox / ".local" / "state" / "stackos"
    seed_path = state_dir / "seed.bin"
    token_path = state_dir / "auth.token"
    seed_path.write_bytes(b"seed stays")
    token_before = token_path.read_text(encoding="utf-8")

    (sandbox / ".codex" / "skills" / "stackos").mkdir(parents=True)
    (sandbox / ".claude" / "skills" / "stackos").mkdir(parents=True)
    plugin_root = sandbox / ".codex" / "plugins" / "stackos"
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    cache_root = sandbox / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos"
    (cache_root / "0.1.0" / ".codex-plugin").mkdir(parents=True)
    (cache_root / "0.1.0" / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

    marketplace = sandbox / ".agents" / "plugins" / "marketplace.json"
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
    claude_mcp = sandbox / ".claude" / "mcp.json"
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
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist><dict /></plist>\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["uninstall"], catch_exceptions=False)

    assert result.exit_code == 0, result.stdout
    assert "Preserved database directory" in result.stdout
    assert "Preserved daemon state directory" in result.stdout
    assert not plist.exists()
    assert not (sandbox / ".codex" / "skills" / "stackos").exists()
    assert not (sandbox / ".claude" / "skills" / "stackos").exists()
    assert not plugin_root.exists()
    assert not cache_root.exists()
    marketplace_payload = json.loads(marketplace.read_text(encoding="utf-8"))
    assert [item["name"] for item in marketplace_payload["plugins"]] == ["other"]
    claude_payload = json.loads(claude_mcp.read_text(encoding="utf-8"))
    assert "stackos" not in claude_payload["mcpServers"]
    assert "other-server" in claude_payload["mcpServers"]
    fake_claude_payload = _fake_claude_state(claude_state)
    assert ["mcp", "remove", "stackos", "--scope", "user"] in fake_claude_payload["calls"]
    assert "stackos" not in fake_claude_payload["servers"]
    assert "other" in fake_claude_payload["servers"]
    assert db_path.read_text(encoding="utf-8") == "db stays\n"
    assert seed_path.read_bytes() == b"seed stays"
    assert token_path.read_text(encoding="utf-8") == token_before


def test_cli_uninstall_fails_when_launchd_unload_fails(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist><dict /></plist>\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["uninstall"])

    assert result.exit_code == 1
    assert "launchd autostart removal failed" in result.stderr
    assert "unload failed" in result.stderr
    assert plist.exists()


def test_cli_backup_creates_private_archive_with_manifest(sandbox: Path, tmp_path: Path) -> None:
    settings = Settings()
    settings.ensure_dirs()
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("CREATE TABLE backup_probe (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO backup_probe (value) VALUES ('db stays')")
        conn.commit()
    settings.seed_path.write_bytes(b"seed stays")
    settings.token_path.write_text("token stays\n", encoding="utf-8")

    output = tmp_path / "stackos-backup.zip"
    result = CliRunner().invoke(
        app,
        ["backup", "--output", str(output)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert output.is_file()
    assert output.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "data/stackos.db",
            "state/auth.token",
            "state/seed.bin",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema"] == "stackos.local-backup.v1"
        assert manifest["restore_status"] == "manual-only; automated restore is not implemented"
        assert [item["path"] for item in manifest["included"]] == [
            "data/stackos.db",
            "state/seed.bin",
            "state/auth.token",
        ]
        extracted_db = tmp_path / "restored-copy.db"
        extracted_db.write_bytes(archive.read("data/stackos.db"))

    with sqlite3.connect(extracted_db) as conn:
        value = conn.execute("SELECT value FROM backup_probe WHERE id = 1").fetchone()[0]
    assert value == "db stays"


def test_cli_backup_includes_staged_seed_backup(sandbox: Path, tmp_path: Path) -> None:
    settings = Settings()
    settings.ensure_dirs()
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("CREATE TABLE backup_probe (id INTEGER PRIMARY KEY)")
        conn.commit()
    settings.seed_path.write_bytes(b"seed stays")
    settings.seed_path.with_suffix(settings.seed_path.suffix + ".bak").write_bytes(b"old seed")
    settings.token_path.write_text("token stays\n", encoding="utf-8")

    output = tmp_path / "stackos-backup.zip"
    result = CliRunner().invoke(
        app,
        ["backup", "--output", str(output)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    with zipfile.ZipFile(output) as archive:
        assert "state/seed.bin.bak" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert "state/seed.bin.bak" in [item["path"] for item in manifest["included"]]
        assert archive.read("state/seed.bin.bak") == b"old seed"


def test_cli_backup_refuses_missing_required_state(sandbox: Path) -> None:
    output = sandbox / "backup.zip"

    result = CliRunner().invoke(app, ["backup", "--output", str(output)])

    assert result.exit_code == 1
    assert "required local state is missing" in result.stderr
    assert not output.exists()


def test_cli_install_tolerates_daemon_down_doctor(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer, "ensure_playwright_browser", lambda: (True, "browser ok"))
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda home: (True, ["codex ok", "claude ok"]),
    )

    def daemon_down_doctor(json_output: bool = False) -> None:
        _ = json_output
        raise local_cli.typer.Exit(code=1)

    monkeypatch.setattr(local_cli, "doctor", daemon_down_doctor)

    result = CliRunner().invoke(app, ["install"], catch_exceptions=False)

    assert result.exit_code == 0, result.stdout
    assert "daemon is not running yet" in result.stdout
    assert "stackos start" in result.stdout


def test_cli_install_preserves_blocking_doctor_failures(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer, "ensure_playwright_browser", lambda: (True, "browser ok"))
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda home: (True, ["codex ok", "claude ok"]),
    )

    def seed_failure_doctor(json_output: bool = False) -> None:
        _ = json_output
        raise local_cli.typer.Exit(code=8)

    monkeypatch.setattr(local_cli, "doctor", seed_failure_doctor)

    result = CliRunner().invoke(app, ["install"])

    assert result.exit_code == 8


def test_cli_install_rejects_multiple_only_flags(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["install", "--skills-only", "--plugins-only", "--skip-doctor"],
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr if hasattr(result, "stderr") else result.output


def test_cli_install_rejects_force_without_launchd(sandbox: Path) -> None:
    result = CliRunner().invoke(app, ["install", "--force", "--skip-doctor"])

    assert result.exit_code == 2
    output = result.stderr if hasattr(result, "stderr") else result.output
    assert "only valid with --launchd" in output


def test_cli_install_launchd_force_overwrites_plist(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launchd_calls: list[bool] = []

    monkeypatch.setattr(installer, "detect_mode", lambda: "package")
    monkeypatch.setattr(installer, "ensure_playwright_browser", lambda: (True, "browser ok"))
    monkeypatch.setattr(installer, "copy_skills", lambda runtime, home: (home / runtime, 1))
    monkeypatch.setattr(
        installer,
        "copy_plugins",
        lambda home: (home / ".codex/plugins/stackos", 1),
    )
    monkeypatch.setattr(installer, "register_plugin_marketplace", lambda home: "marketplace ok")
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda home: (True, ["codex ok", "claude ok"]),
    )

    def fake_install_launchd(
        settings: Settings,
        *,
        home: Path,
        force: bool,
        host: str,
        port: int,
        log_level: str,
    ) -> tuple[bool, str]:
        _ = settings, home, host, port, log_level
        launchd_calls.append(force)
        return True, "installed launchd plist"

    monkeypatch.setattr(launchd_cli, "_install_launchd_autostart", fake_install_launchd)

    result = CliRunner().invoke(
        app,
        ["install", "--launchd", "--force", "--skip-doctor"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stdout
    assert launchd_calls == [True]
    assert "installed launchd plist" in result.stdout


def test_cli_init_idempotent(sandbox: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(app, ["init"], catch_exceptions=False)
    assert first.exit_code == 0, first.stdout
    state = sandbox / ".local" / "state" / "stackos"
    seed_path = state / "seed.bin"
    assert seed_path.is_file()
    seed_bytes_first = seed_path.read_bytes()

    second = runner.invoke(app, ["init"], catch_exceptions=False)
    assert second.exit_code == 0
    # Seed must NOT have rotated on idempotent re-run.
    assert seed_path.read_bytes() == seed_bytes_first


def test_cli_init_force_rejected(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--force"])
    assert result.exit_code == 2


def test_cli_migrate_stamps_create_all_schema(sandbox: Path) -> None:
    """A daemon-created DB stuck at the empty revision upgrades cleanly."""
    settings = Settings()
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    SQLModel.metadata.create_all(engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('0001_initial_empty')")
            )
    finally:
        engine.dispose()

    result = CliRunner().invoke(app, ["migrate"], catch_exceptions=False)

    assert result.exit_code == 0, result.stdout
    assert "stamped existing create_all schema" in result.stdout

    engine = make_engine(settings.db_path)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()
    assert version == HEAD_REVISION


def test_upgrade_to_head_works_outside_repo_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    monkeypatch.chdir(tmp_path)

    result = upgrade_to_head(settings)

    assert result.stamped_existing_schema is False
    assert current_alembic_version(settings) == HEAD_REVISION


def test_upgrade_to_head_works_without_alembic_ini(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    monkeypatch.setattr(
        migrate_module,
        "_alembic_ini_path",
        lambda: tmp_path / "missing-alembic.ini",
    )

    result = upgrade_to_head(settings)

    assert result.stamped_existing_schema is False
    assert current_alembic_version(settings) == HEAD_REVISION


def test_cli_rotate_token_requires_yes(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["rotate-token"])
    assert result.exit_code == 2


def test_cli_rotate_token_reports_restart_even_when_mcp_repair_fails(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    token_path = sandbox / ".local" / "state" / "stackos" / "auth.token"
    before = token_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        installer,
        "repair_mcp_hosts",
        lambda: (False, ["codex: repair failed"]),
    )

    result = runner.invoke(app, ["rotate-token", "--yes"])

    assert result.exit_code == 0
    assert token_path.read_text(encoding="utf-8") != before
    assert "token rotated; MCP host repair needs attention" in result.output
    assert "run `stackos restart`" in result.output


def test_cli_rotate_seed_reencrypts_payload_secrets(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    settings.ensure_dirs()
    ensure_seed_file(settings.seed_path)
    configure_seed_path(settings.seed_path)
    upgrade_to_head(settings)
    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            project = (
                ProjectRepository(session)
                .create(
                    slug="payload-secret-rotation",
                    name="Payload Secret Rotation",
                    domain="rotation.example.test",
                    locale="en-US",
                )
                .data
            )
            assert project.id is not None
            secret_ref = (
                PayloadSecretRepository(session)
                .set(
                    project_id=project.id,
                    value="rotation-canary",
                )
                .secret_ref
            )
            before = session.exec(select(PayloadSecret)).one().encrypted_payload
    finally:
        engine.dispose()

    monkeypatch.setattr(local_cli, "get_settings", lambda: settings)
    result = CliRunner().invoke(app, ["rotate-seed", "--reencrypt"])

    assert result.exit_code == 0
    assert "rotated 1 row(s)" in result.output
    configure_seed_path(settings.seed_path)
    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            row = session.exec(select(PayloadSecret)).one()
            assert row.encrypted_payload != before
            assert (
                PayloadSecretRepository(session).resolve(
                    project_id=row.project_id,
                    secret_ref=secret_ref,
                )
                == "rotation-canary"
            )
            row.encrypted_payload = b"tampered" + row.encrypted_payload
            session.add(row)
            session.commit()
    finally:
        engine.dispose()

    decrypt_ok, issues = doctor_cli._check_credentials_decrypt(settings, True)
    assert decrypt_ok is False
    assert issues[0]["payload_secret_id"] is not None


def test_cli_start_spawns_background_daemon(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, str, Path]] = []

    monkeypatch.setattr(daemon_processes, "_discover_daemon_processes", lambda *args: ([], []))
    monkeypatch.setattr(daemon_processes, "_daemon_health_ok", lambda *args, **kwargs: False)
    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda *args, **kwargs: False)

    def fake_spawn(
        settings: Settings,
        host: str,
        port: int,
        *,
        log_level: str,
        log_path: Path,
        cwd: Path,
        ready_timeout: float = 20.0,
    ) -> tuple[bool, str]:
        _ = settings
        _ = ready_timeout
        calls.append((host, port, log_level, log_path))
        return True, "started daemon pid=42; url=http://127.0.0.1:5180"

    monkeypatch.setattr(daemon_processes, "_spawn_detached_daemon", fake_spawn)

    result = CliRunner().invoke(app, ["start"], catch_exceptions=False)

    assert result.exit_code == 0, result.stdout
    assert "started daemon pid=42" in result.stdout
    assert calls == [("127.0.0.1", 5180, "INFO", sandbox / ".local/state/stackos/daemon.log")]


def test_launchd_autostart_install_writes_python_plist(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "stackos",
        state_dir=sandbox / ".local" / "state" / "stackos",
    )
    launchctl_calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        launchctl_calls.append(args)
        if args[1] == "print":
            return subprocess.CompletedProcess(args, 1, "", "not loaded")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(launchd_cli.shutil, "which", lambda name: "/bin/launchctl")
    monkeypatch.setattr(launchd_cli.subprocess, "run", fake_run)

    ok, message = launchd_cli._install_launchd_autostart(
        settings,
        home=sandbox,
        force=False,
        host="127.0.0.1",
        port=5180,
        log_level="INFO",
    )

    assert ok is True
    assert "installed launchd plist" in message
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    content = plist.read_text(encoding="utf-8")
    assert sys.executable in content
    assert "<string>stackos</string>" in content
    assert "<string>serve</string>" in content
    assert str(settings.log_path) in content
    assert "auth.token" not in content
    assert "seed.bin" not in content
    assert "Authorization" not in content
    assert "Bearer" not in content
    assert "STACKOS_TOKEN" not in content
    assert "AssociatedBundleIdentifiers" not in content
    assert launchctl_calls


def test_packaged_launchd_plist_associates_with_stackos_app(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "stackos",
        state_dir=sandbox / ".local" / "state" / "stackos",
    )
    monkeypatch.setenv(
        "STACKOS_PACKAGED_CLI",
        "/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos",
    )

    content = launchd_cli._launchd_plist_content(
        settings,
        home=sandbox,
        host="127.0.0.1",
        port=5180,
        log_level="INFO",
    )

    payload = plistlib.loads(content)
    assert payload["AssociatedBundleIdentifiers"] == ["io.stackos.desktop"]


def test_launchd_autostart_requires_force_for_different_plist(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "stackos",
        state_dir=sandbox / ".local" / "state" / "stackos",
    )
    plist = sandbox / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist><dict>custom</dict></plist>", encoding="utf-8")

    monkeypatch.setattr(launchd_cli.shutil, "which", lambda name: "/bin/launchctl")

    ok, message = launchd_cli._install_launchd_autostart(
        settings,
        home=sandbox,
        force=False,
        host="127.0.0.1",
        port=5180,
        log_level="INFO",
    )

    assert ok is False
    assert "rerun with --force" in message
    assert "custom" in plist.read_text(encoding="utf-8")


def test_codex_mcp_doctor_accepts_bridge_entries_only() -> None:
    from stackos.host_mcp.bridge import resolve_bridge_command

    expected = " ".join(["stackos", *resolve_bridge_command(runtime="codex")])
    assert not doctor_cli._codex_mcp_line_is_bridge("stackos stdio -")
    assert doctor_cli._codex_mcp_line_is_bridge(expected)
    assert not doctor_cli._codex_mcp_line_is_bridge("stackos http://127.0.0.1:5180/mcp")
    assert not doctor_cli._codex_mcp_line_is_bridge(
        "stackos --url http://127.0.0.1:5180/mcp --bearer-token-env-var STACKOS_TOKEN"
    )


def test_claude_mcp_doctor_treats_legacy_json_as_advisory(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = sandbox / ".claude" / "mcp.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
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
    empty_path = sandbox / "empty-bin"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("STACKOS_CLAUDE_BIN", str(sandbox / "missing-claude"))

    ok, info = doctor_cli._check_claude_mcp_registered(sandbox)

    assert ok is False
    assert info["status"] == "absent"
    assert info["available"] is False
    assert info["advisory"] is True


def test_claude_mcp_doctor_surfaces_stale_cli_entry(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_adapter.claude_mcp,
        "inspect",
        lambda **_: claude_mcp.ClaudeMcpResult(
            ok=False,
            status="stale",
            message="Claude Code has a StackOS MCP entry, but it is stale.",
            scope="project",
            transport="stdio",
            command=["python3", "-m", "stackos", "mcp-bridge"],
            repair="Run `stackos install --mcp-only` or desktop Repair.",
        ),
    )

    ok, info = doctor_cli._check_claude_mcp_registered(sandbox)

    assert ok is False
    assert info["status"] == "registered_stale"
    assert info["host_key"] == "claude-code"
    assert info["repair"] == "Run `stackos install --mcp-only` or desktop Repair."


def test_doctor_plain_output_reports_claude_absent_without_blocking(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = sandbox / ".local" / "state" / "stackos" / "seed.bin"
    seed.write_bytes(b"0" * 32)
    os.chmod(seed, 0o600)
    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda host, port: True)
    monkeypatch.setattr(
        doctor_cli, "_check_credentials_decrypt", lambda settings, db_present: (True, [])
    )
    monkeypatch.setattr(
        doctor_cli, "_check_alembic_at_head", lambda settings, db_present: (True, None)
    )
    monkeypatch.setattr(doctor_cli, "_check_scheduler_jobs", lambda settings: (True, 4))
    monkeypatch.setattr(doctor_cli, "_check_browser_runtime", lambda: (True, {"repair": None}))
    monkeypatch.setattr(
        doctor_cli,
        "_check_installed_assets",
        lambda home: (
            {
                "codex_skills_installed": True,
                "claude_skills_installed": True,
                "plugins_installed": True,
                "plugin_marketplace_registered": True,
                "stackos_plugin_skill_current": True,
            },
            {},
        ),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_check_mcp_hosts",
        lambda home: (
            True,
            [
                {
                    "host_key": "codex",
                    "surface": "shared-config",
                    "status": "registered_current",
                    "message": "Codex StackOS MCP registration is healthy.",
                    "ok": True,
                    "available": True,
                    "advisory": False,
                    "blocking": False,
                    "needs_restart": False,
                    "command": [],
                    "config_path": None,
                    "repair": None,
                    "warnings": [],
                },
                {
                    "host_key": "claude-code",
                    "surface": "cli",
                    "status": "absent",
                    "message": "Claude Code CLI not found; skipping Claude MCP registration.",
                    "ok": True,
                    "available": False,
                    "advisory": True,
                    "blocking": False,
                    "needs_restart": False,
                    "command": [],
                    "config_path": None,
                    "repair": "Install Claude Code, then run desktop Repair.",
                    "warnings": [],
                },
            ],
        ),
    )
    monkeypatch.setattr(doctor_cli, "_check_launchd_plist", lambda home: (True, {}))
    monkeypatch.setattr(
        doctor_cli,
        "_check_provider_readiness",
        lambda settings, db_present: (True, {"status": "ready", "repair": None}),
    )

    result = CliRunner().invoke(app, ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0, result.stdout
    assert "claude_mcp_registered: False" in result.stdout
    assert "Claude Code CLI not found" in result.stdout


def test_mcp_host_status_reports_connections_without_running_full_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor_cli,
        "_check_mcp_hosts",
        lambda home: (
            True,
            [
                {
                    "host_key": "codex",
                    "status": "registered_current",
                    "message": "Codex StackOS MCP registration is healthy.",
                    "ok": True,
                    "available": True,
                    "advisory": False,
                    "blocking": False,
                    "needs_restart": False,
                }
            ],
        ),
    )

    result = CliRunner().invoke(app, ["mcp-host-status", "--json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready"
    assert payload["hosts"][0]["host_key"] == "codex"


def test_doctor_exits_9_for_stale_claude_registration(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = sandbox / ".local" / "state" / "stackos" / "seed.bin"
    seed.write_bytes(b"0" * 32)
    os.chmod(seed, 0o600)
    monkeypatch.setattr(daemon_processes, "_tcp_can_connect", lambda host, port: True)
    monkeypatch.setattr(
        doctor_cli, "_check_credentials_decrypt", lambda settings, db_present: (True, [])
    )
    monkeypatch.setattr(
        doctor_cli, "_check_alembic_at_head", lambda settings, db_present: (True, None)
    )
    monkeypatch.setattr(doctor_cli, "_check_scheduler_jobs", lambda settings: (True, 4))
    monkeypatch.setattr(doctor_cli, "_check_browser_runtime", lambda: (True, {"repair": None}))
    monkeypatch.setattr(
        doctor_cli,
        "_check_installed_assets",
        lambda home: (
            {
                "codex_skills_installed": True,
                "claude_skills_installed": True,
                "plugins_installed": True,
                "plugin_marketplace_registered": True,
                "stackos_plugin_skill_current": True,
            },
            {},
        ),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_check_mcp_hosts",
        lambda home: (
            False,
            [
                {
                    "host_key": "codex",
                    "surface": "shared-config",
                    "status": "registered_current",
                    "message": "Codex StackOS MCP registration is healthy.",
                    "ok": True,
                    "available": True,
                    "advisory": False,
                    "blocking": False,
                    "needs_restart": False,
                    "command": [],
                    "config_path": None,
                    "repair": None,
                    "warnings": [],
                },
                {
                    "host_key": "claude-code",
                    "surface": "cli",
                    "status": "registered_stale",
                    "message": "Claude Code has a StackOS MCP entry, but it is stale.",
                    "ok": False,
                    "available": True,
                    "advisory": False,
                    "blocking": True,
                    "needs_restart": False,
                    "command": [],
                    "config_path": None,
                    "repair": "Run `stackos install --mcp-only` or desktop Repair.",
                    "warnings": [],
                },
            ],
        ),
    )
    monkeypatch.setattr(doctor_cli, "_check_launchd_plist", lambda home: (True, {}))
    monkeypatch.setattr(
        doctor_cli,
        "_check_provider_readiness",
        lambda settings, db_present: (True, {"status": "ready", "repair": None}),
    )

    result = CliRunner().invoke(app, ["doctor"], catch_exceptions=False)

    assert result.exit_code == 9
    assert "claude-code MCP registration needs repair" in result.stdout


def test_doctor_redacts_secret_like_mcp_entries() -> None:
    assert (
        doctor_cli._redact_secretish_mcp_text("stackos --bearer-token-env-var STACKOS_TOKEN")
        == "<redacted: secret-like MCP entry>"
    )
