"""Native contracts checked 2026-11-06; no agent-authored usage is trusted.

Sources: Codex rust-v0.167.0 exec/event_processor_with_jsonl_output.rs;
Grok Build headless-mode contract; Claude Agent SDK cost-tracking;
Antigravity documented protocol is covered in test_host_antigravity_execution.py.
"""

import json
import subprocess
from pathlib import Path

import pytest

from stackos.host_mcp import run_agent
from stackos.host_mcp.adapters import claude_code, codex, grok, hermes
from stackos.host_mcp.result import normalize_usage, usage_snapshot
from tests.helpers.native_messages import messages_output

SCHEMA = {"type": "object", "required": ["summary"]}
# Synthetic v0.23.3 --usage-file fields, checked against official producers:
# https://github.com/NousResearch/hermes-agent/blob/v0.23.3/hermes_cli/oneshot.py
# https://github.com/NousResearch/hermes-agent/blob/v0.23.3/agent/turn_finalizer.py
# https://github.com/NousResearch/hermes-agent/blob/v0.23.3/agent/usage_pricing.py
# No runnable Hermes binary is available locally; these are protocol fixtures.
HERMES_0233 = {
    "session_id": "hermes-session",
    "model": "observed/model",
    "provider": "openrouter",
    "completed": True,
    "failed": False,
    "partial": False,
    "interrupted": False,
    "turn_exit_reason": "completed",
    "input_tokens": 10,
    "cache_read_tokens": 80,
    "cache_write_tokens": 10,
    "output_tokens": 50,
    "reasoning_tokens": 20,
    "total_tokens": 150,
    "auxiliary": {"input_tokens": 999, "output_tokens": 999, "total_tokens": 1998},
    "total_including_auxiliary": {"total_tokens": 2148},
}


@pytest.mark.parametrize("sandbox", ["workspace-write", "normal"])
@pytest.mark.parametrize("resumed", [False, True])
def test_hermes_native_single_query_preserves_approval_policy(
    tmp_path, monkeypatch, sandbox, resumed
):
    """Run exact upstream approval functions; only detection/config facts are synthetic."""
    import argparse
    import contextlib
    import contextvars
    import dataclasses
    import sys
    import types

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/hermes_0233_approval.json").read_text()
    )
    finite = types.ModuleType("gateway.session_finite")
    finite.__dict__.update(
        contextmanager=contextlib.contextmanager,
        _finite_turn=contextvars.ContextVar("finite", default=None),
        _unattended_turn=contextvars.ContextVar("unattended", default=False),
    )
    exec("\n\n".join(fixture["sources"][0]["functions"]), finite.__dict__)
    monkeypatch.setitem(sys.modules, "gateway.session_finite", finite)
    native = types.ModuleType("hermes_approval_fixture")
    monkeypatch.setitem(sys.modules, native.__name__, native)
    native.__dict__.update(dataclass=dataclasses.dataclass)
    exec("\n\n".join(fixture["sources"][1]["functions"]), native.__dict__)
    dangerous = "rm -rf /synthetic-owned-fixture"
    native.__dict__.update(
        _YOLO_MODE_FROZEN=False,
        is_current_session_yolo_enabled=lambda: False,
        approval_context=types.SimpleNamespace(
            _get_approval_mode=lambda: "manual", _get_single_query_approval_mode=lambda: "deny"
        ),
        _should_skip_container_guards=lambda *_a, **_kw: False,
        _floor_block=lambda _cmd: None,
        hardline_scan_incomplete=lambda _cmd: False,
        _command_matches_permanent_allowlist=lambda _cmd: False,
        get_current_session_key=lambda: "hermes-session",
        _approval_scope=lambda *_: None,
        _command_findings=lambda cmd: (
            [("recursive-delete", "recursive deletion")] if cmd == dangerous else []
        ),
        _is_permanently_approved=lambda _: False,
    )
    context = native._Unattended(
        "single_query", "single_query_mode", "no human is present", "here", "trusted"
    )
    native._run_approval_gate = lambda **kwargs: native._unattended_deny(
        kwargs["display_target"], context
    )
    calls = []
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")

    def execute(command, **kwargs):
        calls.append(command)
        # Exact upstream declarations exercise top-level/subcommand placement,
        # defaults and destinations. Hermes consumes --profile before argparse.
        parser = argparse.ArgumentParser()
        chat_parser = parser.add_subparsers(dest="command").add_parser("chat")
        for owner, selected in (
            (parser, "_add_top_level_flags"),
            (chat_parser, "_build_chat_parser"),
        ):
            bindings = {
                "add": owner.add_argument,
                "parser": parser,
                "chat_parser": chat_parser,
                "SUPPRESS": argparse.SUPPRESS,
                "_query_group": chat_parser.add_mutually_exclusive_group(),
                "inherited": lambda target, *a, **kw: target.add_argument(*a, **kw),
            }
            for declaration in fixture["parser"]["declarations"][selected]:
                exec(declaration, bindings)
        profile_index = command.index("--profile")
        argv = command[1:profile_index] + command[profile_index + 2 :]
        args = parser.parse_args(argv)
        # cmd_chat forwards the report through its documented stream dispatcher.
        assert args.output_format == "stream-json"
        assert args.usage_file == command[command.index("--usage-file") + 1]
        assert args.provider == "openrouter" and args.model == "selected/model"
        finite_run = bool(args.oneshot or args.oneshot_exit or args.quiet)
        with finite.finite_turn_scope(finite_run, isinstance(args.oneshot, str)):
            assert native._yolo_active() is False
            denied = native.check_dangerous_command(dangerous, "local")
            assert denied["approved"] is False and "BLOCKED" in denied["message"]
            assert native.check_dangerous_command("python --version", "local")["approved"] is True
        report = Path(command[command.index("--usage-file") + 1])
        report.write_text(json.dumps(HERMES_0233))
        return subprocess.CompletedProcess(
            command,
            0,
            messages_output("hermes", {"text": '{"summary":"OK"}'}),
            "session_id: hermes-session",
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        "hermes",
        tmp_path,
        "Review" if sandbox == "normal" else "Delivery",
        "Work",
        SCHEMA,
        sandbox=sandbox,
        model="selected/model",
        provider="openrouter",
        profile="work",
        resume_thread_id="hermes-session" if resumed else None,
    )
    assert result.ok, result.error
    assert len(calls) == 1
    # Counterfactual control: the rejected entrypoint bypasses the same native gate.
    with finite.finite_turn_scope(True, True):
        assert native.check_dangerous_command(dangerous, "local")["approved"] is True


