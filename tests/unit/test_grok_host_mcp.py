from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from stackos import install as installer
from stackos.host_mcp import service
from stackos.host_mcp.adapters import grok
from stackos.host_mcp.bridge import resolve_bridge_command
from stackos.install import (
    copy_managed_stackos_skill_to,
    copy_stackos_skill_to,
    stackos_skill_is_current,
)

REAL_RESOLVE_GROK_BIN = grok.resolve_grok_bin


@pytest.fixture(autouse=True)
def isolate_grok(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GROK_HOME", "STACKOS_STATE_DIR", "STACKOS_DATA_DIR", "STACKOS_PACKAGED_CLI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fake/grok")


def write_entry(home: Path, command: list[str], extra: str = "") -> Path:
    path = grok.grok_home(home) / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[ui]\ncompact_mode = true\n[mcp_servers.other]\ncommand = "echo"\n'
        "[mcp_servers.stackos]\n"
        f"command = {json.dumps(command[0])}\nargs = {json.dumps(command[1:])}\n{extra}"
    )
    return path


def allow_help(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(_binary, args, *, home):
        calls.append(list(args))
        assert args == ["mcp", "add", "--help"], "unexpected CLI mutation"
        return subprocess.CompletedProcess(args, 0, "--scope user --transport stdio", "")

    monkeypatch.setattr(grok, "_run_grok", run)
    return calls


def write_token(home: Path) -> None:
    token = home / ".local/state/stackos/auth.token"
    token.parent.mkdir(parents=True)
    token.write_text("synthetic-token")


def test_grok_absence_is_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: None)
    result = service.inspect_host("grok", home=tmp_path)
    assert result.connection_state == "unavailable"
    assert result.display_name == "Grok"
    assert result.ok and not result.blocking
    assert grok.register(tmp_path).status == "absent"
    assert grok.remove(tmp_path).status == "removed"


def test_grok_inspects_only_native_user_config(tmp_path, monkeypatch):
    allow_help(monkeypatch)
    # Imported Claude or project servers must not count as native Grok registration.
    (tmp_path / ".claude.json").write_text('{"mcpServers":{"stackos":{"command":"stackos"}}}')
    (tmp_path / ".mcp.json").write_text('{"mcpServers":{"stackos":{"command":"stackos"}}}')
    result = service.inspect_host("grok", home=tmp_path)
    assert result.connection_state == "available"
    assert not result.selected
    assert not (tmp_path / ".grok").exists()


def test_grok_current_registration_is_read_only_and_noop(tmp_path, monkeypatch):
    calls = allow_help(monkeypatch)
    expected = resolve_bridge_command(runtime="grok")
    path = write_entry(tmp_path, expected)
    copy_managed_stackos_skill_to(tmp_path / ".grok/skills")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    for result in (grok.inspect(tmp_path), grok.register(tmp_path)):
        assert result.status == "registered_current"
        assert result.managed and result.selected
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert calls == [["mcp", "add", "--help"]] * 2


@pytest.mark.parametrize("extra", ["", "enabled = false\n"])
def test_grok_stale_or_disabled_owned_registration_needs_repair(tmp_path, monkeypatch, extra):
    allow_help(monkeypatch)
    command = resolve_bridge_command(runtime="grok") if extra else ["/old/stackos", "mcp-bridge"]
    write_entry(tmp_path, command, extra)
    result = service.inspect_host("grok", home=tmp_path)
    assert result.connection_state == "repair_needed"
    assert result.managed and result.selected and result.repairable


@pytest.mark.parametrize(
    "contents",
    [
        "[broken",
        'mcp_servers = "invalid"',
        '[mcp_servers]\nstackos = "invalid"',
    ],
)
def test_grok_preserves_malformed_config(tmp_path, monkeypatch, contents):
    path = tmp_path / ".grok/config.toml"
    path.parent.mkdir()
    path.write_text(contents)
    calls = allow_help(monkeypatch)
    for result in (grok.inspect(tmp_path), grok.register(tmp_path), grok.remove(tmp_path)):
        assert result.status == "config_unreadable"
    assert path.read_text() == contents
    assert calls == []


