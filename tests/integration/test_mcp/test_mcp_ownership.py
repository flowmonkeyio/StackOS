"""Ownership through real adapter/grant paths on disposable fixture state only."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select
from typer.testing import CliRunner

import stackos.cli.operation_commands as operation_cli
import stackos.operations.runs as run_operations
from stackos import controlled_process_receipt as receipt
from stackos.cli import app as cli_app
from stackos.db.models import (
    ActionCall,
    ProjectEvent,
    Run,
    RunKind,
    RunPlan,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
    TrackerTask,
    TrackerTicket,
    WorkItemOwnership,
)
from stackos.operations.dispatcher import OperationDispatcher
from stackos.operations.registry import build_operation_registry
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.workflows.run_plan_grants import RUN_PLAN_OWNER_TOOL_NAMES

from .conftest import MCPClient

SCOPE = "a" * 64


@pytest.mark.parametrize("halt", ["stopped", "denied"])
@pytest.mark.parametrize("mode", ["positive-consumption", "clearance-resume", "late-failure"])
def test_owner_commit_rechecks_halt_without_blocking_late_facts(
    mcp_client: MCPClient,
    owned: dict,
    monkeypatch: pytest.MonkeyPatch,
    halt: str,
    mode: str,
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    attempt_id = None
    if mode == "clearance-resume":
        with Session(owned["engine"]) as session:
            attempt_id = (
                RunRepository(session)
                .start(
                    project_id=owned["project_id"],
                    kind=RunKind.SKILL_RUN,
                    parent_run_id=owned["run_id"],
                    metadata_json={
                        "ownership_binding": {
                            "canonical_item_id": owned["canonical_item_id"],
                            "owner_ref": owned["owner_ref"],
                            "epoch": row["epoch"],
                            "scope_digest": SCOPE,
                        }
                    },
                )
                .data.id
            )
        for state in ("running", "blocked"):
            row = mcp_client.call_tool_structured(
                "run.ownerRecordAttempt",
                _arguments(
                    owned,
                    **_fence(row),
                    attempt_run_id=attempt_id,
                    observation=state,
                    evidence_ref=f"fixture:{state}",
                ),
            )["data"]["ownership"]
    with Session(owned["engine"]) as session:
        events_before = len(session.exec(select(ProjectEvent)).all())
        attempt_before = (
            deepcopy(session.get(Run, attempt_id).metadata_json) if attempt_id else None
        )
    authority = RunRepository.owner_authority
    reads = 0
    injected = False

    def halt_after_repository_read(repo, **kwargs):
        nonlocal reads, injected
        result = authority(repo, **kwargs)
        reads += 1
        if reads == 2:
            # Commit the canonical halt after the repository's last semantic
            # check, before its first ownership/attempt write and commit guard.
            with Session(owned["engine"]) as other:
                ticket = other.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
                metadata = deepcopy(ticket.metadata_json)
                metadata["ownership_continuity"][halt] = True
                ticket.metadata_json = metadata
                other.add(ticket)
                other.commit()
            injected = True
        return result

    monkeypatch.setattr(RunRepository, "owner_authority", halt_after_repository_read)
    if mode == "clearance-resume":
        name = "run.ownerRecordAttempt"
        arguments = _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            observation="running",
            clearance_ref="fixture:clearance",
            evidence_ref="fixture:resumed",
        )
    else:
        name = "run.ownerConsume"
        arguments = _arguments(
            owned,
            **_fence(row),
            attempts_delta=int(mode == "positive-consumption"),
            failures_delta=int(mode == "late-failure"),
            evidence_ref=f"fixture:{mode}",
        )
    if mode == "late-failure":
        receipt = mcp_client.call_tool_structured(name, arguments)
        assert "error" not in receipt, receipt
    else:
        error = mcp_client.call_tool_error(name, arguments)
        assert error["code"] == -32008
    assert injected
    with Session(owned["engine"]) as session:
        current = (
            RunRepository(session)
            .owner_get(project_id=owned["project_id"], canonical_item_id=owned["canonical_item_id"])
            .model_dump(mode="json")
        )
        ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
        assert ticket.metadata_json["ownership_continuity"][halt] is True
        events_after = len(session.exec(select(ProjectEvent)).all())
        if mode == "late-failure":
            assert current["continuity_json"]["failures_used"] == 1
            assert current["continuity_json"]["attempts_used"] == 0
            assert current["revision"] == row["revision"] + 1
            assert events_after == events_before + 1
        else:
            assert current == row
            assert events_after == events_before
            if attempt_id:
                assert session.get(Run, attempt_id).metadata_json == attempt_before
    if mode == "late-failure":
        error = mcp_client.call_tool_error(
            "run.ownerConsume",
            _arguments(
                owned, **_fence(current), attempts_delta=1, evidence_ref="fixture:forbidden"
            ),
        )
        assert error["code"] == -32008


@pytest.fixture
def owned(mcp_client: MCPClient, seeded_project: dict) -> dict:
    pid = seeded_project["data"]["id"]
    plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": pid,
            "run_plan_json": {
                "schema_version": "stackos.run-plan.v1",
                "key": "ownership-fixture",
                "title": "Ownership fixture",
                "steps": [
                    {"id": "write", "title": "Write"},
                    {"id": "later", "title": "Later", "depends_on": ["write"]},
                ],
            },
            "response_mode": "raw",
        },
    )["data"]
    engine = mcp_client.test_client.app.state.engine
    with Session(engine) as session:
        task = session.exec(
            select(TrackerTask).where(TrackerTask.key == f"workflow-{plan['id']}")
        ).one()
        step = session.exec(
            select(RunPlanStep).where(
                RunPlanStep.run_plan_id == plan["id"],
                RunPlanStep.step_id == "write",
            )
        ).one()
        ticket = (
            TrackerRepository(session)
            .create_ticket(
                project_id=pid,
                task_key=task.key,
                key="ownership-work",
                title="Ownership work",
                run_plan_id=plan["id"],
                run_plan_step_id=step.id,
                references_json=[{"ref_type": "external", "ref": "external:fixture-work"}],
            )
            .data.ticket
        )
        assert ticket is not None
        item = f"ticket:{ticket.id}"
        native = session.get(TrackerTicket, ticket.id)
        native.metadata_json = {
            "ownership_continuity": {
                "budget_ref": "budget:original",
                "stop_scope_ref": "stop-scope:original",
                "history_ref": "history:original",
                "scope_digest": SCOPE,
                "stopped": False,
                "denied": False,
                "attempts_used": 0,
                "failures_used": 0,
                "attempt_limit": 2,
                "failure_limit": 2,
                "effect_identity": "effect:stable",
            }
        }
        definition = session.get(RunPlan, plan["id"])
        definition.grant_snapshot_json = {
            "mcp_tool_grants": [
                {"step_id": phase, "tool": name, "targets": [item]}
                for phase in ("write", "later")
                for name in sorted(RUN_PLAN_OWNER_TOOL_NAMES)
            ]
        }
        session.add(native)
        session.add(definition)
        session.commit()
        step_id = step.id
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {
            "project_id": pid,
            "run_plan_id": plan["id"],
            "response_mode": "raw",
        },
    )["data"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "project_id": pid,
            "run_plan_id": plan["id"],
            "step_id": "write",
            "run_token": started["run_token"],
            "response_mode": "raw",
        },
    )
    return {
        "project_id": pid,
        "canonical_item_id": item,
        "owner_ref": "fixture:worker",
        "scope_digest": SCOPE,
        "run_token": started["run_token"],
        "run_id": started["run_id"],
        "run_plan_id": plan["id"],
        "step_id": step_id,
        "engine": engine,
    }


def _arguments(owned: dict, **changes) -> dict:
    return (
        {
            key: owned[key]
            for key in (
                "project_id",
                "canonical_item_id",
                "owner_ref",
                "scope_digest",
                "run_token",
            )
        }
        | {"response_mode": "raw"}
        | changes
    )


def _acquire(mcp: MCPClient, owned: dict, **changes) -> dict:
    out = mcp.call_tool_structured("run.ownerAcquire", _arguments(owned, **changes))
    assert "error" not in out, out
    return out["data"]


def _fence(row: dict) -> dict:
    return {"expected_epoch": row["epoch"], "expected_revision": row["revision"]}


def _start_owned_attempt(mcp: MCPClient, owned: dict, row: dict) -> int:
    started = mcp.call_tool_structured(
        "run.start",
        {
            "project_id": owned["project_id"],
            "kind": "skill-run",
            "parent_run_id": owned["run_id"],
            "metadata_json": {
                "ownership_binding": {
                    "canonical_item_id": owned["canonical_item_id"],
                    "owner_ref": owned["owner_ref"],
                    "epoch": row["epoch"],
                    "scope_digest": SCOPE,
                }
            },
            "response_mode": "raw",
        },
    )
    return started["data"]["run_id"]


def _set_canonical_counter(mcp: MCPClient, owned: dict, counter: str, value: int) -> None:
    with Session(owned["engine"]) as session:
        ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
        metadata = deepcopy(ticket.metadata_json)
    metadata["ownership_continuity"][counter] = value
    updated = mcp.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": owned["project_id"],
            "ticket_key": "ownership-work",
            "patch_json": {"metadata_json": metadata},
            "response_mode": "raw",
        },
    )
    assert updated["data"]["ticket"]["metadata_json"] == metadata


def _blocked_owned_attempt(mcp: MCPClient, owned: dict, state: str = "blocked") -> tuple[dict, int]:
    row = _acquire(mcp, owned)["ownership"]
    attempt_id = _start_owned_attempt(mcp, owned, row)
    for observation in ("running", state):
        result = mcp.call_tool_structured(
            "run.ownerRecordAttempt",
            _arguments(
                owned,
                **_fence(row),
                attempt_run_id=attempt_id,
                observation=observation,
                evidence_ref=f"fixture:{observation}",
            ),
        )
        row = result["data"]["ownership"]
    return row, attempt_id


@pytest.mark.parametrize("previous", ["blocked", "scope_drift"])
@pytest.mark.parametrize("observation", ["running", "waiting"])
@pytest.mark.parametrize(
    ("counter", "value"), [("failures_used", 1), ("failures_used", 2), ("attempts_used", 1)]
)
def test_owner_clearance_rechecks_canonical_counter_floor(
    mcp_client: MCPClient, owned: dict, previous: str, observation: str, counter: str, value: int
) -> None:
    row, attempt_id = _blocked_owned_attempt(mcp_client, owned, previous)
    _set_canonical_counter(mcp_client, owned, counter, value)
    before = _owned_attempt_snapshot(owned, attempt_id)
    error = mcp_client.call_tool_error(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            observation=observation,
            clearance_ref="fixture:clearance",
            evidence_ref="fixture:resume",
        ),
    )
    assert error["code"] == -32008
    assert _owned_attempt_snapshot(owned, attempt_id) == before


@pytest.mark.parametrize(
    ("counter", "value"), [("failures_used", 1), ("failures_used", 2), ("attempts_used", 1)]
)
def test_owner_clearance_replay_rechecks_canonical_counter_floor(
    mcp_client: MCPClient, owned: dict, counter: str, value: int
) -> None:
    row, attempt_id = _blocked_owned_attempt(mcp_client, owned)
    arguments = _arguments(
        owned,
        **_fence(row),
        attempt_run_id=attempt_id,
        observation="running",
        clearance_ref="fixture:clearance",
        evidence_ref="fixture:resume",
        idempotency_key="fixture:resume",
    )
    resumed = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    unchanged = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    assert unchanged["data"] == resumed["data"]
    assert unchanged["idempotency_replay"] is True
    _set_canonical_counter(mcp_client, owned, counter, value)
    before = _owned_attempt_snapshot(owned, attempt_id)
    error = mcp_client.call_tool_error("run.ownerRecordAttempt", arguments)
    assert error["code"] == -32008
    assert _owned_attempt_snapshot(owned, attempt_id) == before


@pytest.mark.parametrize("failures", [1, 2])
def test_owner_clearance_commit_rechecks_canonical_failures(
    mcp_client: MCPClient, owned: dict, monkeypatch: pytest.MonkeyPatch, failures: int
) -> None:
    row, attempt_id = _blocked_owned_attempt(mcp_client, owned)
    before = _owned_attempt_snapshot(owned, attempt_id)
    authority = RunRepository.owner_authority
    reads = 0

    def advance_after_repository_read(repo, **kwargs):
        nonlocal reads
        result = authority(repo, **kwargs)
        reads += 1
        if reads == 2:
            with Session(owned["engine"]) as other:
                ticket = other.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
                metadata = deepcopy(ticket.metadata_json)
                metadata["ownership_continuity"]["failures_used"] = failures
                ticket.metadata_json = metadata
                other.add(ticket)
                other.commit()
        return result

    monkeypatch.setattr(RunRepository, "owner_authority", advance_after_repository_read)
    error = mcp_client.call_tool_error(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            observation="running",
            clearance_ref="fixture:clearance",
            evidence_ref="fixture:resume",
        ),
    )
    assert error["code"] == -32008
    after = _owned_attempt_snapshot(owned, attempt_id)
    assert reads >= 2
    assert after["ticket"]["metadata_json"]["ownership_continuity"]["failures_used"] == failures
    assert {k: v for k, v in after.items() if k != "ticket"} == {
        k: v for k, v in before.items() if k != "ticket"
    }


def test_owner_clearance_preserves_active_final_attempt(mcp_client: MCPClient, owned: dict) -> None:
    row, attempt_id = _blocked_owned_attempt(mcp_client, owned)
    _set_canonical_counter(mcp_client, owned, "attempts_used", 2)
    _set_canonical_counter(mcp_client, owned, "failures_used", 1)
    row = mcp_client.call_tool_structured(
        "run.ownerConsume",
        _arguments(owned, **_fence(row), evidence_ref="fixture:reconcile"),
    )["data"]["ownership"]
    resumed = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            observation="running",
            clearance_ref="fixture:clearance",
            evidence_ref="fixture:resume",
        ),
    )["data"]
    assert resumed["attempt"]["metadata_json"]["ownership_observation"]["state"] == "running"
    assert resumed["ownership"]["continuity_json"] == row["continuity_json"]


@pytest.mark.parametrize("annotation", [{"observation": "scope_drift"}, {"disposition": "failed"}])
def test_owner_late_report_preserved_after_canonical_failure_exhaustion(
    mcp_client: MCPClient, owned: dict, annotation: dict
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    attempt_id = _start_owned_attempt(mcp_client, owned, row)
    row = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            observation="running",
            evidence_ref="fixture:running",
        ),
    )["data"]["ownership"]
    _set_canonical_counter(mcp_client, owned, "failures_used", 2)
    arguments = _arguments(
        owned,
        **_fence(row),
        attempt_run_id=attempt_id,
        **annotation,
        evidence_ref="fixture:late-report",
        idempotency_key="fixture:late-report",
    )
    reported = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    assert reported["data"]["ownership"]["continuity_json"] == row["continuity_json"]
    replay = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    assert replay["data"] == reported["data"]
    assert replay["idempotency_replay"] is True


@pytest.mark.parametrize("terminal", ["finish", "abort"])
def test_owner_attempt_replay_rejects_changed_lifecycle(
    mcp_client: MCPClient, owned: dict, terminal: str
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    attempt_id = _start_owned_attempt(mcp_client, owned, row)
    arguments = _arguments(
        owned,
        **_fence(row),
        attempt_run_id=attempt_id,
        observation="running",
        evidence_ref="fixture:running",
        idempotency_key="fixture:running",
    )
    original = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    changed = mcp_client.call_tool_structured(
        f"run.{terminal}",
        {"project_id": owned["project_id"], "run_id": attempt_id, "response_mode": "raw"}
        | ({"status": "success"} if terminal == "finish" else {}),
    )
    assert changed["data"]["status"] != "running"
    before = _owned_attempt_snapshot(owned, attempt_id)
    assert before["ownership"] == original["data"]["ownership"]
    error = mcp_client.call_tool_error("run.ownerRecordAttempt", arguments)
    assert error["code"] == -32008
    assert "attempt" in error["data"]["detail"]
    assert _owned_attempt_snapshot(owned, attempt_id) == before


@pytest.mark.parametrize("change", ["binding", "parent", "observation", "disposition"])
def test_owner_attempt_replay_rechecks_identity_and_annotations(
    mcp_client: MCPClient, owned: dict, change: str
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    attempt_id = _start_owned_attempt(mcp_client, owned, row)
    arguments = _arguments(
        owned,
        **_fence(row),
        attempt_run_id=attempt_id,
        observation="running",
        evidence_ref="fixture:running",
        idempotency_key="fixture:running",
    )
    original = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    with Session(owned["engine"]) as session:
        attempt = session.get(Run, attempt_id)
        metadata = deepcopy(attempt.metadata_json)
        if change == "binding":
            metadata["ownership_binding"]["owner_ref"] = "fixture:other-owner"
        elif change == "parent":
            attempt.parent_run_id = None
        else:
            metadata[f"ownership_{change}"] = {"state": "changed-by-backend"}
        attempt.metadata_json = metadata
        session.add(attempt)
        session.commit()
    before = _owned_attempt_snapshot(owned, attempt_id)
    assert before["ownership"] == original["data"]["ownership"]
    error = mcp_client.call_tool_error("run.ownerRecordAttempt", arguments)
    assert error["code"] == -32008
    assert "attempt" in error["data"]["detail"]
    assert _owned_attempt_snapshot(owned, attempt_id) == before


def test_owner_attempt_replay_allows_heartbeat_and_unrelated_metadata(
    mcp_client: MCPClient, owned: dict
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    attempt_id = _start_owned_attempt(mcp_client, owned, row)
    arguments = _arguments(
        owned,
        **_fence(row),
        attempt_run_id=attempt_id,
        observation="running",
        evidence_ref="fixture:running",
        idempotency_key="fixture:running",
    )
    original = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    heartbeat = mcp_client.call_tool_structured(
        "run.heartbeat",
        {"project_id": owned["project_id"], "run_id": attempt_id, "response_mode": "raw"},
    )
    assert heartbeat["data"]["heartbeat_at"] != original["data"]["attempt"]["heartbeat_at"]
    with Session(owned["engine"]) as session:
        attempt = session.get(Run, attempt_id)
        attempt.metadata_json = {**attempt.metadata_json, "fixture_note": "unrelated metadata"}
        session.add(attempt)
        session.commit()
    before = _owned_attempt_snapshot(owned, attempt_id)
    replay = mcp_client.call_tool_structured("run.ownerRecordAttempt", arguments)
    assert replay["data"] == original["data"]
    assert replay["idempotency_replay"] is True
    assert _owned_attempt_snapshot(owned, attempt_id) == before


def test_ownership_adapters_have_one_contract_and_no_certificate_operation() -> None:
    registry = build_operation_registry()
    names = RUN_PLAN_OWNER_TOOL_NAMES | {"run.ownerResolve", "run.ownerGet"}
    for surface in ("mcp", "rest", "cli"):
        assert names <= {spec.name for spec in registry.by_surface(surface)}
        assert not any("certify" in spec.name.lower() for spec in registry.by_surface(surface))
    for name in RUN_PLAN_OWNER_TOOL_NAMES:
        spec = registry.get(name)
        assert spec.read_only is False
        assert spec.grant_policy == "run-plan-step-item-grant"
        assert spec.effective_response_policy.allowed_modes == ("compact", "raw")


def test_owner_granted_acquisition_alias_read_and_audit(
    mcp_client: MCPClient,
    owned: dict,
) -> None:
    resolved = mcp_client.call_tool_structured(
        "run.ownerResolve",
        {
            "project_id": owned["project_id"],
            "alias_ref": "external:fixture-work",
            "response_mode": "raw",
        },
    )
    assert resolved["canonical_item_id"] == owned["canonical_item_id"]
    first = _acquire(mcp_client, owned)
    assert first["acquired"] is True
    repeat = _acquire(mcp_client, owned)
    assert repeat["acquired"] is False
    assert repeat["ownership"] == first["ownership"]
    with Session(owned["engine"]) as session:
        events = session.exec(
            select(ProjectEvent).where(ProjectEvent.event_type == "WORK_ITEM_OWNER_ACQUIRED")
        ).all()
        assert len(events) == 1
        assert events[0].run_id == owned["run_id"]
        assert len(session.exec(select(WorkItemOwnership)).all()) == 1


def test_owner_get_returns_bounded_status_in_authenticated_mcp_modes(
    mcp_client: MCPClient, owned: dict
) -> None:
    _acquire(mcp_client, owned)
    with Session(owned["engine"]) as session:
        before = _owner_read_state(session, owned)
    compact = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
        },
    )
    raw = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
            "response_mode": "raw",
        },
    )
    assert compact["data"]["status"]["schema_version"] == "stackos.owner-status.v1"
    assert compact["data"]["status"]["state"] == "not_reserved"
    assert raw.get("data", raw)["status"]["state"] == compact["data"]["status"]["state"]
    assert (
        mcp_client.call_tool_error(
            "run.ownerGet",
            {
                "project_id": owned["project_id"],
                "canonical_item_id": owned["canonical_item_id"],
                "run_token": owned["run_token"],
                "response_mode": "ack",
            },
        )["code"]
        < 0
    )
    with Session(owned["engine"]) as session:
        assert _owner_read_state(session, owned) == before
    rest = mcp_client.test_client.post(
        "/api/v1/operations/run.ownerGet/call",
        json={
            "arguments": {
                "project_id": owned["project_id"],
                "canonical_item_id": owned["canonical_item_id"],
            }
        },
        headers={"Authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert rest.status_code == 200, rest.text
    assert rest.json().get("data", rest.json())["status"]["state"] == "not_reserved"
    wrong_project = mcp_client.call_tool_error(
        "run.ownerGet",
        {
            "project_id": owned["project_id"] + 10000,
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
        },
    )
    invalid_item = mcp_client.call_tool_error(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": "ticket:999999999",
            "run_token": owned["run_token"],
        },
    )
    assert wrong_project["code"] < 0
    assert invalid_item["code"] < 0
    with Session(owned["engine"]) as session:
        assert _owner_read_state(session, owned) == before


def _owner_read_state(session: Session, owned: dict) -> tuple:
    pid = owned["project_id"]
    return (
        tuple(
            row.model_dump(mode="json")
            for row in session.exec(select(Run).where(Run.project_id == pid).order_by(Run.id)).all()
        ),
        tuple(
            row.model_dump(mode="json")
            for row in session.exec(
                select(WorkItemOwnership)
                .where(WorkItemOwnership.project_id == pid)
                .order_by(WorkItemOwnership.id)
            ).all()
        ),
        tuple(
            row.model_dump(mode="json")
            for row in session.exec(
                select(ProjectEvent).where(ProjectEvent.project_id == pid).order_by(ProjectEvent.id)
            ).all()
        ),
        tuple(
            row.model_dump(mode="json")
            for row in session.exec(
                select(ActionCall).where(ActionCall.project_id == pid).order_by(ActionCall.id)
            ).all()
        ),
    )


def _status_reservation(owned: dict, owner: dict, attempt_id: int, intent_ref: str) -> dict:
    return {
        "owner_ref": owner["owner_ref"],
        "epoch": owner["epoch"],
        "scope_digest": SCOPE,
        "manifest_digest": "b" * 64,
        "controller_run_id": owned["run_id"],
        "attempt_run_id": attempt_id,
    }


def _replace_owner_reservation(
    session: Session,
    owned: dict,
    owner: dict,
    attempt_id: int,
    *,
    extra: dict | None = None,
) -> None:
    ownership = session.exec(
        select(WorkItemOwnership).where(
            WorkItemOwnership.project_id == owned["project_id"],
            WorkItemOwnership.canonical_item_id == owned["canonical_item_id"],
        )
    ).one()
    ledger = {"intent:current": _status_reservation(owned, owner, attempt_id, "intent:current")}
    ledger.update(extra or {})
    ownership.continuity_json = ownership.continuity_json | {"launch_reservations": ledger}
    session.add(ownership)
    attempt = session.get(Run, attempt_id)
    if attempt is not None:
        metadata = deepcopy(attempt.metadata_json or {})
        metadata["launch_reservation"] = {
            "intent_ref": "intent:current",
            "manifest_digest": "b" * 64,
        }
        attempt.metadata_json = metadata
        session.add(attempt)


def _seed_status_attempt(owned: dict, owner: dict) -> int:
    with Session(owned["engine"]) as session:
        started = (
            RunRepository(session)
            .start(
                project_id=owned["project_id"],
                kind=RunKind.SKILL_RUN,
                parent_run_id=owned["run_id"],
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": owned["canonical_item_id"],
                        "owner_ref": owner["owner_ref"],
                        "epoch": owner["epoch"],
                        "scope_digest": SCOPE,
                    }
                },
            )
            .data
        )
        attempt_id = started.id
        _replace_owner_reservation(session, owned, owner, attempt_id)
        session.commit()
    return attempt_id


def _status_binding(owned: dict, owner: dict, attempt_id: int) -> receipt._Binding:
    return receipt._Binding(
        project_id=owned["project_id"],
        canonical_item_id=owned["canonical_item_id"],
        owner_ref=owner["owner_ref"],
        epoch=owner["epoch"],
        reservation_revision=owner["revision"],
        scope_digest=SCOPE,
        controller_run_id=owned["run_id"],
        run_plan_id=owned["run_plan_id"],
        run_plan_step_id=owned["step_id"],
        attempt_run_id=attempt_id,
        intent_ref="intent:current",
        manifest_digest="b" * 64,
        host_ref="host:fixture",
        boot_uuid="00000000-0000-0000-0000-000000000002",
    )


def _status_committed_arm_slot(binding: receipt._Binding) -> dict:
    name = receipt._name(binding)
    return {
        "schema_version": 1,
        "phase": "COMMITTED",
        "slot_revision": 4,
        "binding": asdict(binding),
        "binding_digest": name[:-8],
        "name": name,
        "allocation_reservation_revision": binding.reservation_revision,
        "evidence_ref": "fixture:arm-committed",
        "dalloc": [1, 2, 123, 123, 0o40700, 2],
        "fempty": [1, 3, 123, 123, 0o100600, 1, 0, 100, 200],
        "status_flags": 1,
        "offset": 0,
        "sealed_digest": "d" * 64,
        "receipt_digest": "d" * 64,
    }


def test_owner_get_uses_current_reservation_and_real_run_provenance(
    mcp_client: MCPClient, owned: dict
) -> None:
    owner = _acquire(mcp_client, owned)["ownership"]
    with Session(owned["engine"]) as session:
        old_owner_ref = "fixture:prior-worker"
        old_attempt = (
            RunRepository(session)
            .start(
                project_id=owned["project_id"],
                kind=RunKind.SKILL_RUN,
                parent_run_id=owned["run_id"],
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": owned["canonical_item_id"],
                        "owner_ref": old_owner_ref,
                        "epoch": 1,
                        "scope_digest": SCOPE,
                    }
                },
            )
            .data
        )
        old_attempt_id = old_attempt.id
        started = (
            RunRepository(session)
            .start(
                project_id=owned["project_id"],
                kind=RunKind.SKILL_RUN,
                parent_run_id=owned["run_id"],
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": owned["canonical_item_id"],
                        "owner_ref": owned["owner_ref"],
                        "epoch": 2,
                        "scope_digest": SCOPE,
                    }
                },
            )
            .data
        )
        attempt_id = started.id
        ownership = session.exec(
            select(WorkItemOwnership).where(
                WorkItemOwnership.project_id == owned["project_id"],
                WorkItemOwnership.canonical_item_id == owned["canonical_item_id"],
            )
        ).one()
        historical = {
            "owner_ref": old_owner_ref,
            "epoch": 1,
            "scope_digest": SCOPE,
            "manifest_digest": "c" * 64,
            "controller_run_id": owned["run_id"],
            "attempt_run_id": old_attempt_id,
        }
        current = {
            "owner_ref": owner["owner_ref"],
            "epoch": 2,
            "scope_digest": SCOPE,
            "manifest_digest": "b" * 64,
            "controller_run_id": owned["run_id"],
            "attempt_run_id": attempt_id,
        }
        ownership.continuity_json = ownership.continuity_json | {
            "launch_reservations": {"intent:old": historical, "intent:current": current}
        }
        ownership.previous_owner_ref = old_owner_ref
        ownership.epoch = 2
        ownership.revision += 1
        old_row = session.get(Run, old_attempt_id)
        old_row.metadata_json = (old_row.metadata_json or {}) | {
            "launch_reservation": {"intent_ref": "intent:old", "manifest_digest": "c" * 64}
        }
        attempt = session.get(Run, attempt_id)
        foreign_binding = receipt._Binding(
            project_id=owned["project_id"],
            canonical_item_id=owned["canonical_item_id"],
            owner_ref=old_owner_ref,
            epoch=1,
            reservation_revision=owner["revision"],
            scope_digest=SCOPE,
            controller_run_id=owned["run_id"],
            run_plan_id=owned["run_plan_id"],
            run_plan_step_id=owned["step_id"],
            attempt_run_id=old_attempt_id,
            intent_ref="intent:old",
            manifest_digest="c" * 64,
            host_ref="host:fixture",
            boot_uuid="00000000-0000-0000-0000-000000000001",
        )
        foreign_binding_digest = receipt._name(foreign_binding)[:-8]
        attempt.metadata_json = (attempt.metadata_json or {}) | {
            "launch_reservation": {
                "intent_ref": "intent:current",
                "manifest_digest": "b" * 64,
            },
            "native_control": {
                "schema_version": "stackos.native-control.v1",
                "phase": "REGISTERED",
                "control_revision": 1,
                "binding_digest": foreign_binding_digest,
                "manifest_digest": "b" * 64,
                "registration": {"binding": asdict(foreign_binding)},
            },
        }
        session.add(ownership)
        session.add(old_row)
        session.add(attempt)
        session.commit()

    result = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
            "response_mode": "raw",
        },
    )
    status = result.get("data", result)["status"]
    assert status["state"] == "running"
    assert status["original_binding"]["reservation_ref"] == "intent:current"
    assert status["attempt"]["id"] == attempt_id
    assert status["control"] is None


@pytest.mark.parametrize(
    ("case", "expected_state", "expected_needs", "next_actor", "next_action"),
    [
        (
            "success",
            "production_succeeded",
            ["acceptance_review"],
            "main_agent",
            "review_acceptance",
        ),
        ("failed", "production_failed", ["execution_failed"], "agent", "inspect_attempt"),
        ("rejected", "rejected", ["authority_rejected"], "operator", "repair_authority"),
        ("unknown", "unknown", ["provenance_unknown"], "main_agent", "reconcile_provenance"),
        ("missing_attempt", "unknown", ["missing_attempt"], "main_agent", "reconcile_provenance"),
        ("mixed", "mixed", ["provenance_unknown"], "main_agent", "reconcile_provenance"),
        ("wrong_kind", "stale", ["binding_mismatch"], "operator", "repair_authority"),
        ("bool_epoch", "stale", ["binding_mismatch"], "operator", "repair_authority"),
    ],
)
def test_owner_get_real_canonical_state_matrix_is_read_only(
    mcp_client: MCPClient,
    owned: dict,
    case: str,
    expected_state: str,
    expected_needs: list[str],
    next_actor: str,
    next_action: str,
) -> None:
    owner = _acquire(mcp_client, owned)["ownership"]
    attempt_id = _seed_status_attempt(owned, owner)
    with Session(owned["engine"]) as session:
        if case in {"success", "failed"}:
            attempt = session.get(Run, attempt_id)
            attempt.status = RunStatus.SUCCESS if case == "success" else RunStatus.FAILED
            attempt.ended_at = datetime(2026, 1, 2, 3, 4, 5)
            attempt.heartbeat_at = datetime(2026, 1, 2, 3, 4, 6)
            attempt.last_step_at = datetime(2026, 1, 2, 3, 4, 7)
            if case == "success":
                binding = _status_binding(owned, owner, attempt_id)
                attempt.metadata_json = (attempt.metadata_json or {}) | {
                    "receipt_slot": _status_committed_arm_slot(binding),
                    "native_control": {
                        "schema_version": "stackos.native-control.v1",
                        "phase": "REGISTERED",
                        "control_revision": 1,
                        "binding_digest": receipt._name(binding)[:-8],
                        "manifest_digest": "b" * 64,
                        "registration": {"binding": asdict(binding)},
                    },
                }
            session.add(attempt)
        elif case == "rejected":
            ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
            metadata = deepcopy(ticket.metadata_json)
            metadata["ownership_continuity"]["stopped"] = True
            ticket.metadata_json = metadata
            session.add(ticket)
        elif case == "unknown":
            ownership = session.exec(
                select(WorkItemOwnership).where(
                    WorkItemOwnership.project_id == owned["project_id"],
                    WorkItemOwnership.canonical_item_id == owned["canonical_item_id"],
                )
            ).one()
            ownership.continuity_json = ownership.continuity_json | {
                "launch_reservations": {"intent:broken": {"partial": True}}
            }
            session.add(ownership)
        elif case == "missing_attempt":
            _replace_owner_reservation(session, owned, owner, 999999)
        elif case == "mixed":
            _replace_owner_reservation(
                session,
                owned,
                owner,
                attempt_id,
                extra={
                    "intent:second": _status_reservation(owned, owner, attempt_id, "intent:second")
                },
            )
        elif case == "wrong_kind":
            attempt = session.get(Run, attempt_id)
            attempt.kind = RunKind.RUN_PLAN
            session.add(attempt)
        elif case == "bool_epoch":
            attempt = session.get(Run, attempt_id)
            metadata = deepcopy(attempt.metadata_json)
            metadata["ownership_binding"]["epoch"] = True
            attempt.metadata_json = metadata
            flag_modified(attempt, "metadata_json")
            session.add(attempt)
        session.commit()
    if case == "bool_epoch":
        with Session(owned["engine"]) as session:
            persisted = session.get(Run, attempt_id)
            assert type(persisted.metadata_json["ownership_binding"]["epoch"]) is bool
    expected_instants = {}
    if case in {"success", "failed"}:
        with Session(owned["engine"]) as session:
            persisted = session.get(Run, attempt_id)
            for field in ("started_at", "ended_at", "heartbeat_at", "last_step_at"):
                value = getattr(persisted, field)
                expected_instants[field] = (
                    value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
                )

    with Session(owned["engine"]) as session:
        before = _owner_read_state(session, owned)
    response = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
            "response_mode": "raw",
        },
    )
    status = response.get("data", response)["status"]
    assert status["state"] == expected_state
    assert status["needs"] == expected_needs
    assert status["next_actor"] == next_actor
    assert status["next_action"] == next_action
    assert status["acceptance_state"] == "not_evaluated"
    if case == "success":
        assert status["control"] == {
            "schema_version": "stackos.native-control.v1",
            "phase": "REGISTERED",
            "control_revision": 1,
            "binding_digest": status["control"]["binding_digest"],
            "manifest_digest": "b" * 64,
        }
    if case in {"success", "failed"}:
        for field in ("started_at", "ended_at", "heartbeat_at", "last_step_at"):
            observed = datetime.fromisoformat(status["attempt"][field].replace("Z", "+00:00"))
            assert observed == expected_instants[field]
    with Session(owned["engine"]) as session:
        assert _owner_read_state(session, owned) == before


@pytest.mark.parametrize(
    "arm_case",
    [
        "matching",
        "foreign_plan",
        "foreign_step",
        "foreign_reservation_revision",
        "foreign_host",
        "missing_slot",
        "malformed_slot",
    ],
)
def test_owner_get_control_requires_full_original_arm_binding(
    mcp_client: MCPClient, owned: dict, arm_case: str
) -> None:
    owner = _acquire(mcp_client, owned)["ownership"]
    attempt_id = _seed_status_attempt(owned, owner)
    arm_binding = _status_binding(owned, owner, attempt_id)
    registered_binding = arm_binding
    if arm_case.startswith("foreign_"):
        fields = asdict(arm_binding)
        key = {
            "foreign_plan": "run_plan_id",
            "foreign_step": "run_plan_step_id",
            "foreign_reservation_revision": "reservation_revision",
            "foreign_host": "host_ref",
        }[arm_case]
        fields[key] = "host:foreign" if key == "host_ref" else fields[key] + 100
        registered_binding = receipt._Binding(**fields)
    registered_digest = receipt._name(registered_binding)[:-8]

    with Session(owned["engine"]) as session:
        attempt = session.get(Run, attempt_id)
        metadata = deepcopy(attempt.metadata_json)
        metadata["native_control"] = {
            "schema_version": "stackos.native-control.v1",
            "phase": "REGISTERED",
            "control_revision": 1,
            "binding_digest": registered_digest,
            "manifest_digest": "b" * 64,
            "registration": {"binding": asdict(registered_binding)},
        }
        if arm_case == "missing_slot":
            metadata.pop("receipt_slot", None)
        elif arm_case == "malformed_slot":
            metadata["receipt_slot"] = {"phase": "COMMITTED"}
        else:
            metadata["receipt_slot"] = _status_committed_arm_slot(arm_binding)
        attempt.metadata_json = metadata
        session.add(attempt)
        session.commit()

    with Session(owned["engine"]) as session:
        before = _owner_read_state(session, owned)
    response = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
            "response_mode": "raw",
        },
    )
    status = response.get("data", response)["status"]
    assert status["state"] == "running"
    if arm_case == "matching":
        assert status["control"] is not None
        assert status["control"]["binding_digest"] == registered_digest
    else:
        assert status["control"] is None
    with Session(owned["engine"]) as session:
        assert _owner_read_state(session, owned) == before


@pytest.mark.parametrize("snapshot_enabled", [True, False])
def test_owner_get_file_wal_snapshot_is_coherent_across_real_projection_reads(
    mcp_client: MCPClient,
    owned: dict,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_enabled: bool,
) -> None:
    if not snapshot_enabled:
        monkeypatch.setattr(run_operations, "_begin_owner_status_snapshot", lambda _session: None)
    owner = _acquire(mcp_client, owned)["ownership"]
    with owned["engine"].connect() as connection:
        mode = connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
    assert str(mode).lower() == "wal"
    with Session(owned["engine"]) as session:
        attempt = (
            RunRepository(session)
            .start(
                project_id=owned["project_id"],
                kind=RunKind.SKILL_RUN,
                parent_run_id=owned["run_id"],
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": owned["canonical_item_id"],
                        "owner_ref": owned["owner_ref"],
                        "epoch": owner["epoch"],
                        "scope_digest": SCOPE,
                    }
                },
            )
            .data
        )
        attempt_id = attempt.id
        ownership = session.exec(
            select(WorkItemOwnership).where(
                WorkItemOwnership.project_id == owned["project_id"],
                WorkItemOwnership.canonical_item_id == owned["canonical_item_id"],
            )
        ).one()
        ownership.continuity_json = ownership.continuity_json | {
            "launch_reservations": {
                "intent:current": {
                    "owner_ref": owner["owner_ref"],
                    "epoch": owner["epoch"],
                    "scope_digest": SCOPE,
                    "manifest_digest": "b" * 64,
                    "controller_run_id": owned["run_id"],
                    "attempt_run_id": attempt_id,
                }
            }
        }
        child = session.get(Run, attempt_id)
        child.metadata_json = (child.metadata_json or {}) | {
            "launch_reservation": {"intent_ref": "intent:current", "manifest_digest": "b" * 64}
        }
        session.add(ownership)
        session.add(child)
        session.commit()

    original_owner_get = RunRepository.owner_get
    interleaved = False

    def owner_get_then_commit(repo, **kwargs):
        nonlocal interleaved
        row = original_owner_get(repo, **kwargs)
        if not interleaved:
            with Session(owned["engine"]) as second:
                current = second.exec(
                    select(WorkItemOwnership).where(
                        WorkItemOwnership.project_id == owned["project_id"],
                        WorkItemOwnership.canonical_item_id == owned["canonical_item_id"],
                    )
                ).one()
                current.revision += 1
                current.epoch += 1
                current.previous_owner_ref = current.owner_ref
                current.owner_ref = "fixture:next-owner"
                second.add(current)
                child = second.get(Run, attempt_id)
                child_metadata = deepcopy(child.metadata_json)
                child_metadata["ownership_binding"]["owner_ref"] = "fixture:next-owner"
                child_metadata["ownership_binding"]["epoch"] = owner["epoch"] + 1
                child.metadata_json = child_metadata
                second.add(child)
                second.commit()
            interleaved = True
        return row

    monkeypatch.setattr(RunRepository, "owner_get", owner_get_then_commit)
    response = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
            "response_mode": "raw",
        },
    )
    assert interleaved
    response_data = response.get("data", response)
    if snapshot_enabled:
        assert response_data["status"]["owner"]["revision"] == owner["revision"]
        assert response_data["status"]["owner"]["epoch"] == owner["epoch"]
        assert response_data["status"]["state"] == "running"
        assert response_data["status"]["attempt"]["status"] == "running"
        assert response_data["status"]["attempt"]["id"] == attempt_id
    else:
        assert response_data["status"]["state"] == "stale"
    fresh = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "run_token": owned["run_token"],
            "response_mode": "raw",
        },
    )
    fresh_status = fresh.get("data", fresh)["status"]
    assert fresh_status["owner"]["revision"] == owner["revision"] + 1
    assert fresh_status["owner"]["epoch"] == owner["epoch"] + 1
    assert fresh_status["state"] == "stale"


@pytest.mark.parametrize(
    "case",
    [
        "missing-token",
        "invalid-token",
        "wrong-item",
        "wrong-tool",
        "wrong-binding",
        "no-step",
        "multiple-steps",
        "terminal-run",
    ],
)
def test_owner_grant_denials_do_not_acquire(
    mcp_client: MCPClient,
    owned: dict,
    case: str,
) -> None:
    args = _arguments(owned)
    if case == "missing-token":
        args.pop("run_token")
    elif case == "invalid-token":
        args["run_token"] = "fixture:invalid-token"
    elif case == "wrong-item":
        args["canonical_item_id"] = "ticket:99999"
    else:
        with Session(owned["engine"]) as session:
            plan = session.get(RunPlan, owned["run_plan_id"])
            step = session.get(RunPlanStep, owned["step_id"])
            ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
            if case == "wrong-tool":
                grants = deepcopy(plan.grant_snapshot_json)
                grants["mcp_tool_grants"] = [
                    entry
                    for entry in grants["mcp_tool_grants"]
                    if entry["tool"] != "run.ownerAcquire"
                ]
                plan.grant_snapshot_json = grants
                session.add(plan)
            elif case == "wrong-binding":
                ticket.run_plan_step_id = None
                session.add(ticket)
            elif case == "no-step":
                step.status = RunPlanStepStatus.PENDING
                session.add(step)
            elif case == "multiple-steps":
                other = session.exec(
                    select(RunPlanStep).where(
                        RunPlanStep.run_plan_id == plan.id,
                        RunPlanStep.step_id == "later",
                    )
                ).one()
                other.status = RunPlanStepStatus.RUNNING
                session.add(other)
            elif case == "terminal-run":
                run = session.get(Run, owned["run_id"])
                run.status = RunStatus.ABORTED
                session.add(run)
            session.commit()
    error = mcp_client.call_tool_error("run.ownerAcquire", args)
    assert error["code"] in {-32007, -32008}
    with Session(owned["engine"]) as session:
        assert session.exec(select(WorkItemOwnership)).all() == []


def test_owner_request_bound_replay_and_stale_cached_fence(
    mcp_client: MCPClient,
    owned: dict,
) -> None:
    first = _acquire(mcp_client, owned, idempotency_key="fixture:acquire")
    replay = _acquire(mcp_client, owned, idempotency_key="fixture:acquire", response_mode="compact")
    assert replay["acquired"] is False
    assert replay["ownership"] == first["ownership"]
    changed = mcp_client.call_tool_error(
        "run.ownerAcquire",
        _arguments(
            owned,
            idempotency_key="fixture:acquire",
            owner_ref="fixture:another-worker",
        ),
    )
    assert "another request" in changed["data"]["detail"]
    checkpoint_args = _arguments(
        owned,
        **_fence(first["ownership"]),
        checkpoint_ref="evidence:criterion",
        idempotency_key="fixture:checkpoint",
    )
    checkpoint = mcp_client.call_tool_structured("run.ownerCheckpoint", checkpoint_args)
    same = mcp_client.call_tool_structured("run.ownerCheckpoint", checkpoint_args)
    assert same["data"] == checkpoint["data"]
    assert same["idempotency_replay"] is True
    stale = mcp_client.call_tool_error(
        "run.ownerAcquire",
        _arguments(
            owned,
            idempotency_key="fixture:acquire",
        ),
    )
    assert "stale" in stale["data"]["detail"]


@pytest.mark.parametrize(
    ("counter", "limit"),
    [("attempts_used", "attempt_limit"), ("failures_used", "failure_limit")],
)
def test_owner_checkpoint_replay_rechecks_current_canonical_counter_floor(
    mcp_client: MCPClient,
    owned: dict,
    counter: str,
    limit: str,
) -> None:
    first = _acquire(mcp_client, owned)["ownership"]
    arguments = _arguments(
        owned,
        **_fence(first),
        checkpoint_ref="evidence:criterion",
        idempotency_key="fixture:checkpoint-counter-floor",
    )
    checkpoint = mcp_client.call_tool_structured("run.ownerCheckpoint", arguments)
    row = checkpoint["data"]["ownership"]
    assert row["continuity_json"][counter] == 0
    unchanged = mcp_client.call_tool_structured("run.ownerCheckpoint", arguments)
    assert unchanged["data"] == checkpoint["data"]
    assert unchanged["idempotency_replay"] is True

    with Session(owned["engine"]) as session:
        ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
        metadata = deepcopy(ticket.metadata_json)
        metadata["ownership_continuity"][counter] = metadata["ownership_continuity"][limit]
    updated = mcp_client.call_tool_structured(
        "tracker.updateTicket",
        {
            "project_id": owned["project_id"],
            "ticket_key": "ownership-work",
            "patch_json": {"metadata_json": metadata},
            "actor": "fixture:canonical-accounting",
            "response_mode": "raw",
        },
    )
    assert updated["data"]["ticket"]["metadata_json"] == metadata
    with Session(owned["engine"]) as session:
        before = (
            RunRepository(session)
            .owner_get(project_id=owned["project_id"], canonical_item_id=owned["canonical_item_id"])
            .model_dump(mode="json")
        )
        assert before == row
        events_before = [event.id for event in session.exec(select(ProjectEvent)).all()]

    fresh_error = mcp_client.call_tool_error(
        "run.ownerCheckpoint",
        _arguments(
            owned,
            **_fence(row),
            checkpoint_ref="evidence:fresh-checkpoint-with-stale-counters",
        ),
    )
    replay_error = mcp_client.call_tool_error("run.ownerCheckpoint", arguments)
    with Session(owned["engine"]) as session:
        current = (
            RunRepository(session)
            .owner_get(project_id=owned["project_id"], canonical_item_id=owned["canonical_item_id"])
            .model_dump(mode="json")
        )
        ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
        assert current == before
        assert current["continuity_json"][counter] == 0
        assert ticket.metadata_json == metadata
        assert (
            ticket.metadata_json["ownership_continuity"][counter]
            == metadata["ownership_continuity"][limit]
        )
        assert [event.id for event in session.exec(select(ProjectEvent)).all()] == events_before
    assert fresh_error.get("code") == -32008, fresh_error
    assert replay_error.get("code") == fresh_error["code"], replay_error


def test_owner_continuity_cannot_reset_or_extend_budget(
    mcp_client: MCPClient,
    owned: dict,
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    consumed = mcp_client.call_tool_structured(
        "run.ownerConsume",
        _arguments(
            owned,
            **_fence(row),
            attempts_delta=1,
            failures_delta=1,
            evidence_ref="evidence:attempt-1",
        ),
    )["data"]["ownership"]
    assert consumed["continuity_json"]["effect_identity"] == "effect:stable"
    assert consumed["continuity_json"]["attempts_used"] == 1
    error = mcp_client.call_tool_error(
        "run.ownerConsume",
        _arguments(
            owned,
            **_fence(consumed),
            attempts_delta=-1,
            evidence_ref="evidence:bad-reset",
        ),
    )
    assert error["code"] == -32602
    error = mcp_client.call_tool_error(
        "run.ownerConsume",
        _arguments(
            owned,
            **_fence(consumed),
            attempt_limit=100,
            evidence_ref="evidence:bad-limit",
        ),
    )
    assert error["code"] == -32602
    exhausted = mcp_client.call_tool_structured(
        "run.ownerConsume",
        _arguments(
            owned,
            **_fence(consumed),
            failures_delta=2,
            evidence_ref="evidence:late-failures",
        ),
    )["data"]["ownership"]
    assert exhausted["continuity_json"]["failures_used"] == 3
    assert exhausted["continuity_json"]["stopped"] is True
    error = mcp_client.call_tool_error("run.ownerAcquire", _arguments(owned))
    assert "stop" in error["data"]["detail"]


def test_owner_commit_interruption_uses_readback_without_second_acquisition(
    mcp_client: MCPClient,
    owned: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_record(*args, **kwargs):
        raise RuntimeError("fixture: reply/cache interruption after commit")

    monkeypatch.setattr(OperationDispatcher, "_idempotency_record", failed_record)
    mcp_client.call_tool_error(
        "run.ownerAcquire",
        _arguments(
            owned,
            idempotency_key="fixture:interrupted",
        ),
    )
    monkeypatch.undo()
    inspected = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "response_mode": "raw",
        },
    )
    assert inspected["ownership"]["epoch"] == 1
    error = mcp_client.call_tool_error(
        "run.ownerAcquire",
        _arguments(
            owned,
            idempotency_key="fixture:interrupted",
        ),
    )
    assert "in-flight" in error["data"]["detail"]
    observed = _acquire(mcp_client, owned, idempotency_key="fixture:explicit-readback-intent")
    assert observed["acquired"] is False
    with Session(owned["engine"]) as session:
        assert len(session.exec(select(WorkItemOwnership)).all()) == 1


@pytest.mark.parametrize("state", ["confirmed_hang", "host_suspended", "accepted_completion"])
def test_owner_caller_cannot_manufacture_verified_observation(
    mcp_client: MCPClient,
    owned: dict,
    state: str,
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    error = mcp_client.call_tool_error(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=99999,
            observation=state,
            evidence_ref="fixture:unverified-claim",
        ),
    )
    assert error["code"] == -32602


def test_owner_reported_activity_and_terminal_attempt_do_not_accept_ticket(
    mcp_client: MCPClient,
    owned: dict,
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    with Session(owned["engine"]) as session:
        attempt = (
            RunRepository(session)
            .start(
                project_id=owned["project_id"],
                kind=RunKind.SKILL_RUN,
                parent_run_id=owned["run_id"],
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": owned["canonical_item_id"],
                        "owner_ref": owned["owner_ref"],
                        "epoch": row["epoch"],
                        "scope_digest": SCOPE,
                    }
                },
            )
            .data
        )
        attempt_id = attempt.id
        ticket_before = session.get(
            TrackerTicket, int(owned["canonical_item_id"].split(":")[1])
        ).status
    for state in ("running", "suspected_stall", "running", "review_ready"):
        result = mcp_client.call_tool_structured(
            "run.ownerRecordAttempt",
            _arguments(
                owned,
                **_fence(row),
                attempt_run_id=attempt_id,
                observation=state,
                evidence_ref=f"fixture:observed-{state}",
            ),
        )["data"]
        row = result["ownership"]
    finished = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            disposition="succeeded",
            evidence_ref="fixture:production-result",
        ),
    )["data"]
    assert finished["attempt"]["status"] == "success"
    with Session(owned["engine"]) as session:
        assert (
            session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1])).status
            == ticket_before
        )
    error = mcp_client.call_tool_error(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(finished["ownership"]),
            attempt_run_id=attempt_id,
            observation="running",
            evidence_ref="fixture:reactivate",
        ),
    )
    assert "terminal attempt" in error["data"]["detail"]


def _review_ready_owned_attempt(mcp_client: MCPClient, owned: dict) -> tuple[dict, int]:
    row = _acquire(mcp_client, owned)["ownership"]
    with Session(owned["engine"]) as session:
        attempt_id = (
            RunRepository(session)
            .start(
                project_id=owned["project_id"],
                kind=RunKind.SKILL_RUN,
                parent_run_id=owned["run_id"],
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": owned["canonical_item_id"],
                        "owner_ref": owned["owner_ref"],
                        "epoch": row["epoch"],
                        "scope_digest": SCOPE,
                    }
                },
            )
            .data.id
        )
    for observation in ("running", "review_ready"):
        result = mcp_client.call_tool_structured(
            "run.ownerRecordAttempt",
            _arguments(
                owned,
                **_fence(row),
                attempt_run_id=attempt_id,
                observation=observation,
                evidence_ref=f"fixture:{observation}",
            ),
        )
        row = result["data"]["ownership"]
    return row, attempt_id


def _owned_attempt_snapshot(owned: dict, attempt_id: int) -> dict:
    with Session(owned["engine"]) as session:
        return {
            "ownership": (
                RunRepository(session)
                .owner_get(
                    project_id=owned["project_id"], canonical_item_id=owned["canonical_item_id"]
                )
                .model_dump(mode="json")
            ),
            "attempt": session.get(Run, attempt_id).model_dump(mode="json"),
            "ticket": session.get(
                TrackerTicket, int(owned["canonical_item_id"].split(":")[1])
            ).model_dump(mode="json"),
            "events": [event.id for event in session.exec(select(ProjectEvent)).all()],
        }


@pytest.mark.parametrize("observation", ["blocked", "scope_drift"])
def test_owner_successful_production_can_record_review_rejection_without_reactivation(
    mcp_client: MCPClient,
    owned: dict,
    observation: str,
) -> None:
    row, attempt_id = _review_ready_owned_attempt(mcp_client, owned)
    ticket_before = _owned_attempt_snapshot(owned, attempt_id)["ticket"]
    finished = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            disposition="succeeded",
            evidence_ref="fixture:production-result",
        ),
    )["data"]
    assert finished["attempt"]["status"] == "success"
    assert finished["attempt"]["ended_at"] is not None
    disposition = deepcopy(finished["attempt"]["metadata_json"]["ownership_disposition"])
    assert disposition["state"] == "succeeded"
    annotated = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(finished["ownership"]),
            attempt_run_id=attempt_id,
            observation=observation,
            evidence_ref="fixture:review-rejection",
        ),
    )
    assert "ownership" in annotated.get("data", {}), annotated
    result = annotated["data"]
    assert result["attempt"]["status"] == "success"
    assert result["attempt"]["ended_at"] == finished["attempt"]["ended_at"]
    assert result["attempt"]["metadata_json"]["ownership_disposition"] == disposition
    reported = result["attempt"]["metadata_json"]["ownership_observation"]
    assert reported["state"] == observation
    assert reported["evidence_ref"] == "fixture:review-rejection"
    assert result["ownership"]["epoch"] == finished["ownership"]["epoch"]
    assert result["ownership"]["revision"] == finished["ownership"]["revision"] + 1
    assert result["ownership"]["continuity_json"] == finished["ownership"]["continuity_json"]
    before = _owned_attempt_snapshot(owned, attempt_id)
    assert before["ticket"] == ticket_before
    assert before["attempt"]["status"] == "success"
    for forbidden in (
        {"observation": "running", "clearance_ref": "fixture:clearance"},
        {"observation": "waiting", "clearance_ref": "fixture:clearance"},
        {"disposition": "failed"},
    ):
        error = mcp_client.call_tool_error(
            "run.ownerRecordAttempt",
            _arguments(
                owned,
                **_fence(result["ownership"]),
                attempt_run_id=attempt_id,
                evidence_ref="fixture:forbidden-terminal-change",
                **forbidden,
            ),
        )
        assert error.get("code") == -32008, error
        assert _owned_attempt_snapshot(owned, attempt_id) == before


@pytest.mark.parametrize("disposition", ["cancelled", "failed"])
@pytest.mark.parametrize("observation", ["blocked", "scope_drift"])
def test_owner_unsuccessful_terminal_attempt_still_rejects_review_annotations(
    mcp_client: MCPClient,
    owned: dict,
    disposition: str,
    observation: str,
) -> None:
    row, attempt_id = _review_ready_owned_attempt(mcp_client, owned)
    finished = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            disposition=disposition,
            evidence_ref=f"fixture:{disposition}",
        ),
    )["data"]
    assert (
        finished["attempt"]["status"] == {"cancelled": "aborted", "failed": "failed"}[disposition]
    )
    before = _owned_attempt_snapshot(owned, attempt_id)
    error = mcp_client.call_tool_error(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(finished["ownership"]),
            attempt_run_id=attempt_id,
            observation=observation,
            evidence_ref="fixture:forbidden-terminal-annotation",
        ),
    )
    assert error.get("code") == -32008, error
    assert _owned_attempt_snapshot(owned, attempt_id) == before


def test_owner_reassignment_requires_backend_evidence_and_retains_continuity(
    mcp_client: MCPClient,
    owned: dict,
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    args = _arguments(owned, **_fence(row), new_owner_ref="fixture:replacement")
    error = mcp_client.call_tool_error("run.ownerReassign", args)
    assert "quiescence" in error["data"]["detail"]
    with Session(owned["engine"]) as session:
        from tests.integration.test_controlled_process_terminal import certify_empty

        repo = RunRepository(session)
        current = repo.owner_get(
            project_id=owned["project_id"], canonical_item_id=owned["canonical_item_id"]
        )
        certified = certify_empty(repo, current).model_dump()
    replacement = mcp_client.call_tool_structured(
        "run.ownerReassign",
        _arguments(
            owned,
            **_fence(certified),
            new_owner_ref="fixture:replacement",
        ),
    )["data"]["ownership"]
    assert replacement["epoch"] == 2
    assert replacement["previous_owner_ref"] == owned["owner_ref"]
    assert replacement["continuity_json"] == row["continuity_json"]
    error = mcp_client.call_tool_error(
        "run.ownerCheckpoint",
        _arguments(
            owned,
            **_fence(row),
            checkpoint_ref="fixture:stale-owner",
        ),
    )
    assert "stale" in error["data"]["detail"]


@pytest.mark.parametrize("change", ["stop", "scope", "limit"])
def test_owner_current_canonical_authority_blocks_checkpoint(
    mcp_client: MCPClient,
    owned: dict,
    change: str,
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    with Session(owned["engine"]) as session:
        ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
        metadata = deepcopy(ticket.metadata_json)
        key, value = {
            "stop": ("stopped", True),
            "scope": ("scope_digest", "b" * 64),
            "limit": ("attempt_limit", 100),
        }[change]
        metadata["ownership_continuity"][key] = value
        ticket.metadata_json = metadata
        session.add(ticket)
        session.commit()
    error = mcp_client.call_tool_error(
        "run.ownerCheckpoint",
        _arguments(
            owned,
            **_fence(row),
            checkpoint_ref="fixture:changed-authority",
        ),
    )
    assert error["code"] == -32008
    inspected = mcp_client.call_tool_structured(
        "run.ownerGet",
        {
            "project_id": owned["project_id"],
            "canonical_item_id": owned["canonical_item_id"],
            "response_mode": "raw",
        },
    )
    assert inspected["authority_valid"] is False
    assert inspected["ownership"] == row
    assert inspected["captured_at"]


def test_owner_revoked_grant_during_call_rolls_back_before_audit(
    mcp_client: MCPClient,
    owned: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquire = RunRepository.owner_acquire

    def revoke_before_write(repo, **kwargs):
        with Session(owned["engine"]) as other:
            plan = other.get(RunPlan, owned["run_plan_id"])
            grants = deepcopy(plan.grant_snapshot_json)
            grants["mcp_tool_grants"] = [
                entry for entry in grants["mcp_tool_grants"] if entry["tool"] != "run.ownerAcquire"
            ]
            plan.grant_snapshot_json = grants
            other.add(plan)
            other.commit()
        return acquire(repo, **kwargs)

    monkeypatch.setattr(RunRepository, "owner_acquire", revoke_before_write)
    error = mcp_client.call_tool_error("run.ownerAcquire", _arguments(owned))
    assert error["code"] == -32007
    with Session(owned["engine"]) as session:
        assert session.exec(select(WorkItemOwnership)).all() == []
        assert (
            session.exec(
                select(ProjectEvent).where(ProjectEvent.event_type == "WORK_ITEM_OWNER_ACQUIRED")
            ).all()
            == []
        )


def test_owner_rest_and_cli_use_current_grants(
    mcp_client: MCPClient,
    owned: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = mcp_client.test_client
    args = _arguments(owned)
    args.pop("run_token")
    headers = {"Authorization": f"Bearer {mcp_client.auth_token}"}
    denied = client.post(
        "/api/v1/operations/run.ownerAcquire/call",
        json={"arguments": args},
        headers=headers,
    )
    assert denied.status_code == 403
    statuses = []

    def in_process_request(method, path, *, body=None, **kwargs):
        response = client.request(
            method, path, json=body, headers=headers | {"X-StackOS-Client-Surface": "cli"}
        )
        statuses.append(response.status_code)
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", in_process_request)
    input_file = tmp_path / "cli-owner.json"
    input_file.write_text(json.dumps(args))
    runner = CliRunner()
    denied_cli = runner.invoke(
        cli_app, ["ops", "call", "run.ownerAcquire", "--input", str(input_file)]
    )
    assert denied_cli.exit_code != 0
    assert statuses == [403]
    input_file.write_text(json.dumps(_arguments(owned)))
    accepted_cli = runner.invoke(
        cli_app, ["ops", "call", "run.ownerAcquire", "--input", str(input_file)]
    )
    assert accepted_cli.exit_code == 0, accepted_cli.stdout
    receipt = json.loads(accepted_cli.stdout)
    assert receipt["data"]["ownership"]["canonical_item_id"] == owned["canonical_item_id"]
    assert receipt["run_id"] == owned["run_id"]
    assert statuses == [403, 200]


@pytest.mark.parametrize("name", ["run.start", "run.finish"])
def test_existing_run_operations_cannot_forge_owned_observation(
    mcp_client: MCPClient,
    owned: dict,
    name: str,
) -> None:
    args = {
        "project_id": owned["project_id"],
        "metadata_json": {"ownership_observation": {"state": "accepted_completion"}},
    }
    if name == "run.start":
        args["kind"] = "skill-run"
    else:
        args.update(run_id=owned["run_id"], status="success")
    error = mcp_client.call_tool_error(name, args)
    assert error["code"] == -32602
    assert "fenced attempt owner" in error["data"]["detail"]


def test_reported_scope_drift_is_retained_without_admitting_changed_scope(
    mcp_client: MCPClient,
    owned: dict,
) -> None:
    row = _acquire(mcp_client, owned)["ownership"]
    with Session(owned["engine"]) as session:
        attempt_id = (
            RunRepository(session)
            .start(
                project_id=owned["project_id"],
                kind=RunKind.SKILL_RUN,
                parent_run_id=owned["run_id"],
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": owned["canonical_item_id"],
                        "owner_ref": owned["owner_ref"],
                        "epoch": row["epoch"],
                        "scope_digest": SCOPE,
                    }
                },
            )
            .data.id
        )
    observed = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(row),
            attempt_run_id=attempt_id,
            observation="running",
            evidence_ref="fixture:reported-running",
        ),
    )["data"]["ownership"]
    with Session(owned["engine"]) as session:
        ticket = session.get(TrackerTicket, int(owned["canonical_item_id"].split(":")[1]))
        metadata = deepcopy(ticket.metadata_json)
        metadata["ownership_continuity"]["scope_digest"] = "b" * 64
        ticket.metadata_json = metadata
        session.add(ticket)
        session.commit()
    drift = mcp_client.call_tool_structured(
        "run.ownerRecordAttempt",
        _arguments(
            owned,
            **_fence(observed),
            attempt_run_id=attempt_id,
            observation="scope_drift",
            evidence_ref="fixture:reported-drift",
        ),
    )["data"]
    assert drift["attempt"]["metadata_json"]["ownership_observation"]["state"] == "scope_drift"
    error = mcp_client.call_tool_error(
        "run.ownerCheckpoint",
        _arguments(
            owned,
            **_fence(drift["ownership"]),
            checkpoint_ref="fixture:invalidated-candidate",
        ),
    )
    assert error["code"] == -32008