@pytest.mark.parametrize(
    "fault", [None, "exit", "partial", "failed", "interrupted", "incomplete", "text", "schema"]
)
def test_hermes_0233_result_and_usage_survive_failures(tmp_path, monkeypatch, fault):
    report = dict(HERMES_0233)
    if fault in {"partial", "failed", "interrupted"}:
        report[fault] = True
    if fault == "incomplete":
        report["completed"] = False
    paths = []
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")

    def execute(command, **kwargs):
        assert command[command.index("--profile") + 1] == "work"
        assert command[command.index("--provider") + 1] == "openrouter"
        assert command[command.index("--model") + 1] == "selected/model"
        assert "Role" in command[command.index("--query") + 1]
        assert "Work" in command[command.index("--query") + 1]
        assert not {"--query-file", "-z", "--yolo"}.intersection(command)
        path = Path(command[command.index("--usage-file") + 1])
        paths.append(path)
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert not path.exists()
        path.write_text(json.dumps(report))
        text = "bad JSON" if fault == "text" else "{}" if fault == "schema" else '{"summary":"OK"}'
        return subprocess.CompletedProcess(
            command, 2 if fault == "exit" else 0, messages_output("hermes", {"text": text}), ""
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        "hermes",
        tmp_path,
        "Role",
        "Work",
        SCHEMA,
        model="selected/model",
        provider="openrouter",
        profile="work",
    )
    assert result.ok is (fault is None)
    if fault in {"partial", "failed", "interrupted", "incomplete"}:
        assert ("completed=false" if fault == "incomplete" else fault) in result.error
    assert result.exit_code == (2 if fault == "exit" else 0)
    assert result.thread_id == "hermes-session"
    assert result.model_observation == {
        "status": "observed",
        "source": "hermes.usage-file.v0.23.3",
        "session_id": "hermes-session",
        "model": "observed/model",
        "provider": "openrouter",
    }
    assert result.usage == usage_snapshot(
        "hermes.oneshot.main.v0.23.3",
        "session",
        100,
        50,
        150,
        cache_read=80,
        cache_write=10,
        reasoning=20,
    )
    assert len(paths) == 1 and not paths[0].parent.exists()


@pytest.mark.parametrize(
    "fault", ["missing", "malformed", "array", "oversized", "nested", "symlink", "fifo"]
)
def test_hermes_optional_report_is_bounded_owned_and_unknown(tmp_path, monkeypatch, fault):
    import os

    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps(HERMES_0233))
    paths = []
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")

    def execute(command, **kwargs):
        path = Path(command[command.index("--usage-file") + 1])
        paths.append(path)
        if fault == "malformed":
            path.write_text("{")
        elif fault == "array":
            path.write_text("[]")
        elif fault == "oversized":
            path.write_text(" " * 65537 + json.dumps(HERMES_0233))
        elif fault == "nested":
            path.write_text("[" * 32000 + "0" + "]" * 32000)
        elif fault == "symlink":
            path.symlink_to(foreign)
        elif fault == "fifo":
            os.mkfifo(path)
        return subprocess.CompletedProcess(
            command,
            0,
            messages_output(
                "hermes", {"text": '{"summary":"OK"}', "session_id": "owned-terminal-session"}
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        "hermes", tmp_path, "Role", "Work", SCHEMA, model="model", provider="openrouter"
    )
    assert result.ok
    assert result.thread_id == "owned-terminal-session"
    assert result.usage is None and result.model_observation is None
    assert not paths[0].parent.exists()
    assert json.loads(foreign.read_text()) == HERMES_0233


@pytest.mark.parametrize("exit_code", [0, 1, 130])
def test_hermes_terminal_identity_survives_without_optional_metrics(
    tmp_path, monkeypatch, exit_code
):
    # Independent identity is emitted for failed/interrupted results too:
    # https://github.com/NousResearch/hermes-agent/blob/v0.23.3/hermes_cli/stream_json.py
    from stackos.repositories.tracker.receipt_projection import receipt_input

    terminal = {
        "type": "result",
        "session_id": "owned-terminal-session",
        "exit_code": exit_code,
        "error": "interrupted" if exit_code else None,
        "text": '{"summary":"OK"}',
    }
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(
            command, exit_code, json.dumps(terminal), "interrupted" if exit_code else ""
        ),
    )
    result = run_agent(
        "hermes", tmp_path, "Role", "Work", SCHEMA, model="model", provider="openrouter"
    )
    assert result.ok is (exit_code == 0)
    assert result.exit_code == exit_code
    assert result.thread_id == terminal["session_id"]
    assert result.usage is None and result.model_observation is None
    receipt = receipt_input(
        {"launch": {"host_key": "hermes"}, "thread_id": result.thread_id, "error": result.error}
    )
    assert receipt["thread_id"] == terminal["session_id"]


