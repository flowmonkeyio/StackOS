"""Antigravity's native MCP and canonical skill share the install lifecycle."""

from __future__ import annotations

import json

import pytest

from stackos import install
from stackos.host_mcp import service
from stackos.host_mcp.adapters import antigravity
from stackos.host_mcp.bridge import resolve_bridge_command


@pytest.fixture(autouse=True)
def isolated_host(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("STACKOS_STATE_DIR", "STACKOS_DATA_DIR", "STACKOS_PACKAGED_CLI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(antigravity, "resolve_antigravity_bin", lambda: "/fixture/agy")
    monkeypatch.setattr(antigravity, "token_preflight", lambda _: None)
    definition = next(d for d in service._definitions() if d.policy.host_key == "antigravity")
    monkeypatch.setattr(service, "_definitions", lambda: [definition])


def write_config(home, *, owned=True):
    command = resolve_bridge_command(runtime="antigravity") if owned else ["foreign-server"]
    path = antigravity.config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "theme": "preserve",
                "mcpServers": {
                    "other": {"command": "sibling"},
                    "stackos": {"command": command[0], "args": command[1:]},
                },
            }
        )
    )
    return path


def test_registration_installs_canonical_skill_and_preserves_user_settings(tmp_path):
    path = antigravity.config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"theme":"preserve","mcpServers":{"other":{"command":"sibling"}}}')
    root = antigravity.skills_root(tmp_path)
    sibling = root / "personal/SKILL.md"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("Personal guidance")

    result = service.register_host("antigravity", home=tmp_path)

    assert result.connection_state == "connected"
    assert install.stackos_skill_is_owned(root)
    assert install.stackos_skill_is_current(root)
    assert (root / "stackos/references/browser.md").is_file()
    saved = json.loads(path.read_text())
    assert saved["theme"] == "preserve"
    assert saved["mcpServers"]["other"] == {"command": "sibling"}
    assert sibling.read_text() == "Personal guidance"
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / ".gemini/GEMINI.md").exists()


@pytest.mark.parametrize("state", ["missing", "outdated", "canonical_unreceipted"])
def test_repairs_skill_without_rewriting_current_mcp(tmp_path, monkeypatch, state):
    config = write_config(tmp_path)
    root = antigravity.skills_root(tmp_path)
    if state == "outdated":
        target = install.copy_managed_stackos_skill_to(root)
        (target / "references/browser.md").write_text("Old guidance")
        (target / "personal-notes.txt").write_text("Keep notes")
    elif state == "canonical_unreceipted":
        install.copy_stackos_skill_to(root)
    before = config.read_bytes(), config.stat().st_mtime_ns

    def unexpected_write(*args, **kwargs):
        pytest.fail("Skill repair must not rewrite current MCP configuration")

    monkeypatch.setattr(antigravity, "upsert_mcp_server", unexpected_write)
    assert service.inspect_host("antigravity", home=tmp_path).repairable
    assert antigravity.register(tmp_path).status == "registered_current"
    assert install.stackos_skill_is_owned(root)
    assert install.stackos_skill_is_current(root, allow_extra=True)
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    if state == "outdated":
        assert (root / "stackos/personal-notes.txt").read_text() == "Keep notes"
    skill_before = (root / "stackos/SKILL.md").stat().st_mtime_ns
    assert antigravity.register(tmp_path).status == "registered_current"
    assert (root / "stackos/SKILL.md").stat().st_mtime_ns == skill_before


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("kind", ["authored", "incomplete", "symlink"])
def test_unowned_skill_is_preserved_before_any_mcp_write(tmp_path, configured, kind):
    config = write_config(tmp_path) if configured else antigravity.config_path(tmp_path)
    config_before = config.read_bytes() if configured else None
    root = antigravity.skills_root(tmp_path)
    target = root / "stackos"
    if kind == "incomplete":
        install.copy_stackos_skill_to(root)
        (target / "references/browser.md").unlink()
    elif kind == "symlink":
        external = tmp_path / "personal"
        external.mkdir()
        (external / "SKILL.md").write_text("Personal guidance")
        root.mkdir(parents=True, exist_ok=True)
        target.symlink_to(external, target_is_directory=True)
    else:
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("Personal guidance")
    skill_before = (target / "SKILL.md").read_bytes()

    assert antigravity.register(tmp_path).status == "registered_unmanaged"
    assert not antigravity.remove(tmp_path).ok
    assert (target / "SKILL.md").read_bytes() == skill_before
    assert target.is_symlink() == (kind == "symlink")
    assert (config.read_bytes() if config.exists() else None) == config_before
    assert not install.stackos_skill_is_owned(root)