@pytest.mark.parametrize(
    ("command", "extra", "status"),
    [
        (["/foreign/server"], "", "registered_unmanaged"),
        (
            ["stackos", "mcp-bridge"],
            'env = { TOKEN = "synthetic-private-token" }\n',
            "registered_unsafe",
        ),
        (["stackos", "mcp-bridge"], 'url = "https://example.invalid/mcp"\n', "registered_unsafe"),
        (
            ["stackos", "mcp-bridge"],
            'env = { PYTHONPATH = "/foreign/path" }\n',
            "registered_unsafe",
        ),
    ],
)
def test_grok_never_mutates_foreign_or_unsafe_entries(
    tmp_path, monkeypatch, command, extra, status
):
    calls = allow_help(monkeypatch)
    path = write_entry(tmp_path, command, extra)
    before = path.read_bytes()
    for result in (grok.inspect(tmp_path), grok.register(tmp_path), grok.remove(tmp_path)):
        assert result.status == status
        assert not result.managed
        assert "synthetic-private-token" not in str(result.to_info())
        assert "/foreign/path" not in str(result.to_info())
    assert path.read_bytes() == before
    assert calls == []


def test_grok_rejects_incompatible_cli_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        grok,
        "_run_grok",
        lambda _bin, args, **_kwargs: subprocess.CompletedProcess(args, 0, "old CLI", ""),
    )
    result = service.inspect_host("grok", home=tmp_path)
    assert result.status == "unsupported_host_version"
    assert result.connection_state == "update_required"
    assert not result.blocking
    assert grok.register(tmp_path).status == "unsupported_host_version"
    assert not (tmp_path / ".grok").exists()


def test_grok_registration_requires_token(tmp_path, monkeypatch):
    calls = allow_help(monkeypatch)
    result = grok.register(tmp_path)
    assert result.status == "token_missing"
    assert not result.ok
    assert calls == [["mcp", "add", "--help"]]


def test_grok_native_cli_lifecycle_uses_user_scope_and_verifies_readback(tmp_path, monkeypatch):
    custom = tmp_path / "grok-custom"
    monkeypatch.setenv("GROK_HOME", str(custom))
    write_token(tmp_path)
    path = write_entry(tmp_path, ["/old/stackos", "mcp-bridge", "--runtime", "claude"])
    sibling = custom / "skills/personal/SKILL.md"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("Personal skill")
    mutations: list[list[str]] = []

    def run(_bin, args, *, home):
        assert home == tmp_path
        if args == ["mcp", "add", "--help"]:
            return subprocess.CompletedProcess(args, 0, "--scope user --transport", "")
        mutations.append(list(args))
        assert args[2:5] == ["--scope", "user", "stackos"]
        if args[1] == "add":
            assert args[5] == "--"
            write_entry(home, list(args[6:]), "enabled = true\n")
        else:
            path.write_text('[ui]\ncompact_mode = true\n[mcp_servers.other]\ncommand = "echo"\n')
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(grok, "_run_grok", run)
    registered = service.register_host("grok", home=tmp_path)
    assert registered.connection_state == "connected"
    assert registered.status == "registered"
    stored = tomllib.loads(path.read_text())
    assert stored["mcp_servers"]["stackos"]["args"][-2:] == ["--runtime", "grok"]
    assert stored["mcp_servers"]["other"] == {"command": "echo"}
    assert stored["ui"] == {"compact_mode": True}
    assert not (tmp_path / ".grok").exists()
    assert service.register_host("grok", home=tmp_path).status == "registered_current"
    removed = service.remove_host("grok", home=tmp_path)
    assert removed.status == "removed"
    assert not (custom / "skills/stackos").exists()
    assert sibling.read_text() == "Personal skill"
    assert tomllib.loads(path.read_text())["mcp_servers"] == {"other": {"command": "echo"}}
    assert [args[1] for args in mutations] == ["add"]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_grok_does_not_claim_registration_without_readback(tmp_path, monkeypatch, exit_code):
    write_token(tmp_path)

    def run(_bin, args, **_kwargs):
        help_text = "--scope user --transport" if args[-1] == "--help" else ""
        return subprocess.CompletedProcess(
            args, 0 if help_text else exit_code, help_text, "private-detail"
        )

    monkeypatch.setattr(grok, "_run_grok", run)
    result = grok.register(tmp_path)
    assert result.status == "register_failed"
    assert not result.ok
    assert "private-detail" not in str(result.to_info())


