"""The console can read safe host status; scoped agents cannot inspect other hosts."""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, get_ident
from uuid import uuid4

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.accounts import HostAccountError, HostAccounts
from stackos.host_mcp.result import HostMcpResult
from stackos.operations import build_operation_registry
from stackos.operations import system as system_operations

from .conftest import MCPClient
from .test_mcp_bridge_agent_path import (
    _initialize,
    _operation_data,
    _scoped_bridge,
    _send,
    _toolbox_call,
)
from .test_mcp_host_accounts import B, FakeNativeAccounts


@pytest.fixture
def inspected(monkeypatch: pytest.MonkeyPatch):
    threads: list[int] = []

    def inspect():
        threads.append(get_ident())
        result = service.normalize_result(
            HostMcpResult(
                host_key="codex",
                surface="cli",
                status="registered_current",
                message="Healthy api_key=synthetic-secret",
                ok=True,
                available=True,
                command=["stackos", "raw-config-fragment"],
                config_path="/private/host-config.json",
                target={"profile": "private-profile"},
                targets=[{"profile": "private-profile"}],
                selected=True,
                managed=True,
            ),
            next(d.policy for d in service._definitions() if d.policy.host_key == "codex"),
        )
        result = replace(
            result,
            agent_execution={
                **result.agent_execution,
                "auth_status_command": ["/private/auth-cli", "private-auth-command"],
                "config_path": "/private/execution-config",
                "credential": "private-execution-credential",
            },
        )
        return service.HostMcpAggregate(ok=True, results=[result])

    monkeypatch.setattr(service, "inspect_all", inspect)
    return threads


def test_host_status_has_one_read_only_local_admin_contract() -> None:
    spec = build_operation_registry().get("hostMcp.status")
    assert spec.mutating is False
    assert spec.grant_policy == "local-admin-read"
    assert all(spec.surfaces.is_enabled(surface) for surface in ("mcp", "rest", "cli"))
    assert spec.response_policy.default_mode == "raw"
    assert spec.response_policy.allowed_modes == ("raw",)
    schema = spec.output_model.model_json_schema()
    capability_fields = schema["$defs"]["HostMcpAgentExecutionOut"]["properties"]
    assert {
        "models",
        "reasoning_efforts",
        "custom_models",
        "provider_selection",
        "profile_selection",
        "requires_provider_model",
    } <= capability_fields.keys()
    assert not {"auth_status_command", "config_path", "credential"} & capability_fields.keys()


def test_console_subscription_snapshot_is_independent_and_allowlisted(mcp_client, monkeypatch):
    assert set(service.subscription_readers()) == {"codex", "claude-code", "grok", "antigravity"}
    client = mcp_client.test_client
    path = "/api/v1/operations/hostMcp.subscriptions/call"
    spec = build_operation_registry().get("hostMcp.subscriptions")
    assert spec.read_only and spec.surfaces.rest.browser_safe
    assert not spec.surfaces.mcp.enabled and not spec.surfaces.cli.enabled
    calls = []

    def healthy():
        calls.append("codex")
        return {
            "plan": "ChatGPT Pro",
            "account": "private-account",
            "meters": [{"scope": "Weekly", "remaining_percent": 0, "resets_at": None}],
        }

    def offline():
        raise RecursionError("private-provider-error")

    monkeypatch.setattr(
        service,
        "subscription_readers",
        lambda: {
            "codex": healthy,
            "claude-code": offline,
            "grok": lambda: None,
        },
    )
    assert client.post(path, json={"arguments": {}}).status_code == 401
    headers = {"Authorization": f"Bearer {client.app.state.ui_token}"}
    for arguments in ({"binary": "/tmp/user-choice"}, {"run_token": "test-run-token"}):
        assert client.post(path, json={"arguments": arguments}, headers=headers).is_error
    assert not calls
    response = client.post(path, json={"arguments": {"response_mode": "raw"}}, headers=headers)
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["checked_at"]
    assert snapshot["readings"] == {
        "codex": {
            "plan": "ChatGPT Pro",
            "meters": [{"scope": "Weekly", "remaining_percent": 0, "resets_at": None}],
        },
        "claude-code": None,
        "grok": None,
    }
    assert calls == ["codex"]
    assert snapshot["selections"] == {
        key: {"account_ref": None, "revision": 0} for key in ("codex", "claude-code", "grok")
    }


