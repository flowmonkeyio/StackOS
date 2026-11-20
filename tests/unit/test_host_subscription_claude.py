import json
from subprocess import CompletedProcess

import pytest

from stackos.host_mcp import subscription_claude as claude


def native(monkeypatch, *, version="2.1.326 (Claude Code)", account=None, events=(), usage_ok=True):
    calls = []
    if account is None:
        account = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "subscriptionType": "max",
            "email": "private@example.invalid",
            "orgId": "private-org-id",
        }

    def capture(binary, argv, **kwargs):
        assert binary == "/test/claude"
        calls.append(argv)
        if argv == ["--version"]:
            return CompletedProcess(argv, 0, version)
        if argv == ["auth", "status", "--json"]:
            return CompletedProcess(argv, 0, json.dumps(account))
        return CompletedProcess(argv, 0 if usage_ok else 1, "\n".join(map(json.dumps, events)))

    monkeypatch.setattr(claude, "run_capture", capture)
    return calls


def test_native_usage_reports_account_and_scoped_meters(monkeypatch):
    limits = [
        {"kind": "session", "percent": 0, "resets_at": "2091-05-03T09:00:00-07:00"},
        {"kind": "weekly_all", "percent": 61.5, "resets_at": "2091-05-05T16:00:00.001Z"},
        {
            "kind": "weekly_scoped",
            "scope": {"model": {"display_name": "Fable"}},
            "percent": 100,
            "resets_at": "2091-05-05T16:00:00.002Z",
        },
        {
            "kind": "weekly_scoped",
            "scope": {"surface": {"display_name": "Claude Code"}},
            "percent": 25,
        },
        {"kind": "weekly_all", "percent": True},
        {"kind": "weekly_all", "percent": 103},
        {"kind": "weekly_all", "percent": 5, "resets_at": "2091-05-05"},
        {"kind": "weekly_all", "percent": 5, "resets_at": "2001-05-05T16:00:00Z"},
        {"kind": "weekly_scoped", "percent": 5, "scope": {"display_name": "private-org-id"}},
        {"kind": "weekly_scoped", "percent": 5, "scope": {"model": {"display_name": "a@b"}}},
    ]
    report = {"rate_limits": {"limits": limits, "extra_usage": {"monthly_limit": 1700}}}
    calls = native(
        monkeypatch,
        events=[
            {"type": "system", "usage_report": {"rate_limits": {"limits": []}}},
            {"type": "assistant", "usage_report": report},
        ],
    )
    assert claude.read_claude("/test/claude") == {
        "plan": "Claude Max",
        "meters": [
            {"scope": "5-hour", "remaining_percent": 100, "resets_at": "2091-05-03T16:00:00+00:00"},
            {
                "scope": "All models",
                "remaining_percent": 38.5,
                "resets_at": "2091-05-05T16:00:00.001000+00:00",
            },
            {
                "scope": "Fable",
                "remaining_percent": 0,
                "resets_at": "2091-05-05T16:00:00.002000+00:00",
            },
            {"scope": "Claude Code", "remaining_percent": 75, "resets_at": None},
        ],
    }
    assert calls == [
        ["--version"],
        ["auth", "status", "--json"],
        [
            "--safe-mode",
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--max-turns",
            "0",
            "--no-session-persistence",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "/usage",
        ],
    ]


@pytest.mark.parametrize("version", ["2.1.40 (Claude Code)", "unknown", "3.0.0 (Claude Code)"])
def test_unknown_command_surface_is_never_given_a_prompt(monkeypatch, version):
    calls = native(monkeypatch, version=version)
    assert claude.read_claude("/test/claude") is None
    assert calls == [["--version"]]


@pytest.mark.parametrize("version", ["2.1.41 (Claude Code)", "2.1.272 (Claude Code)"])
def test_plan_only_versions(monkeypatch, version):
    calls = native(monkeypatch, version=version)
    assert claude.read_claude("/test/claude") == {"plan": "Claude Max", "meters": []}
    assert len(calls) == 2


@pytest.mark.parametrize("usage_ok", [False, True])
def test_plan_survives_command_failure_or_missing_report(monkeypatch, usage_ok):
    native(monkeypatch, usage_ok=usage_ok, events=[{"type": "result", "result": "private-text"}])
    assert claude.read_claude("/test/claude") == {"plan": "Claude Max", "meters": []}


@pytest.mark.parametrize("logged_in,method", [(False, "none"), (True, "api_key")])
def test_only_subscriber_auth_starts_usage_read(monkeypatch, logged_in, method):
    calls = native(monkeypatch, account={"loggedIn": logged_in, "authMethod": method})
    assert claude.read_claude("/test/claude") is None
    assert len(calls) == 2
