from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from stackos.host_mcp import subscription_claude
from stackos.host_mcp.accounts import DesiredAccount, NativeAccountProfile, ResolvedAccountSelection
from stackos.host_mcp.adapters import claude_accounts, claude_code

ACCOUNT = "fef10000-1111-4111-8111-000000000001"
ORGANIZATION = "fef10000-1111-4111-8111-000000000002"
SESSION = "fef10000-1111-4111-8111-000000000003"
EMAIL = "fixture@example.invalid"
SCHEMA = {"type": "object", "required": ["summary"]}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    directory = tmp_path / "profile"
    directory.mkdir(mode=0o700)
    default = tmp_path / "default"
    original = default / "projects" / "original" / f"{uuid4()}.jsonl"
    original.parent.mkdir(parents=True)
    original.write_text(
        json.dumps({"type": "user", "sessionId": original.stem, "cwd": str(tmp_path)}) + "\n"
    )
    (directory / "projects").symlink_to(default / "projects", target_is_directory=True)
    (directory / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "accountUuid": ACCOUNT,
                    "organizationUuid": ORGANIZATION,
                    "emailAddress": EMAIL,
                }
            }
        )
    )
    status = {
        "loggedIn": True,
        "authMethod": "claude.ai",
        "apiProvider": "firstParty",
        "configDirectory": str(directory),
        "projectsDirectory": str(directory / "projects"),
        "email": EMAIL,
        "orgId": ORGANIZATION,
        "subscriptionType": "pro",
    }
    calls = []

    def capture(binary, args, *, env, cwd):
        calls.append((binary, args, cwd))
        if args == ["agents", "--json"]:
            assert Path(env["CLAUDE_CONFIG_DIR"]) in (directory, default)
            return subprocess.CompletedProcess(args, 0, "[]", "")
        assert env["CLAUDE_CONFIG_DIR"] == str(directory)
        return subprocess.CompletedProcess(args, 0, json.dumps(status), "")

    monkeypatch.setattr(claude_accounts, "subprocess_env_for_cli", lambda _: {"PATH": "/usr/bin"})
    monkeypatch.setattr(claude_accounts, "run_capture", capture)
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/native/claude")
    selection = ResolvedAccountSelection(
        DesiredAccount(account_ref=ACCOUNT, revision=2),
        NativeAccountProfile(ACCOUNT, directory),
        (ACCOUNT, ORGANIZATION),
    )
    return selection, original, status, calls


def test_native_status_verifies_person_and_workspace(profile):
    selection, _, status, _ = profile
    assert selection.profile is not None
    assert claude_accounts.accounts.read_identity(selection.profile).principal_key == (
        ACCOUNT,
        ORGANIZATION,
    )
    status["orgId"] = SESSION
    assert claude_accounts.accounts.read_identity(selection.profile) == "unavailable"
    status["authMethod"] = "api_key"
    assert claude_accounts.accounts.read_identity(selection.profile) == "conflicting_credentials"
    status["loginExpiresAt"] = "2000-01-01T00:00:00Z"
    assert claude_accounts.accounts.read_identity(selection.profile) == "expired"


def test_adopted_default_preserves_implicit_global_metadata(tmp_path, profile, monkeypatch):
    _, _, status, _ = profile
    home = tmp_path / "home"
    default = home / ".claude"
    default.mkdir(parents=True)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "accountUuid": ACCOUNT,
                    "organizationUuid": ORGANIZATION,
                    "emailAddress": EMAIL,
                }
            }
        )
    )
    status.update(configDirectory=str(default), projectsDirectory=str(default / "projects"))
    monkeypatch.setattr(claude_accounts, "subprocess_env_for_cli", lambda _: {"HOME": str(home)})

    def capture(_, args, *, env, cwd):
        assert "CLAUDE_CONFIG_DIR" not in env
        return subprocess.CompletedProcess(args, 0, json.dumps(status), "")

    monkeypatch.setattr(claude_accounts, "run_capture", capture)
    adopted = NativeAccountProfile(ACCOUNT, None)
    identity = claude_accounts.accounts.read_identity(adopted)
    assert identity.principal_key == (ACCOUNT, ORGANIZATION)
    selection = ResolvedAccountSelection(
        DesiredAccount(account_ref=ACCOUNT), adopted, identity.principal_key
    )
    env, _ = claude_accounts.selected_profile("/native/claude", selection)
    assert "CLAUDE_CONFIG_DIR" not in env