@pytest.mark.parametrize(
    "mode", ["success", "plan-unavailable", "native-error", "malformed", "timeout"]
)
def test_antigravity_native_command_reaches_console_independently(
    mcp_client, monkeypatch, tmp_path, mode
):
    from stackos.host_mcp import subscription_native as native

    payload = json.loads(
        (Path(__file__).parents[2] / "fixtures" / "antigravity_usage_success.json").read_text()
    )
    for group in payload["command"]["data"]["groups"]:
        group["buckets"][0]["reset_time"] = "2099-01-01T00:00:00Z"
    payload["command"]["data"]["groups"][0]["buckets"][0]["remaining_fraction"] = 0
    if mode == "native-error":
        payload["status"] = "ERROR"
    executable = tmp_path / "agy"
    marker = tmp_path / "argv.json"
    plan_marker = tmp_path / "plan-call.json"
    plan = "Antigravity Starter Quota" if mode == "success" else None
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, select, shlex, subprocess, sys, threading, time\n"
        "if sys.argv[1:4] == ['--mode', 'plan', '--settings']:\n"
        "    assert all(os.isatty(fd) for fd in (0, 1, 2))\n"
        "    settings = json.loads(sys.argv[4])\n"
        "    assert set(settings) == {'statusLine'}\n"
        "    assert settings['statusLine']['enabled'] is True\n"
        "    command = shlex.split(settings['statusLine']['command'])\n"
        f"    state = {{'plan_tier': {plan!r}, 'email': 'private-identity-canary'}}\n"
        "    observed = {'argv': sys.argv[1:4], 'cwd': os.getcwd(),\n"
        "        'stdin_ready': bool(select.select([0], [], [], 0)[0])}\n"
        f"    pathlib.Path({str(plan_marker)!r}).write_text(json.dumps(observed))\n"
        "    subprocess.run(command, input=json.dumps(state), text=True, check=True)\n"
        + ("    threading.Event().wait()\n" if mode == "success" else "")
        + "    raise SystemExit(0)\n"
        # Reproduce legitimate startup latency before the command can report argv.
        + ("time.sleep(0.5)\n" if mode == "timeout" else "")
        + f"pathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        + ("threading.Event().wait()\n" if mode == "timeout" else "")
        + f"print({('Sign in to continue' if mode == 'malformed' else json.dumps(payload))!r})\n"
    )
    executable.chmod(0o700)
    monkeypatch.setattr(service.antigravity, "resolve_antigravity_bin", lambda: str(executable))
    reader = service.subscription_readers()["antigravity"]
    capture = native.run_capture
    spawn = native.ManagedProcessControl.spawn
    processes = []
    capture_results = []

    def observed_spawn(control, *args, **kwargs):
        process = spawn(control, *args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(native.ManagedProcessControl, "spawn", observed_spawn)

    def bounded(*args, **kwargs):
        assert kwargs["timeout"] == 12
        # Keep the real reader budget: a shortened timer can kill the interpreter
        # before its argv marker and never establish the hanging-call precondition.
        result = capture(*args, **kwargs)
        capture_results.append(result)
        return result

    monkeypatch.setattr(native, "run_capture", bounded)
    sibling = {"plan": "ChatGPT Pro", "meters": []}
    monkeypatch.setattr(
        service, "subscription_readers", lambda: {"antigravity": reader, "codex": lambda: sibling}
    )
    policy = next(d.policy for d in service._definitions() if d.policy.host_key == "antigravity")
    connected = service.normalize_result(
        HostMcpResult(
            host_key="antigravity",
            surface="cli-json",
            status="registered_current",
            message="Connected",
            ok=True,
            available=True,
            selected=True,
            managed=True,
        ),
        policy,
    )
    monkeypatch.setattr(
        service, "inspect_all", lambda: service.HostMcpAggregate(ok=True, results=[connected])
    )
    client = mcp_client.test_client
    headers = {"Authorization": f"Bearer {client.app.state.ui_token}"}
    status = client.post(
        "/api/v1/operations/hostMcp.status/call", json={"arguments": {}}, headers=headers
    )
    assert status.status_code == 200
    assert status.json()["items"][0]["connection_state"] == "connected"
    response = client.post(
        "/api/v1/operations/hostMcp.subscriptions/call", json={"arguments": {}}, headers=headers
    )
    assert response.status_code == 200, response.text
    readings = response.json()["readings"]
    assert readings["codex"] == sibling
    if mode in {"success", "plan-unavailable"}:
        assert readings["antigravity"] == {
            "plan": plan,
            "meters": [
                {
                    "scope": "Gemini Models · Weekly",
                    "remaining_percent": 0,
                    "resets_at": "2099-01-01T00:00:00Z",
                },
                {
                    "scope": "Claude and GPT models · Weekly",
                    "remaining_percent": 100,
                    "resets_at": "2099-01-01T00:00:00Z",
                },
            ],
        }
    else:
        assert readings["antigravity"] is None
    argv = ["--print", "/usage", "--output-format", "json"]
    assert json.loads(marker.read_text()) == argv
    assert len(processes) == (2 if mode in {"success", "plan-unavailable"} else 1)
    assert processes[0].args == [str(executable), *argv]
    assert all(process.poll() is not None for process in processes)
    assert "private-identity-canary" not in response.text
    if mode in {"success", "plan-unavailable"}:
        observed = json.loads(plan_marker.read_text())
        assert observed["argv"] == ["--mode", "plan", "--settings"]
        assert observed["stdin_ready"] is False
        assert not Path(observed["cwd"]).exists()
    else:
        assert not plan_marker.exists()
    if mode == "timeout":
        assert capture_results == [None]
        # The real owner terminated and reaped our hanging child.
        assert processes[0].returncode < 0
        status = client.post(
            "/api/v1/operations/hostMcp.status/call", json={"arguments": {}}, headers=headers
        )
        assert status.status_code == 200
        assert status.json()["items"][0]["connection_state"] == "connected"


@pytest.fixture
def subscription_accounts(tmp_path, monkeypatch):
    natives = {key: FakeNativeAccounts() for key in ("codex", "claude-code", "grok")}
    store = HostAccounts(tmp_path, natives)
    for host_key in natives:
        account = store.adopt(host_key)
        store.select(host_key, account.account_ref, 0)
    monkeypatch.setattr(service, "host_accounts", lambda _data_dir: store)
    return store, natives


@pytest.mark.parametrize("host_key", ["codex", "claude-code", "grok"])
@pytest.mark.parametrize("delete_during_read", [False, True])
def test_subscription_read_isolates_a_deleted_selected_profile(
    mcp_client, monkeypatch, subscription_accounts, host_key, delete_during_read
):
    store, natives = subscription_accounts
    selected = {key: store.resolve(key) for key in natives}
    profile = selected[host_key].profile
    assert profile is not None and profile.directory is not None
    calls = []
    healthy = {
        key: {
            "plan": f"Selected {key} quota",
            "meters": [{"scope": "Weekly", "remaining_percent": 37, "resets_at": None}],
        }
        for key in natives
    }

    def reader(key):
        def read():
            calls.append(key)
            if key == host_key:
                assert delete_during_read
                profile.directory.rmdir()
            return healthy[key]

        return read

    # Put the damaged host first: its preflight must not prevent sibling reads.
    monkeypatch.setattr(
        service,
        "subscription_readers",
        lambda: {key: reader(key) for key in (host_key, *sorted(natives.keys() - {host_key}))},
    )
    if not delete_during_read:
        profile.directory.rmdir()
    client = mcp_client.test_client
    response = client.post(
        "/api/v1/operations/hostMcp.subscriptions/call",
        json={"arguments": {}},
        headers={"Authorization": f"Bearer {client.app.state.ui_token}"},
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["checked_at"]
    assert snapshot["readings"] == {**healthy, host_key: None}
    assert snapshot["selections"] == {
        key: selection.desired.model_dump()
        for key, selection in selected.items()
        if key != host_key
    }
    assert sorted(calls) == sorted(key for key in natives if delete_during_read or key != host_key)
    # Damage stays selected; the read must neither switch nor invent native defaults.
    assert store.inventory().hosts[host_key].desired == selected[host_key].desired
    with pytest.raises(HostAccountError, match="profile_missing"):
        store.resolve(host_key)
    assert all(len(native.activations) == 1 for native in natives.values())


@pytest.mark.parametrize("return_to_original", [False, True])
def test_subscription_read_drops_only_the_account_that_changed(
    mcp_client, monkeypatch, subscription_accounts, return_to_original
) -> None:
    store, natives = subscription_accounts
    first = store.resolve("codex").desired
    enrollment = str(uuid4())
    assert store.add("codex", enrollment).status == "pending"
    natives["codex"].identities[natives["codex"].started[-1].ref] = B
    second = store.complete("codex", enrollment)
    assert second.status == "enrolled" and second.account_ref is not None
    entered, release = Event(), Event()

    def interrupted_read():
        entered.set()
        assert release.wait(5), "held quota reader was not released"
        return {"plan": "Old account quota", "meters": []}

    monkeypatch.setattr(
        service,
        "subscription_readers",
        lambda: {
            "codex": interrupted_read,
            "claude-code": lambda: {"plan": "Unchanged sibling", "meters": []},
        },
    )
    client = mcp_client.test_client
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            client.post,
            "/api/v1/operations/hostMcp.subscriptions/call",
            json={"arguments": {}},
            headers={"Authorization": f"Bearer {client.app.state.ui_token}"},
        )
        try:
            assert entered.wait(5), "quota reader did not start"
            current = store.select("codex", second.account_ref, first.revision)
            if return_to_original:
                current = store.select("codex", first.account_ref, current.revision)
            assert current != first
        finally:
            release.set()
        result = pending.result(timeout=5)
    assert result.status_code == 200, result.text
    snapshot = result.json()
    assert snapshot["readings"] == {
        "codex": None,
        "claude-code": {"plan": "Unchanged sibling", "meters": []},
    }
    assert snapshot["selections"]["codex"] == current.model_dump()


@pytest.mark.parametrize("transition", ["A-to-B", "A-to-B-to-A", "delete-profile"])
def test_subscription_snapshot_fences_a_finished_reader_until_siblings_finish(
    mcp_client, monkeypatch, subscription_accounts, transition
):
    store, natives = subscription_accounts
    first = store.resolve("codex")
    enrollment = str(uuid4())
    assert store.add("codex", enrollment).status == "pending"
    natives["codex"].identities[natives["codex"].started[-1].ref] = B
    second = store.complete("codex", enrollment)
    assert second.status == "enrolled" and second.account_ref is not None
    fast_finished, sibling_entered, release = Event(), Event(), Event()

    def fast_reader():
        return {"plan": "Old A quota", "meters": []}

    original_to_thread = system_operations.to_thread

    async def observe_completion(function, *args, **kwargs):
        result = await original_to_thread(function, *args, **kwargs)
        if args and args[0] is fast_reader:
            # Observe the next loop turn after the caller receives the native
            # result. Keep real account resolution and thread execution intact.
            asyncio.get_running_loop().call_soon(fast_finished.set)
        return result

    monkeypatch.setattr(system_operations, "to_thread", observe_completion)

    def slow_sibling():
        sibling_entered.set()
        assert release.wait(5), "held sibling reader was not released"
        return {"plan": "Healthy held sibling", "meters": []}

    monkeypatch.setattr(
        service,
        "subscription_readers",
        lambda: {"codex": fast_reader, "claude-code": slow_sibling},
    )
    client = mcp_client.test_client
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            client.post,
            "/api/v1/operations/hostMcp.subscriptions/call",
            json={"arguments": {}},
            headers={"Authorization": f"Bearer {client.app.state.ui_token}"},
        )
        try:
            assert sibling_entered.wait(5), "sibling reader did not start"
            assert fast_finished.wait(5), "fast reader did not finish"
            assert not pending.done()
            if transition == "delete-profile":
                assert first.profile is not None and first.profile.directory is not None
                first.profile.directory.rmdir()
                expected_selection = None
            else:
                current = store.select("codex", second.account_ref, first.desired.revision)
                if transition == "A-to-B-to-A":
                    current = store.select("codex", first.desired.account_ref, current.revision)
                expected_selection = current.model_dump()
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["readings"] == {
        "codex": None,
        "claude-code": {"plan": "Healthy held sibling", "meters": []},
    }
    assert snapshot["selections"].get("codex") == expected_selection
    assert (
        snapshot["selections"]["claude-code"] == store.resolve("claude-code").desired.model_dump()
    )


