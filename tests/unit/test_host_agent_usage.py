"""Native contracts checked 2026-11-06; no agent-authored usage is trusted.

Sources: Codex sdk/typescript/src/events.ts; Claude Agent SDK cost-tracking;
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


@pytest.mark.parametrize(
    "host,envelope,expected",
    [
        ("claude-code", CLAUDE, {"input": 110, "output": 40, "total": 150}),
        ("gemini-cli", GEMINI, {"input": 110, "output": 40, "total": 150}),
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
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1 if fault == "exit" else 0, json.dumps(value), ""
        ),
    )
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
        result = claude_code._agent_result(json.dumps(value))
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
        claude_code._agent_result(json.dumps({**CLAUDE, "subtype": "error_during_execution"})).usage
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
