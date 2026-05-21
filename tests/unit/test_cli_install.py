"""Tests for the `content-stack install` Typer subcommand and its primitives.

Mirrors the bash-script behavior (see
`tests/integration/test_install_scripts/`) but exercises the Python
code paths in isolation so they stay green on platforms that lack
`bash` or `rsync`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel
from typer.testing import CliRunner

import content_stack.cli as cli_module
import content_stack.db.models  # noqa: F401  (populate SQLModel metadata)
from content_stack import install as installer
from content_stack.cli import app
from content_stack.config import Settings
from content_stack.db.connection import make_engine
from content_stack.db.migrate import current_alembic_version, upgrade_to_head


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox HOME with a token in place."""
    home = tmp_path / "home"
    home.mkdir()
    state = home / ".local" / "state" / "content-stack"
    state.mkdir(parents=True)
    token = state / "auth.token"
    token.write_text("unit-test-token\n", encoding="utf-8")
    os.chmod(token, 0o600)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CONTENT_STACK_HOME", str(home))
    monkeypatch.setenv("CONTENT_STACK_DATA_DIR", str(home / ".local" / "share" / "content-stack"))
    monkeypatch.setenv("CONTENT_STACK_STATE_DIR", str(state))
    return home


def test_detect_mode_clone(sandbox: Path) -> None:
    """In dev (running from the repo) we resolve `clone` mode."""
    assert installer.detect_mode() == "clone"


def test_copy_skills_clone_mode(sandbox: Path) -> None:
    target, count = installer.copy_skills("codex", home=sandbox)
    assert target == sandbox / ".codex" / "skills" / "content-stack"
    assert target.is_dir()
    assert count == 0


