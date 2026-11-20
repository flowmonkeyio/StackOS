import json
import subprocess
import sys
from hashlib import sha256
from uuid import uuid4

import pytest

from stackos.host_mcp.adapters import claude_credentials as credentials


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS Keychain contract")
def test_disposable_native_keychain_round_trip(tmp_path):
    env = {
        "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(tmp_path / str(uuid4())),
        "USER": "stackos-disposable-test",
    }
    service, account = credentials.keychain_target(env)
    value = {"claudeAiOauth": {"accessToken": "synthetic-test-only"}, "unrelated": "preserved"}
    try:
        credentials.write_store(env, value, "keychain")
        assert credentials.read_store(env) == (value, "keychain")
    finally:
        result = credentials._security(["delete-generic-password", "-a", account, "-s", service])
        assert result.returncode in (0, 44)


def test_locked_primary_does_not_fall_back_to_file(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    (tmp_path / ".credentials.json").write_text("{}")
    monkeypatch.setattr(
        credentials,
        "_security",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 36, "", "locked"),
    )
    with pytest.raises(credentials.ClaudeCredentialError, match="locked"):
        credentials.read_store({"CLAUDE_CONFIG_DIR": str(tmp_path)})


def test_missing_primary_uses_existing_private_native_file(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    native_file = tmp_path / ".credentials.json"
    native_file.write_text('{"claudeAiOauth":{"accessToken":"synthetic"}}')
    native_file.chmod(0o600)
    monkeypatch.setattr(
        credentials,
        "_security",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 44, "", "missing"),
    )
    assert credentials.read_store({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == (
        {"claudeAiOauth": {"accessToken": "synthetic"}},
        "file",
    )


def test_keychain_write_uses_stdin_and_has_no_secret_argv(monkeypatch):
    calls = []

    def security(args, *, payload=None):
        calls.append((args, payload))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(credentials, "_security", security)
    credentials.write_store({}, {"claudeAiOauth": {"token": "synthetic"}}, "keychain")
    assert calls[0][0] == ["-i"]
    assert (
        json.dumps({"claudeAiOauth": {"token": "synthetic"}}, separators=(",", ":")).encode().hex()
        in calls[0][1]
    )
    with pytest.raises(credentials.ClaudeCredentialError, match="limit"):
        credentials.write_store({}, {"claudeAiOauth": {"token": "x" * 3000}}, "keychain")
    assert len(calls) == 1


def test_native_service_hashes_raw_nfc_env_and_respects_empty_secure_override(tmp_path):
    raw = str(tmp_path / "cafe\u0301" / ".." / "raw")
    env = {"HOME": str(tmp_path), "USER": "bad user", "CLAUDE_CONFIG_DIR": raw}
    service, account = credentials.keychain_target(env)
    assert (
        service
        == "Claude Code-credentials-" + sha256(raw.replace("e\u0301", "é").encode()).hexdigest()[:8]
    )
    assert account == "claude-code-user"
    env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = ""
    assert credentials.keychain_target(env)[0] == "Claude Code-credentials"
    assert credentials.store_directory(env)[0] == tmp_path / ".claude"


def test_keychain_interactive_error_is_failed_even_with_zero_process_exit(monkeypatch):
    monkeypatch.setattr(
        credentials,
        "_security",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "", "native write error"),
    )
    with pytest.raises(credentials.ClaudeCredentialError, match="write failed"):
        credentials.write_store({}, {"claudeAiOauth": {"token": "synthetic"}}, "keychain")