def test_console_reads_only_normalized_host_status(mcp_client: MCPClient, inspected) -> None:
    client = mcp_client.test_client
    response = client.post(
        "/api/v1/operations/hostMcp.status/call",
        json={"arguments": {}},
        headers={"Authorization": f"Bearer {client.app.state.ui_token}"},
    )
    assert response.status_code == 200, response.text
    result = _operation_data(response.json())
    assert result["ok"] is True
    row = result["items"][0]
    assert row["host_key"] == "codex"
    assert row["display_name"] == "ChatGPT / Codex"
    assert row["connection_state"] == "connected"
    assert row["status_label"] == "Connected"
    assert row["selected"] is True
    assert row["managed"] is True
    assert not {"command", "config_path", "target", "targets"} & row.keys()
    capability = row["agent_execution"]
    expected = asdict(
        next(
            d.policy.agent_execution for d in service._definitions() if d.policy.host_key == "codex"
        )
    )
    expected.pop("auth_status_command")
    expected.update(credential_status="not_checked", model_access="not_checked")
    assert capability == json.loads(json.dumps(expected))
    assert not {"auth_status_command", "config_path", "credential"} & capability.keys()
    for secret in (
        "synthetic-secret",
        "raw-config-fragment",
        "private-profile",
        "/private/host-config",
        "/private/auth-cli",
        "private-auth-command",
        "/private/execution-config",
        "private-execution-credential",
    ):
        assert secret not in response.text
    assert len(inspected) == 1


