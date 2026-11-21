"""Native quota reads preserve real windows without returning account identity."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import pytest

from stackos.host_mcp import service
from stackos.host_mcp import subscription_native as native
from stackos.host_mcp.subscription_codex import read_codex
from stackos.host_mcp.subscription_grok import read_grok

FUTURE = "2099-01-01T00:00:00+00:00"
FUTURE_EPOCH = int(datetime(2099, 1, 1, tzinfo=UTC).timestamp())


@pytest.fixture
def antigravity_usage():
    # Sanitized agy 1.5.13 SUCCESS captured 2026-11-21. No account identity,
    # tier or credits were reported. Keep the captured reset in the fixture;
    # move it forward only in tests so ordinary discovery stays date-independent.
    path = Path(__file__).parents[1] / "fixtures" / "antigravity_usage_success.json"
    payload = json.loads(path.read_text())
    for group in payload["command"]["data"]["groups"]:
        group["buckets"][0]["reset_time"] = FUTURE
    return payload


def antigravity_read(monkeypatch, payload, returncode=0):
    monkeypatch.setattr(service.antigravity, "resolve_antigravity_bin", lambda: "fake-agy")

    def capture(binary, args, **kwargs):
        assert binary == "fake-agy"
        assert args == ["--print", "/usage", "--output-format", "json"]
        assert 0 < kwargs["timeout"] <= 12
        assert 0 < kwargs["max_output_bytes"] <= 1_048_576
        return subprocess.CompletedProcess([binary], returncode, json.dumps(payload), "")

    monkeypatch.setattr(native, "run_capture", capture)
    return service.subscription_readers()["antigravity"]()


def test_antigravity_registered_reader_preserves_both_native_groups(monkeypatch, antigravity_usage):
    payload = antigravity_usage
    payload["email"] = payload["tier"] = payload["credits"] = "private-identity-canary"
    assert antigravity_read(monkeypatch, payload) == {
        "plan": None,
        "meters": [
            {"scope": "Gemini Models · Weekly", "remaining_percent": 100, "resets_at": FUTURE},
            {
                "scope": "Claude and GPT models · Weekly",
                "remaining_percent": 100,
                "resets_at": FUTURE,
            },
        ],
    }


@pytest.mark.parametrize("fraction", [0, 1, 0.375])
def test_antigravity_zero_full_and_partial_are_real_allowances(
    monkeypatch, antigravity_usage, fraction
):
    antigravity_usage["command"]["data"]["groups"][0]["buckets"][0]["remaining_fraction"] = fraction
    reading = antigravity_read(monkeypatch, antigravity_usage)
    assert reading["meters"][0]["remaining_percent"] == fraction * 100
    assert reading["meters"][1]["remaining_percent"] == 100


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("remaining_fraction", -0.1),
        ("remaining_fraction", 1.1),
        ("remaining_fraction", True),
        ("remaining_fraction", "0.5"),
        ("remaining_fraction", float("nan")),
        ("remaining_fraction", float("inf")),
        ("remaining_fraction", 10**400),
        ("reset_time", "bad"),
        ("reset_time", "2020-01-01T00:00:00Z"),
        ("reset_time", "2099-01-01"),
        ("reset_time", True),
        ("reset_time", {}),
        ("id", None),
        ("name", None),
        ("window", None),
    ],
)
def test_antigravity_invalid_bucket_does_not_fabricate_or_erase_sibling(
    monkeypatch, antigravity_usage, field, value
):
    antigravity_usage["command"]["data"]["groups"][0]["buckets"][0][field] = value
    reading = antigravity_read(monkeypatch, antigravity_usage)
    assert len(reading["meters"]) == 1
    assert reading["meters"][0]["scope"] == "Claude and GPT models · Weekly"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"status": "ERROR"},
        {"status": "SUCCESS", "command": {"name": "usage", "data": {"groups": {}}}},
        {"status": "SUCCESS", "command": {"name": "other", "data": {"groups": []}}},
    ],
)
def test_antigravity_malformed_or_native_error_is_unavailable(monkeypatch, payload):
    assert antigravity_read(monkeypatch, payload) is None


def test_antigravity_nonzero_success_and_auth_wait_are_unavailable(monkeypatch, antigravity_usage):
    assert antigravity_read(monkeypatch, antigravity_usage, returncode=1) is None
    antigravity_usage["error"] = "Native command failed"
    assert antigravity_read(monkeypatch, antigravity_usage) is None
    monkeypatch.setattr(native, "run_capture", lambda *a, **kw: None)
    assert service.subscription_readers()["antigravity"]() is None
    monkeypatch.setattr(
        native, "run_capture", lambda *a, **kw: subprocess.CompletedProcess([], 0, "Sign in", "")
    )
    assert service.subscription_readers()["antigravity"]() is None


def reply(value):
    return {"result": value}


@pytest.mark.parametrize(
    ("host_key", "argv", "methods", "responses", "plan", "scope", "remaining"),
    [
        (
            "codex",
            ["app-server", "--listen", "stdio://"],
            ["initialize", "initialized", "account/read", "account/rateLimits/read"],
            [
                {},
                {"account": {"planType": "pro", "email": "private-identity-canary"}},
                {"rateLimits": {"primary": {"usedPercent": 24, "windowDurationMins": 300}}},
            ],
            "ChatGPT Pro",
            "5-hour",
            76,
        ),
        (
            "grok",
            ["--no-auto-update", "agent", "stdio"],
            ["initialize", "_x.ai/billing", "_x.ai/auth/check_subscription"],
            [
                {},
                {"config": {"creditUsagePercent": 2}},
                {
                    "authenticated": True,
                    "meta": {"subscription_tier": "SuperGrok", "email": "private-identity-canary"},
                },
            ],
            "SuperGrok",
            "Current limit",
            98,
        ),
    ],
)
def test_registered_subscription_reader_uses_its_native_protocol(
    monkeypatch, host_key, argv, methods, responses, plan, scope, remaining
):
    monkeypatch.setattr(service.codex, "resolve_codex_bin", lambda: "fake-codex")
    monkeypatch.setattr(service.grok, "resolve_grok_bin", lambda home: "fake-grok")

    def capture(binary, args, stdin, timeout, limit, receive, *, env):
        assert binary == f"fake-{host_key}"
        assert args == argv
        assert env is None
        sent = [json.loads(stdin)["method"]]
        for request_id, result in enumerate(responses, 1):
            response = json.dumps({"id": request_id, "result": result}).encode() + b"\n"
            outgoing, done = receive(response)
            sent.extend(json.loads(line)["method"] for line in outgoing.splitlines())
            assert done == (request_id == len(responses))
        assert sent == methods
        return subprocess.CompletedProcess([binary, *args], 0, "", "")

    monkeypatch.setattr(native, "_capture", capture)
    reading = service.subscription_readers()[host_key]()
    assert reading == {
        "plan": plan,
        "meters": [{"scope": scope, "remaining_percent": remaining, "resets_at": None}],
    }


def test_codex_keeps_keyed_windows_without_default_duplicates_or_identity(monkeypatch):
    default = {
        "limitId": "codex",
        "planType": "pro",
        "primary": {"usedPercent": 24, "windowDurationMins": 10080, "resetsAt": FUTURE_EPOCH},
        "secondary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": None},
        "credits": {"balance": "private-identity-canary"},
    }

    def rpc(binary, args, calls):
        assert binary == "codex"
        assert args == ["app-server", "--listen", "stdio://"]
        assert [c[0] for c in calls] == ["initialize", "account/read", "account/rateLimits/read"]
        assert calls[1][1] == {"refreshToken": False}
        assert calls[2][1] is None
        return [
            reply({}),
            reply(
                {
                    "account": {
                        "type": "chatgpt",
                        "email": "private-identity-canary",
                        "planType": "pro",
                    }
                }
            ),
            reply(
                {
                    "rateLimits": default,
                    "rateLimitsByLimitId": {
                        "codex": default,
                        "private-identity-canary": {
                            "primary": {
                                "usedPercent": 100,
                                "windowDurationMins": 1440,
                                "resetsAt": FUTURE_EPOCH,
                            }
                        },
                    },
                }
            ),
        ]

    monkeypatch.setattr(native, "read_rpc", rpc)
    result = read_codex("codex")
    assert result == {
        "plan": "ChatGPT Pro",
        "meters": [
            {"scope": "Weekly", "remaining_percent": 76, "resets_at": FUTURE},
            {"scope": "5-hour", "remaining_percent": 100, "resets_at": None},
            {"scope": "Additional limit 2: Daily", "remaining_percent": 0, "resets_at": FUTURE},
        ],
    }
    assert "private-identity-canary" not in json.dumps(result)


@pytest.mark.parametrize("used", [-1, 101, True, "2", float("nan"), float("inf"), 10**400])
def test_invalid_quota_is_unknown_not_a_full_or_empty_allowance(monkeypatch, used):
    monkeypatch.setattr(
        native,
        "read_rpc",
        lambda *a: [
            reply({}),
            reply({}),
            reply(
                {
                    "rateLimits": {"primary": {"usedPercent": used}},
                }
            ),
        ],
    )
    assert read_codex("codex") is None


@pytest.mark.parametrize("reset", [0, "2020-01-01T00:00:00Z", "2099-01-01", "bad", True, {}])
def test_expired_or_invalid_reset_does_not_report_stale_allowance(monkeypatch, reset):
    monkeypatch.setattr(
        native,
        "read_rpc",
        lambda *a: [
            reply({}),
            reply({}),
            reply(
                {
                    "rateLimits": {"primary": {"usedPercent": 40, "resetsAt": reset}},
                }
            ),
        ],
    )
    assert read_codex("codex") is None


def test_grok_reads_native_billing_and_keeps_shared_pool_separate_from_tokens(monkeypatch):
    def rpc(binary, args, calls):
        assert binary == "grok"
        assert args == ["--no-auto-update", "agent", "stdio"]
        assert [c[0] for c in calls] == [
            "initialize",
            "_x.ai/billing",
            "_x.ai/auth/check_subscription",
        ]
        return [
            reply({}),
            reply(
                {
                    "subscription_tier": "SuperGrok Heavy",
                    "config": {
                        "creditUsagePercent": 2.0,
                        "isUnifiedBillingUser": True,
                        "currentPeriod": {
                            "type": "USAGE_PERIOD_TYPE_WEEKLY",
                            "end": "2098-12-31T16:00:00-08:00",
                        },
                        "prepaidBalance": {"val": 0},
                        "tokens": 900_000,
                    },
                }
            ),
            reply({"authenticated": True, "meta": {"email": "private-identity-canary"}}),
        ]

    monkeypatch.setattr(native, "read_rpc", rpc)
    assert read_grok("grok") == {
        "plan": "SuperGrok Heavy",
        "meters": [
            {"scope": "Shared weekly", "remaining_percent": 98.0, "resets_at": FUTURE},
        ],
    }


def test_grok_unsupported_billing_preserves_entitlement_only(monkeypatch):
    monkeypatch.setattr(
        native,
        "read_rpc",
        lambda *a: [
            reply({}),
            {"error": {"code": -32601}},
            reply(
                {
                    "authenticated": True,
                    "meta": {"subscription_tier": "SuperGrok Heavy"},
                }
            ),
        ],
    )
    assert read_grok("grok") == {"plan": "SuperGrok Heavy", "meters": []}


@pytest.mark.parametrize("reader", [read_codex, read_grok])
def test_failed_or_private_unrecognized_plan_is_unknown(monkeypatch, reader):
    monkeypatch.setattr(native, "read_rpc", lambda *a: [])
    assert reader("missing") is None
    monkeypatch.setattr(
        native,
        "read_rpc",
        lambda *a: [
            reply({}),
            reply(
                {
                    "subscription_tier": "private-identity-canary",
                    "account": {"planType": "private-identity-canary"},
                }
            ),
            reply({}),
        ],
    )
    assert reader("binary") is None


def test_explicit_non_subscription_auth_is_unknown(monkeypatch):
    monkeypatch.setattr(
        native,
        "read_rpc",
        lambda *a: [
            reply({}),
            reply(
                {
                    "account": {"type": "apiKey"},
                    "subscription_tier": "SuperGrok Heavy",
                }
            ),
            reply({"authenticated": False}),
        ],
    )
    assert read_codex("codex") is None
    assert read_grok("grok") is None


def test_rpc_waits_for_initialize_and_does_not_create_work(monkeypatch):
    calls = [
        ("initialize", {}),
        ("account/read", {"refreshToken": False}),
        ("account/rateLimits/read", None),
    ]

    def capture(binary, args, stdin, timeout, limit, receive, *, env):
        assert json.loads(stdin)["method"] == "initialize"
        assert env == {"NATIVE_PROFILE": "isolated"}
        assert receive(b"[" * 10_000 + b"]" * 10_000 + b"\n") == (b"", False)
        assert receive(b'{"jsonrpc":"2.0","id":1,') == (b"", False)
        outgoing, done = receive(b'"result":{}}\n')
        assert not done
        sent = [json.loads(line) for line in outgoing.splitlines()]
        assert [r["method"] for r in sent] == ["initialized", "account/read"]
        outgoing, done = receive(b'{"method":"notice"}\n{"id":2,"result":{}}\n')
        assert not done
        assert json.loads(outgoing) == {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "account/rateLimits/read",
        }
        assert receive(b'{"id":3,"result":{}}\n') == (b"", True)
        return subprocess.CompletedProcess([binary], -15, "", "")

    monkeypatch.setattr(native, "_capture", capture)
    assert (
        len(native.read_rpc("codex", ["app-server"], calls, env={"NATIVE_PROFILE": "isolated"}))
        == 3
    )


@pytest.mark.skipif(os.name != "posix", reason="Native host group retirement requires POSIX")
@pytest.mark.parametrize("profile_env", [None, {"STACKOS_SUBSCRIPTION_ENV_TEST": "isolated"}])
def test_capture_uses_neutral_cwd_cli_environment_and_preserves_exit_code(monkeypatch, profile_env):
    def cli_environment(binary):
        assert binary == sys.executable
        return {"STACKOS_SUBSCRIPTION_ENV_TEST": "ready"}

    monkeypatch.setattr(native, "subprocess_env_for_cli", cli_environment)
    result = native.run_capture(
        sys.executable,
        [
            "-c",
            "import os,sys;"
            "print(os.environ['STACKOS_SUBSCRIPTION_ENV_TEST']);print(os.getcwd());sys.exit(3)",
        ],
        env=profile_env,
    )
    assert result is not None and result.returncode == 3
    marker, cwd = result.stdout.splitlines()
    assert marker == ("isolated" if profile_env else "ready")
    assert "stackos-subscription-" in cwd
    assert not os.path.exists(cwd)


@pytest.mark.skipif(os.name != "posix", reason="Native host group retirement requires POSIX")
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_capture_bounds_either_output_stream(stream):
    assert (
        native.run_capture(
            sys.executable, ["-c", f"import sys;sys.{stream}.write('x'*10000)"], max_output_bytes=64
        )
        is None
    )


@pytest.mark.skipif(os.name != "posix", reason="Native host group retirement requires POSIX")
def test_capture_timeout_also_bounds_unconsumed_stdin():
    start = monotonic()
    assert (
        native.run_capture(
            sys.executable, ["-c", "import time;time.sleep(20)"], stdin="x" * 100_000, timeout=0.15
        )
        is None
    )
    assert monotonic() - start < 2


@pytest.mark.skipif(os.name != "posix", reason="Native host group retirement requires POSIX")
def test_capture_retires_descendant_on_timeout(tmp_path):
    marker = tmp_path / "child-stopped"
    child = (
        "import signal,time,pathlib,sys;"
        "signal.signal(signal.SIGTERM,lambda *a:"
        f"(pathlib.Path({str(marker)!r}).touch(),sys.exit(0)));"
        "time.sleep(20)"
    )
    parent = (
        "import subprocess,signal,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
        "signal.signal(signal.SIGTERM,lambda *a:(p.wait(timeout=1),sys.exit(0)));"
        "time.sleep(20)"
    )
    assert native.run_capture(sys.executable, ["-c", parent], timeout=0.5) is None
    assert marker.exists()
