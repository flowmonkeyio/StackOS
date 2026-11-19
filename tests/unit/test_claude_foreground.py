from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.adapters.claude_accounts import ClaudeAccountError, transcript_writer
from stackos.host_mcp.foreground import ForegroundSlot, ForegroundStatus
from tests.unit.test_foreground_harness import foreground_terminal

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "claude_foreground.py"
HOST = "claude-code"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    data, default, work = (tmp_path / name for name in ("data", "default", "work"))
    for directory in (data, default, work):
        directory.mkdir(mode=0o700)
    username = "a@example.invalid"
    user_tool = {"type": "stdio", "command": "/usr/bin/true"}
    local_tool = {"type": "stdio", "command": "/usr/bin/false"}
    (default / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "accountUuid": "fef10000-1111-4111-8111-000000000001",
                    "organizationUuid": "fef10000-1111-4111-8111-000000000010",
                    "emailAddress": username,
                },
                "mcpServers": {"user-tool": user_tool},
                "projects": {
                    str(work): {
                        "mcpServers": {"local-tool": local_tool},
                        "hasTrustDialogAccepted": True,
                    }
                },
                "unrelatedAccountData": "must-stay-in-original-profile",
            }
        )
    )
    settings = default / "settings.json"
    settings.write_text('{"permissions":{"defaultMode":"default"}}')
    executable = tmp_path / "claude"
    executable.write_text(
        "#!/bin/sh\nexec " + shlex.join([sys.executable, str(FIXTURE), "native"]) + ' "$@"\n'
    )
    executable.chmod(0o700)
    for key in list(os.environ):
        if key.startswith(("CLAUDE", "ANTHROPIC")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(default))
    monkeypatch.setenv("STACKOS_CLAUDE_BIN", str(executable))
    monkeypatch.setenv("STACKOS_DATA_DIR", str(data))
    accounts = service.host_accounts(data)
    a = accounts.adopt(HOST).account_ref
    enrollment = str(uuid4())
    accounts.add(HOST, enrollment)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if ref := accounts.complete(HOST, enrollment).account_ref:
            break
    else:
        pytest.fail("Isolated Claude fixture did not complete native sign-in")
    enrolled = next((data / "host-accounts").glob("profile-*"))
    metadata = json.loads((enrolled / ".claude.json").read_text())
    assert metadata["mcpServers"] == {"user-tool": user_tool}
    assert metadata["projects"][str(work)]["mcpServers"] == {"local-tool": local_tool}
    assert "unrelatedAccountData" not in metadata
    assert (enrolled / "settings.json").resolve() == settings
    accounts.select(HOST, a, 0)
    return accounts, a, ref, data, work, default


def _confirmed(slot, revision):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        state = slot.status(revision)
        if state.phase == "confirmed" and state.confirmed_revision == revision:
            return state.session_id
        time.sleep(0.025)
    pytest.fail(f"Expected confirmed Claude revision {revision}, got {state}")


def _run(work):
    return foreground_terminal(
        [
            sys.executable,
            str(FIXTURE),
            "stackos",
            "harness",
            "run",
            HOST,
            "--",
            "--model",
            "sonnet",
            "--permission-mode",
            "default",
        ],
        work,
        dict(os.environ),
    )


def _assert_shell(terminal, code):
    terminal.read_until(b"shell modes restored True\r\n")
    terminal.read_until(f"shell returned {code}\r\n".encode())
    terminal.send("operator-marker")
    terminal.read_until(b"shell received operator-marker\r\n")
    assert terminal.exit_code() == code


def test_registered_claude_switch_keeps_tty_transcript_settings_and_writer(harness):
    accounts, a, b, data, work, default = harness
    slot = ForegroundSlot(data, HOST)
    with _run(work) as terminal:
        initial = terminal.native_start()
        session_id = initial["session_id"]
        for revision, email in enumerate(
            ("a@example.invalid", "b@example.invalid", "a@example.invalid"), 1
        ):
            state = initial if revision == 1 else terminal.native_start()
            assert state == dict(initial, email=email)
            assert state["args"] == ["--model", "sonnet", "--permission-mode", "default"]
            assert _confirmed(slot, revision) == session_id
            with (
                pytest.raises(ClaudeAccountError, match="already has a writer"),
                transcript_writer(session_id),
            ):
                pytest.fail("The headless writer acquired an active foreground UUID")
            terminal.send(f"turn-{revision}")
            terminal.read_until(f"effect turn-{revision}\n".encode())
            if revision < 3:
                accounts.select(HOST, b if revision == 1 else a, revision)
            terminal.send("exit")
        _assert_shell(terminal, 0)
    transcripts = list((default / "projects").glob(f"*/{session_id}.jsonl"))
    assert len(transcripts) == 1
    rows = [json.loads(line) for line in transcripts[0].read_text().splitlines()]
    assert [(r["principal"], r["message"]) for r in rows] == [
        ("a@example.invalid", "turn-1"),
        ("b@example.invalid", "turn-2"),
        ("a@example.invalid", "turn-3"),
    ]
    with transcript_writer(session_id):
        assert slot.status(3).phase == "closed"