def test_hermes_exact_resume_uses_terminal_identity_without_optional_metrics(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")

    def execute(command, **kw):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            messages_output(
                "hermes",
                {
                    "session_id": "owned-terminal-session",
                    "text": '{"summary":"OK"}',
                },
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", execute)
    first = run_agent(
        "hermes", tmp_path, "Role", "Work", SCHEMA, model="model", provider="openrouter"
    )
    assert first.ok and first.thread_id == "owned-terminal-session" and first.usage is None
    resumed = run_agent(
        "hermes",
        tmp_path,
        "Role",
        "Repair",
        SCHEMA,
        model="model",
        provider="openrouter",
        resume_thread_id=first.thread_id,
    )
    assert resumed.ok and resumed.thread_id == first.thread_id and resumed.usage is None
    assert len(calls) == 2 and calls[1][calls[1].index("--resume") + 1] == first.thread_id


def test_hermes_conflicting_terminal_and_report_identity_fails_closed():
    result = hermes._agent_result(
        messages_output(
            "hermes", {"session_id": "different-terminal-session", "text": '{"summary":"OK"}'}
        ),
        HERMES_0233,
    )
    assert not result.ok and "identity mismatch" in result.error
    assert result.thread_id is None
    assert result.usage is None and result.model_observation is None


@pytest.mark.parametrize("session_id", [" ", " padded ", "--last", "latest", 123])
def test_hermes_invalid_terminal_identity_is_not_retained(session_id):
    result = hermes._agent_result(
        messages_output("hermes", {"session_id": session_id, "text": '{"summary":"OK"}'})
    )
    assert not result.ok and result.thread_id is None


def test_hermes_corrupt_optional_identity_cannot_discard_valid_terminal_identity():
    result = hermes._agent_result(
        messages_output(
            "hermes", {"session_id": "owned-terminal-session", "text": '{"summary":"OK"}'}
        ),
        {**HERMES_0233, "session_id": 123},
    )
    assert result.ok and result.thread_id == "owned-terminal-session"
    assert result.usage is None and result.model_observation is None


def test_hermes_failed_start_with_empty_native_identity_keeps_failure_reason():
    result = hermes._agent_result(
        json.dumps(
            {
                "type": "result",
                "session_id": "",
                "exit_code": 1,
                "text": "",
                "error": "credentials unavailable",
            }
        )
    )
    assert not result.ok and result.error == "credentials unavailable"
    assert result.thread_id is None and result.usage is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", None),
        ("output_tokens", -1),
        ("cache_read_tokens", True),
        ("cache_write_tokens", "10"),
        ("total_tokens", 50),
    ],
)
def test_hermes_malformed_counters_do_not_invent_zero(field, value):
    result = hermes._agent_result(
        messages_output("hermes", {"text": '{"summary":"OK"}'}), {**HERMES_0233, field: value}
    )
    assert result.usage is None
    assert result.thread_id == "hermes-session" and result.result == {"summary": "OK"}


@pytest.mark.parametrize(
    "fault", ["missing_result", "native_error", "missing_exit", "invalid_text"]
)
def test_hermes_chat_terminal_failure_keeps_report_evidence(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")

    def execute(command, **kwargs):
        Path(command[command.index("--usage-file") + 1]).write_text(json.dumps(HERMES_0233))
        terminal = {"type": "result", "exit_code": 0, "text": '{"summary":"OK"}'}
        if fault == "missing_result":
            terminal["type"] = "text"
        elif fault == "native_error":
            terminal.update(exit_code=1, error="native denied")
        elif fault == "missing_exit":
            del terminal["exit_code"]
        else:
            terminal["text"] = "invalid JSON"
        return subprocess.CompletedProcess(command, 0, json.dumps(terminal), "")

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        "hermes", tmp_path, "Role", "Work", SCHEMA, model="model", provider="openrouter"
    )
    assert not result.ok and result.error
    assert result.thread_id == "hermes-session"
    assert result.usage["snapshot"]["total"] == 150


@pytest.mark.parametrize("failure", ["launch", "exit"])
def test_hermes_failure_without_report_stays_failed_and_cleans(tmp_path, monkeypatch, failure):
    paths = []
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")

    def execute(command, **kwargs):
        paths.append(Path(command[command.index("--usage-file") + 1]))
        if failure == "launch":
            raise OSError("fixture launch unavailable")
        return subprocess.CompletedProcess(command, 2, '{"summary":"OK"}', "fixture failure")

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        "hermes", tmp_path, "Role", "Work", SCHEMA, model="model", provider="openrouter"
    )
    assert not result.ok and result.error
    assert result.exit_code == (None if failure == "launch" else 2)
    assert result.usage is None and result.thread_id is None
    assert len(paths) == 1 and not paths[0].parent.exists()