def test_isolated_native_enrollment_and_discard_keep_default_state(tmp_path, monkeypatch):
    accounts = claude_accounts.ClaudeAccounts()
    home = tmp_path / "home"
    default = home / ".claude"
    isolated = tmp_path / "isolated"
    default.mkdir(parents=True)
    isolated.mkdir(mode=0o700)
    (default / "settings.json").write_text('{"model":"fixture"}')
    credentials = default / ".credentials.json"
    credentials.write_text("synthetic default fixture")
    monkeypatch.setattr(claude_accounts.claude_mcp, "resolve_claude_bin", lambda: "/native/claude")
    monkeypatch.setattr(claude_accounts, "subprocess_env_for_cli", lambda _: {"HOME": str(home)})
    calls = []
    logged_in = False

    def capture(binary, args, *, env, cwd=None):
        nonlocal logged_in
        config = env.get("CLAUDE_CONFIG_DIR")
        calls.append((args, config))
        if args == ["auth", "logout"]:
            assert config == str(isolated)
            logged_in = False
            return subprocess.CompletedProcess(args, 0, "", "")
        directory = isolated if config else default
        active = not config or logged_in
        status = {
            "loggedIn": active,
            "configDirectory": str(directory),
            "projectsDirectory": str(directory / "projects"),
        }
        return subprocess.CompletedProcess(args, 0 if active else 1, json.dumps(status), "")

    class Process:
        def spawn(self, command, **kwargs):
            nonlocal logged_in
            assert command == ["/native/claude", "auth", "login", "--claudeai"]
            assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(isolated)
            assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
            os.fstat(kwargs["pass_fds"][0])
            logged_in = True
            return self

        def wait(self, timeout):
            return 0

        def stop(self):
            return "stopped"

    class ImmediateThread:
        def __init__(self, *, target, daemon, name):
            self.start = target

    monkeypatch.setattr(claude_accounts, "run_capture", capture)
    monkeypatch.setattr(claude_accounts, "ManagedProcessControl", Process)
    monkeypatch.setattr(claude_accounts, "Thread", ImmediateThread)
    selection = NativeAccountProfile(ACCOUNT, isolated)
    accounts.start_login(selection)
    assert (isolated / "settings.json").resolve() == default / "settings.json"
    assert (isolated / "projects").resolve() == default / "projects"
    assert not (isolated / ".credentials.json").exists()
    assert accounts.discard_login(selection)
    assert not isolated.exists()
    assert (["auth", "logout"], str(isolated)) in calls
    assert credentials.read_text() == "synthetic default fixture"
    assert (default / "projects").is_dir()


def test_headless_selected_profile_uses_exact_original_file_and_inherits_lease(
    tmp_path,
    monkeypatch,
    profile,
):
    selection, original, _, calls = profile
    session_id = original.stem

    def execute(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "2.1.327 (Claude Code)", "")
        assert command[command.index("--resume") + 1] == str(original.resolve())
        assert command[command.index("--permission-mode") + 1] == "dontAsk"
        assert kwargs["input"] == "follow up"
        assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(selection.profile.directory)
        os.fstat(kwargs["pass_fds"][0])
        with (
            pytest.raises(claude_accounts.ClaudeAccountError, match="already has a writer"),
            claude_accounts.transcript_writer(session_id),
        ):
            pytest.fail("another writer acquired the selected session")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "session_id": session_id,
                    "structured_output": {"summary": "retained"},
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = claude_code.run_agent(
        tmp_path,
        "role",
        "follow up",
        SCHEMA,
        sandbox="read-only",
        resume_thread_id=session_id,
        account_selection=selection,
    )
    assert result.ok and result.thread_id == session_id
    assert calls == [
        ("/native/claude", ["auth", "status", "--json"], tmp_path),
        ("/native/claude", ["agents", "--json"], tmp_path),
        ("/native/claude", ["agents", "--json"], tmp_path),
    ]
    with claude_accounts.transcript_writer(session_id):
        assert original.exists()


