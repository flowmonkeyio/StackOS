"""Outbound exchange through REST, its invocation owner and local consumer.

Fast cases substitute only Supabase HTTP; real Auth/Postgres/HTTP proof is
retained separately. Native host selection/execution belongs to its next ticket.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlmodel import Session

from stackos.api.deps import get_settings
from stackos.db.models import LocalRuntimeConnection
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.result import AgentRunResult
from stackos.host_mcp.service import ActiveInvocation, AgentExecutionScope
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.local_runtime.exchange import InvocationExchange, NativeLaunch
from stackos.local_runtime.worker import OutboundWorker
from stackos.repositories.base import ConflictError, ValidationError

ORIGIN = "http://127.0.0.1:18510"


@pytest.fixture
def connected(client, settings, monkeypatch):
    selected = settings.model_copy(
        update={
            "process_role": "server",
            "environment_id": "exchange-test",
            "server_url": ORIGIN,
            "supabase_url": "http://127.0.0.1:18521",
            "supabase_anon_key": SecretStr("synthetic-anon"),
        }
    )
    client.app.state.settings = selected
    client.app.dependency_overrides[get_settings] = lambda: selected
    client.base_url = httpx.URL(ORIGIN)
    client.headers.clear()
    client.headers["Origin"] = ORIGIN
    user_id = str(uuid4())
    original = httpx.AsyncClient

    def respond(request):
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": user_id, "email_confirmed_at": "2026-11-10"})
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh"})

    monkeypatch.setattr(
        "stackos.auth.httpx.AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": "password123"}
    )
    assert login.status_code == 200, login.text
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    project = client.post(
        "/api/v1/projects", json={"slug": "owned", "name": "Owned", "domain": "example.test"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]
    issued = call(client, "authorize", project_id=project_id)
    assert issued.status_code == 200, issued.text
    identity = {"project_id": project_id, "connection_id": issued.json()["connection_id"]}
    token = issued.json()["credential"]
    client.cookies.clear()
    client.headers["Authorization"] = f"Bearer {token}"
    return client, selected, identity


def call(client, name, **arguments):
    return client.post(
        f"/api/v1/operations/localRuntime.{name}/call", json={"arguments": arguments}
    )


def test_bound_computer_can_poll_without_general_authority(connected):
    client, _selected, identity = connected
    response = call(client, "poll", **identity)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "idle"
    assert client.get("/api/v1/projects").status_code == 403
    assert call(client, "authorize", project_id=identity["project_id"]).status_code == 403


def offer(connected, *, resume_thread_id=None):
    client, _settings, identity = connected
    with Session(client.app.state.engine) as session:
        connection = session.get(LocalRuntimeConnection, identity["connection_id"])
        session.expunge(connection)
    invocation = ActiveInvocation(
        invocation_id=uuid4().hex,
        scope=AgentExecutionScope(
            identity["project_id"], "task", "ticket", "delivery", 1, "delivery"
        ),
        host_key="codex",
        model="selected-model",
        started_at="2026-11-10T00:00:00Z",
    )
    launch = NativeLaunch(
        instructions="Assigned project instructions",
        prompt="Bounded fixture work",
        output_schema={"type": "object"},
        resume_thread_id=resume_thread_id,
    )
    exchange = client.app.state.operation_services["local_invocations"]
    handoff = exchange.offer(connection, invocation, launch)
    return exchange, connection, invocation, launch, handoff


def terminal(invocation, **overrides):
    return {
        "invocation_id": invocation.invocation_id,
        "attempt": invocation.scope.attempt,
        "state": "completed",
        "outcome": {
            "exit_code": 0,
            "thread_id": "exact-native-session",
            "result": {"status": "success"},
        },
    } | overrides


def local_store(connected, tmp_path):
    client, selected, identity = connected
    status = call(client, "status", **identity).json()
    root = tmp_path / "chosen"
    root.mkdir()
    info = root.stat()
    binding = LocalBinding(
        **identity,
        server_url=ORIGIN,
        environment_id=selected.environment_id,
        user_id=status["user_id"],
        credential=client.headers["Authorization"][7:],
        root=str(root),
        root_device=info.st_dev,
        root_inode=info.st_ino,
    )
    store = LocalConnectionStore(tmp_path / "runtime")
    with store.reserve() as stream:
        stream.write(
            json.dumps(
                binding.model_dump(mode="json")
                | {"credential": binding.credential.get_secret_value()}
            )
        )
    return store


def test_duplicate_poll_and_terminal_result_are_consumed_once(connected):
    client, _selected, identity = connected
    exchange, connection, invocation, launch, handoff = offer(connected)
    assert exchange.offer(connection, invocation, launch) is handoff
    first = call(client, "poll", **identity)
    assert first.status_code == 200, first.text
    assert first.json()["invocation_id"] == invocation.invocation_id
    assert first.json()["scope"]["attempt"] == 1
    assert first.json()["launch"]["resume_thread_id"] is None
    second = call(client, "poll", **identity).json()
    assert second["needs_attention"] is True and "launch" not in second
    report = terminal(invocation)
    accepted = call(client, "report", **identity, report=report)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["replayed"] is False
    assert handoff.ready.is_set() and handoff.outcome().ok
    repeated = call(client, "report", **identity, report=report)
    assert repeated.status_code == 200 and repeated.json()["replayed"] is True
    conflict = terminal(invocation, outcome={"exit_code": 1, "error": "different"})
    assert call(client, "report", **identity, report=conflict).status_code == 409
    assert handoff.outcome().exit_code == 0
    with pytest.raises(ConflictError):
        exchange.release(connection, invocation)
    invocation.finished = True  # Existing host owner retires only after persistence.
    exchange.release(connection, invocation)
    with pytest.raises(ValidationError):
        exchange.offer(connection, invocation, launch)
    assert call(client, "report", **identity, report=report).status_code == 409
    assert (
        call(client, "poll", **identity, invocation_id=invocation.invocation_id).json()["state"]
        == "unknown"
    )


def test_parallel_poll_and_report_cannot_duplicate_acceptance(connected):
    client, _selected, identity = connected
    _exchange, _connection, invocation, _launch, handoff = offer(connected)
    with ThreadPoolExecutor(max_workers=4) as executor:
        polls = list(executor.map(lambda _: call(client, "poll", **identity).json(), range(8)))
        assert sum(p["state"] == "launch" for p in polls) == 1
        reports = list(
            executor.map(
                lambda _: call(client, "report", **identity, report=terminal(invocation)).json(),
                range(8),
            )
        )
    assert sum(not r["replayed"] for r in reports) == 1
    assert handoff.outcome().thread_id == "exact-native-session"


def test_malformed_or_premature_report_cannot_complete_an_invocation(connected):
    client, _selected, identity = connected
    _exchange, _connection, invocation, _launch, handoff = offer(connected)
    assert call(client, "report", **identity, report=terminal(invocation)).status_code == 409
    for changes in (
        {"state": "completed", "outcome": None},
        {"state": "unknown"},
        {"outcome": {"exit_code": "0"}},
        {"command": "arbitrary"},
    ):
        assert (
            call(client, "report", **identity, report=terminal(invocation, **changes)).status_code
            == 422
        )
    assert not handoff.ready.is_set()


def test_wrong_invocation_attempt_session_and_second_computer_refuse(connected):
    client, _selected, identity = connected
    exchange, connection, invocation, launch, handoff = offer(
        connected, resume_thread_id="retained-session"
    )
    other = connection.model_copy(update={"id": str(uuid4())})
    with pytest.raises(ConflictError):
        exchange.offer(other, replace(invocation, invocation_id=uuid4().hex), launch)
    assert exchange.poll(other, invocation.invocation_id)["state"] == "unknown"
    assert call(client, "poll", **identity, invocation_id="unknown").json()["state"] == "unknown"
    assert not handoff.ready.is_set()
    call(client, "poll", **identity)
    for changes in ({"invocation_id": "unknown"}, {"attempt": 2}, {}):
        assert (
            call(client, "report", **identity, report=terminal(invocation, **changes)).status_code
            == 409
        )
    assert not handoff.ready.is_set()
    report = terminal(
        invocation, outcome={"exit_code": 0, "thread_id": "retained-session", "result": {}}
    )
    assert call(client, "report", **identity, report=report).status_code == 200


@pytest.mark.parametrize("fault", ["poll-response", "report-request", "report-response"])
def test_network_loss_never_relaunches_local_work(connected, tmp_path, fault):
    client, _selected, _identity = connected
    _exchange, _connection, _invocation, _launch, handoff = offer(connected)
    store = local_store(connected, tmp_path)
    launches = []

    def runner(command, root, binding):
        launches.append(command["invocation_id"])
        # Real fixed fixture process at the folder selected locally; the server
        # cannot supply argv, cwd, paths or a general tool/proxy request.
        process = subprocess.run(
            [
                sys.executable,
                "-E",
                "-c",
                "from pathlib import Path; Path('result.txt').write_text('once')",
            ],
            cwd=root,
            check=True,
        )
        return AgentRunResult(process.returncode, "exact-native-session", {"status": "success"})

    class DropOnce:
        dropped = False

        def post(self, url, **kwargs):
            match = (fault == "poll-response" and ".poll/" in url) or (
                fault.startswith("report-") and ".report/" in url
            )
            if match and not self.dropped:
                self.dropped = True
                if fault != "report-request":
                    response = client.post(url, **kwargs)
                    assert response.status_code == 200, response.text
                raise httpx.ReadError("fixture connection lost")
            return client.post(url, **kwargs)

    worker = OutboundWorker(DropOnce(), store, runner)
    with pytest.raises(httpx.ReadError):
        worker.exchange_once()
    if fault == "poll-response":
        for consumer in (worker, OutboundWorker(client, store, runner)):
            with pytest.raises(ValueError, match="reconciliation"):
                consumer.exchange_once()
        assert not launches and not handoff.ready.is_set()
    else:
        result = worker.exchange_once()
        assert result["state"] == "completed"
        assert result["replayed"] is (fault == "report-response")
        assert worker.exchange_once()["state"] == "completed"
        assert launches == [handoff.call.invocation_id]
        assert Path(store.load().root, "result.txt").read_text() == "once"
        assert handoff.outcome().ok


def test_unknown_runner_outcome_blocks_new_admission_without_killing(connected, tmp_path):
    client, _settings, identity = connected
    exchange, connection, invocation, launch, handoff = offer(connected)
    store = local_store(connected, tmp_path)
    launches = []

    def uncertain(_command, _root, _binding):
        launches.append(1)
        raise RuntimeError("synthetic native outcome lost")

    worker = OutboundWorker(client, store, uncertain)
    with pytest.raises(RuntimeError):
        worker.exchange_once()
    assert handoff.ready.is_set() and not handoff.outcome().ok
    assert not invocation.process_control.cancelled.is_set()
    assert call(client, "poll", **identity).json()["needs_attention"]
    with pytest.raises(ValueError, match="reconciliation"):
        worker.exchange_once()
    with pytest.raises(ConflictError):
        exchange.offer(connection, replace(invocation, invocation_id=uuid4().hex), launch)
    with pytest.raises(ConflictError):
        exchange.release(connection, invocation)
    assert call(client, "report", **identity, report=terminal(invocation)).status_code == 409
    assert launches == [1]


@pytest.mark.parametrize("change", ["project", "connection", "environment", "owner", "revoked"])
def test_bound_identity_is_rechecked_before_exchange(connected, change):
    from stackos.db.models import Project

    client, _selected, identity = connected
    _exchange, connection, invocation, _launch, handoff = offer(connected)
    args = dict(identity)
    with Session(client.app.state.engine) as session:
        if change == "project":
            args["project_id"] += 100
        elif change == "connection":
            other = connection.model_copy(
                update={"id": str(uuid4()), "credential_digest": uuid4().hex}
            )
            session.add(other)
            session.commit()
            args["connection_id"] = other.id
        elif change in {"environment", "owner"}:
            project = session.get(Project, identity["project_id"])
            setattr(
                project,
                "environment_id" if change == "environment" else "owner_user_id",
                str(uuid4()),
            )
            session.add(project)
            session.commit()
        else:
            assert call(client, "revoke", **identity).status_code == 200
    expected = 404 if change in {"project", "connection"} else 401
    assert call(client, "poll", **args).status_code == expected
    assert call(client, "report", **args, report=terminal(invocation)).status_code == expected
    assert handoff.state == "pending" and not handoff.ready.is_set()


def test_no_arbitrary_rpc_or_browser_exchange_and_no_replay_credentials(connected):
    from stackos.operations.registry import build_operation_registry

    client, _settings, identity = connected
    for field, value in (
        ("command", "echo unsafe"),
        ("path", "/tmp/other"),
        ("url", "http://foreign.test"),
        ("idempotency_key", "replay"),
    ):
        assert call(client, "poll", **identity, **{field: value}).status_code in {403, 422}
    assert call(client, "poll", **identity, response_mode="ack").status_code == 422
    client.headers.pop("Authorization")
    login = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": "password123"}
    )
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert call(client, "poll", **identity).status_code == 403
    registry = build_operation_registry()
    for name in ("localRuntime.poll", "localRuntime.report"):
        spec = registry.get(name)
        assert spec.surfaces.rest.enabled and not spec.surfaces.rest.browser_safe
        assert not spec.surfaces.mcp.enabled and not spec.surfaces.cli.enabled
        assert spec.idempotency_policy == "handler"
        assert spec.response_policy.allowed_modes == ("raw",)
        spec.input_model.model_validate(spec.examples[0].arguments)


def test_fresh_transport_never_claims_restart_success(connected):
    _exchange, connection, invocation, _launch, _handoff = offer(connected)
    assert InvocationExchange().poll(connection, invocation.invocation_id) == {
        "state": "unknown",
        "needs_attention": True,
    }


@pytest.mark.parametrize("transition", ["cancelled", "retired", "finished"])
def test_late_result_does_not_revive_fenced_invocation(connected, transition):
    client, _selected, identity = connected
    _exchange, _connection, invocation, _launch, handoff = offer(connected)
    assert call(client, "poll", **identity).json()["state"] == "launch"
    if transition == "finished":
        invocation.finished = True
    else:
        getattr(invocation, transition).set()
    assert call(client, "report", **identity, report=terminal(invocation)).status_code == 409
    assert call(client, "poll", **identity).json()["state"] == "unknown"
    assert not handoff.ready.is_set()
    assert not invocation.process_control.cancelled.is_set()


def test_changed_local_folder_cannot_execute_and_valid_next_attempt_can(connected, tmp_path):
    client, _selected, _identity = connected
    exchange, connection, invocation, launch, handoff = offer(connected)
    store = local_store(connected, tmp_path)
    launches = []

    def runner(command, root, binding):
        assert command["host_key"] == invocation.host_key
        assert command["model"] == invocation.model
        launches.append(root)
        return AgentRunResult(0, "exact-native-session", {"status": "success"})

    worker = OutboundWorker(client, store, runner)
    assert worker.exchange_once()["state"] == "completed"
    invocation.finished = True
    exchange.release(connection, invocation)
    next_call = replace(
        invocation,
        invocation_id=uuid4().hex,
        scope=replace(invocation.scope, attempt=2),
        finished=False,
        process_control=ManagedProcessControl(),
    )
    next_handoff = exchange.offer(connection, next_call, launch)
    assert worker.exchange_once()["state"] == "completed"
    assert len(launches) == 2 and handoff.outcome().ok and next_handoff.outcome().ok
    next_call.finished = True
    exchange.release(connection, next_call)
    third = replace(
        next_call,
        invocation_id=uuid4().hex,
        scope=replace(next_call.scope, attempt=3),
        finished=False,
        process_control=ManagedProcessControl(),
    )
    uncertain = exchange.offer(connection, third, launch)
    root = Path(store.load().root)
    root.rename(tmp_path / "original")
    root.mkdir()
    with pytest.raises(ValueError, match="folder binding changed"):
        worker.exchange_once()
    assert len(launches) == 2 and uncertain.state == "unknown"