def test_grok_remove_requires_confirmed_absence(tmp_path, monkeypatch):
    write_entry(tmp_path, resolve_bridge_command(runtime="grok"))
    copy_stackos_skill_to(tmp_path / ".grok/skills")
    monkeypatch.setattr(
        grok,
        "remove_mcp_server",
        lambda *_args, **_kwargs: (True, None, True),
    )
    assert grok.remove(tmp_path).status == "remove_failed"


def test_grok_subprocess_pins_inspected_home_and_redacts_failures(tmp_path, monkeypatch):
    captured = {}

    def fail(argv, **kwargs):
        captured.update(kwargs)
        raise subprocess.TimeoutExpired(argv, 20, output="synthetic-private-token")

    monkeypatch.setattr(grok.subprocess, "run", fail)
    result = grok._run_grok("/grok/bin/grok", ["mcp", "add", "--help"], home=tmp_path)
    assert captured["env"]["GROK_HOME"] == str(tmp_path / ".grok")
    assert captured["env"]["PATH"].split(os.pathsep)[0] == "/grok/bin"
    assert result.returncode == 124
    assert "synthetic-private-token" not in str(result)


def test_grok_uses_shared_discovery_and_explicit_override(tmp_path, monkeypatch):
    captured = {}

    def resolve(command, **kwargs):
        captured.update(kwargs)
        assert command == "grok"
        return "/grok/bin/grok"

    monkeypatch.setattr(grok, "resolve_cli_bin", resolve)
    assert REAL_RESOLVE_GROK_BIN(tmp_path) == "/grok/bin/grok"
    assert captured["env_var"] == "STACKOS_GROK_BIN"
    assert str(tmp_path / ".grok/bin/grok") in captured["common_candidates"]


def test_grok_is_an_automatic_host_in_the_shared_lifecycle():
    definitions = service._definitions()
    grok_definition = next(row for row in definitions if row.policy.host_key == "grok")
    assert grok_definition.policy.setup_policy == "automatic"
    assert grok_definition.adapter is grok


@pytest.mark.parametrize("outdated", [False, True])
def test_grok_repairs_skill_without_rewriting_current_mcp(tmp_path, monkeypatch, outdated):
    calls = allow_help(monkeypatch)
    write_token(tmp_path)
    config = write_entry(tmp_path, resolve_bridge_command(runtime="grok"))
    skills = tmp_path / ".grok/skills"
    sibling = skills / "personal/SKILL.md"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("User-owned skill")
    if outdated:
        target = copy_stackos_skill_to(skills)
        (target / "references/browser.md").write_text("Old browser guidance")
    before = config.read_bytes(), config.stat().st_mtime_ns
    assert grok.inspect(tmp_path).status == "registered_stale"
    assert grok.register(tmp_path).status == "registered"
    assert stackos_skill_is_current(skills)
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    assert sibling.read_text() == "User-owned skill"
    assert all(args == ["mcp", "add", "--help"] for args in calls)


def test_grok_preserves_unmanaged_skill_before_first_connection(tmp_path, monkeypatch):
    calls = allow_help(monkeypatch)
    skill = tmp_path / ".grok/skills/stackos/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("A user-authored skill named stackos")
    for result in (grok.inspect(tmp_path), grok.register(tmp_path), grok.remove(tmp_path)):
        assert result.status == "registered_unmanaged"
        assert not result.managed
    assert skill.read_text() == "A user-authored skill named stackos"
    assert not (tmp_path / ".grok/config.toml").exists()
    assert calls == []