@pytest.mark.parametrize(
    "arguments", [{"home": "/private/home"}, {"profile": "other"}, {"project_id": 1}]
)
def test_host_status_rejects_inspection_targets(
    mcp_client: MCPClient, inspected, arguments
) -> None:
    response = mcp_client.test_client.post(
        "/api/v1/operations/hostMcp.status/call",
        json={"arguments": arguments},
        headers=mcp_client._headers(),
    )
    assert response.status_code == 422, response.text
    assert inspected == []


def test_scoped_agent_cannot_inspect_host_status(
    mcp_client: MCPClient, seeded_project: dict, inspected
) -> None:
    project_id = seeded_project["data"]["id"]
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "path:host-status",
            "last_known_root": "/tmp/host-status",
        },
    )
    proxy, client = _scoped_bridge(
        mcp_client, cwd="/tmp/host-status", repo_fingerprint="path:host-status"
    )
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list")
    result = _toolbox_call(proxy, client, "hostMcp.status", {})
    assert result["result"]["isError"] is True, result
    assert "local-admin" in str(result), result
    assert inspected == []


def test_scoped_agent_cannot_read_subscriptions(
    mcp_client, seeded_project, monkeypatch, subscription_accounts
):
    store, _ = subscription_accounts
    before = store.inventory()
    calls = []

    def reader():
        calls.append("codex")
        return {"plan": "Unauthorized quota", "meters": []}

    monkeypatch.setattr(service, "subscription_readers", lambda: {"codex": reader})
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": seeded_project["data"]["id"],
            "repo_fingerprint": "path:subscriptions",
            "last_known_root": "/tmp/subscriptions",
        },
    )
    proxy, client = _scoped_bridge(
        mcp_client, cwd="/tmp/subscriptions", repo_fingerprint="path:subscriptions"
    )
    _initialize(proxy, client)
    result = _toolbox_call(proxy, client, "hostMcp.subscriptions", {})
    assert result["result"]["isError"] is True, result
    assert "hostMcp.subscriptions" in str(result), result
    assert not calls
    assert store.inventory() == before