def test_hermes_unmanaged_protocol_reaches_final_receipt_and_exact_resume(tmp_path, monkeypatch):
    import sys

    from stackos.host_mcp import service
    from stackos.repositories.tracker.agent_stats import _model_identity
    from stackos.repositories.tracker.receipt_projection import receipt_input

    native = tmp_path / "hermes-fixture"
    output_paths = tmp_path / "paths.jsonl"
    native.write_text(
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "assert '--query' in args and '--usage-file' in args and '-z' not in args\n"
        f"report = {HERMES_0233!r}\n"
        "if '--resume' in args:\n"
        " assert args[args.index('--resume')+1] == report['session_id']\n"
        " for k in ('input_tokens','output_tokens','total_tokens','cache_read_tokens',"
        "'cache_write_tokens','reasoning_tokens'): report[k] *= 2\n"
        "path = Path(args[args.index('--usage-file')+1])\n"
        f"with open({str(output_paths)!r}, 'a') as log: log.write(str(path)+'\\n')\n"
        "path.write_text(json.dumps(report))\n"
        'print(json.dumps({"type":"result", "exit_code":0, "text":\'{"summary":"OK"}\'}))\n'
    )
    native.chmod(0o700)
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: str(native))
    previous = None
    measured = []
    for resumed in (False, True):
        result = service.run_agent(
            "hermes",
            tmp_path,
            "Role",
            "Work",
            SCHEMA,
            model="selected/model",
            provider="openrouter",
            profile="work",
            resume_thread_id="hermes-session" if resumed else None,
            previous_usage=previous,
        )
        assert result.ok, result.error
        assert result.model_observation["model"] == "observed/model"
        assert result.model_observation["provider"] == "openrouter"
        previous = normalize_usage(
            result.usage, session_id=result.thread_id, resumed=resumed, previous=previous
        )
        measured.append(previous["measured"]["total"])
        receipt = receipt_input(
            {
                "launch": {"host_key": "hermes", "model": "selected/model"},
                "thread_id": result.thread_id,
                "model_observation": result.model_observation,
                "usage": previous,
            }
        )
        assert receipt["model_observation"]["provider"] == "openrouter"
        assert _model_identity(receipt) == ("observed/model", "observed")
        assert receipt["usage"]["measured"]["total"] == 150
    assert measured == [150, 150]
    paths = [Path(line) for line in output_paths.read_text().splitlines()]
    assert len(paths) == 2 and paths[0] != paths[1]
    assert all(not path.parent.exists() for path in paths)


# Minimal native counters captured 2026-11-06 in the approved usage probe.
# Session IDs and response text are synthetic; no transcript is retained.
CODEX_0167 = [
    {
        "input_tokens": 18419,
        "cached_input_tokens": 7168,
        "cache_write_input_tokens": 0,
        "output_tokens": 23,
        "reasoning_output_tokens": 14,
    },
    {
        "input_tokens": 36895,
        "cached_input_tokens": 25472,
        "cache_write_input_tokens": 0,
        "output_tokens": 30,
        "reasoning_output_tokens": 14,
    },
]
GROK_1077 = [
    {
        "input_tokens": 6820,
        "cache_read_input_tokens": 8832,
        "cache_creation_input_tokens": 0,
        "output_tokens": 57,
        "reasoning_tokens": 52,
        "total_tokens": 15709,
    },
    {
        "input_tokens": 147,
        "cache_read_input_tokens": 15616,
        "cache_creation_input_tokens": 0,
        "output_tokens": 33,
        "reasoning_tokens": 28,
        "total_tokens": 15796,
    },
]
CLAUDE = {
    "type": "result",
    "session_id": "session",
    "structured_output": {"summary": "OK"},
    "modelUsage": {
        "main": {
            "inputTokens": 10,
            "outputTokens": 30,
            "cacheReadInputTokens": 80,
            "cacheCreationInputTokens": 10,
        },
        "helper": {
            "inputTokens": 5,
            "outputTokens": 10,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 5,
        },
    },
    # Narrower main-loop counters must not be added or substituted.
    "usage": {"input_tokens": 999999, "output_tokens": 999999},
}


def test_captured_codex_0167_cumulative_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")
    launches = []

    def execute(command, **kwargs):
        counters = CODEX_0167[len(launches)]
        launches.append(command)
        Path(command[command.index("-o") + 1]).write_text('{"summary":"OK"}')
        events = [
            {"type": "thread.started", "thread_id": "session"},
            {"type": "turn.completed", "usage": counters},
        ]
        return subprocess.CompletedProcess(command, 0, "\n".join(map(json.dumps, events)), "")

    monkeypatch.setattr(subprocess, "run", execute)
    previous = None
    totals = []
    for resumed in (False, True):
        result = run_agent(
            "codex",
            tmp_path,
            "Instructions",
            "Work",
            SCHEMA,
            resume_thread_id="session" if resumed else None,
        )
        assert result.ok
        measured = normalize_usage(
            result.usage, session_id=result.thread_id, resumed=resumed, previous=previous
        )
        assert measured["basis"] == "session"
        assert measured["measured"]["cache_read"] == (18304 if resumed else 7168)
        assert measured["measured"]["cache_write"] == 0
        assert measured["measured"]["reasoning"] == (0 if resumed else 14)
        totals.append(measured["measured"]["total"])
        previous = measured
    assert totals == [18442, 18483]
    assert sum(totals) == 36925  # Never 55367, nor last_token_usage for a multi-request turn.
    assert len(launches) == 2


