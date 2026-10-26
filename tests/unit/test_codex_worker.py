"""Fake-only command custody: no process, socket, provider or database birth."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy
import sqlmodel

import stackos.actions.codex_worker as codex
from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.repository import execution
from stackos.actions.repository.background import BackgroundActionTasks, CommandBinding


@pytest.fixture(autouse=True)
def no_native_or_database(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("native/process/database factory forbidden in source proof")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    monkeypatch.setattr(sqlmodel, "Session", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)
    monkeypatch.setattr(codex.CodexPipeIO, "digest", forbidden)


def binding(**changes):
    return replace(
        CommandBinding(1, 2, 3, 4, 5, 6, "ticket:7", "lead", 1, 2, "scope", "a" * 40), **changes
    )


class Clock:
    value = 10.0

    def __call__(self):
        return self.value


async def nothing(_slot):
    pass


async def close_known(slot, deadline):
    assert deadline == slot.deadline
    slot.cli_exit = -15
    slot.reaped = slot.fds_closed = True


def request():
    return ActionConnectorRequest(
        project_id=1,
        plugin_slug="engineering",
        action_key="codex.recorded-turn",
        action_ref="engineering.codex.recorded-turn",
        provider_key=None,
        operation="codex.recorded-turn",
        config_json={},
        action_call_id=2,
        input_json={
            "prompt": "recorded fake",
            "canonical_item_id": "ticket:7",
            "owner_ref": "lead",
            "epoch": 1,
            "revision": 2,
            "scope_digest": "scope",
            "source_revision": "a" * 40,
            "attempt_run_id": 3,
        },
    )


def profile():
    return codex.CodexProfile(
        Path("/approved/source"),
        Path("/private/attempt/schema.json"),
        "a" * 40,
        "review:profile",
        ("b" * 64, "1", "c" * 64, "d" * 64),
        ("b" * 64, "1", "c" * 64, "d" * 64),
        Path("/approved/bin/codex"),
        Path("/private/codex/config.toml"),
        Path("/private/codex/reviewed-capabilities.json"),
        (
            ("PATH", "/usr/bin:/bin"),
            ("HOME", "/private/home"),
            ("TMPDIR", "/private/tmp"),
            ("CODEX_HOME", "/private/codex"),
        ),
    )


def test_gate0_exact_recorded_profile_and_independent_usage():
    # Sanitized historical data only; production usage must come from actual events.
    data = {
        "result": {"status": "completed"},
        "cli": {"exit_code": -15},
        "usage": {
            "input_tokens": 19758,
            "output_tokens": 1767,
            "total_tokens": 21525,
            "allowance_tokens": 16000,
            "hard_cap": False,
        },
        "termination": {"descendants": "unknown", "remote": "unknown"},
    }
    assert data["result"]["status"] == "completed"
    assert data["cli"]["exit_code"] == -15
    assert data["usage"]["input_tokens"] + data["usage"]["output_tokens"] == 21525
    assert data["usage"]["total_tokens"] > data["usage"]["allowance_tokens"]
    assert data["usage"]["hard_cap"] is False
    assert data["termination"] == {"descendants": "unknown", "remote": "unknown"}
    args = profile().argv()
    assert args == (
        "/approved/bin/codex",
        *codex.FIXED_ARGS,
        "--cd",
        "/approved/source",
        "--output-schema",
        "/private/attempt/schema.json",
        "-",
    )


@pytest.mark.parametrize("change", ["missing", "binary", "version", "config", "capability"])
def test_missing_or_changed_pins_deny_before_slot_or_factory(change):
    owner = BackgroundActionTasks()
    pins = profile()
    if change != "missing":
        index = {"binary": 0, "version": 1, "config": 2, "capability": 3}[change]
        values = list(pins.observed)
        values[index] = "unreviewed"
        pins = replace(pins, observed=tuple(values))
    connector = codex.CodexWorkerConnector(
        owner, profile=None if change == "missing" else pins, transport=object()
    )
    with pytest.raises(ActionConnectorError) as error:
        asyncio.run(connector.execute(request()))
    assert error.value.output_json["status"] == "unsupported"
    assert error.value.output_json["attempts_consumed"] == 0
    assert owner._commands == {}


def test_sealed_deadline_precedes_preparation_and_expiry_denies_acquisition():
    async def scenario():
        clock, owner = Clock(), BackgroundActionTasks()
        slot = owner.command_slot(binding(), clock=clock)

        async def prepare(original):
            assert original.started == 10 and original.deadline == 100
            assert original.work_deadline == 88
            clock.value = 89

        def forbidden(_slot):
            raise AssertionError("expired preparation cannot acquire")

        result = await owner.command(
            slot,
            prepare=prepare,
            authorize=lambda: None,
            acquire=forbidden,
            work=nothing,
            cleanup=close_known,
            clock=clock,
        )
        assert slot.resources == {} and result["attempts_consumed"] == 0
        assert result["failure"] == "TimeoutError"
        assert owner.command_slot(binding(), clock=clock) is slot
        assert slot.deadline == 100

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reason", ["stale-owner", "stopped", "expired-budget", "approval", "grant"]
)
def test_fresh_admission_denial_after_async_prepare_has_no_acquisition(reason):
    async def scenario():
        owner, clock = BackgroundActionTasks(), Clock()
        slot = owner.command_slot(binding(), clock=clock)
        prepared = []

        async def prepare(_slot):
            prepared.append(True)
            await asyncio.sleep(0)

        def deny():
            assert prepared
            raise ValueError(reason)

        result = await owner.command(
            slot,
            prepare=prepare,
            authorize=deny,
            acquire=lambda _slot: pytest.fail("denied acquisition"),
            work=nothing,
            cleanup=close_known,
            clock=clock,
        )
        assert result["attempts_consumed"] == 0 and not slot.resources

    asyncio.run(scenario())


def test_partial_acquisition_objects_survive_task_failure_and_ack_is_actual():
    async def scenario():
        owner, clock = BackgroundActionTasks(), Clock()
        slot = owner.command_slot(binding(), clock=clock)
        process, pipe = object(), object()

        def partial(original):
            original.adopt("process", process)
            original.adopt("stdin", pipe)
            raise RuntimeError("partial setup")

        async def unknown(_slot, deadline):
            assert deadline == 100

        result = await owner.command(
            slot,
            prepare=nothing,
            authorize=lambda: None,
            acquire=partial,
            work=nothing,
            cleanup=unknown,
            clock=clock,
        )
        assert slot.resources == {"process": process, "stdin": pipe}
        assert slot.tasks[0].done()
        assert result["cleanup"]["status"] == "unknown"
        assert result["custody"]["acknowledged"] is True
        assert result["custody"]["daemon_incarnation"] == owner._incarnation
        assert result["clock"]["deadline"] == 100
        assert owner._commands[(2, 3)] is slot
        with pytest.raises(RuntimeError, match="holds capacity"):
            owner.command_slot(binding(action_call_id=9, attempt_run_id=8), clock=clock)

    asyncio.run(scenario())


def test_requester_loss_does_not_cancel_retained_work_or_drop_handles():
    async def scenario():
        owner, clock = BackgroundActionTasks(), Clock()
        slot = owner.command_slot(binding(), clock=clock)
        started, finish = asyncio.Event(), asyncio.Event()
        handle = object()

        async def work(original):
            started.set()
            await finish.wait()
            original.observe(1, {"result": {"status": "completed", "turn_id": "original"}})

        requester = asyncio.create_task(
            owner.command(
                slot,
                prepare=nothing,
                authorize=lambda: None,
                acquire=lambda original: original.adopt("process", handle),
                work=work,
                cleanup=close_known,
                clock=clock,
            )
        )
        await started.wait()
        requester.cancel()
        with pytest.raises(asyncio.CancelledError):
            await requester
        assert not slot.tasks[0].done() and slot.resources["process"] is handle
        assert owner.receipt(slot, clock=clock)["custody"]["acknowledged"] is True
        finish.set()
        result = await slot.tasks[0]
        assert result["result"]["turn_id"] == "original"
        assert result["cleanup"]["status"] == "confirmed"
        assert result["quiescent"] is False and result["capacity_reusable"] is False

    asyncio.run(scenario())


def test_completed_result_survives_cleanup_deadline_and_late_duplicate_events():
    async def scenario():
        owner, clock = BackgroundActionTasks(), Clock()
        slot = owner.command_slot(binding(), clock=clock)

        async def work(original):
            original.observe(
                2,
                {
                    "result": {"status": "completed", "turn_id": "useful"},
                    "usage": {"total_tokens": 21525},
                },
            )
            original.observe(1, {"result": {"status": "completed", "turn_id": "duplicate"}})
            original.observe(3, {"usage": {"total_tokens": 1}})
            clock.value = 100

        async def stuck(_slot, deadline):
            assert deadline == 100
            await asyncio.Event().wait()

        result = await owner.command(
            slot,
            prepare=nothing,
            authorize=lambda: None,
            acquire=lambda original: original.adopt("pipe", object()),
            work=work,
            cleanup=stuck,
            clock=clock,
        )
        assert result["result"] == {"status": "completed", "turn_id": "useful"}
        assert result["usage"]["total_tokens"] == 21525
        assert result["cleanup"]["status"] == "unknown"
        assert result["custody"]["acknowledged"] is True
        assert result["retry_safe"] is False
        shutdown = await owner.shutdown(clock=clock)
        assert shutdown["clean"] is False
        assert shutdown["commands"][0]["custody"]["acknowledged"] is False
        assert owner._commands[(2, 3)] is slot and slot.resources

    asyncio.run(scenario())


def test_unsupported_permission_stops_and_preserves_unknown_termination():
    async def scenario():
        owner = BackgroundActionTasks()
        slot = owner.command_slot(binding())

        async def permission(original):
            original.observe(1, {"type": "permission.required"})

        result = await owner.command(
            slot,
            prepare=nothing,
            authorize=lambda: None,
            acquire=lambda original: original.adopt("process", object()),
            work=permission,
            cleanup=close_known,
        )
        assert result["failure"] == "unsupported-permission"
        assert result["next_action"] == "needs-operator"
        assert result["termination"] == {"descendants": "unknown", "remote": "unknown"}

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["done", "failed", "cancelled"])
def test_generic_background_semantics_and_owned_slot_survive_callbacks(outcome):
    async def scenario():
        owner = BackgroundActionTasks()
        slot = owner.command_slot(binding())
        handle = object()
        slot.adopt("process", handle)

        async def worker(report):
            report({"phase": "observing"})
            if outcome == "failed":
                raise RuntimeError("expected fake worker error")
            if outcome == "cancelled":
                raise asyncio.CancelledError

        owner.start(2, worker)
        tasks = list(owner._tasks)
        await asyncio.wait(tasks)
        await asyncio.sleep(0)
        assert not owner._tasks and owner.progress(2) is None
        assert owner._commands[(2, 3)].resources["process"] is handle

    asyncio.run(scenario())


def test_background_worker_uses_new_session_and_canonical_services(monkeypatch):
    sessions, calls = [], []

    class Session:
        def __init__(self, bind):
            self.info = {}
            self.bind = bind
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    class Repository:
        def __init__(self, session, **_kwargs):
            self.session = session

        async def _execute_prepared(self, _prepared, **_kwargs):
            calls.append(self.session.info["operation_services"])

    monkeypatch.setattr(execution, "Session", Session)
    services = {"background_action_tasks": BackgroundActionTasks(), "action_connectors": object()}
    asyncio.run(
        execution._execute_background_action(
            repository_type=Repository,
            bind=object(),
            connectors=object(),
            asset_dir=None,
            prepared=SimpleNamespace(),
            action_call_id=2,
            progress_callback=lambda _: None,
            operation_services=services,
        )
    )
    assert len(sessions) == 1 and calls == [services]


def test_fresh_admission_reuses_current_canonical_helpers_and_owner_cas(monkeypatch):
    from stackos.db.models import ActionCallStatus, RunKind, RunStatus
    from stackos.mcp import permissions
    from stackos.operations.actions import execution as operations
    from stackos.repositories import runs

    seen = []
    original = binding()
    inp = request().input_json
    call = SimpleNamespace(
        project_id=1,
        run_id=4,
        run_plan_id=5,
        run_plan_step_id=6,
        status=ActionCallStatus.RUNNING,
        request_json=inp,
    )
    controller = SimpleNamespace(project_id=1)
    attempt = SimpleNamespace(
        project_id=1,
        parent_run_id=4,
        status=RunStatus.RUNNING,
        kind=RunKind.SKILL_RUN,
        metadata_json={
            "ownership_binding": {
                "canonical_item_id": "ticket:7",
                "owner_ref": "lead",
                "epoch": 1,
                "scope_digest": "scope",
            }
        },
    )

    class Session:
        def get(self, _model, identity, **kwargs):
            assert kwargs == {"populate_existing": True}
            seen.append(("fresh", identity))
            return {
                2: call,
                4: controller,
                3: attempt,
                5: SimpleNamespace(
                    grant_snapshot_json={
                        "action_contracts": [
                            {"action": request().action_ref, "approval_ref": "review:sealed"}
                        ]
                    }
                ),
            }[identity]

        def __init__(self):
            self.info = {}

        def exec(self, _statement):
            class Rows(list):
                def first(self):
                    return SimpleNamespace(
                        status="approved",
                        run_plan_step_id=6,
                        decision_json={"unknown_cost": "admitted"},
                    )

            return Rows()

    class Runs:
        def __init__(self, session, audit_run_id):
            assert session is fresh and audit_run_id == 4

        def _owner_row(self, **kwargs):
            assert kwargs == {"project_id": 1, "canonical_item_id": "ticket:7"}
            return "current-owner"

        def _owner_admissible(self, owner, scope):
            assert (owner, scope) == ("current-owner", "scope")
            seen.append("admissible")

        def owner_consume(self, **kwargs):
            assert kwargs["expected_epoch"] == 1 and kwargs["expected_revision"] == 2
            assert kwargs["attempts_delta"] == 1 and kwargs["failures_delta"] == 0
            seen.append("canonical-consume-CAS")

    fresh = Session()
    monkeypatch.setattr(runs, "RunRepository", Runs)
    monkeypatch.setattr(
        permissions, "_check_run_plan_dynamic_grant", lambda *_args: seen.append("current-grant")
    )
    monkeypatch.setattr(
        permissions,
        "active_run_plan_step",
        lambda *_args: (SimpleNamespace(id=5, grant_snapshot_json={}), SimpleNamespace(id=6)),
    )
    monkeypatch.setattr(
        operations,
        "_ensure_action_contract_approval",
        lambda *_args, **_kwargs: seen.append("current-approval"),
    )
    codex.fresh_admission(
        replace(request(), session=fresh), original, cost_approval_ref="review:sealed"
    )
    assert seen == [
        ("fresh", 2),
        ("fresh", 4),
        ("fresh", 3),
        "current-grant",
        "current-approval",
        ("fresh", 5),
        "admissible",
        "canonical-consume-CAS",
    ]


def test_fixed_input_rejects_caller_command_or_config_expansion():
    connector = codex.CodexWorkerConnector(BackgroundActionTasks())
    for field in ("argv", "shell", "model", "config", "mcp_servers"):
        expanded = replace(request(), input_json={**request().input_json, field: "unreviewed"})
        assert connector.validate(expanded)


def test_connector_uses_private_fresh_session_and_keeps_completed_unknown_receipt(monkeypatch):
    owner, retained, sessions = BackgroundActionTasks(), object(), []
    bind = object()
    scope_open = []

    class Session:
        def __init__(self, engine):
            assert engine is bind
            self.info = {}
            sessions.append(self)

        def __enter__(self):
            scope_open.append(True)
            return self

        def __exit__(self, *_args):
            scope_open.pop()

    class Transport:
        async def prepare(self, slot):
            assert slot.deadline - slot.started == 90
            assert slot.work_deadline - slot.started == 78
            assert not slot.resources

        def acquire(self, slot, argv, prompt):
            assert sessions and not scope_open
            assert argv == profile().argv() and prompt == "recorded fake"
            slot.adopt("original-process", retained)

        async def observe(self, slot):
            slot.observe(
                1,
                {
                    "result": {"status": "completed", "turn_id": "gate0"},
                    "usage": {"input_tokens": 19758, "output_tokens": 1767, "total_tokens": 21525},
                },
            )

        async def cleanup(self, slot, deadline):
            assert deadline == slot.deadline
            slot.reaped = True
            slot.cli_exit = -15
            # FD/descendant/remote cleanup is deliberately unproved.

    caller_session = SimpleNamespace(
        info={"operation_services": {"background_action_tasks": owner}},
        get_bind=lambda: bind,
        get=lambda *_args, **_kwargs: SimpleNamespace(run_id=4, run_plan_id=5, run_plan_step_id=6),
    )

    def admission(fresh, original, *, cost_approval_ref):
        assert cost_approval_ref == profile().approval_ref
        assert fresh.session is not caller_session and fresh.session is sessions[0]
        assert fresh.session.info["operation_services"]["background_action_tasks"] is owner
        assert scope_open and original.action_call_id == 2

    monkeypatch.setattr(sqlmodel, "Session", Session)
    monkeypatch.setattr(codex, "fresh_admission", admission)
    connector = codex.CodexWorkerConnector(owner, profile=profile(), transport=Transport())
    with pytest.raises(ActionConnectorError) as error:
        asyncio.run(connector.execute(replace(request(), session=caller_session)))
    result = error.value.output_json
    assert result["result"] == {"status": "completed", "turn_id": "gate0"}
    assert result["usage"]["total_tokens"] == 21525
    assert result["cleanup"]["status"] == "unknown"
    assert result["custody"]["acknowledged"] is True
    assert result["cli"] == {"exit_code": -15, "reaped": True}
    assert owner._commands[(2, 3)].resources["original-process"] is retained


def test_completed_replay_reprojects_current_live_custody():
    async def scenario():
        owner, clock = BackgroundActionTasks(), Clock()
        slot = owner.command_slot(binding(), clock=clock)
        arguments = dict(
            prepare=nothing,
            authorize=lambda: None,
            acquire=lambda original: original.adopt("process", object()),
            work=nothing,
            cleanup=close_known,
            clock=clock,
        )
        first = await owner.command(slot, **arguments)
        assert first["custody"]["acknowledged"] is True
        await owner.shutdown(clock=clock)
        assert owner.receipt(slot, clock=clock)["custody"]["acknowledged"] is False
        replay = await owner.command(slot, **arguments)
        assert replay["clock"] == first["clock"]
        assert replay["custody"]["acknowledged"] is False

    asyncio.run(scenario())


def test_receipt_binding_copy_cannot_mutate_original():
    owner, clock = BackgroundActionTasks(), Clock()
    original = binding()
    slot = owner.command_slot(original, clock=clock)
    receipt = owner.receipt(slot, clock=clock)
    receipt["binding"]["owner_ref"] = "mutated-observation"
    assert original.owner_ref == "lead"
    assert owner.receipt(slot, clock=clock)["binding"]["owner_ref"] == "lead"


def test_shutdown_original_audit_uses_canonical_redaction(monkeypatch):
    from stackos.db.models import ActionCallStatus

    observed, stored = [], []
    row = SimpleNamespace(
        id=2,
        project_id=1,
        run_id=4,
        run_plan_id=5,
        run_plan_step_id=6,
        status=ActionCallStatus.SUCCESS,
        metadata_json={"preserved": "original"},
    )

    class Session:
        def get(self, *_args, **_kwargs):
            return row

        def execute(self, statement):
            stored.append(statement.compile().params["metadata_json"])
            return SimpleNamespace(rowcount=1)

        def commit(self):
            pass

    def redact(value):
        observed.append(value)
        return {"canonical_redaction": True}

    receipt = {
        "binding": {
            "action_call_id": 2,
            "project_id": 1,
            "controller_run_id": 4,
            "run_plan_id": 5,
            "run_plan_step_id": 6,
        },
        "result": {"private": "recorded-value"},
    }
    monkeypatch.setattr(execution, "_redact_for_audit", redact)
    execution.ActionExecutionMixin.record_codex_custody(SimpleNamespace(_s=Session()), receipt)
    assert observed == [receipt]
    assert stored == [{"preserved": "original", "codex_custody": {"canonical_redaction": True}}]
    assert row.status == ActionCallStatus.SUCCESS


class FakePipe:
    def __init__(self, name):
        self.name, self.closed = name, False


class FakePopen:
    def __init__(self, *, natural=False, stubborn=False, term_ignored=False):
        self.stdin, self.stdout, self.stderr = (
            FakePipe(name) for name in ("stdin", "stdout", "stderr")
        )
        self.returncode = 0 if natural else None
        self.stubborn, self.term_ignored = stubborn, term_ignored
        self.signals, self.waits = [], []

    def poll(self):
        return self.returncode

    def wait(self, *, timeout):
        assert timeout == 0 and self.returncode is not None
        self.waits.append(timeout)
        return self.returncode

    def terminate(self):
        self.signals.append("TERM")
        if not self.stubborn and not self.term_ignored:
            self.returncode = -15

    def kill(self):
        self.signals.append("KILL")
        if not self.stubborn:
            self.returncode = -9


def recorded_events(input_tokens=257, output_tokens=19):
    return b"".join(
        json.dumps(event).encode() + b"\n"
        for event in (
            {"type": "thread.started", "thread_id": "recorded-turn"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "useful fake"}},
            {
                "type": "turn.completed",
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            },
        )
    )


class FakePipeIO(codex.CodexPipeIO):
    def __init__(
        self, slot, process, *, frames=None, setup_failure=None, close_failure=None, bad_pin=None
    ):
        self.slot, self.process = slot, process
        self.frames = recorded_events() if frames is None else frames
        self.setup_failure, self.close_failure = setup_failure, close_failure
        self.bad_pin, self.written, self.pin_reads = bad_pin, b"", []

    def digest(self, path, limit, deadline, clock):
        assert deadline == self.slot.work_deadline and clock() < deadline
        self.pin_reads.append((path, limit))
        if path == self.bad_pin:
            return "unreviewed"
        return {
            profile().binary: "b" * 64,
            profile().config_path: "c" * 64,
            profile().capability_path: "d" * 64,
        }[path]

    def nonblocking(self, stream):
        assert self.slot.resources[stream.name] is stream
        if stream.name == self.setup_failure:
            raise OSError("injected partial pipe setup")

    def write(self, stream, payload):
        assert stream is self.process.stdin and not stream.closed
        self.written += payload
        return len(payload)

    def read(self, stream):
        assert self.slot.resources[stream.name] is stream
        if stream.name == "stdout" and self.frames:
            result, self.frames = self.frames, b""
            return result
        if self.process.poll() is not None:
            return b""
        raise BlockingIOError

    def close(self, stream):
        assert self.slot.resources[stream.name] is stream
        if stream.name == self.close_failure:
            raise OSError("injected original FD close failure")
        stream.closed = True


def production_case(
    *,
    natural=False,
    stubborn=False,
    term_ignored=False,
    frames=None,
    setup_failure=None,
    close_failure=None,
    bad_pin=None,
    factory_failure=False,
):
    owner, clock = BackgroundActionTasks(), Clock()
    slot = owner.command_slot(binding(), clock=clock)
    process = FakePopen(natural=natural, stubborn=stubborn, term_ignored=term_ignored)
    io = FakePipeIO(
        slot,
        process,
        frames=frames,
        setup_failure=setup_failure,
        close_failure=close_failure,
        bad_pin=bad_pin,
    )
    calls = []

    def factory(argv, **kwargs):
        assert "codex-io" in slot.resources and "process" not in slot.resources
        assert slot.acquired is False and slot.birth_unknown is True
        assert argv == profile().argv()
        assert kwargs == {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(profile().cwd),
            "env": dict(profile().environment),
            "shell": False,
            "close_fds": True,
            "bufsize": 0,
        }
        calls.append(argv)
        if factory_failure:
            raise OSError("injected process birth without returned handle")
        return process

    async def pause(_seconds):
        clock.value += 0.5
        await asyncio.sleep(0)

    transport = codex.CodexProcessTransport(
        profile(), process_factory=factory, io=io, clock=clock, pause=pause
    )
    return owner, clock, slot, process, io, calls, transport


async def run_production(case, authorize=lambda: None):
    owner, clock, slot, _process, _io, _calls, transport = case
    return await owner.command(
        slot,
        prepare=transport.prepare,
        authorize=authorize,
        acquire=lambda original: transport.acquire(original, profile().argv(), "fake prompt"),
        work=transport.observe,
        cleanup=transport.cleanup,
        clock=clock,
    )


@pytest.mark.parametrize("natural", [False, True])
def test_production_transport_actual_logic_result_usage_pipe_reap_and_cancel(natural):
    case = production_case(natural=natural)
    result = asyncio.run(run_production(case))
    owner, clock, slot, process, io, calls, _transport = case
    assert len(calls) == 1 and len(io.pin_reads) == 3
    assert io.written == b"fake prompt\n"
    assert result["result"] == {
        "status": "completed",
        "turn_id": "recorded-turn",
        "text": "useful fake",
    }
    # Production parser uses these event numbers, not historical21525.
    assert result["usage"]["total_tokens"] == 276
    assert result["cli"] == {"exit_code": 0 if natural else -15, "reaped": True}
    assert result["cleanup"]["status"] == "confirmed"
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    assert slot.resources["process"] is process and owner._commands[(2, 3)] is slot
    assert process.waits and all(wait == 0 for wait in process.waits)
    assert result["termination"] == {"descendants": "unknown", "remote": "unknown"}
    assert result["quiescent"] is False and result["capacity_reusable"] is False
    if natural:
        assert process.signals == []
        assert result["cancellation"] == {"requested": False, "requested_at": None}
    else:
        assert process.signals == ["TERM"] and result["cancellation"]["requested"]
    assert clock() <= slot.deadline


def test_production_popen_failure_empty_frame_cannot_ack_process_custody():
    case = production_case(factory_failure=True)
    result = asyncio.run(run_production(case))
    owner, _clock, slot, _process, _io, calls, _transport = case
    assert len(calls) == 1 and set(slot.resources) == {"codex-io"}
    assert slot.resources["codex-io"].process is None
    assert slot.birth_unknown is True and slot.acquired is False
    assert result["cleanup"]["status"] == "unknown"
    assert result["effects"]["status"] == "unknown"
    assert result["custody"]["acknowledged"] is False
    assert result["attempts_consumed"] == 1 and result["retry_safe"] is False
    assert owner._commands[(2, 3)] is slot


@pytest.mark.parametrize("partial", ["setup", "close"])
def test_production_partial_original_pipe_setup_or_close_keeps_exact_owner(partial):
    case = production_case(
        setup_failure="stdout" if partial == "setup" else None,
        close_failure="stdout" if partial == "close" else None,
    )
    result = asyncio.run(run_production(case))
    owner, _clock, slot, process, _io, _calls, _transport = case
    assert slot.resources["process"] is process
    assert all(
        slot.resources[name] is getattr(process, name) for name in ("stdin", "stdout", "stderr")
    )
    assert result["custody"]["acknowledged"] is True
    assert result["cleanup"]["status"] == ("unknown" if partial == "close" else "confirmed")
    assert owner._commands[(2, 3)] is slot and result["retry_safe"] is False


@pytest.mark.parametrize("stubborn", [False, True])
def test_production_term_kill_cleanup_never_refreshes_original_deadline(stubborn):
    case = production_case(stubborn=stubborn, term_ignored=True)
    result = asyncio.run(run_production(case))
    _owner, clock, slot, process, _io, _calls, _transport = case
    assert process.signals == ["TERM", "KILL"]
    assert slot.deadline == 100 and slot.work_deadline == 88
    assert clock() <= 100
    assert result["cleanup"]["status"] == ("unknown" if stubborn else "confirmed")
    assert result["cli"]["reaped"] is not stubborn
    assert result["custody"]["acknowledged"] is True
    assert result["termination"] == {"descendants": "unknown", "remote": "unknown"}


@pytest.mark.parametrize(
    "frames", [b"{malformed}\n", b"x" * 65537, b'{"type":"approval.requested"}\n']
)
def test_production_bounded_output_and_unsupported_permission_fail_closed(frames):
    case = production_case(frames=frames)
    result = asyncio.run(run_production(case))
    assert result["result"]["status"] == "unknown"
    assert result["retry_safe"] is False
    if b"approval" in frames:
        assert result["next_action"] == "needs-operator"


def test_production_actual_pin_mismatch_prevents_process_factory():
    case = production_case(bad_pin=profile().config_path)
    result = asyncio.run(run_production(case))
    assert case[5] == [] and case[2].resources == {}
    assert result["attempts_consumed"] == 0


def test_post_authorize_original_deadline_blocks_production_popen_without_refund():
    case = production_case()
    clock = case[1]

    def authorize():
        clock.value = 89

    result = asyncio.run(run_production(case, authorize))
    assert case[5] == [] and case[2].resources == {}
    assert result["attempts_consumed"] == 1
    assert result["clock"]["deadline"] == 100
    # The production acquisition seam also refuses a delayed/queued direct call.
    with pytest.raises(ValueError, match="fixed native admission"):
        case[6].acquire(case[2], profile().argv(), "fake prompt")
    assert case[5] == []


def test_registered_default_consumer_has_production_transport_but_no_native_pins():
    connector = codex.CodexWorkerConnector(BackgroundActionTasks())
    assert isinstance(connector.transport, codex.CodexProcessTransport)
    with pytest.raises(ActionConnectorError) as error:
        asyncio.run(connector.execute(request()))
    assert error.value.output_json["status"] == "unsupported"


@pytest.mark.parametrize(
    "prior_state", ["orphan-unknown", "running-no-receipt", "completed-unknown"]
)
def test_prior_unknown_codex_command_blocks_fresh_daemon_admission(monkeypatch, prior_state):
    from stackos.db.models import ActionCallStatus, RunKind, RunStatus
    from stackos.mcp import permissions
    from stackos.operations.actions import execution as operations
    from stackos.repositories import runs
    from stackos.repositories.base import ConflictError

    consumed = []
    current = SimpleNamespace(
        project_id=1,
        run_id=4,
        run_plan_id=5,
        run_plan_step_id=6,
        status=ActionCallStatus.RUNNING,
        request_json=request().input_json,
    )
    controller = SimpleNamespace(project_id=1)
    attempt = SimpleNamespace(
        project_id=1,
        parent_run_id=4,
        status=RunStatus.RUNNING,
        kind=RunKind.SKILL_RUN,
        metadata_json={
            "ownership_binding": {
                "canonical_item_id": "ticket:7",
                "owner_ref": "lead",
                "epoch": 1,
                "scope_digest": "scope",
            }
        },
    )
    prior = SimpleNamespace(
        id=99,
        project_id=1,
        operation="codex.recorded-turn",
        dry_run=False,
        status=ActionCallStatus.RUNNING
        if prior_state == "running-no-receipt"
        else ActionCallStatus.FAILED,
        request_json={"canonical_item_id": "ticket:7"},
        metadata_json=None,
        response_json=None
        if prior_state == "running-no-receipt"
        else {
            "outcome_unknown": True,
            "retry_safe": False,
            "result": {"status": "completed" if prior_state == "completed-unknown" else "unknown"},
        },
    )

    class Session:
        def get(self, _model, identity, **_kwargs):
            return {
                2: current,
                4: controller,
                3: attempt,
                5: SimpleNamespace(
                    grant_snapshot_json={
                        "action_contracts": [
                            {"action": request().action_ref, "approval_ref": "review:sealed"}
                        ]
                    }
                ),
            }[identity]

        def __init__(self):
            self.info = {}

        def exec(self, _statement):
            class Rows(list):
                def first(self):
                    return SimpleNamespace(
                        status="approved",
                        run_plan_step_id=6,
                        decision_json={"unknown_cost": "admitted"},
                    )

            return Rows([prior])

    class Runs:
        def __init__(self, *_args, **_kwargs):
            pass

        def _owner_row(self, **_kwargs):
            return object()

        def _owner_admissible(self, *_args):
            pass

        def owner_consume(self, **kwargs):
            consumed.append(kwargs)

    monkeypatch.setattr(runs, "RunRepository", Runs)
    monkeypatch.setattr(permissions, "_check_run_plan_dynamic_grant", lambda *_args: None)
    monkeypatch.setattr(
        permissions,
        "active_run_plan_step",
        lambda *_args: (SimpleNamespace(id=5, grant_snapshot_json={}), SimpleNamespace(id=6)),
    )
    monkeypatch.setattr(operations, "_ensure_action_contract_approval", lambda *_a, **_k: None)
    # A new in-memory daemon owner has no slot; persisted original state must still deny.
    assert BackgroundActionTasks()._commands == {}
    with pytest.raises(ConflictError, match="prior Codex command"):
        codex.fresh_admission(
            replace(request(), session=Session()), binding(), cost_approval_ref="review:sealed"
        )
    assert consumed == []


@pytest.mark.parametrize("failed_name", ["stdin", "stdout"])
def test_original_close_then_raise_latches_unknown_without_retry(failed_name):
    case = production_case()
    _owner, _clock, slot, process, _io, _calls, transport = case
    closed = []

    class CloseThenRaise(FakePipeIO):
        def close(self, stream):
            super().close(stream)
            closed.append(stream.name)
            if stream.name == failed_name:
                raise OSError("injected close succeeded then raised")

    transport._io = CloseThenRaise(slot, process)
    result = asyncio.run(run_production(case))
    assert getattr(process, failed_name).closed is True
    assert closed.count(failed_name) == 1
    assert result["cleanup"]["status"] == "unknown"
    assert result["custody"]["acknowledged"] is True
    assert slot.resources[failed_name] is getattr(process, failed_name)


def test_first_wait_projects_live_custody_after_completion_resume_barrier():
    async def scenario():
        owner, clock = BackgroundActionTasks(), Clock()
        slot = owner.command_slot(binding(), clock=clock)
        original_receipt, queued = owner.receipt, []

        def completion_barrier(retained, **kwargs):
            snapshot = original_receipt(retained, **kwargs)
            if not queued:
                queued.append(True)
                asyncio.get_running_loop().call_soon(setattr, owner, "_accepting", False)
            return snapshot

        owner.receipt = completion_barrier
        result = await owner.command(
            slot,
            prepare=nothing,
            authorize=lambda: None,
            acquire=lambda original: original.adopt("process", object()),
            work=nothing,
            cleanup=close_known,
            clock=clock,
        )
        assert owner._accepting is False  # completion/requester-resume barrier actually ran
        assert result["clock"]["deadline"] == 100
        assert result["custody"]["acknowledged"] is False

    asyncio.run(scenario())


def test_prior_codex_query_is_scoped_and_only_known_no_acquisition_denial_is_clear():
    from stackos.db.models import ActionCallStatus

    class Session:
        def exec(self, statement):
            values = statement.compile().params.values()
            assert 1 in values and 2 in values
            assert "codex.recorded-turn" in values and "ticket:7" in values
            return [
                SimpleNamespace(
                    id=99,
                    status=ActionCallStatus.FAILED,
                    response_json={
                        "status": "unsupported",
                        "provider_executed": False,
                        "attempts_consumed": 0,
                    },
                )
            ]

    execution.ActionExecutionMixin.require_codex_prior_clear(
        SimpleNamespace(_s=Session()),
        project_id=1,
        canonical_item_id="ticket:7",
        action_call_id=2,
    )


@pytest.mark.parametrize("failed_name", ["stdin", "stdout"])
def test_cancelled_original_close_latches_unknown_without_retry(failed_name):
    case = production_case()
    _owner, _clock, slot, process, _io, _calls, transport = case
    closes = []

    class CancelledClose(FakePipeIO):
        def close(self, stream):
            super().close(stream)
            closes.append(stream.name)
            if stream.name == failed_name:
                raise asyncio.CancelledError("injected close cancellation after closed")

    transport._io = CancelledClose(slot, process)
    result = asyncio.run(run_production(case))
    assert getattr(process, failed_name).closed is True
    assert closes.count(failed_name) == 1
    assert failed_name in slot.resources["codex-io"].failed_closes
    assert result["cleanup"]["status"] == "unknown"
    assert slot.resources[failed_name] is getattr(process, failed_name)


def test_fresh_admission_rejects_missing_worker_session_before_access():
    with pytest.raises(ValueError, match="worker Session required"):
        codex.fresh_admission(request(), binding())


def test_canonical_custody_audit_preserves_numeric_usage_and_redacts_secret_keys():
    from stackos.db.models import ActionCallStatus

    saved = []
    row = SimpleNamespace(
        id=2,
        project_id=1,
        run_id=4,
        run_plan_id=5,
        run_plan_step_id=6,
        status=ActionCallStatus.SUCCESS,
        metadata_json={},
    )

    class Session:
        def get(self, *_args, **_kwargs):
            return row

        def execute(self, statement):
            saved.append(statement.compile().params["metadata_json"]["codex_custody"])
            return SimpleNamespace(rowcount=1)

        def commit(self):
            pass

    receipt = {
        "binding": {
            "action_call_id": 2,
            "project_id": 1,
            "controller_run_id": 4,
            "run_plan_id": 5,
            "run_plan_step_id": 6,
        },
        "usage": {
            "input_tokens": 257,
            "output_tokens": 19,
            "total_tokens": 276,
            "allowance_tokens": 16000,
            "hard_cap": False,
        },
        "diagnostic": {"access_token": "synthetic-not-a-real-credential"},
    }
    execution.ActionExecutionMixin.record_codex_custody(SimpleNamespace(_s=Session()), receipt)
    assert saved[0]["usage"] == {
        "unit": "tokens",
        "input": 257,
        "output": 19,
        "total": 276,
        "allowance": 16000,
        "hard_cap": False,
    }
    assert saved[0]["diagnostic"]["access_token"] == "[redacted]"


def test_usage_projection_does_not_exempt_invalid_counters_or_secrets():
    for invalid in (-1, True, "synthetic-not-a-number"):
        output = execution._redact_for_audit(
            execution._codex_usage_projection(
                {
                    "usage": {"input_tokens": invalid},
                    "diagnostic": {"access_token": "synthetic-not-a-real-credential"},
                }
            )
        )
        assert "input" not in output["usage"]
        assert output["usage"]["input_tokens"] == "[redacted]"
        assert output["diagnostic"]["access_token"] == "[redacted]"
