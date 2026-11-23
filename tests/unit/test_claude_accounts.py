from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from stackos.host_mcp.accounts import (
    DesiredAccount,
    HostAccountError,
    HostAccounts,
    NativeAccountProfile,
    ResolvedAccountSelection,
)
from stackos.host_mcp.adapters import claude_accounts, claude_code
from stackos.host_mcp.adapters import claude_credentials as credentials

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
            assert kwargs["env"]["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == str(isolated)
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
    assert not (isolated / "settings.json").exists()
    assert not (isolated / "projects").exists()
    assert not (isolated / ".credentials.json").exists()
    assert accounts.discard_login(selection)
    assert not isolated.exists()
    assert (["auth", "logout"], str(isolated)) in calls
    assert credentials.read_text() == "synthetic default fixture"
    assert not (default / "projects").exists()


def _registry(tmp_path, monkeypatch, *, native_keychain=False, initial_login=True):
    home = tmp_path / "home"
    config = home / ".claude"
    config.mkdir(parents=True, mode=0o700)
    env = {"HOME": str(home), "USER": "stackos-disposable-test", "CLAUDE_CONFIG_DIR": str(config)}
    # A unique explicit directory hashes to a disposable service even for the native home.
    monkeypatch.setattr(
        claude_accounts.claude_mcp, "resolve_claude_bin", lambda: "/synthetic/claude"
    )
    monkeypatch.setattr(claude_accounts, "subprocess_env_for_cli", lambda _: env.copy())
    if not native_keychain:

        def file_read(environment):
            directory, _ = credentials.store_directory(environment)
            return credentials.read_object(directory / ".credentials.json"), "file"

        monkeypatch.setattr(credentials, "read_store", file_read)

    def save(environment, suffix):
        directory, _ = credentials.store_directory(environment)
        _, backend = credentials.read_store(environment)
        credentials.write_store(
            environment,
            {"claudeAiOauth": {"accessToken": suffix}, "mcpSecret": "retained-" + suffix},
            backend,
        )
        metadata = {
            "oauthAccount": {
                "accountUuid": ACCOUNT if suffix == "A" else SESSION,
                "organizationUuid": ORGANIZATION,
                "emailAddress": f"{suffix}@example.invalid",
            },
            "projects": {"original": {"hasTrustDialogAccepted": True}},
            "mcpServers": {"original": {}},
            "slackTag": "retained-" + suffix,
        }
        credentials.write_object(directory / ".claude.json", metadata)

    if initial_login:
        save(env, "A")
    else:
        _, backend = credentials.read_store(env)
        credentials.write_store(env, {"otherCredential": "preserved"}, backend)
        credentials.write_object(config / ".claude.json", {"slackTag": "preserved"})

    def status(_binary, environment, cwd=None):
        directory = Path(environment.get("CLAUDE_CONFIG_DIR") or home / ".claude")
        row = claude_accounts.read_native_object(directory / ".claude.json").get("oauthAccount")
        if row is None:
            return {
                "configDirectory": str(directory),
                "projectsDirectory": str(directory / "projects"),
                "loggedIn": False,
                "authMethod": "none",
            }
        return {
            "configDirectory": str(directory),
            "projectsDirectory": str(directory / "projects"),
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "email": row["emailAddress"],
            "orgId": row["organizationUuid"],
        }

    monkeypatch.setattr(claude_accounts, "_status", status)
    adapter = claude_accounts.ClaudeAccounts()
    monkeypatch.setattr(
        adapter,
        "start_login",
        lambda profile: save(claude_accounts._identity_env("/synthetic/claude", profile), "B"),
    )
    store = HostAccounts(tmp_path, {"claude-code": adapter})
    a = store.adopt("claude-code").account_ref if initial_login else None
    enrollment = str(uuid4())
    store.add("claude-code", enrollment)
    b = store.complete("claude-code", enrollment).account_ref
    return store, env, a, b


def _exercise_switch(store, env, a, b):
    config = Path(env["CLAUDE_CONFIG_DIR"])
    conversation = config / "projects" / "original" / "C.jsonl"
    conversation.parent.mkdir(parents=True)
    conversation.write_text("original conversation C")
    settings = config / "settings.json"
    settings.write_text("original settings")
    original_metadata = json.loads((config / ".claude.json").read_text())
    current, backend = credentials.read_store(env)
    credentials.write_store(
        env, {**current, "claudeAiOauth": {"accessToken": "renewed-A"}}, backend
    )
    assert store.select("claude-code", b, 0).revision == 1
    assert credentials.read_store(env)[0]["claudeAiOauth"] == {"accessToken": "B"}
    current, backend = credentials.read_store(env)
    credentials.write_store(
        env, {**current, "claudeAiOauth": {"accessToken": "renewed-B"}}, backend
    )
    store.select("claude-code", a, 1)
    assert credentials.read_store(env)[0]["claudeAiOauth"] == {"accessToken": "renewed-A"}
    store.select("claude-code", b, 2)
    assert credentials.read_store(env)[0]["claudeAiOauth"] == {"accessToken": "renewed-B"}
    assert credentials.read_store(env)[0]["mcpSecret"] == "retained-A"
    final_metadata = json.loads((config / ".claude.json").read_text())
    for key in ("projects", "mcpServers", "slackTag"):
        assert final_metadata[key] == original_metadata[key]
    assert conversation.read_text() == "original conversation C"
    assert settings.read_text() == "original settings"


def test_select_persists_only_subscription_and_preserves_native_configuration(
    tmp_path, monkeypatch
):
    store, env, a, b = _registry(tmp_path, monkeypatch)
    _exercise_switch(store, env, a, b)


def test_first_account_selection_and_scoped_native_rollback(tmp_path, monkeypatch):
    store, env, _, b = _registry(tmp_path, monkeypatch, initial_login=False)
    assert store.select("claude-code", b, 0).revision == 1
    assert credentials.read_store(env)[0]["claudeAiOauth"] == {"accessToken": "B"}
    selected = store.resolve("claude-code").profile
    claude_accounts.ClaudeAccounts().activate(None, selected)
    assert credentials.read_store(env)[0] == {"otherCredential": "preserved"}
    assert json.loads((Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json").read_text()) == {
        "slackTag": "preserved"
    }


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="Native macOS Keychain")
def test_select_uses_actual_disposable_native_keychain(tmp_path, monkeypatch):
    try:
        store, env, a, b = _registry(tmp_path, monkeypatch, native_keychain=True)
        _exercise_switch(store, env, a, b)
    finally:
        # Include captures as well as enrolled profiles, even if an assertion fails.
        for directory in [
            tmp_path / "home" / ".claude",
            *(tmp_path / "host-accounts").glob("profile-*"),
        ]:
            service, account = credentials.keychain_target(
                {"CLAUDE_CONFIG_DIR": str(directory), "USER": "stackos-disposable-test"}
            )
            result = credentials._security(
                ["delete-generic-password", "-a", account, "-s", service]
            )
            assert result.returncode in (0, 44)


def test_metadata_write_failure_restores_native_login_and_selection(tmp_path, monkeypatch):
    store, env, a, b = _registry(tmp_path, monkeypatch)
    store.select("claude-code", a, 0)
    metadata = Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    before = metadata.read_bytes()
    real_write = credentials.write_object

    def fail_metadata(path, value):
        if path == metadata:
            raise OSError("synthetic metadata replacement failure")
        return real_write(path, value)

    monkeypatch.setattr(credentials, "write_object", fail_metadata)
    with pytest.raises(HostAccountError, match="activation_failed"):
        store.select("claude-code", b, 1)
    assert metadata.read_bytes() == before
    assert credentials.read_store(env)[0]["claudeAiOauth"]["accessToken"] == "A"
    assert store.resolve("claude-code").desired.account_ref == a


def test_failed_secure_store_write_keeps_prior_login_and_metadata(tmp_path, monkeypatch):
    store, env, a, b = _registry(tmp_path, monkeypatch)
    store.select("claude-code", a, 0)
    metadata = Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    before = metadata.read_bytes()
    real_write = credentials.write_store

    def fail_native(environment, value, backend):
        if environment == env:
            raise credentials.ClaudeCredentialError("Synthetic native write refusal")
        return real_write(environment, value, backend)

    monkeypatch.setattr(credentials, "write_store", fail_native)
    with pytest.raises(HostAccountError, match="activation_failed"):
        store.select("claude-code", b, 1)
    assert metadata.read_bytes() == before
    assert credentials.read_store(env)[0]["claudeAiOauth"]["accessToken"] == "A"
    assert store.resolve("claude-code").desired.account_ref == a


def test_native_resume_keeps_original_session_id_and_environment(tmp_path, monkeypatch):
    session = str(uuid4())
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/synthetic/claude")
    monkeypatch.setattr(
        claude_code,
        "subprocess_env_for_cli",
        lambda _: {"CLAUDE_CONFIG_DIR": "/original/native/home"},
    )

    def execute(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "2.1.327 (Claude Code)", "")
        assert command[command.index("--resume") + 1] == session
        assert not kwargs.get("pass_fds")
        assert kwargs["env"] == {
            "CLAUDE_CONFIG_DIR": "/original/native/home",
            "STACKOS_WORKSPACE_ROOT": str(tmp_path.resolve()),
        }
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"type": "system", "subtype": "init", "session_id": session})
            + "\n"
            + json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": session,
                    "structured_output": {"summary": "retained"},
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = claude_code.run_agent(tmp_path, "role", "prompt", SCHEMA, resume_thread_id=session)
    assert result.ok and result.thread_id == session