def test_grok_rejects_symlink_skill_without_touching_target(tmp_path, monkeypatch):
    allow_help(monkeypatch)
    write_entry(tmp_path, resolve_bridge_command(runtime="grok"))
    foreign = tmp_path / "outside/SKILL.md"
    foreign.parent.mkdir()
    foreign.write_text("User content")
    skills = tmp_path / ".grok/skills"
    skills.mkdir()
    (skills / "stackos").symlink_to(foreign.parent, target_is_directory=True)
    assert grok.register(tmp_path).status == "registered_unmanaged"
    assert grok.remove(tmp_path).status == "remove_failed"
    assert foreign.read_text() == "User content"


def test_canonical_skill_readiness_detects_reference_and_extra_file_changes(tmp_path):
    assert not stackos_skill_is_current(tmp_path)
    target = copy_stackos_skill_to(tmp_path)
    assert stackos_skill_is_current(tmp_path)
    (target / "references/browser.md").write_text("outdated")
    assert not stackos_skill_is_current(tmp_path)
    copy_stackos_skill_to(tmp_path)
    (target / "retired.txt").write_text("obsolete")
    assert not stackos_skill_is_current(tmp_path)
    copy_stackos_skill_to(tmp_path)
    assert stackos_skill_is_current(tmp_path)


def test_grok_removal_cleans_canonical_mirror_after_partial_removal(tmp_path, monkeypatch):
    allow_help(monkeypatch)
    target = copy_stackos_skill_to(tmp_path / ".grok/skills")
    definition = next(row for row in service._definitions() if row.policy.host_key == "grok")
    monkeypatch.setattr(service, "_definitions", lambda: [definition])
    current = service.inspect_host("grok", home=tmp_path)
    assert current.connection_state == "available"
    assert current.selected and current.managed
    ok, _messages = installer.remove_mcp_hosts(home=tmp_path)
    assert ok
    assert not target.exists()


def test_grok_bulk_uninstall_retries_config_cleanup_after_skill_removal(tmp_path, monkeypatch):
    path = write_entry(tmp_path, resolve_bridge_command(runtime="grok"))
    target = copy_stackos_skill_to(tmp_path / ".grok/skills")
    sibling = target.parent / "personal/SKILL.md"
    sibling.parent.mkdir()
    sibling.write_text("Personal skill")
    definition = next(row for row in service._definitions() if row.policy.host_key == "grok")
    monkeypatch.setattr(service, "_definitions", lambda: [definition])
    allow_help(monkeypatch)
    remove_config = grok.remove_mcp_server
    monkeypatch.setattr(grok, "remove_mcp_server", lambda *_args, **_kwargs: (False, "busy", False))
    assert not installer.remove_mcp_hosts(home=tmp_path)[0]
    assert "stackos" in tomllib.loads(path.read_text())["mcp_servers"]
    assert not target.exists()
    monkeypatch.setattr(grok, "remove_mcp_server", remove_config)
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert not target.exists()
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert sibling.read_text() == "Personal skill"
    assert tomllib.loads(path.read_text()) == {
        "ui": {"compact_mode": True},
        "mcp_servers": {"other": {"command": "echo"}},
    }


@pytest.mark.parametrize("kind", ["foreign", "symlink", "incomplete"])
def test_grok_bulk_uninstall_preserves_unowned_orphan_skills(tmp_path, monkeypatch, kind):
    calls = allow_help(monkeypatch)
    definition = next(row for row in service._definitions() if row.policy.host_key == "grok")
    monkeypatch.setattr(service, "_definitions", lambda: [definition])
    target = tmp_path / ".grok/skills/stackos"
    if kind == "incomplete":
        copy_stackos_skill_to(target.parent)
        (target / "references/browser.md").unlink()
    elif kind == "symlink":
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("User guidance")
        target.parent.mkdir(parents=True)
        target.symlink_to(foreign, target_is_directory=True)
    else:
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("User guidance")
    before = (target / "SKILL.md").read_bytes()
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert (target / "SKILL.md").read_bytes() == before
    assert target.is_symlink() == (kind == "symlink")
    assert not (tmp_path / ".grok/config.toml").exists()
    assert calls == []