@pytest.mark.parametrize("failure", ["wrong-b", "hold-b"])
def test_failed_or_withdrawn_claude_admission_discards_input_and_restores_tty(harness, failure):
    accounts, a, b, data, work, default = harness
    (data / failure).touch()
    slot = ForegroundSlot(data, HOST)
    with _run(work) as terminal:
        initial = terminal.native_start()
        session_id = initial["session_id"]
        assert _confirmed(slot, 1) == session_id
        terminal.send("original")
        terminal.read_until(b"effect original\n")
        accounts.select(HOST, b, 1)
        terminal.send("exit")
        assert terminal.native_start()["email"] == "b@example.invalid"
        terminal.send("forbidden")
        if failure == "hold-b":
            accounts.select(HOST, a, 2)
        terminal.read_until(f"stackos harness run {HOST} --session {session_id}".encode())
        _assert_shell(terminal, 1)
    snapshot = slot.status(accounts.resolve(HOST).desired.revision)
    assert snapshot.phase == "failed" and snapshot.confirmed_revision == 1
    assert snapshot.session_id == session_id
    transcript = next((default / "projects").glob(f"*/{session_id}.jsonl"))
    assert [json.loads(line)["message"] for line in transcript.read_text().splitlines()] == [
        "original"
    ]
    with transcript_writer(session_id):
        assert transcript.is_file()


def test_unenrolled_claude_executes_ordinary_cli_with_native_arguments(tmp_path):
    executable = tmp_path / "ordinary-claude"
    executable.write_text('#!/bin/sh\nprintf "ordinary %s|%s\\n" "$1" "$2"\nexit 13\n')
    executable.chmod(0o700)
    env = dict(
        os.environ, STACKOS_CLAUDE_BIN=str(executable), STACKOS_DATA_DIR=str(tmp_path / "data")
    )
    command = [
        sys.executable,
        "-m",
        "stackos",
        "harness",
        "run",
        HOST,
        "--",
        "--continue",
        "prompt",
    ]
    with foreground_terminal(command, tmp_path, env) as terminal:
        terminal.read_until(b"ordinary --continue|prompt\r\n")
        assert terminal.exit_code() == 13
    assert not (tmp_path / "data" / "host-foreground").exists()


def test_claude_native_exit_does_not_authorize_switch_with_unsettled_prompt(harness):
    accounts, _, b, data, work, default = harness
    slot = ForegroundSlot(data, HOST)
    with _run(work) as terminal:
        session_id = terminal.native_start()["session_id"]
        assert _confirmed(slot, 1) == session_id
        terminal.send("original")
        terminal.read_until(b"effect original\n")
        terminal.send("pending")
        terminal.read_until(b"tool is pending\n")
        accounts.select(HOST, b, 1)
        terminal.send("exit")
        terminal.read_until(f"stackos harness run {HOST} --session {session_id}".encode())
        _assert_shell(terminal, 1)
        assert terminal.received.count(b"native {") == 1
    state = slot.status(2)
    assert state.phase == "failed" and state.confirmed_revision == 1
    transcript = next((default / "projects").glob(f"*/{session_id}.jsonl"))
    assert [json.loads(line)["message"] for line in transcript.read_text().splitlines()] == [
        "original"
    ]


def test_status_can_read_open_snapshot_during_native_publish(tmp_path, monkeypatch):
    slot = ForegroundSlot(tmp_path, HOST)
    session = str(uuid4())
    with slot.acquire():
        slot.publish(ForegroundStatus(phase="pending", session_id=session, requested_revision=1))
        replacement = tmp_path / "updated-status"
        replacement.write_text(
            ForegroundStatus(
                phase="confirmed", session_id=session, requested_revision=1, confirmed_revision=1
            ).model_dump_json()
        )
        replacement.chmod(0o600)
        native_open = os.open

        def open_during_publish(path, flags, *args, **kwargs):
            fd = native_open(path, flags, *args, **kwargs)
            if path == slot.status_path and replacement.exists():
                os.replace(replacement, path)
                assert os.fstat(fd).st_nlink == 0
            return fd

        monkeypatch.setattr(os, "open", open_during_publish)
        assert slot.status(1).phase == "pending"
        assert slot.status(1).phase == "confirmed"
