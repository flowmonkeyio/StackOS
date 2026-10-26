"""Isolated canonical action journey; only process and pipe boundaries are doubles.

No TestClient, installed daemon, full-grant sentinel, direct row fabrication,
grant mutation, or mocked admission is used. This module also exposes the small
private runtime used by the separately HELD real-process entrypoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from stackos.actions import ActionRepository
from stackos.actions.codex_worker import CodexPipeIO, CodexProcessTransport, CodexProfile
from stackos.actions.repository.background import BackgroundActionTasks
from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import ActionCall, ActionCallStatus, RunPlanStep, WorkItemOwnership
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.dispatcher import OperationDispatcher
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import ConflictError
from stackos.server import _build_action_connectors

ACTION = "engineering.codex.recorded-turn"
REVISION = "5de90e673472370c020e4db460013ce78487e7fc"
SCOPE = hashlib.sha256(b"isolated-canonical-no-model").hexdigest()
APPROVAL = "codex-reviewed-profile"


class CanonicalRuntime:
    """Private NEW SQLite/services; all canonical records come from operations."""

    def __init__(self, root: Path, profile: CodexProfile | None, transport=None):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.settings = Settings(data_dir=root / "data", state_dir=root / "state")
        self.engine = make_engine(root / "owned.sqlite")
        SQLModel.metadata.create_all(self.engine)  # same canonical schema bootstrap as lifespan
        self.owner = BackgroundActionTasks()
        self.owner.activate()
        self.connectors = _build_action_connectors(
            background_tasks=self.owner,
            codex_profile=profile,
            codex_transport=transport,
        )
        self.services = {
            "settings": self.settings,
            "action_connectors": self.connectors,
            "background_action_tasks": self.owner,
        }
        self.dispatcher = OperationDispatcher(build_operation_registry())
        self.project_id = None
        self.token = None  # ephemeral token never enters evidence output
        self.events = []
        self.inflight_polls = []

    async def call(self, operation, *, controlled=False, **arguments):
        if self.project_id is not None and operation != "project.create":
            arguments.setdefault("project_id", self.project_id)
        if controlled:
            arguments["run_token"] = self.token
        arguments["response_mode"] = "raw"
        with Session(self.engine) as session:
            session.info["operation_services"] = self.services
            result = await self.dispatcher.dispatch(
                operation,
                arguments,
                session=session,
                surface="mcp",
                settings=self.settings,
            )
        self.events.append(operation)
        return result.payload.get("data", result.payload)

    async def prepare(self, packet, *, action_grant=True):
        """Use explicit reviewed facts; fixture defaults never authorize real work."""
        self.packet = packet
        self.scope = packet["continuity"]["scope_digest"]
        approval = packet["approval_ref"]
        project = await self.call(
            "project.create",
            slug=packet["project_slug"],
            name=packet["title"],
            domain="example.invalid",
        )
        self.project_id = project["id"]
        task = await self.call(
            "tracker.createTask",
            key=packet["key"],
            title=packet["title"],
            metadata_json={
                "ownership_continuity": packet["continuity"],
            },
        )
        self.item = "task:" + str(task["task"]["id"])
        grants = [
            {"step_id": "work", "tool": name, "targets": [self.item]}
            for name in ("run.ownerAcquire", "run.ownerStop")
        ]
        if action_grant:
            grants.append({"step_id": "work", "tool": "action.execute", "action_refs": [ACTION]})
        plan = await self.call(
            "runPlan.create",
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": packet["key"],
                "title": packet["title"],
                "steps": [
                    {
                        "id": "work",
                        "title": "One explicit action",
                        "action_refs": [ACTION],
                        "approval_refs": [approval],
                    }
                ],
                "approvals": [{"key": approval, "step_id": "work"}],
                "grants": {
                    "mcp_tool_grants": grants,
                    "action_contracts": [
                        {"key": "one", "action": ACTION, "approval_ref": approval}
                    ],
                },
            },
        )
        self.plan_id = plan["id"]
        with Session(self.engine) as session:
            step = session.exec(
                select(RunPlanStep).where(
                    RunPlanStep.run_plan_id == self.plan_id,
                    RunPlanStep.step_id == "work",
                )
            ).one()
            self.step_id = step.id
        await self.call(
            "tracker.updateTask",
            task_key=packet["key"],
            patch_json={
                "source_json": {"run_plan_id": self.plan_id, "run_plan_step_id": self.step_id},
            },
        )
        await self.call(
            "runPlan.update",
            run_plan_id=self.plan_id,
            approval_key=approval,
            approval_status="approved",
            decided_by=packet["decided_by"],
            decision_json=packet["decision_json"],
        )
        started = await self.call("runPlan.start", run_plan_id=self.plan_id)
        self.token, self.run_id = started["run_token"], started["run_id"]
        await self.call(
            "runPlan.claimStep", controlled=True, run_plan_id=self.plan_id, step_id="work"
        )
        acquired = await self.call(
            "run.ownerAcquire",
            controlled=True,
            canonical_item_id=self.item,
            owner_ref=packet["owner_ref"],
            scope_digest=self.scope,
        )
        owner = acquired["ownership"]
        attempt = await self.call(
            "run.start",
            kind="skill-run",
            parent_run_id=self.run_id,
            metadata_json={
                "ownership_binding": {
                    "canonical_item_id": self.item,
                    "owner_ref": owner["owner_ref"],
                    "epoch": owner["epoch"],
                    "scope_digest": self.scope,
                }
            },
        )
        self.input = {
            "prompt": packet["prompt"],
            "canonical_item_id": self.item,
            "owner_ref": owner["owner_ref"],
            "epoch": owner["epoch"],
            "revision": owner["revision"],
            "scope_digest": self.scope,
            "source_revision": packet["source_revision"],
            "attempt_run_id": attempt["run_id"],
        }

    async def execute(self, idempotency_key="one-original-action"):
        return await self.call(
            "action.execute",
            controlled=True,
            action_ref=ACTION,
            input_json=self.input,
            idempotency_key=idempotency_key,
        )

    def snapshot(self):
        with Session(self.engine) as session:
            calls = list(session.exec(select(ActionCall)))
            owner = session.exec(select(WorkItemOwnership)).one()
            return calls, owner

    def save_evidence(self, path, disposition):
        calls, owner = self.snapshot()
        with Session(self.engine) as session:
            audits = [
                ActionRepository(session)
                .get_call(
                    project_id=self.project_id,
                    action_call_id=call.id,
                )
                .model_dump(mode="json")
                for call in calls
            ]
        path.write_text(
            json.dumps(
                {
                    "classification": "no-model source integration"
                    if self.packet["no_model"]
                    else "actual held-model invocation",
                    "project_id": self.project_id,
                    "run_id": self.run_id,
                    "plan_id": self.plan_id,
                    "step_id": self.step_id,
                    "canonical_item_id": self.item,
                    "operations": self.events,
                    "inflight_polls": self.inflight_polls,
                    "action_calls": audits,
                    "ownership": owner.model_dump(mode="json"),
                    "shutdown": disposition,
                },
                indent=2,
            )
            + "\n"
        )


def fixture_packet():
    return {
        "no_model": True,
        "project_slug": "private-runtime",
        "key": "private-codex",
        "title": "Private canonical source acceptance",
        "approval_ref": APPROVAL,
        "decided_by": "isolated-fixture-operator",
        "decision_json": {"unknown_cost": "admitted"},
        "owner_ref": "fixture:author",
        "prompt": "Return one fixture result; no tools.",
        "source_revision": REVISION,
        "continuity": {
            "budget_ref": "fixture:finite-budget",
            "stop_scope_ref": "fixture:stop",
            "history_ref": "fixture:history",
            "scope_digest": SCOPE,
            "stopped": False,
            "denied": False,
            "attempts_used": 0,
            "failures_used": 0,
            "attempt_limit": 1,
            "failure_limit": 1,
            "effect_identity": "fixture:single-effect",
        },
    }


def fixture_profile(root):
    root.mkdir(parents=True, exist_ok=True)
    paths = [root / name for name in ("not-an-executable", "config.json", "capability.json")]
    for path in paths:
        path.write_text("synthetic no-model fixture pin\n")
    schema = root / "schema.json"
    schema.write_text('{"type":"object"}')
    pins = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)
    return CodexProfile(
        cwd=root,
        schema_path=schema,
        source_revision=REVISION,
        approval_ref=APPROVAL,
        expected=(pins[0], "fixture-version", pins[1], pins[2]),
        observed=(pins[0], "fixture-version", pins[1], pins[2]),
        binary=paths[0],
        config_path=paths[1],
        capability_path=paths[2],
        environment=tuple((key, str(root)) for key in ("PATH", "HOME", "TMPDIR", "CODEX_HOME")),
    )


class Pipe:
    def __init__(self, name):
        self.name, self.closed = name, False

    def close(self):
        self.closed = True


class Process:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = (
            Pipe(name) for name in ("stdin", "stdout", "stderr")
        )

    def poll(self):
        return 0

    def wait(self, timeout):
        assert timeout == 0
        return 0

    def terminate(self):
        raise AssertionError("naturally exited fake must not be terminated")

    def kill(self):
        raise AssertionError("naturally exited fake must not be killed")


class Pipes(CodexPipeIO):
    def __init__(self):
        self.sent = False
        self.after_pins = None

    def digest(self, path, limit, deadline, clock):
        result = super().digest(path, limit, deadline, clock)
        if path.name == "capability.json" and self.after_pins is not None:
            callback, self.after_pins = self.after_pins, None
            callback()
        return result

    def nonblocking(self, stream):
        pass  # fake pipe boundary only

    def write(self, stream, payload):
        return len(payload)

    def read(self, stream):
        if stream.name != "stdout" or self.sent:
            return b""
        self.sent = True
        events = [
            {"type": "thread.started", "thread_id": "fixture-turn"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "fixture"}},
            {"type": "turn.completed", "usage": {"input_tokens": 257, "output_tokens": 19}},
        ]
        return b"".join(json.dumps(event).encode() + b"\n" for event in events)


@pytest.mark.parametrize(
    "mode",
    [
        "admitted",
        "missing-grant",
        "missing-marker",
        "profile-mismatch",
        "default-profile",
        "stopped-owner",
        "revoked-after-prepare",
        "stale-fence",
        "prior-unknown",
        "wrong-project",
        "inflight-selected-owner",
    ],
)
def test_canonical_codex_action_no_model(tmp_path, monkeypatch, mode):
    import stackos.actions.codex_worker as codex

    def forbidden(*_args, **_kwargs):
        raise AssertionError("real process/socket boundary is forbidden in no-model proof")

    monkeypatch.setattr(codex.subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    profile = fixture_profile(tmp_path / "pins")
    if mode == "profile-mismatch":
        from dataclasses import replace

        profile = replace(profile, approval_ref="different-profile-review")
    process = Process()
    births = []

    def acquire(argv, **kwargs):
        assert argv == profile.argv() and kwargs["shell"] is False
        births.append(argv)
        return process

    pipes = Pipes()
    observation_ready, observation_release = asyncio.Event(), asyncio.Event()

    async def observation_pause(delay):
        if mode == "inflight-selected-owner":
            observation_ready.set()
            await observation_release.wait()
        else:
            await asyncio.sleep(delay)

    transport = CodexProcessTransport(
        profile,
        process_factory=acquire,
        io=pipes,
        pause=observation_pause,
    )
    runtime = CanonicalRuntime(
        tmp_path / "runtime", None if mode == "default-profile" else profile, transport
    )

    async def scenario():
        owners = [runtime.owner]
        revocations = []
        completed = False
        packet = fixture_packet()
        if mode == "missing-marker":
            packet["decision_json"] = {}
        try:
            await runtime.prepare(packet, action_grant=mode != "missing-grant")
            if mode == "stopped-owner":
                await runtime.call(
                    "run.ownerStop",
                    controlled=True,
                    canonical_item_id=runtime.item,
                    owner_ref=runtime.input["owner_ref"],
                    scope_digest=runtime.scope,
                    expected_epoch=runtime.input["epoch"],
                    expected_revision=runtime.input["revision"],
                    reason_ref="fixture:stop",
                )
            if mode == "stale-fence":
                runtime.input["revision"] += 1
            if mode == "revoked-after-prepare":
                # Queued before the preparation task completes; normal lifecycle
                # update runs before command's wait resumes its fresh admission.
                pipes.after_pins = lambda: revocations.append(
                    asyncio.create_task(
                        runtime.call(
                            "runPlan.update",
                            run_plan_id=runtime.plan_id,
                            approval_key=APPROVAL,
                            approval_status="approved",
                            decided_by="isolated-fixture-operator",
                            decision_json={},
                        )
                    )
                )
            if mode == "wrong-project":
                other = await runtime.call(
                    "project.create", slug="other", name="Other", domain="other.invalid"
                )
                with pytest.raises(ToolNotGrantedError, match="not scoped to this project"):
                    await runtime.call(
                        "action.execute",
                        controlled=True,
                        project_id=other["id"],
                        action_ref=ACTION,
                        input_json=runtime.input,
                    )
                assert runtime.snapshot()[0] == []
            elif mode in {"missing-grant", "missing-marker", "profile-mismatch", "default-profile"}:
                with pytest.raises((ConflictError, ToolNotGrantedError)):
                    await runtime.execute()
                assert runtime.snapshot()[0] == []
            else:
                started = await runtime.execute()
                assert started["cost_cents"] is None
                assert runtime.owner._tasks
                assert runtime.connectors.get("codex-worker").owner is runtime.owner
                if mode == "inflight-selected-owner":
                    from stackos.actions.repository.background import BACKGROUND_ACTION_TASKS

                    await asyncio.wait_for(observation_ready.wait(), timeout=5)
                    original_id = started["action_call"]["id"]
                    slot = next(iter(runtime.owner._commands.values()))
                    assert slot.acquired and slot.binding.action_call_id == original_id
                    assert slot.resources["process"] is process
                    assert BACKGROUND_ACTION_TASKS.progress(original_id) is None
                    poll = await runtime.call("actionCall.get", action_call_id=original_id)
                    runtime.inflight_polls.append(poll)
                    assert poll["action_call_id"] == original_id and poll["status"] == "running"
                    assert poll["progress"]["phase"] == "accepted"
                    assert poll["poll_arguments"] == {"action_call_id": original_id}
                    observation_release.set()
                await asyncio.wait_for(asyncio.gather(*tuple(runtime.owner._tasks)), timeout=5)
                calls, owner = runtime.snapshot()
                assert len(calls) == 1 and calls[0].id == started["action_call"]["id"]
                call = calls[0]
                assert call.status == ActionCallStatus.FAILED
                assert call.run_id == runtime.run_id and call.run_plan_step_id == runtime.step_id
                assert call.request_json == runtime.input
                assert call.metadata_json["execution_cost"] == {"state": "unknown"}
                if mode in {"admitted", "prior-unknown", "inflight-selected-owner"}:
                    assert len(births) == 1 and owner.continuity_json["attempts_used"] == 1
                    assert call.response_json["result"]["status"] == "completed"
                    assert call.response_json["usage"]["total"] == 276
                    assert call.response_json["cleanup"]["status"] == "confirmed"
                    assert call.response_json["termination"] == {
                        "descendants": "unknown",
                        "remote": "unknown",
                    }
                else:
                    assert births == [] and owner.continuity_json["attempts_used"] == 0
                if mode == "revoked-after-prepare":
                    assert len(revocations) == 1
                    await revocations[0]
                read = await runtime.call("actionCall.get", action_call_id=call.id)
                assert (
                    read["action_call_id"] == call.id and read["output_json"] == call.response_json
                )
                with Session(runtime.engine) as session:
                    assert (
                        ActionRepository(session)
                        .get_call(
                            project_id=runtime.project_id,
                            action_call_id=call.id,
                        )
                        .cost_cents
                        is None
                    )
                if mode == "prior-unknown":
                    replay = await runtime.execute()
                    assert replay["action_call"]["id"] == call.id and len(births) == 1
                    original_response = dict(call.response_json)
                    # Simulate loss of only process-local daemon state. Persisted
                    # original ActionCall and canonical authority remain unchanged.
                    runtime.owner = BackgroundActionTasks()
                    runtime.owner.activate()
                    owners.append(runtime.owner)
                    runtime.connectors = _build_action_connectors(
                        background_tasks=runtime.owner,
                        codex_profile=profile,
                        codex_transport=transport,
                    )
                    runtime.services.update(
                        action_connectors=runtime.connectors, background_action_tasks=runtime.owner
                    )
                    fresh = await runtime.call(
                        "run.start",
                        kind="skill-run",
                        parent_run_id=runtime.run_id,
                        metadata_json={
                            "ownership_binding": {
                                "canonical_item_id": runtime.item,
                                "owner_ref": runtime.input["owner_ref"],
                                "epoch": runtime.input["epoch"],
                                "scope_digest": runtime.scope,
                            }
                        },
                    )
                    runtime.input = {
                        **runtime.input,
                        "attempt_run_id": fresh["run_id"],
                        "revision": owner.revision,
                    }
                    await runtime.execute("new-after-original-unknown")
                    await asyncio.wait_for(asyncio.gather(*tuple(runtime.owner._tasks)), timeout=5)
                    final_calls, final_owner = runtime.snapshot()
                    assert len(final_calls) == 2 and len(births) == 1
                    original = next(row for row in final_calls if row.id == call.id)
                    assert original.response_json == original_response
                    assert final_owner.continuity_json["attempts_used"] == 1
                    assert final_owner.revision == owner.revision
            completed = True
        finally:
            observation_release.set()
            dispositions = []
            try:
                for retained in owners:
                    if retained._tasks:
                        await asyncio.wait_for(
                            asyncio.gather(*tuple(retained._tasks), return_exceptions=True),
                            timeout=5,
                        )
                    dispositions.append(await retained.shutdown())
                if hasattr(runtime, "run_id"):
                    runtime.save_evidence(runtime.root / "proof.json", {"owners": dispositions})
            finally:
                runtime.engine.dispose()
            if completed and mode in {"admitted", "prior-unknown", "inflight-selected-owner"}:
                assert dispositions[0]["clean"] is False
                assert all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr))
                assert next(iter(owners[0]._commands.values())).resources["process"] is process

    asyncio.run(scenario())