def test_captured_grok_1077_invocation_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fake/grok")
    launches = []

    def execute(command, **kwargs):
        counters = GROK_1077[len(launches)]
        launches.append(command)
        envelope = {
            "sessionId": "session",
            "stopReason": "end_turn",
            "text": '{"summary":"OK"}',
            "usage": counters,
            # Same native facts in another projection must not be added twice.
            "modelUsage": {"grok-4.7-build": {"inputTokens": 999999}},
        }
        return subprocess.CompletedProcess(command, 0, messages_output("grok", envelope), "")

    monkeypatch.setattr(subprocess, "run", execute)
    previous = None
    totals = []
    for resumed in (False, True):
        result = run_agent(
            "grok",
            tmp_path,
            "Instructions",
            "Work",
            SCHEMA,
            resume_thread_id="session" if resumed else None,
        )
        assert result.ok
        measured = normalize_usage(
            result.usage, session_id=result.thread_id, resumed=resumed, previous=previous
        )
        assert measured["basis"] == "invocation"
        assert measured["measured"]["cache_read"] == (15616 if resumed else 8832)
        assert measured["measured"]["cache_write"] == 0
        assert measured["measured"]["reasoning"] == (28 if resumed else 52)
        totals.append(measured["measured"]["total"])
        previous = measured
    assert totals == [15709, 15796]
    assert sum(totals) == 31505


def test_captured_claude_21319_mapping_is_preserved():
    previous = None
    totals = []
    # Detailed native-fields evidence: fresh, then cumulative same-session resume.
    for resumed, counts in enumerate(((2, 0, 7624, 52, 0), (6, 7624, 7714, 271, 166))):
        value = {
            **CLAUDE,
            "modelUsage": {
                "claude-opus-5-5": dict(
                    zip(
                        (
                            "inputTokens",
                            "cacheReadInputTokens",
                            "cacheCreationInputTokens",
                            "outputTokens",
                            "thinkingTokens",
                        ),
                        counts,
                        strict=True,
                    )
                )
            },
        }
        result = claude_code._agent_result(json.dumps(value), native_version="2.1.319")
        measured = normalize_usage(
            result.usage, session_id=result.thread_id, resumed=bool(resumed), previous=previous
        )
        totals.append(measured["measured"]["total"])
        assert measured["measured"]["cache_read"] == (7624 if resumed else 0)
        assert measured["measured"]["cache_write"] == (90 if resumed else 7624)
        assert measured["measured"]["reasoning"] == (166 if resumed else 0)
        previous = measured
    assert totals == [7678, 7937]


@pytest.mark.parametrize(
    "fault", [None, "native", "exit", "schema", "text", "missing_text", "session", "stop"]
)
def test_grok_usage_survives_result_errors(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fake/grok")
    value = {
        "sessionId": "session",
        "stopReason": "end_turn",
        "text": '{"summary":"OK"}',
        "usage": GROK_1077[0],
    }
    if fault == "native":
        value.update(type="error", error="denied")
    elif fault == "schema":
        value["text"] = "{}"
    elif fault == "text":
        value["text"] = "bad"
    elif fault == "missing_text":
        value.pop("text")
    elif fault == "stop":
        value["stopReason"] = "max_tokens"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(
            command, int(fault == "exit"), messages_output("grok", value), ""
        ),
    )
    result = run_agent(
        "grok",
        tmp_path,
        "Instructions",
        "Work",
        SCHEMA,
        resume_thread_id="wrong" if fault == "session" else None,
    )
    assert result.ok is (fault is None)
    assert result.thread_id == "session"
    assert result.usage["snapshot"] == {
        "input": 15652,
        "output": 57,
        "total": 15709,
        "cache_read": 8832,
        "cache_write": 0,
        "reasoning": 52,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"input_tokens": None},
        {"cache_read_input_tokens": True},
        {"cache_creation_input_tokens": -1},
        {"output_tokens": "57"},
        {"total_tokens": 1},
        {"total_tokens": None},
    ],
)
def test_grok_malformed_usage_is_optional(change):
    value = {
        "sessionId": "session",
        "stopReason": "end_turn",
        "text": '{"summary":"OK"}',
        "usage": {**GROK_1077[0], **change},
    }
    result = grok._agent_result(json.dumps(value))
    assert result.ok and result.thread_id == "session"
    assert result.usage is None


def test_grok_cache_creation_and_incomplete_usage():
    value = {
        "sessionId": "session",
        "stopReason": "end_turn",
        "text": '{"summary":"OK"}',
        "usage": {**GROK_1077[0], "cache_creation_input_tokens": 10, "total_tokens": 15719},
    }
    assert grok._agent_result(json.dumps(value)).usage["snapshot"]["total"] == 15719
    # The native contract explicitly marks incomplete aggregation; do not publish a full total.
    assert grok._agent_result(json.dumps({**value, "usage_is_incomplete": True})).usage is None


