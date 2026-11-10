"""The computer consumer uses the existing native host, never a server runner."""

import json
from dataclasses import asdict

import pytest

from stackos.host_mcp import service as hosts
from stackos.host_mcp.result import AgentRunResult
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.local_runtime.exchange import InvocationCommand, NativeLaunch
from stackos.local_runtime.native import NativeAgentRunner


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = tmp_path / "chosen"
    root.mkdir()
    stat = root.stat()
    binding = LocalBinding(
        server_url="http://127.0.0.1:19810",
        environment_id="native-test",
        user_id="owner",
        project_id=1,
        connection_id="computer",
        credential="synthetic",
        root=str(root),
        root_device=stat.st_dev,
        root_inode=stat.st_ino,
    )
    store = LocalConnectionStore(tmp_path / "state")
    with store.reserve() as stream:
        stream.write(json.dumps(binding.model_dump(mode="json") | {"credential": "synthetic"}))
    observed = []
    monkeypatch.setattr(
        "stackos.local_runtime.native.check_codex_source",
        lambda *args, **kwargs: observed.append(kwargs) or {"candidate": {}},
    )
    return root, store, observed


def command(**launch):
    return InvocationCommand(
        invocation_id="exact-invocation",
        state="launch",
        needs_attention=False,
        scope=hosts.AgentExecutionScope(1, "task", "ticket", "delivery", 2, "delivery"),
        host_key="codex",
        model="selected-model",
        launch=NativeLaunch(
            instructions="current instructions",
            prompt="current prompt",
            output_schema={"type": "object"},
            **launch,
        ),
    ).model_dump()


def test_native_runner_forwards_selection_scope_and_exact_resume(local, monkeypatch):
    root, store, observed = local
    reference = {"path": "/local/routes.toml", "sha256": "a" * 64}
    calls = []
    expected = AgentRunResult(
        0, thread_id="exact-session", result={"status": "success"}, usage={"status": "measured"}
    )
    monkeypatch.setattr(
        hosts, "run_agent", lambda *args, **kwargs: calls.append((args, kwargs)) or expected
    )
    run = NativeAgentRunner(store, mcp_config_ref=reference)
    assert (
        run(command(resume_thread_id="exact-session", provider="selected-provider"), root)
        == expected
    )
    args, kwargs = calls[0]
    assert args == ("codex", root, "current instructions", "current prompt", {"type": "object"})
    assert kwargs["model"] == "selected-model" and kwargs["provider"] == "selected-provider"
    assert kwargs["resume_thread_id"] == "exact-session"
    assert kwargs["mcp_config_ref"] == reference
    assert asdict(kwargs["execution_scope"]) == command()["scope"]
    assert observed[0]["mcp_config_ref"] == reference


@pytest.mark.parametrize("change", ["folder", "project", "binding", "host", "source"])
def test_invalid_local_identity_never_enters_host(local, monkeypatch, change):
    root, store, _ = local
    request = command()
    if change == "folder":
        root = root.parent
    elif change == "project":
        request["scope"]["project_id"] = 2
    elif change == "binding":
        store.path.unlink()
    elif change == "host":
        request["host_key"] = "unsupported"
    else:

        def refuse(*args, **kwargs):
            raise ValueError("source drift")

        monkeypatch.setattr("stackos.local_runtime.native.check_codex_source", refuse)
    monkeypatch.setattr(hosts, "run_agent", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(ValueError):
        NativeAgentRunner(store)(request, root)


def test_server_cannot_spawn_a_native_host(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "stackos.config.get_settings", lambda: SimpleNamespace(process_role="server")
    )
    monkeypatch.setattr(
        hosts, "_definition_by_key", lambda *_: pytest.fail("server must not discover/spawn hosts")
    )
    result = hosts.run_agent("codex", tmp_path, "instructions", "prompt", {})
    assert not result.ok and "connected computer" in result.error


def test_returned_candidate_is_checked_locally_and_failure_keeps_native_usage(local, monkeypatch):
    from stackos.repositories.base import ValidationError

    root, store, observed = local
    candidate = {"base_commit_sha": "a" * 40, "commit_sha": "b" * 40}
    expected = AgentRunResult(
        0,
        thread_id="delivery-session",
        result={"status": "success", "outputs": json.dumps({"commit_sha": "c" * 40})},
        usage={"status": "measured", "snapshot": {"total": 123}},
    )
    monkeypatch.setattr(hosts, "run_agent", lambda *a, **kw: expected)
    checks = []

    def refuse(*args):
        checks.append(args)
        raise ValidationError("dirty or wrong candidate")

    monkeypatch.setattr("stackos.local_runtime.native._candidate_commit", refuse)
    result = NativeAgentRunner(store)(command(candidate=candidate), root)
    assert observed[0]["candidate"] == candidate
    assert checks == [(str(root), "c" * 40, "a" * 40)]
    assert not result.ok and result.result is None
    assert result.thread_id == expected.thread_id and result.usage == expected.usage


def test_runtime_consumer_uses_local_routes_and_stops_on_uncertainty(local, monkeypatch):
    from threading import Event
    from types import SimpleNamespace

    from stackos.local_runtime.service import consume_invocations

    _root, store, _ = local
    selected = SimpleNamespace(
        state_dir=store.path.parent,
        environment_id="native-test",
        server_url="http://127.0.0.1:19810",
        native_mcp_config_ref={"local": "reference"},
    )
    calls = []

    class Consumer:
        def __init__(self, client, chosen_store, runner):
            assert chosen_store.path == store.path
            assert runner.mcp_config_ref == selected.native_mcp_config_ref
            assert runner.launch_allowed()

        def exchange_once(self):
            calls.append("poll")
            raise ValueError("uncertain; attention required")

    monkeypatch.setattr("stackos.local_runtime.service.OutboundWorker", Consumer)
    consume_invocations(selected, Event())
    assert calls == ["poll"]  # No second poll, launch, signal or retry.


@pytest.mark.parametrize("returned_id", [None, "", "replacement-session"])
def test_unknown_or_replacement_session_cannot_be_a_success(local, monkeypatch, returned_id):
    root, store, _ = local
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs["resume_thread_id"])
        return AgentRunResult(0, thread_id=returned_id, result={"status": "success"})

    monkeypatch.setattr(hosts, "run_agent", run)
    result = NativeAgentRunner(store)(command(resume_thread_id="exact-session"), root)
    assert not result.ok and result.result is None
    assert calls == ["exact-session"]


def test_returned_candidate_is_canonicalized_without_changing_other_outputs(local, monkeypatch):
    root, store, _ = local
    monkeypatch.setattr(
        hosts,
        "run_agent",
        lambda *a, **kw: AgentRunResult(
            0,
            thread_id="session",
            result={
                "status": "success",
                "outputs": json.dumps({"commit_sha": "abcdef0", "domain": 3}),
            },
        ),
    )
    monkeypatch.setattr("stackos.local_runtime.native._candidate_commit", lambda *a: "a" * 40)
    result = NativeAgentRunner(store)(command(), root)
    assert result.ok and json.loads(result.result["outputs"]) == {
        "commit_sha": "a" * 40,
        "domain": 3,
    }