@pytest.mark.parametrize("where", ["transcript-owner", "enrolled-peer"])
def test_headless_resume_refuses_existing_native_writer_before_spawn(
    tmp_path, monkeypatch, profile, where
):
    selection, original, _, _ = profile
    owner = original.parent.parent.parent
    peer = tmp_path / "peer"
    peer.mkdir()
    (peer / "projects").symlink_to(owner / "projects", target_is_directory=True)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    selection = replace(selection, enrolled_directories=(peer, unrelated))
    native_capture = claude_accounts.run_capture
    active = owner if where == "transcript-owner" else peer
    before = original.read_bytes()

    def capture(binary, args, *, env, cwd):
        if args == ["agents", "--json"]:
            directory = Path(env["CLAUDE_CONFIG_DIR"])
            assert directory != unrelated
            rows = [{"pid": 42, "sessionId": original.stem}] if directory == active else []
            return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
        return native_capture(binary, args, env=env, cwd=cwd)

    monkeypatch.setattr(claude_accounts, "run_capture", capture)
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: pytest.fail("spawned a second writer")
    )
    result = claude_code.run_agent(
        tmp_path,
        "role",
        "prompt",
        SCHEMA,
        resume_thread_id=original.stem,
        account_selection=selection,
    )
    assert not result.ok and "active native session" in result.error
    assert original.read_bytes() == before


@pytest.mark.parametrize("condition", ["missing", "ambiguous", "foreign", "stale", "unsafe"])
def test_managed_resume_fails_before_spawning(tmp_path, monkeypatch, profile, condition):
    selection, original, _, _ = profile
    current = None
    if condition == "missing":
        original.unlink()
    elif condition == "ambiguous":
        second = original.parent.parent / "second" / original.name
        second.parent.mkdir()
        second.write_text(original.read_text())
    elif condition == "foreign":
        original.write_text(original.read_text().replace(str(tmp_path), str(tmp_path / "other")))
    elif condition == "stale":

        def current():
            return False
    else:
        target = original.with_suffix(".backup")
        original.rename(target)
        original.symlink_to(target)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: pytest.fail("unexpected native turn"))
    result = claude_code.run_agent(
        tmp_path,
        "role",
        "prompt",
        SCHEMA,
        resume_thread_id=original.stem,
        account_selection=selection,
        account_selection_current=current,
    )
    assert not result.ok and result.thread_id is None
    assert (
        "selection changed" in result.error
        if condition == "stale"
        else "exact local" in result.error
    )


def test_quota_reads_the_verified_profile_without_returning_identity(monkeypatch, profile):
    selection, _, status, _ = profile
    calls = []

    def capture(binary, args, *, env):
        assert binary == "/native/claude"
        assert env["CLAUDE_CONFIG_DIR"] == str(selection.profile.directory)
        calls.append(args)
        if args == ["--version"]:
            stdout = "2.1.327 (Claude Code)"
        elif args == ["auth", "status", "--json"]:
            stdout = json.dumps(status)
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(subscription_claude, "run_capture", capture)
    assert subscription_claude.read_claude("/native/claude", selection) == {
        "plan": "Claude Pro",
        "meters": [],
    }
    assert len(calls) == 3 and calls[-1][-1] == "/usage"


def test_conflicting_environment_does_not_probe_or_run_native(monkeypatch, profile):
    selection, _, _, calls = profile
    monkeypatch.setattr(
        claude_accounts, "subprocess_env_for_cli", lambda _: {"ANTHROPIC_API_KEY": "fixture"}
    )
    assert claude_accounts.accounts.read_identity(selection.profile) == "conflicting_credentials"
    assert subscription_claude.read_claude("/native/claude", selection) is None
    assert calls == []


@pytest.mark.parametrize("scope", ["user", "project"])
def test_competing_settings_block_the_managed_turn_before_native_probe(tmp_path, profile, scope):
    selection, original, _, calls = profile
    directory = selection.profile.directory if scope == "user" else tmp_path / ".claude"
    directory.mkdir(exist_ok=True)
    (directory / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://fixture.invalid"}})
    )
    result = claude_code.run_agent(
        tmp_path,
        "role",
        "prompt",
        SCHEMA,
        resume_thread_id=original.stem,
        account_selection=selection,
    )
    assert not result.ok and "Competing Claude Code authentication" in result.error
    assert calls == []