def test_absent_host_is_optional_and_does_not_install_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(antigravity, "resolve_antigravity_bin", lambda: None)
    result = service.inspect_host("antigravity", home=tmp_path)
    assert result.connection_state == "unavailable" and not result.blocking
    assert antigravity.register(tmp_path).status == "absent"
    assert not (tmp_path / ".gemini").exists()


@pytest.mark.parametrize("configured", [False, True])
def test_bulk_cleanup_without_binary_or_mcp_retains_unrelated_files(
    tmp_path, monkeypatch, configured
):
    config = write_config(tmp_path) if configured else antigravity.config_path(tmp_path)
    root = antigravity.skills_root(tmp_path)
    target = install.copy_managed_stackos_skill_to(root)
    (target / "SKILL.md").write_text("Modified installed guidance")
    (target / "references/browser.md").unlink()
    extra = target / "personal-notes.txt"
    extra.write_text("Keep notes")
    monkeypatch.setattr(antigravity, "resolve_antigravity_bin", lambda: None)
    current = service.inspect_host("antigravity", home=tmp_path)
    assert current.selected and current.managed and not current.blocking

    assert service.remove_all(home=tmp_path).ok
    assert not (target / "SKILL.md").exists()
    assert extra.read_text() == "Keep notes"
    assert not install.stackos_skill_is_owned(root)
    if configured:
        assert json.loads(config.read_text()) == {
            "theme": "preserve",
            "mcpServers": {"other": {"command": "sibling"}},
        }
    else:
        assert not config.exists()
    assert service.remove_all(home=tmp_path).ok


@pytest.mark.parametrize("malformed", [False, True])
def test_owned_skill_cleanup_preserves_foreign_or_damaged_config(tmp_path, monkeypatch, malformed):
    config = write_config(tmp_path, owned=False)
    if malformed:
        config.write_text("{broken")
    before = config.read_bytes()
    target = install.copy_managed_stackos_skill_to(antigravity.skills_root(tmp_path))
    monkeypatch.setattr(antigravity, "resolve_antigravity_bin", lambda: None)

    assert not service.remove_all(home=tmp_path).ok
    assert not target.exists()
    assert config.read_bytes() == before


def test_partial_skill_install_reports_failure_and_can_be_repaired(tmp_path, monkeypatch):
    original_copy = install.copy_stackos_skill_to

    def failed_copy(*args, **kwargs):
        raise OSError("simulated interrupted copy")

    monkeypatch.setattr(install, "copy_stackos_skill_to", failed_copy)
    assert antigravity.register(tmp_path).status == "register_failed"
    assert install.stackos_skill_is_owned(antigravity.skills_root(tmp_path))
    assert service.inspect_host("antigravity", home=tmp_path).repairable
    monkeypatch.setattr(install, "copy_stackos_skill_to", original_copy)
    assert antigravity.register(tmp_path).status == "registered_current"


def test_uninstall_retries_mcp_cleanup_after_skill_removal(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    target = install.copy_managed_stackos_skill_to(antigravity.skills_root(tmp_path))
    original_remove = antigravity.remove_mcp_server
    monkeypatch.setattr(antigravity, "remove_mcp_server", lambda *a, **kw: (False, "busy", False))
    assert not service.remove_all(home=tmp_path).ok
    assert not target.exists()
    monkeypatch.setattr(antigravity, "remove_mcp_server", original_remove)
    assert service.remove_all(home=tmp_path).ok
    assert "stackos" not in json.loads(config.read_text())["mcpServers"]