@pytest.fixture
def grok_without_cli(monkeypatch):
    definition = next(row for row in service._definitions() if row.policy.host_key == "grok")
    monkeypatch.setattr(service, "_definitions", lambda: [definition])
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: None)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Cleanup must not invoke the missing Grok executable")

    monkeypatch.setattr(grok, "_run_grok", forbidden)


def test_grok_bulk_uninstall_without_cli_preserves_unrelated_files(tmp_path, grok_without_cli):
    config = write_entry(tmp_path, ["/old/stackos", "mcp-bridge"])
    config.write_text("# Keep user settings\n" + config.read_text())
    target = copy_managed_stackos_skill_to(tmp_path / ".grok/skills")
    (target / "SKILL.md").write_text("Modified installed guidance")
    (target / "references/browser.md").unlink()
    extra = target / "personal-notes.txt"
    extra.write_text("User notes")
    sibling = target.parent / "personal/SKILL.md"
    sibling.parent.mkdir()
    sibling.write_text("Personal skill")
    current = service.inspect_host("grok", home=tmp_path)
    assert current.connection_state == "unavailable"
    assert current.selected and current.managed
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert not (target / "SKILL.md").exists()
    assert extra.read_text() == "User notes"
    assert sibling.read_text() == "Personal skill"
    assert tomllib.loads(config.read_text()) == {
        "ui": {"compact_mode": True},
        "mcp_servers": {"other": {"command": "echo"}},
    }
    assert "# Keep user settings" in config.read_text()
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert extra.read_text() == "User notes"


@pytest.mark.parametrize("entire_tree_missing", [False, True])
def test_grok_orphan_receipt_cleanup_without_cli_is_repeatable(
    tmp_path, monkeypatch, grok_without_cli, entire_tree_missing
):
    custom = tmp_path / "custom-grok"
    monkeypatch.setenv("GROK_HOME", str(custom))
    target = copy_managed_stackos_skill_to(custom / "skills")
    if entire_tree_missing:
        import shutil

        shutil.rmtree(target)
    else:
        (target / "SKILL.md").write_text("Changed since install")
        (target / "references/browser.md").unlink()
    assert service.inspect_host("grok", home=tmp_path).managed
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert not target.exists()
    assert not service.inspect_host("grok", home=tmp_path).managed
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert not (tmp_path / ".grok").exists()


@pytest.mark.parametrize("contents", ["[broken", '[mcp_servers.stackos]\ncommand = "foreign"\n'])
def test_grok_cleans_owned_skill_but_preserves_foreign_config_without_cli(
    tmp_path, grok_without_cli, contents
):
    target = copy_managed_stackos_skill_to(tmp_path / ".grok/skills")
    (target / "SKILL.md").write_text("Modified owned copy")
    config = tmp_path / ".grok/config.toml"
    config.write_text(contents)
    assert not installer.remove_mcp_hosts(home=tmp_path)[0]
    assert not target.exists()
    assert config.read_text() == contents


def test_grok_legacy_owned_config_supports_cleanup_without_cli(tmp_path, grok_without_cli):
    config = write_entry(tmp_path, resolve_bridge_command(runtime="grok"))
    target = copy_stackos_skill_to(tmp_path / ".grok/skills")
    (target / "references/browser.md").unlink()
    (target / "SKILL.md").write_text("Modified legacy installation")
    assert installer.remove_mcp_hosts(home=tmp_path)[0]
    assert not target.exists()
    assert "stackos" not in tomllib.loads(config.read_text())["mcp_servers"]