@pytest.mark.parametrize(
    "version,expected_basis,expected_total",
    [
        ("2.1.276 (Claude Code)", "invocation", 380),
        ("2.1.277 (Claude Code)", "session", 230),
        ("2.1.290", "session", 230),
        ("unrecognized", None, None),
        ("2.1.277-beta.1 (Claude Code)", None, None),
        ("3.0.0 (Claude Code)", None, None),
    ],
)
def test_claude_native_version_proves_resumed_usage_basis(
    tmp_path, monkeypatch, version, expected_basis, expected_total
):
    """Cost-tracking contract: claude -p totals carry over only since 2.1.277."""
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/selected/claude")
    launches, probes = [], []

    def execute(command, **kwargs):
        assert command[0] == "/selected/claude"
        assert kwargs["cwd"] == tmp_path
        if command == ["/selected/claude", "--version"]:
            probes.append(command)
            assert kwargs["timeout"] <= 5
            return subprocess.CompletedProcess(command, 0, version, "")
        launches.append(command)
        if len(launches) == 2:
            assert command[command.index("--resume") + 1] == "session"
        value = {
            **CLAUDE,
            "modelUsage": {
                "main": {
                    "inputTokens": 100 if len(launches) == 1 else 160,
                    "outputTokens": 50 if len(launches) == 1 else 70,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                }
            },
        }
        return subprocess.CompletedProcess(command, 0, messages_output("claude-code", value), "")

    monkeypatch.setattr(subprocess, "run", execute)
    previous = None
    contributions = []
    for resumed in (False, True):
        result = run_agent(
            "claude-code",
            tmp_path,
            "Instructions",
            "Work",
            SCHEMA,
            resume_thread_id="session" if resumed else None,
        )
        assert result.ok
        measured = normalize_usage(
            result.usage, session_id=result.thread_id, resumed=resumed, previous=previous
        )
        if expected_basis is None:
            assert measured["measured"] is None
        else:
            assert measured["basis"] == expected_basis
            contributions.append(measured["measured"]["total"])
        previous = measured
    if expected_total is not None:
        assert sum(contributions) == expected_total
    assert len(launches) == len(probes) == 2


@pytest.mark.parametrize("probe_fault", ["oserror", "timeout", "unicode", "nonzero", "malformed"])
@pytest.mark.parametrize("native_failure", [False, True])
def test_claude_version_probe_failure_cannot_change_native_result(
    tmp_path, monkeypatch, probe_fault, native_failure
):
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/selected/claude")
    launches = []

    def execute(command, **kwargs):
        if command == ["/selected/claude", "--version"]:
            if probe_fault == "oserror":
                raise OSError("unavailable")
            if probe_fault == "timeout":
                raise subprocess.TimeoutExpired(command, 3)
            if probe_fault == "unicode":
                raise UnicodeError("invalid version output")
            return subprocess.CompletedProcess(
                command,
                1 if probe_fault == "nonzero" else 0,
                "2.1.277 (Claude Code)" if probe_fault == "nonzero" else "bad",
                "",
            )
        launches.append(command)
        value = {**CLAUDE, "is_error": native_failure, "errors": ["native denied"]}
        return subprocess.CompletedProcess(
            command, int(native_failure), messages_output("claude-code", value), ""
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent("claude-code", tmp_path, "Instructions", "Work", SCHEMA)
    assert result.ok is not native_failure
    assert result.exit_code == int(native_failure)
    assert result.usage is None
    assert result.result == CLAUDE["structured_output"]
    assert ("native denied" in result.error) if native_failure else result.error is None
    assert len(launches) == 1


def test_claude_unproven_terminal_version_and_older_baseline_are_not_trusted():
    assert claude_code._agent_result(json.dumps({**CLAUDE, "version": "2.1.277"})).usage is None
    result = claude_code._agent_result(json.dumps(CLAUDE), native_version="2.1.277")
    previous = normalize_usage(
        usage_snapshot("claude.model-usage", "session", 10, 10), session_id="session", resumed=False
    )
    measured = normalize_usage(result.usage, session_id="session", resumed=True, previous=previous)
    assert measured["reason"] == "missing_baseline"
    assert measured["snapshot"]["total"] == 150
    assert measured["measured"] is None


def test_independent_invocations_sum_once_and_missing_session_is_unknown():
    contributions = [
        normalize_usage(
            usage_snapshot("fixture", "invocation", i, o), session_id="a", resumed=True
        )["measured"]["total"]
        for i, o in ((60, 40), (30, 20))
    ]
    assert sum(contributions) == 150
    assert (
        normalize_usage(
            usage_snapshot("fixture", "session", 100, 50), session_id=None, resumed=False
        )["reason"]
        == "missing_session"
    )


@pytest.mark.parametrize("basis", ["invocation", "session"])
def test_shared_measurement_and_cumulative_baseline(basis):
    first = normalize_usage(
        usage_snapshot("fixture", basis, 100, 50), session_id="a", resumed=False
    )
    assert first["measured"]["total"] == 150
    second = normalize_usage(
        usage_snapshot("fixture", basis, 160, 70), session_id="a", resumed=True, previous=first
    )
    assert second["measured"]["total"] == (80 if basis == "session" else 230)
    if basis == "session":
        legacy = normalize_usage(
            usage_snapshot("fixture", basis, 100, 50), session_id="a", resumed=True
        )
        assert legacy["measured"] is None and legacy["reason"] == "missing_baseline"
        assert (
            normalize_usage(
                usage_snapshot("fixture", basis, 160, 70),
                session_id="a",
                resumed=True,
                previous=legacy,
            )["measured"]["total"]
            == 80
        )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"session_id": "other"}, "missing_baseline"),
        ({"scope": "other"}, "missing_baseline"),
        ({"basis": "invocation"}, "missing_baseline"),
        ({"snapshot": {"input": 300, "output": 100, "total": 400}}, "invalid_delta"),
        ({"snapshot": {"input": True, "output": 0, "total": 1}}, "missing_baseline"),
        ({"snapshot": None}, "missing_baseline"),
    ],
)
def test_incompatible_or_decreasing_baseline_is_unavailable(change, reason):
    baseline = normalize_usage(
        usage_snapshot("fixture", "session", 100, 50), session_id="a", resumed=False
    )
    result = normalize_usage(
        usage_snapshot("fixture", "session", 160, 70),
        session_id="a",
        resumed=True,
        previous={**baseline, **change},
    )
    assert result["measured"] is None and result["reason"] == reason