def test_copy_plugins_hydrates_catalogs(sandbox: Path) -> None:
    target, count = installer.copy_plugins(home=sandbox)

    assert target == sandbox / ".codex" / "plugins" / "content-stack"
    assert count == 1
    assert (target / ".codex-plugin" / "plugin.json").is_file()
    mcp = json.loads((target / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["content-stack"] == {
        "command": sys.executable,
        "args": ["-m", "content_stack", "mcp-bridge"],
    }
    assert (target / "skills" / "content-stack" / "SKILL.md").is_file()
    assert not (target / "skills" / "catalog").exists()


def test_copy_plugins_refreshes_existing_codex_cache(sandbox: Path) -> None:
    cache = (
        sandbox / ".codex" / "plugins" / "cache" / "local-content-stack" / "content-stack" / "0.1.0"
    )
    (cache / ".codex-plugin").mkdir(parents=True)
    (cache / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (cache / ".mcp.json").write_text(
        '{"mcpServers":{"content-stack":{"command":"content-stack","args":["mcp-bridge"]}}}',
        encoding="utf-8",
    )

    installer.copy_plugins(home=sandbox)

    mcp = json.loads((cache / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["content-stack"] == {
        "command": sys.executable,
        "args": ["-m", "content_stack", "mcp-bridge"],
    }


def test_bridge_autostart_spawns_loopback_daemon(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "content-stack",
        state_dir=sandbox / ".local" / "state" / "content-stack",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        pid = 12345

        def poll(self) -> None:
            return None

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(cli_module, "_tcp_can_connect", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli_module, "_wait_for_daemon", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)

    ok, message = cli_module._autostart_bridge_daemon(settings, "127.0.0.1", 5180)

    assert ok is True
    assert "auto-started daemon" in message
    assert calls
    args, kwargs = calls[0]
    assert args == [
        sys.executable,
        "-m",
        "content_stack",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "5180",
        "--log-level",
        settings.log_level,
    ]
    assert kwargs["stdin"] is cli_module.subprocess.DEVNULL
    assert kwargs["stderr"] is cli_module.subprocess.STDOUT
    assert kwargs["start_new_session"] is True


def test_bridge_autostart_rejects_non_loopback(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=sandbox / ".local" / "share" / "content-stack",
        state_dir=sandbox / ".local" / "state" / "content-stack",
    )

    def fail_popen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Popen should not be called for non-loopback hosts")

    monkeypatch.setattr(cli_module, "_tcp_can_connect", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli_module.subprocess, "Popen", fail_popen)

    ok, message = cli_module._autostart_bridge_daemon(settings, "0.0.0.0", 5180)

    assert ok is False
    assert "non-loopback" in message


def test_copy_skills_idempotent(sandbox: Path) -> None:
    installer.copy_skills("codex", home=sandbox)
    target = sandbox / ".codex" / "skills" / "content-stack"
    snap1 = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    installer.copy_skills("codex", home=sandbox)
    snap2 = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert snap1 == snap2


def test_copy_skills_deletes_stale(sandbox: Path) -> None:
    installer.copy_skills("codex", home=sandbox)
    target = sandbox / ".codex" / "skills" / "content-stack"
    stale = target / "legacy-seo-skill" / "stale.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("not in source\n", encoding="utf-8")
    installer.copy_skills("codex", home=sandbox)
    assert not stale.exists()


def test_register_mcp_claude_creates_file(sandbox: Path) -> None:
    target = sandbox / ".claude" / "mcp.json"
    msg = installer.register_mcp_claude(home=sandbox, target=target)
    assert "Registered" in msg
    payload = json.loads(target.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["content-stack"]
    assert server["transport"] == "stdio"
    assert server["args"] == ["-m", "content_stack", "mcp-bridge"]
    assert "headers" not in server


def test_register_mcp_claude_preserves_other_servers(sandbox: Path) -> None:
    target = sandbox / ".claude" / "mcp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"mcpServers": {"other": {"transport": "stdio", "command": "/bin/true"}}}),
        encoding="utf-8",
    )
    installer.register_mcp_claude(home=sandbox, target=target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "other" in payload["mcpServers"]
    assert "content-stack" in payload["mcpServers"]


def test_register_mcp_claude_remove(sandbox: Path) -> None:
    target = sandbox / ".claude" / "mcp.json"
    installer.register_mcp_claude(home=sandbox, target=target)
    msg = installer.register_mcp_claude(home=sandbox, target=target, remove=True)
    assert "Unregistered" in msg
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "content-stack" not in payload["mcpServers"]


def test_register_mcp_claude_atomic_no_temp_leftover(sandbox: Path) -> None:
    target = sandbox / ".claude" / "mcp.json"
    installer.register_mcp_claude(home=sandbox, target=target)
    leftovers = list(target.parent.glob(".mcp.*"))
    assert leftovers == []


def test_register_mcp_codex_no_path(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When `codex` is not on PATH, the helper returns a friendly notice."""
    # Pin PATH to a directory that contains no `codex` binary.
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    msg = installer.register_mcp_codex(home=sandbox, port=5180)
    assert "not on PATH" in msg


def test_cli_install_skills_only_subcommand(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["install", "--skills-only", "--skip-doctor"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert (sandbox / ".codex" / "skills" / "content-stack").is_dir()
    assert (sandbox / ".claude" / "skills" / "content-stack").is_dir()
    assert not (sandbox / ".codex" / "plugins" / "content-stack").exists()


def test_cli_install_default_is_plugin_first(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["install", "--skip-doctor"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert (
        sandbox / ".codex" / "plugins" / "content-stack" / ".codex-plugin" / "plugin.json"
    ).is_file()
    assert not (sandbox / ".codex" / "skills" / "content-stack").exists()


def test_cli_install_rejects_multiple_only_flags(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["install", "--skills-only", "--plugins-only", "--skip-doctor"],
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr if hasattr(result, "stderr") else result.output


def test_cli_init_idempotent(sandbox: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(app, ["init"], catch_exceptions=False)
    assert first.exit_code == 0, first.stdout
    state = sandbox / ".local" / "state" / "content-stack"
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
    assert version == "0012_stackos_action_calls"


def test_upgrade_to_head_works_outside_repo_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    monkeypatch.chdir(tmp_path)

    result = upgrade_to_head(settings)

    assert result.stamped_existing_schema is False
    assert current_alembic_version(settings) == "0012_stackos_action_calls"


def test_cli_rotate_token_requires_yes(sandbox: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["rotate-token"])
    assert result.exit_code == 2


def test_codex_mcp_doctor_accepts_bridge_entries_only() -> None:
    assert cli_module._codex_mcp_line_is_bridge("content-stack stdio -")
    assert cli_module._codex_mcp_line_is_bridge(
        "content-stack /path/python -m content_stack mcp-bridge"
    )
    assert not cli_module._codex_mcp_line_is_bridge("content-stack http://127.0.0.1:5180/mcp")
    assert not cli_module._codex_mcp_line_is_bridge(
        "content-stack --url http://127.0.0.1:5180/mcp --bearer-token-env-var CONTENT_STACK_TOKEN"
    )
