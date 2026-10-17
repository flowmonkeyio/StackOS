"""Real dispatcher/grant/owner/action paths on disposable SQLite, no host."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from stackos.actions import ActionConnectorRegistry, ActionConnectorResult, ActionRepository
from stackos.actions import controlled_command as fixed
from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.repository.audit import ActionAuditMixin
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
    IdempotencyKey,
    Plugin,
    PluginSource,
    Project,
    Run,
    RunPlan,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
    TrackerTask,
    WorkItemOwnership,
)
from stackos.mcp.context import bind_context, build_context
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations import build_operation_registry
from stackos.operations.actions.controlled import (
    _controlled_intent_key,
    _ControlledAuthority,
    _issue_controlled_authority,
)
from stackos.operations.actions.schemas import ActionExecuteInput
from stackos.operations.dispatcher import OperationDispatcher
from stackos.repositories.base import ConflictError, RepositoryError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository

REF = "engineering.controlled-proof.v1"
SCOPE = "a" * 64
PUBLIC_KEYS = {
    "schema_version",
    "request_digest",
    "command_digest",
    "scope_digest",
    "intent_ref",
    "epoch",
}


class Echo:
    key = "fixture-echo"

    def __init__(self):
        self.calls = 0

    def validate(self, request):
        return []

    def estimate_cost_cents(self, request):
        return 0

    async def execute(self, request):
        self.calls += 1
        return ActionConnectorResult(
            output_json={"echo": request.input_json}, metadata_json={"ordinary": "unchanged"}
        )


@pytest.fixture(autouse=True)
def no_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external effect forbidden")

    for name in ("Popen", "run"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("fork", "posix_spawn", "posix_spawnp", "execv", "execve", "kill", "killpg"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("stackos.crypto.load_seed", forbidden)
    monkeypatch.setattr("stackos.crypto.seed.load_seed", forbidden)
    monkeypatch.setattr("stackos.crypto.seed.ensure_seed_file", forbidden)


@dataclass
class Case:
    engine: object
    session: Session
    project: int
    plan: int
    step: int
    run: int
    token: str
    task: int
    args: dict
    source: Path
    connectors: ActionConnectorRegistry
    echo: Echo

    async def dispatch(self, args=None, *, operation="action.execute", session=None):
        active = session or self.session
        active.info["operation_services"] = {"action_connectors": self.connectors}
        return (
            await OperationDispatcher(build_operation_registry()).dispatch(
                operation,
                deepcopy(self.args if args is None else args),
                session=active,
                surface="mcp",
                client_surface="cli",
            )
        ).payload

    def approval(self, *, change=None):
        row = self.session.exec(
            select(ApprovalRequest).where(ApprovalRequest.run_plan_id == self.plan)
        ).one()
        value = deepcopy(row.decision_json)
        if change is not None:
            change(value)
        RunPlanRepository(self.session).update(
            run_plan_id=self.plan,
            approval_key="command",
            approval_status=ApprovalRequestStatus.APPROVED,
            decided_by="fixture:operator",
            decision_json=value,
        )

    def stop(self):
        task = self.session.get(TrackerTask, self.task)
        value = deepcopy(task.metadata_json)
        value["ownership_continuity"]["stopped"] = True
        task.metadata_json = value
        self.session.add(task)
        self.session.commit()


@pytest.fixture
def case_factory(tmp_path, monkeypatch):
    engines = []
    sessions = []

    def make(initial_status="approved"):
        index = len(engines)
        folder = tmp_path / str(index)
        folder.mkdir()
        source = folder / "source"
        for relative in fixed._CONTROLLED_SOURCE_FILES:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((Path(fixed.__file__).resolve().parents[2] / relative).read_bytes())
        monkeypatch.setattr(fixed, "_runtime_source_root", lambda: source)
        engine = create_engine(
            f"sqlite:///{folder / 'case.db'}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        engines.append(engine)
        SQLModel.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_idempotency "
                    "ON idempotency_keys(project_id, tool_name, idempotency_key)"
                )
            )
        session = Session(engine)
        sessions.append(session)
        project = Project(slug="fixture", name="Fixture", domain="example.test", locale="en-US")
        plugin = Plugin(slug="engineering", name="Engineering", source=PluginSource.BUILTIN)
        session.add_all([project, plugin])
        session.commit()
        assert project.id is not None and plugin.id is not None
        action = Action(
            plugin_id=plugin.id,
            key="controlled-proof.v1",
            name="Controlled",
            risk_level="write",
            input_schema_json=fixed._ControlledCommandInput.model_json_schema(),
            config_json={
                "connector": "controlled-command",
                "operation": "controlled-proof.v1",
                "execution_mode": "foreground",
                "direct_run": "workflow-only",
                "controlled_host": "unavailable",
                "requires_credential": False,
                "allows_credential": False,
            },
        )
        ordinary = Action(
            plugin_id=plugin.id,
            key="echo",
            name="Echo",
            risk_level="read",
            input_schema_json={"type": "object"},
            config_json={
                "connector": "fixture-echo",
                "operation": "echo",
                "requires_credential": False,
            },
        )
        session.add_all([action, ordinary])
        session.commit()
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project.id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "fixture.admission",
                "title": "Admission",
                "approvals": []
                if initial_status == "missing"
                else [{"key": "command", "title": "Exact command"}],
                "grants": {
                    "mcp_tool_grants": [
                        {
                            "step_id": "admit",
                            "tool": "action.execute",
                            "action_refs": [REF, "engineering.echo"],
                        }
                    ],
                    "action_contracts": [
                        {"key": "controlled", "action": REF, "approval_ref": "command"}
                    ],
                },
                "steps": [
                    {"id": "admit", "title": "Admit", "action_refs": [REF, "engineering.echo"]}
                ],
            },
        ).data
        started = plans.start(plan.id, project_id=project.id).data
        step = plans.get_step(plan.id, "admit")
        TrackerRepository(session).create_task(project_id=project.id, key="source", title="Source")
        task = session.exec(select(TrackerTask).where(TrackerTask.key == "source")).one()
        task.source_json = {"run_plan_id": plan.id, "run_plan_step_id": step.id}
        task.metadata_json = {
            "ownership_continuity": {
                "scope_digest": SCOPE,
                "budget_ref": "fixture:budget",
                "stop_scope_ref": "fixture:stop",
                "history_ref": "fixture:history",
                "stopped": False,
                "denied": False,
                "attempts_used": 0,
                "failures_used": 0,
                "attempt_limit": 2,
                "failure_limit": 2,
            }
        }
        session.add(task)
        session.commit()
        owner = (
            RunRepository(session)
            .owner_acquire(
                project_id=project.id,
                canonical_item_id=f"task:{task.id}",
                owner_ref="fixture:lead",
                scope_digest=SCOPE,
            )
            .ownership
        )
        command = fixed._resolve_controlled_command()
        payload = {
            "canonical_item_id": owner.canonical_item_id,
            "owner_ref": owner.owner_ref,
            "expected_epoch": owner.epoch,
            "expected_revision": owner.revision,
            "scope_digest": SCOPE,
            "intent_ref": "fixture:intent",
            "command_key": "noop-v1",
            "command_digest": command.digest,
        }
        decision = {
            "controlled_command": {
                "schema_version": "stackos.controlled-command-approval.v1",
                "action_ref": REF,
                "project_id": project.id,
                "controller_run_id": started.run_id,
                "run_plan_id": plan.id,
                "run_plan_step_id": step.id,
                "canonical_item_id": owner.canonical_item_id,
                "owner_ref": owner.owner_ref,
                "epoch": owner.epoch,
                "scope_digest": SCOPE,
                "intent_ref": payload["intent_ref"],
                "command_digest": command.digest,
                "command": command.descriptor(),
            }
        }
        if initial_status not in {"missing", "pending"}:
            plans.update(
                run_plan_id=plan.id,
                approval_key="command",
                approval_status=ApprovalRequestStatus(initial_status),
                decided_by="fixture:operator",
                decision_json=decision,
            )
        plans.claim_step(
            run_plan_id=plan.id, run_id=started.run_id, step_id="admit", claimed_by="fixture:lead"
        )
        connectors = ActionConnectorRegistry()
        connectors.register(fixed.ControlledCommandConnector())
        echo = Echo()
        connectors.register(echo)
        args = {
            "project_id": project.id,
            "run_token": started.run_token,
            "action_ref": REF,
            "input_json": payload,
            "response_mode": "raw",
        }
        return Case(
            engine,
            session,
            project.id,
            plan.id,
            step.id,
            started.run_id,
            started.run_token,
            task.id,
            args,
            source,
            connectors,
            echo,
        )

    yield make
    for session in sessions:
        session.close()
    for engine in engines:
        engine.dispose()


@pytest.fixture
def case(case_factory):
    return case_factory()


def snapshot(case, *, calls=True):
    case.session.expire_all()
    models = [Run, WorkItemOwnership] + ([ActionCall] if calls else [])
    return {
        model.__name__: [
            row.model_dump(mode="json")
            for row in case.session.exec(select(model).order_by(model.id)).all()
        ]
        for model in models
    }


def assert_public(case, output):
    data = output["data"]
    public = data["metadata_json"]
    assert set(public) == {"controlled_admission"}
    assert set(public["controlled_admission"]) == PUBLIC_KEYS
    assert data["action_call"]["metadata_json"] == public
    assert data["action_call"]["response_json"] == data["output_json"]
    assert data["output_json"]["host_dispatched"] is False
    row = case.session.get(ActionCall, data["action_call"]["id"])
    assert "approval_decision_digest" in row.metadata_json["controlled_admission"]
    repo = ActionRepository(case.session)
    assert repo.get_call(project_id=case.project, action_call_id=row.id).metadata_json == public
    assert repo._call_audit_out(row).metadata_json == public
    queried = repo.query_calls(project_id=case.project)
    assert queried.items[0].metadata_json == public
    got = asyncio.run(
        case.dispatch(
            {"project_id": case.project, "action_call_id": row.id, "response_mode": "raw"},
            operation="actionCall.get",
        )
    )
    queried_wire = asyncio.run(
        case.dispatch(
            {"project_id": case.project, "response_mode": "raw"}, operation="actionCall.query"
        )
    )
    for exposed in (data, got, queried_wire):
        serialized = json.dumps(exposed)
        assert (
            "approval_decision_digest" not in serialized and "continuity_digest" not in serialized
        )
        assert str(case.source) not in serialized and case.token not in serialized


def test_current_dispatch_records_terminal_host_unavailable(case):
    before = snapshot(case, calls=False)
    output = asyncio.run(case.dispatch())
    assert output["data"]["action_call"]["status"] == "failed"
    assert output["data"]["output_json"]["code"] == "controlled_host_unavailable"
    assert output["data"]["output_json"]["admission_verified"] is True
    assert snapshot(case, calls=False) == before and case.echo.calls == 0
    assert_public(case, output)
    # A real change to the immutable source fixture is denied on fresh replay.
    path = case.source / fixed._CONTROLLED_SOURCE_FILES[0]
    path.write_bytes(path.read_bytes() + b"\n# source drift\n")
    after = snapshot(case)
    with pytest.raises(ConflictError):
        asyncio.run(case.dispatch())
    assert snapshot(case) == after


@pytest.mark.parametrize(
    "case_id",
    [
        "no-token",
        "system",
        "test-sentinel",
        "wrong-project",
        "step-ref",
        "grant-ref",
        "two-running-steps",
        "terminal-controller",
        "wrong-item-binding",
        "stale-epoch",
        "stale-owner-revision",
    ],
)
def test_controlled_grant_and_context_rejection(case, case_id):
    args = deepcopy(case.args)
    if case_id == "no-token":
        args["run_token"] = "unrecognized"
    elif case_id == "system":
        args.pop("run_token")
    elif case_id == "test-sentinel":
        row = case.session.get(Run, case.run)
        row.metadata_json = row.metadata_json | {"skill_name": "__test__"}
        case.session.add(row)
    elif case_id == "wrong-project":
        args["project_id"] += 1
    elif case_id == "step-ref":
        row = case.session.get(RunPlanStep, case.step)
        row.action_refs_json = ["engineering.echo"]
        case.session.add(row)
    elif case_id == "grant-ref":
        row = case.session.get(RunPlan, case.plan)
        value = deepcopy(row.grant_snapshot_json)
        value["mcp_tool_grants"][0]["action_refs"] = ["engineering.echo"]
        row.grant_snapshot_json = value
        case.session.add(row)
    elif case_id == "two-running-steps":
        case.session.add(
            RunPlanStep(
                run_plan_id=case.plan,
                step_id="other",
                title="Other",
                position=2,
                status=RunPlanStepStatus.RUNNING,
            )
        )
    elif case_id == "terminal-controller":
        row = case.session.get(Run, case.run)
        row.status = RunStatus.SUCCESS
        case.session.add(row)
    elif case_id == "wrong-item-binding":
        row = case.session.get(TrackerTask, case.task)
        row.source_json = {"run_plan_id": case.plan, "run_plan_step_id": case.step + 100}
        case.session.add(row)
    else:
        field = "expected_epoch" if case_id == "stale-epoch" else "expected_revision"
        args["input_json"][field] += 1
    case.session.commit()
    before = snapshot(case)
    with pytest.raises((RepositoryError, ToolNotGrantedError)):
        asyncio.run(case.dispatch(args))
    assert snapshot(case) == before and case.echo.calls == 0


@pytest.mark.parametrize(
    "case_id", ["missing", "pending", "rejected", "cancelled", "decision-drift"]
)
def test_controlled_approval_rejection(case_factory, case_id):
    case = case_factory("approved" if case_id == "decision-drift" else case_id)
    if case_id == "decision-drift":
        case.approval(
            change=lambda d: d["controlled_command"]["command"]["environment"].update(
                PATH="/different"
            )
        )
    before = snapshot(case)
    with pytest.raises((RepositoryError, ToolNotGrantedError)):
        asyncio.run(case.dispatch())
    assert snapshot(case) == before


@pytest.mark.parametrize(
    "case_id", ["direct-action", "direct-repository", "registry", "background", "durable"]
)
def test_controlled_bypasses_are_closed(case, case_id, monkeypatch):
    args = deepcopy(case.args)
    if case_id == "background":
        sync = PluginRepository.sync_builtin_plugins

        def background_sync(repo, *args, **kwargs):
            result = sync(repo, *args, **kwargs)
            # The catalog synchronizes builtins before every resolution. Apply
            # the isolated fault after that real writer, not before it.
            row = repo._s.exec(select(Action).where(Action.key == "controlled-proof.v1")).one()
            row.config_json = row.config_json | {"execution_mode": "background"}
            repo._s.add(row)
            repo._s.commit()
            return result

        monkeypatch.setattr(PluginRepository, "sync_builtin_plugins", background_sync)
        resolved = ActionRepository(case.session).describe(project_id=case.project, action_ref=REF)
        assert resolved.manifest.execution_mode == "background"
        assert resolved.manifest.config_json["execution_mode"] == "background"
    before = snapshot(case)
    match = "controlled manifest is incompatible" if case_id == "background" else None
    with pytest.raises((RepositoryError, ToolNotGrantedError, ActionConnectorError), match=match):
        if case_id == "direct-action":
            args.pop("run_token")
            args.update(confirm_direct=True, intent_summary="Fixture controlled direct denial")
            asyncio.run(case.dispatch(args, operation="action.run"))
        elif case_id == "direct-repository":
            asyncio.run(
                ActionRepository(case.session).execute(
                    project_id=case.project, action_ref=REF, input_json=args["input_json"]
                )
            )
        elif case_id == "registry":
            request = ActionConnectorRequest(
                project_id=case.project,
                plugin_slug="engineering",
                action_key="controlled-proof.v1",
                action_ref=REF,
                provider_key=None,
                operation="controlled-proof.v1",
                input_json=args["input_json"],
                config_json={},
                session=case.session,
                action_call_id=1,
            )
            asyncio.run(case.connectors.get("controlled-command").execute(request))
        else:
            if case_id == "durable":
                args["account_interval_seconds"] = 1.0
            asyncio.run(case.dispatch(args))
    assert snapshot(case) == before


@pytest.mark.parametrize("case_id", ["identical", "payload-conflict", "unknown-history"])
def test_controlled_replay_preserves_original_outcome(case, case_id):
    first = asyncio.run(case.dispatch())
    args = deepcopy(case.args)
    if case_id == "payload-conflict":
        args["input_json"]["expected_revision"] += 1
    elif case_id == "unknown-history":
        row = case.session.get(ActionCall, first["data"]["action_call"]["id"])
        row.response_json = row.response_json | {"outcome_unknown": True}
        case.session.add(row)
        case.session.commit()
    before = snapshot(case)
    if case_id == "identical":
        second = asyncio.run(case.dispatch(args))
        assert second["data"]["replayed"] is True
        assert second["data"]["action_call"] == first["data"]["action_call"]
        assert second["data"]["output_json"] == first["data"]["output_json"]
        assert_public(case, second)
    else:
        with pytest.raises(ConflictError):
            asyncio.run(case.dispatch(args))
    assert snapshot(case) == before


@pytest.mark.parametrize("case_id", ["before-row", "after-row"])
def test_controlled_owner_stop_prevents_progress(case, case_id, monkeypatch):
    reserve = ActionAuditMixin._reserve_background_call

    def stopped(repo, **kwargs):
        if case_id == "before-row":
            case.stop()
        result = reserve(repo, **kwargs)
        if case_id == "after-row":
            case.stop()
        return result

    monkeypatch.setattr(ActionAuditMixin, "_reserve_background_call", stopped)
    before = snapshot(case, calls=False)
    if case_id == "before-row":
        with pytest.raises(ConflictError):
            asyncio.run(case.dispatch())
        assert case.session.exec(select(ActionCall)).all() == []
    else:
        out = asyncio.run(case.dispatch())
        assert out["data"]["output_json"]["code"] == "authority_changed"
        assert out["data"]["action_call"]["status"] == "failed"
    assert snapshot(case, calls=False) == before


def test_controlled_concurrent_intent_has_one_audit_winner(case):
    barrier = Barrier(2)

    def contender():
        with Session(case.engine) as session:
            barrier.wait(timeout=5)
            try:
                return asyncio.run(case.dispatch(session=session))["data"]["action_call"]["id"]
            except ConflictError:
                return None

    before = snapshot(case, calls=False)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: contender(), range(2)))
    assert any(value is not None for value in results)
    case.session.expire_all()
    calls = case.session.exec(select(ActionCall)).all()
    assert len(calls) == 1 and calls[0].status == ActionCallStatus.FAILED
    assert {value for value in results if value is not None} == {calls[0].id}
    assert snapshot(case, calls=False) == before


@pytest.mark.parametrize("case_id", ["after-reserve", "before-terminal-ack"])
def test_controlled_interruption_retains_original_record(case, case_id, monkeypatch):
    reserve = ActionAuditMixin._reserve_background_call
    final = ActionAuditMixin._finalize_running_call
    validate = _ControlledAuthority.revalidate
    armed = False
    fired = False

    def reserved(repo, **kwargs):
        nonlocal armed
        value = reserve(repo, **kwargs)
        armed = True
        return value

    def revalidate(authority):
        nonlocal fired
        if case_id == "after-reserve" and armed and not fired:
            fired = True
            raise asyncio.CancelledError
        return validate(authority)

    def finalized(repo, **kwargs):
        nonlocal fired
        value = final(repo, **kwargs)
        if case_id == "before-terminal-ack" and not fired:
            fired = True
            raise asyncio.CancelledError
        return value

    monkeypatch.setattr(ActionAuditMixin, "_reserve_background_call", reserved)
    monkeypatch.setattr(_ControlledAuthority, "revalidate", revalidate)
    monkeypatch.setattr(ActionAuditMixin, "_finalize_running_call", finalized)
    before = snapshot(case, calls=False)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(case.dispatch())
    assert fired
    case.session.expire_all()
    calls = case.session.exec(select(ActionCall)).all()
    assert len(calls) == 1 and calls[0].status == ActionCallStatus.FAILED
    assert calls[0].response_json["host_dispatched"] is False
    assert snapshot(case, calls=False) == before


def test_controlled_running_audit_remains_inspectable_after_terminal_write_failure(
    case, monkeypatch
):
    ordinary = {
        "project_id": case.project,
        "run_token": case.token,
        "action_ref": "engineering.echo",
        "input_json": {"value": "ordinary"},
        "response_mode": "raw",
    }
    ordinary_out = asyncio.run(case.dispatch(ordinary))
    ordinary_id = ordinary_out["data"]["action_call"]["id"]
    before = snapshot(case, calls=False)

    def unavailable(*args, **kwargs):
        raise RepositoryError("fixture terminal persistence unavailable")

    monkeypatch.setattr(ActionAuditMixin, "_finalize_running_call", unavailable)
    with pytest.raises(RepositoryError, match="fixture terminal persistence unavailable"):
        asyncio.run(case.dispatch())
    case.session.expire_all()
    row = case.session.exec(
        select(ActionCall).where(ActionCall.action_key == "controlled-proof.v1")
    ).one()
    assert row.status == ActionCallStatus.RUNNING and row.response_json is None
    repo = ActionRepository(case.session)
    direct = repo.get_call(project_id=case.project, action_call_id=row.id)
    assert direct.status == ActionCallStatus.RUNNING and direct.response_json is None
    assert set(direct.metadata_json) == {"controlled_admission"}
    assert set(direct.metadata_json["controlled_admission"]) == PUBLIC_KEYS
    got = asyncio.run(
        case.dispatch(
            {
                "project_id": case.project,
                "action_call_id": row.id,
                "response_mode": "raw",
            },
            operation="actionCall.get",
        )
    )
    assert got["status"] == "running" and got["output_json"] is None
    assert got["poll_operation"] == "actionCall.get"
    assert got["retry_safe"] is None and got["outcome_unknown"] is None
    queried = asyncio.run(
        case.dispatch(
            {
                "project_id": case.project,
                "response_mode": "raw",
            },
            operation="actionCall.query",
        )
    )
    rows = {item["id"]: item for item in queried["items"]}
    assert set(rows) == {row.id, ordinary_id}
    assert rows[row.id]["status"] == "running" and rows[row.id]["response_json"] is None
    assert rows[row.id]["metadata_json"] == direct.metadata_json
    assert rows[ordinary_id]["status"] == "success"
    assert rows[ordinary_id]["metadata_json"]["ordinary"] == "unchanged"
    for exposed in (got, queried):
        serialized = json.dumps(exposed)
        assert (
            "approval_decision_digest" not in serialized and "continuity_digest" not in serialized
        )
        assert str(case.source) not in serialized and case.token not in serialized
    assert snapshot(case, calls=False) == before
    assert len(case.session.exec(select(ActionCall)).all()) == 2 and case.echo.calls == 1


def test_controlled_reconciled_orphan_is_visible_but_not_replayable(case, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RepositoryError("fixture terminal persistence unavailable")

    monkeypatch.setattr(ActionAuditMixin, "_finalize_running_call", unavailable)
    with pytest.raises(RepositoryError, match="fixture terminal persistence unavailable"):
        asyncio.run(case.dispatch())
    repo = ActionRepository(case.session)
    row = case.session.exec(select(ActionCall)).one()
    assert row.status == ActionCallStatus.RUNNING and row.response_json is None
    before = snapshot(case, calls=False)
    assert repo.reconcile_running_calls() == 1
    case.session.refresh(row)
    orphan = {"outcome_unknown": True, "retry_safe": False}
    assert row.status == ActionCallStatus.FAILED and row.response_json == orphan
    assert row.error == "daemon-restart-orphan"
    got = asyncio.run(
        case.dispatch(
            {
                "project_id": case.project,
                "action_call_id": row.id,
                "response_mode": "raw",
            },
            operation="actionCall.get",
        )
    )
    assert got["status"] == "failed" and got["output_json"] == orphan
    assert got["outcome_unknown"] is True and got["retry_safe"] is False
    assert "admission_verified" not in got["output_json"]
    queried = asyncio.run(
        case.dispatch(
            {
                "project_id": case.project,
                "response_mode": "raw",
            },
            operation="actionCall.query",
        )
    )
    assert len(queried["items"]) == 1
    assert queried["items"][0]["response_json"] == orphan
    assert set(queried["items"][0]["metadata_json"]["controlled_admission"]) == PUBLIC_KEYS
    retained = snapshot(case)
    with pytest.raises(ConflictError, match="controlled_history_unknown"):
        asyncio.run(case.dispatch())
    assert snapshot(case) == retained
    assert snapshot(case, calls=False) == before
    # This exact canonical exception does not accept a malformed terminal body.
    row.response_json = orphan | {"unexpected": True}
    case.session.add(row)
    case.session.commit()
    with pytest.raises(ConflictError, match="controlled_history_unknown"):
        repo.get_call(project_id=case.project, action_call_id=row.id)
    with pytest.raises(ConflictError, match="controlled_history_unknown"):
        repo.query_calls(project_id=case.project)


def test_controlled_dry_run_is_not_current_admission(case_factory):
    case = case_factory("pending")
    args = deepcopy(case.args) | {"dry_run": True}
    before = snapshot(case, calls=False)
    out = asyncio.run(case.dispatch(args))
    assert out["data"]["action_call"]["status"] == ActionCallStatus.DRY_RUN.value
    assert out["data"]["action_call"]["dry_run"] is True
    assert out["data"]["dry_run"] is True
    assert out["data"]["output_json"]["admission_verified"] is False
    assert out["data"]["output_json"]["host_dispatched"] is False
    assert out["data"]["output_json"]["certified"] is False
    assert snapshot(case, calls=False) == before


@pytest.mark.parametrize("case_id", ["decision-drift", "owner-stopped", "cached-success"])
def test_controlled_cached_replay_rechecks_current_authority(case, case_id):
    parsed = ActionExecuteInput.model_validate(case.args)
    with bind_context(build_context(case.args, case.session)) as ctx:
        key = _controlled_intent_key(_issue_controlled_authority(parsed, ctx))
    args = deepcopy(case.args) | {"idempotency_key": key}
    first = asyncio.run(case.dispatch(args))
    unchanged = asyncio.run(case.dispatch(args))
    assert unchanged["data"]["action_call"]["id"] == first["data"]["action_call"]["id"]
    assert_public(case, unchanged)
    if case_id == "decision-drift":
        case.approval(change=lambda d: d.update(review_revision=2))
        case.session.expire_all()
        approval = case.session.exec(
            select(ApprovalRequest).where(ApprovalRequest.run_plan_id == case.plan)
        ).one()
        assert type(approval.decision_json["review_revision"]) is int
        assert approval.decision_json["review_revision"] == 2
        assert (
            approval.decision_json["controlled_command"]["command_digest"]
            == case.args["input_json"]["command_digest"]
        )
    elif case_id == "owner-stopped":
        case.stop()
    else:
        row = case.session.get(ActionCall, first["data"]["action_call"]["id"])
        row.status = ActionCallStatus.SUCCESS
        case.session.add(row)
        case.session.commit()
    before = snapshot(case)
    with pytest.raises((RepositoryError, ToolNotGrantedError)):
        asyncio.run(case.dispatch(args))
    assert snapshot(case) == before


def test_unrelated_local_action_fresh_and_cached_replay_unchanged(case):
    args = {
        "project_id": case.project,
        "run_token": case.token,
        "action_ref": "engineering.echo",
        "input_json": {"value": "ordinary"},
        "idempotency_key": "ordinary:intent",
        "response_mode": "raw",
    }
    first = asyncio.run(case.dispatch(args))
    second = asyncio.run(case.dispatch(args))
    assert case.echo.calls == 1
    assert first["data"]["action_call"] == second["data"]["action_call"]
    assert (
        first["data"]["output_json"]
        == second["data"]["output_json"]
        == {"echo": {"value": "ordinary"}}
    )
    assert first["data"]["metadata_json"] == second["data"]["metadata_json"]
    assert first["data"]["metadata_json"]["ordinary"] == "unchanged"
    row = case.session.get(ActionCall, first["data"]["action_call"]["id"])
    repo = ActionRepository(case.session)
    assert (
        repo.get_call(project_id=case.project, action_call_id=row.id).metadata_json
        == row.metadata_json
    )
    assert repo.query_calls(project_id=case.project).items[0].metadata_json == row.metadata_json


def test_default_commit_guard_reservation_and_replay_unchanged(case):
    repo = ActionRepository(case.session)
    manifest = repo.describe(project_id=case.project, action_ref="engineering.echo").manifest
    before = snapshot(case, calls=False)
    for explicit in (False, True):
        kwargs = {"commit_guard": None} if explicit else {}
        values = dict(
            project_id=case.project,
            manifest=manifest,
            credential_ref=None,
            run_id=None,
            run_plan_id=None,
            run_plan_step_id=None,
            idempotency_key=f"ordinary:{explicit}",
            request_json={"value": "same"},
            provider_context_json=None,
            metadata_json={"ordinary": "unchanged"},
            estimated_cost_cents=0,
        )
        row, replay = repo._reserve_background_call(**values, **kwargs)
        assert not replay
        row = repo._finalize_running_call(
            project_id=case.project,
            action_call_id=row.id,
            credential=None,
            response_json={"value": "same"},
            metadata_json=row.metadata_json,
            status=ActionCallStatus.SUCCESS,
            cost_cents=0,
            duration_ms=0,
            error=None,
            commit=True,
        )
        frozen = snapshot(case)
        repeated, replay = repo._reserve_background_call(**values, **kwargs)
        assert replay and repeated.id == row.id and repeated.response_json == row.response_json
        assert snapshot(case) == frozen
    assert len(case.session.exec(select(ActionCall)).all()) == 2
    assert (
        len(
            case.session.exec(
                select(IdempotencyKey).where(IdempotencyKey.tool_name == "action.background")
            ).all()
        )
        == 2
    )
    assert snapshot(case, calls=False) == before