@pytest.mark.parametrize(
    "usage",
    [
        None,
        [],
        "bad",
        {},
        {"snapshot": []},
        {"unit": "tokens", "basis": [], "scope": "a", "snapshot": {"input": 1, "output": 1}},
        {
            "unit": "tokens",
            "basis": "invocation",
            "scope": "a",
            "snapshot": {"input": -1, "output": 1, "total": 0},
        },
    ],
)
def test_optional_malformed_usage_never_raises(usage):
    assert normalize_usage(usage, session_id="a", resumed=False)["measured"] is None


@pytest.mark.parametrize("reset", [False, True])
def test_details_do_not_invalidate_legacy_or_reset_base_delta(reset):
    previous = {
        "unit": "tokens",
        "scope": "fixture",
        "basis": "session",
        "session_id": "a",
        "snapshot": {"input": 100, "output": 50, "total": 150},
        "status": "unavailable" if reset else "measured",
        "reason": "operator_reset" if reset else None,
        "measured": None,
    }
    current = normalize_usage(
        usage_snapshot("fixture", "session", 160, 70, cache_read=40, cache_write=10, reasoning=5),
        session_id="a",
        resumed=True,
        previous=previous,
    )
    assert current["status"] == "measured"
    assert current["measured"] == {
        "input": 60,
        "output": 20,
        "total": 80,
        "cache_read": None,
        "cache_write": None,
        "reasoning": None,
    }
    following = normalize_usage(
        usage_snapshot("fixture", "session", 170, 75, cache_read=40, cache_write=12, reasoning=8),
        session_id="a",
        resumed=True,
        previous=current,
    )
    assert following["measured"] == {
        "input": 10,
        "output": 5,
        "total": 15,
        "cache_read": 0,
        "cache_write": 2,
        "reasoning": 3,
    }


@pytest.mark.parametrize(
    "details,expected",
    [
        ({}, {"cache_read": None, "cache_write": None, "reasoning": None}),
        (
            {"cache_read": 0, "cache_write": 0, "reasoning": 0},
            {"cache_read": 0, "cache_write": 0, "reasoning": 0},
        ),
        (
            {"cache_read": True, "cache_write": "1", "reasoning": []},
            {"cache_read": None, "cache_write": None, "reasoning": None},
        ),
        (
            {"cache_read": 101, "cache_write": 5, "reasoning": 51},
            {"cache_read": None, "cache_write": 5, "reasoning": None},
        ),
        (
            {"cache_read": 70, "cache_write": 40, "reasoning": 10},
            {"cache_read": None, "cache_write": None, "reasoning": 10},
        ),
    ],
)
def test_optional_detail_validation_preserves_base(details, expected):
    # Exercise both the adapter helper and the untrusted persisted-map boundary.
    for usage in (
        usage_snapshot("fixture", "invocation", 100, 50, **details),
        {
            "unit": "tokens",
            "scope": "fixture",
            "basis": "invocation",
            "snapshot": {"input": 100, "output": 50, "total": 150, **details},
        },
    ):
        result = normalize_usage(usage, session_id="a", resumed=False)
        assert result["status"] == "measured"
        assert result["measured"] == {"input": 100, "output": 50, "total": 150, **expected}


@pytest.mark.parametrize(
    "current_details,expected",
    [
        (
            {"cache_read": 19, "cache_write": 12, "reasoning": 4},
            {"cache_read": None, "cache_write": 2, "reasoning": None},
        ),
        (
            {"cache_read": 65, "cache_write": 30, "reasoning": 10},
            {"cache_read": None, "cache_write": None, "reasoning": 5},
        ),
        (
            {"cache_read": 25, "cache_write": 10, "reasoning": 30},
            {"cache_read": 5, "cache_write": 0, "reasoning": None},
        ),
    ],
)
def test_detail_delta_bounds_do_not_erase_base(current_details, expected):
    previous = normalize_usage(
        usage_snapshot("fixture", "session", 100, 50, cache_read=20, cache_write=10, reasoning=5),
        session_id="a",
        resumed=False,
    )
    result = normalize_usage(
        usage_snapshot("fixture", "session", 160, 70, **current_details),
        session_id="a",
        resumed=True,
        previous=previous,
    )
    assert result["measured"] == {"input": 60, "output": 20, "total": 80, **expected}


@pytest.mark.parametrize("detail", [None, True, -1, "10", 99999])
def test_invalid_grok_optional_detail_preserves_other_counts(detail):
    value = {
        "sessionId": "session",
        "stopReason": "end_turn",
        "text": '{"summary":"OK"}',
        "usage": {**GROK_1077[0], "reasoning_tokens": detail},
    }
    result = grok._agent_result(json.dumps(value))
    assert result.ok
    assert result.usage["snapshot"] == {
        "input": 15652,
        "output": 57,
        "total": 15709,
        "cache_read": 8832,
        "cache_write": 0,
        "reasoning": None,
    }


