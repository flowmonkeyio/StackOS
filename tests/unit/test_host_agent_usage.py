"""Native contracts checked 2026-11-06; no agent-authored usage is trusted.

Sources: Codex rust-v0.167.0 exec/event_processor_with_jsonl_output.rs;
Grok Build headless-mode contract; Claude Agent SDK cost-tracking;
Gemini CLI headless JSON and core/src/telemetry/uiTelemetry.ts (hydrate).
"""

import json
import subprocess
from pathlib import Path

import pytest

from stackos.host_mcp import run_agent
from stackos.host_mcp.adapters import claude_code, codex, gemini_cli, grok, hermes
from stackos.host_mcp.result import normalize_usage, usage_snapshot

SCHEMA = {"type": "object", "required": ["summary"]}
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
GEMINI = {
    "session_id": "session",
    "response": '{"summary":"OK"}',
    "stats": {
        "models": {
            "main": {
                "tokens": {
                    "prompt": 100,
                    "candidates": 20,
                    "thoughts": 10,
                    "tool": 5,
                    "cached": 80,
                    "input": 20,
                    "total": 135,
                },
                "roles": {"main": {"tokens": {"total": 135}}},
            },
            "helper": {
                "tokens": {
                    "prompt": 10,
                    "candidates": 5,
                    "thoughts": 0,
                    "tool": 0,
                    "cached": 0,
                    "total": 15,
                }
            },
        }
    },
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
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

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
            command, int(fault == "exit"), json.dumps(value), ""
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
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

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
        return subprocess.CompletedProcess(command, int(native_failure), json.dumps(value), "")

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


def test_gemini_details_use_models_once_and_leave_cache_write_unknown():
    first = normalize_usage(
        gemini_cli._agent_result(json.dumps(GEMINI)).usage,
        session_id="session",
        resumed=False,
    )
    assert first["measured"] == {
        "input": 110,
        "output": 40,
        "total": 150,
        "cache_read": 80,
        "cache_write": None,
        "reasoning": 10,
    }
    value = json.loads(json.dumps(GEMINI))
    value["stats"]["models"]["main"]["tokens"].update(
        prompt=120,
        cached=90,
        thoughts=15,
        total=160,
    )
    second = normalize_usage(
        gemini_cli._agent_result(json.dumps(value)).usage,
        session_id="session",
        resumed=True,
        previous=first,
    )
    assert second["measured"] == {
        "input": 20,
        "output": 5,
        "total": 25,
        "cache_read": 10,
        "cache_write": None,
        "reasoning": 5,
    }


@pytest.mark.parametrize("host", ["gemini", "grok"])
@pytest.mark.parametrize("detail", [None, True, -1, "10", 99999])
def test_invalid_native_optional_detail_preserves_other_counts(host, detail):
    if host == "gemini":
        value = json.loads(json.dumps(GEMINI))
        value["stats"]["models"]["helper"]["tokens"]["cached"] = detail
        result = gemini_cli._agent_result(json.dumps(value))
        assert result.usage["snapshot"] == {
            "input": 110,
            "output": 40,
            "total": 150,
            "cache_read": None,
            "cache_write": None,
            "reasoning": 10,
        }
    else:
        value = {
            "sessionId": "session",
            "stopReason": "end_turn",
            "text": '{"summary":"OK"}',
            "usage": {**GROK_1077[0], "reasoning_tokens": detail},
        }
        result = grok._agent_result(json.dumps(value))
        assert result.usage["snapshot"] == {
            "input": 15652,
            "output": 57,
            "total": 15709,
            "cache_read": 8832,
            "cache_write": 0,
            "reasoning": None,
        }
    assert result.ok


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
        (
            "gemini-cli",
            GEMINI,
            {
                "input": 110,
                "output": 40,
                "total": 150,
                "cache_read": 80,
                "cache_write": None,
                "reasoning": 10,
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
    monkeypatch.setattr(gemini_cli, "resolve_gemini_bin", lambda: "/fake/gemini")
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
            command, 1 if fault == "exit" else 0, json.dumps(value), ""
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


@pytest.mark.parametrize("host,envelope", [("claude-code", CLAUDE), ("gemini-cli", GEMINI)])
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
    if host == "claude-code":
        value["modelUsage"] = malformed
        result = claude_code._agent_result(json.dumps(value), native_version="2.1.277")
    else:
        value["stats"]["models"] = malformed
        result = gemini_cli._agent_result(json.dumps(value))
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