def test_missing_detail_is_not_bridged_on_the_next_resume():
    previous = normalize_usage(
        usage_snapshot("fixture", "session", 100, 50, cache_read=20, cache_write=10, reasoning=5),
        session_id="a",
        resumed=False,
    )
    missing = normalize_usage(
        usage_snapshot("fixture", "session", 160, 70, cache_write=10, reasoning=10),
        session_id="a",
        resumed=True,
        previous=previous,
    )
    current = normalize_usage(
        usage_snapshot("fixture", "session", 200, 90, cache_read=30, cache_write=10, reasoning=15),
        session_id="a",
        resumed=True,
        previous=missing,
    )
    assert current["measured"] == {
        "input": 40,
        "output": 20,
        "total": 60,
        "cache_read": None,
        "cache_write": 0,
        "reasoning": 5,
    }


@pytest.mark.parametrize("thinking", [None, True, -1, "10", 100])
def test_claude_optional_thinking_requires_all_model_rows(thinking):
    value = json.loads(json.dumps(CLAUDE))
    value["modelUsage"]["main"]["thinkingTokens"] = thinking
    value["modelUsage"]["helper"]["thinkingTokens"] = 5
    result = claude_code._agent_result(json.dumps(value), native_version="2.1.319")
    assert result.ok
    assert result.usage["snapshot"] == {
        "input": 110,
        "output": 40,
        "total": 150,
        "cache_read": 80,
        "cache_write": 15,
        "reasoning": None,
    }


@pytest.mark.parametrize(
    "host,envelope,expected",
    [
        (
            "claude-code",
            CLAUDE,
            {
                "input": 110,
                "output": 40,
                "total": 150,
                "cache_read": 80,
                "cache_write": 15,
                "reasoning": None,
            },
        ),
    ],
)
@pytest.mark.parametrize("fault", [None, "native", "exit", "schema", "session", "text"])
def test_native_usage_survives_common_error_paths(
    tmp_path, monkeypatch, host, envelope, expected, fault
):
    value = json.loads(json.dumps(envelope))
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/fake/claude")
    if fault == "native":
        value.update(is_error=True, error="native failed", result="native failed")
    if fault == "schema":
        value.update(structured_output={}, response="{}")
    if fault == "text":
        value.update(structured_output=None, response="malformed")

    def execute(command, **kwargs):
        if command == ["/fake/claude", "--version"]:
            return subprocess.CompletedProcess(command, 0, "2.1.277 (Claude Code)", "")
        return subprocess.CompletedProcess(
            command, 1 if fault == "exit" else 0, messages_output(host, value), ""
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        host,
        tmp_path,
        "Instructions",
        "Work",
        SCHEMA,
        resume_thread_id="wrong" if fault == "session" else None,
    )
    assert result.ok is (fault is None)
    assert result.usage["snapshot"] == expected
    assert result.usage["basis"] == "session"
    if fault == "session":
        assert "resume identity mismatch" in result.error


@pytest.mark.parametrize("fault", [None, "native", "exit", "schema", "text", "session"])
def test_codex_usage_survives_all_result_paths(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")

    def execute(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(
            "bad" if fault == "text" else "{}" if fault == "schema" else '{"summary":"OK"}'
        )
        events = [
            {"type": "thread.started", "thread_id": "session"},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}},
        ]
        if fault == "native":
            events.append({"type": "turn.failed", "error": "denied"})
        return subprocess.CompletedProcess(
            command, 1 if fault == "exit" else 0, "\n".join(map(json.dumps, events)), ""
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        "codex",
        tmp_path,
        "Instructions",
        "Work",
        SCHEMA,
        resume_thread_id="wrong" if fault == "session" else None,
    )
    assert result.ok is (fault is None)
    assert result.usage["snapshot"]["total"] == 150
    assert result.usage["snapshot"]["cache_read"] is None
    assert result.usage["snapshot"]["cache_write"] is None
    assert result.usage["snapshot"]["reasoning"] is None


@pytest.mark.parametrize("host,envelope", [("claude-code", CLAUDE)])
@pytest.mark.parametrize(
    "malformed",
    [
        None,
        [],
        "bad",
        {"m": []},
        {"m": {"tokens": []}},
        {
            "m": {
                "inputTokens": True,
                "outputTokens": 1,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            }
        },
    ],
)
def test_malformed_native_usage_is_optional(host, envelope, malformed):
    value = json.loads(json.dumps(envelope))
    value["modelUsage"] = malformed
    result = claude_code._agent_result(json.dumps(value), native_version="2.1.277")
    assert result.ok and result.usage is None


def test_unproven_scope_and_unsupported_hosts_remain_unavailable():
    assert (
        claude_code._agent_result(
            json.dumps(
                {"structured_output": {}, "usage": {"input_tokens": 100, "output_tokens": 50}}
            )
        ).usage
        is None
    )
    assert (
        claude_code._agent_result(
            json.dumps({**CLAUDE, "subtype": "error_during_execution"}), native_version="2.1.277"
        ).usage
        is None
    )
    assert (
        hermes._agent_result(
            json.dumps({"type": "result", "exit_code": 0, "text": "{}", "usage": {"total": 150}})
        ).usage
        is None
    )
    assert (
        grok._agent_result(
            json.dumps({"stopReason": "end_turn", "text": "{}", "usage": {"total": 150}})
        ).usage
        is None
    )
