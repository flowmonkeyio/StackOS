"""Parent-absent source acceptance: real loop, operations, persistence and admission.

Only the worker process/pipe boundary is deterministic. No model, socket,
installed daemon, fabricated control-plane rows or unrestricted test grants.
"""

import asyncio
import json
import socket

import pytest
from sqlmodel import Session, select

from stackos.actions.codex_worker import CodexProcessTransport
from stackos.db.models import ActionCall, RunPlan, RunPlanStep, TrackerTicket, WorkItemOwnership
from stackos.jobs.agent_dispatch import AdmittedTicket, AgentDispatchLoop, AgentDispatchScope
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_codex_action_runtime import (
    ACTION,
    APPROVAL,
    REVISION,
    SCOPE,
    CanonicalRuntime,
    Pipes,
    Process,
    fixture_packet,
    fixture_profile,
)


class PerProcessPipes(Pipes):
    def __init__(self):
        super().__init__()
        self.streams = set()

    def read(self, stream):
        self.sent = stream in self.streams
        result = super().read(stream)
        if result:
            self.streams.add(stream)
        return result


async def prepare(runtime, *, action_grant=True, bind=True, start=True):
    project = await runtime.call(
        "project.create",
        slug="parent-absent",
        name="Parent absent source acceptance",
        domain="example.invalid",
    )
    runtime.project_id = project["id"]
    await runtime.call("tracker.createTask", key="accepted-work", title="Explicitly admitted work")
    items = {}
    for key in ("stalled", "independent", "capacity-wait"):
        result = await runtime.call(
            "tracker.createTicket",
            task_key="accepted-work",
            key=key,
            title=key,
            metadata_json={"ownership_continuity": fixture_packet()["continuity"]},
        )
        items[key] = f"ticket:{result['ticket']['id']}"
    grants = [
        {"step_id": "work", "tool": tool, "targets": list(items.values())}
        for tool in ("run.ownerAcquire", "run.ownerRecordAttempt")
    ]
    if action_grant:
        grants.append({"step_id": "work", "tool": "action.execute", "action_refs": [ACTION]})
    plan = await runtime.call(
        "runPlan.create",
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "parent-absent",
            "title": "Parent absent source acceptance",
            "steps": [
                {
                    "id": "work",
                    "title": "Admitted work",
                    "action_refs": [ACTION],
                    "approval_refs": [APPROVAL],
                }
            ],
            "approvals": [{"key": APPROVAL, "step_id": "work"}],
            "grants": {
                "mcp_tool_grants": grants,
                "action_contracts": [
                    {"key": "admitted", "action": ACTION, "approval_ref": APPROVAL}
                ],
            },
        },
    )
    runtime.plan_id = plan["id"]
    with Session(runtime.engine) as session:
        runtime.step_id = (
            session.exec(
                select(RunPlanStep).where(
                    RunPlanStep.run_plan_id == runtime.plan_id, RunPlanStep.step_id == "work"
                )
            )
            .one()
            .id
        )
    # Exact existing-ticket binding uses the canonical draft-only operation.
    # It must not be replaced by raw rows, predicted IDs, or weaker owner grants.
    if bind:
        for key in items:
            await runtime.call(
                "tracker.updateTicket",
                ticket_key=key,
                patch_json={"run_plan_id": runtime.plan_id, "run_plan_step_id": runtime.step_id},
            )
    await runtime.call(
        "runPlan.update",
        run_plan_id=runtime.plan_id,
        approval_key=APPROVAL,
        approval_status="approved",
        decided_by="isolated-fixture-operator",
        decision_json={"unknown_cost": "admitted"},
    )
    if start:
        started = await runtime.call("runPlan.start", run_plan_id=runtime.plan_id)
        runtime.token, runtime.run_id = started["run_token"], started["run_id"]
        await runtime.call(
            "runPlan.claimStep", controlled=True, run_plan_id=runtime.plan_id, step_id="work"
        )
    return items


def receipts(runtime):
    with Session(runtime.engine) as session:
        return {
            ticket.key: (ticket.context_json or {}).get("agent_dispatch", {})
            for ticket in session.exec(select(TrackerTicket))
        }


async def observe_until(predicate, loop, *, seconds=10):
    async with asyncio.timeout(seconds):
        while not predicate():
            assert loop.last_error is None, loop.last_error
            await asyncio.sleep(0.01)


def make_scope(runtime, *, capacity=2):
    return AgentDispatchScope(
        project_id=runtime.project_id,
        run_plan_id=runtime.plan_id,
        step_id="work",
        controller_run_id=runtime.run_id,
        run_token=runtime.token,
        source_revision=REVISION,
        tickets=tuple(
            AdmittedTicket(key, SCOPE, "Return fixture result without tools.")
            for key in ("stalled", "independent", "capacity-wait")
        ),
        operator_ref="Adi",
        capacity=capacity,
        interval_seconds=0.01,
        stall_seconds=2,
    )


def test_parent_absent_loop_escalates_and_dispatches_independent_capacity(tmp_path, monkeypatch):
    import stackos.actions.codex_worker as codex

    def forbidden(*args, **kwargs):
        raise AssertionError("real process/network forbidden")

    monkeypatch.setattr(codex.subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    profile = fixture_profile(tmp_path / "pins")
    births = []
    release = asyncio.Event()

    def acquire(argv, **kwargs):
        births.append(argv)
        return Process()

    class Workers(CodexProcessTransport):
        async def observe(self, slot):
            if slot.binding.canonical_item_id == items["stalled"]:
                await release.wait()  # deterministic backend stall, not a scheduler transition
            await super().observe(slot)

    transport = Workers(profile, process_factory=acquire, io=PerProcessPipes())
    runtime = CanonicalRuntime(tmp_path / "runtime", profile, transport)
    items = {}

    async def scenario():
        nonlocal items
        items = await prepare(runtime)
        scope = make_scope(runtime)
        loop = AgentDispatchLoop(
            engine=runtime.engine, settings=runtime.settings, services=runtime.services, scope=scope
        )
        loop.start()  # the only scheduler stimulus; parent is absent from now on
        try:
            await observe_until(
                lambda: (
                    receipts(runtime)["stalled"].get("state") == "escalated"
                    and receipts(runtime)["independent"].get("state") == "review_ready"
                ),
                loop,
            )
            before_restart = receipts(runtime)
            assert len(births) == 2
            assert not before_restart["capacity-wait"]
            assert before_restart["stalled"]["outcome_unknown"] is True
            assert before_restart["stalled"]["notification_delivery"] == "unconfirmed"
            with Session(runtime.engine) as session:
                tickets = {row.key: row for row in session.exec(select(TrackerTicket))}
                failed = tickets["stalled"]
                rca_key = before_restart["stalled"]["rca_ticket_key"]
                assert tickets[rca_key].status == "not-started"
                brief = TrackerRepository(session).brief(
                    project_id=runtime.project_id, ticket_key="stalled"
                )
                assert rca_key in brief.ticket.dependency_keys
                assert "independent review" in " ".join(tickets[rca_key].definition_of_done_json)
                assert failed.status not in {"complete", "failed"}
                assert not tickets["independent"].completion_evidence_json
                owners = list(session.exec(select(WorkItemOwnership)))
                assert len(owners) == 2
                assert all(owner.continuity_json["attempts_used"] == 1 for owner in owners)
                assert all(owner.quiescence_ref is None for owner in owners)
            # Restart just the selection lifecycle: same durable state, original
            # command owner still retained. It cannot reuse either occupied slot.
            await loop.stop()
            restarted = AgentDispatchLoop(
                engine=runtime.engine,
                settings=runtime.settings,
                services=runtime.services,
                scope=scope,
            )
            restarted.start()
            try:
                await asyncio.sleep(0.08)
                assert restarted.last_error is None
                assert len(births) == 2
                assert receipts(runtime) == before_restart
                with Session(runtime.engine) as session:
                    plan = session.get(RunPlan, runtime.plan_id)
                    assert plan.metadata_json["agent_dispatch"]["occupied"] == 2
                    assert (
                        "occupied"
                        in plan.metadata_json["agent_dispatch"]["reasons"]["capacity-wait"]
                    )
                    calls = list(session.exec(select(ActionCall)))
                    evidence = {
                        "classification": "deterministic parent-absent source acceptance; no model",
                        "project_id": runtime.project_id,
                        "plan_id": runtime.plan_id,
                        "controller_run_id": runtime.run_id,
                        "action_call_ids": [call.id for call in calls],
                        "births": len(births),
                        "receipts": before_restart,
                        "no_ready_reasons": plan.metadata_json["agent_dispatch"],
                    }
                (tmp_path / "parent-absent-evidence.json").write_text(
                    json.dumps(evidence, indent=2)
                )
            finally:
                await restarted.stop()
        finally:
            await loop.stop()
            release.set()  # backend finishes original work; no retry or new process
            await asyncio.gather(*loop._launches.values(), return_exceptions=True)
            await runtime.owner.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "missing-pair",
        "bool-id",
        "mixed-patch",
        "missing-step",
        "foreign-project",
        "mismatched-step",
        "started-plan",
        "started-ticket",
        "rebind",
        "mirror",
        "attempt",
        "dry-run",
    ],
)
def test_one_time_ticket_binding_rejects_unsafe_context(tmp_path, case):
    runtime = CanonicalRuntime(tmp_path / "runtime", None)

    async def scenario():
        items = await prepare(runtime, bind=False, start=False)
        patch = {"run_plan_id": runtime.plan_id, "run_plan_step_id": runtime.step_id}
        key = "stalled"
        dry_run = False
        if case == "missing-pair":
            patch.pop("run_plan_step_id")
        elif case == "bool-id":
            patch["run_plan_id"] = True
        elif case == "mixed-patch":
            patch["title"] = "must not mutate"
        elif case == "missing-step":
            patch["run_plan_step_id"] = 99999
        elif case in {"foreign-project", "mismatched-step"}:
            if case == "foreign-project":
                other = await runtime.call(
                    "project.create", slug="other", name="Other", domain="example.invalid"
                )
                project_id = other["id"]
            else:
                project_id = runtime.project_id
            other_plan = await runtime.call(
                "runPlan.create",
                project_id=project_id,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": "other",
                    "title": "Other",
                    "steps": [{"id": "work", "title": "Other work"}],
                },
            )
            with Session(runtime.engine) as session:
                other_step = session.exec(
                    select(RunPlanStep).where(RunPlanStep.run_plan_id == other_plan["id"])
                ).one()
                patch["run_plan_step_id"] = other_step.id
            if case == "foreign-project":
                patch["run_plan_id"] = other_plan["id"]
        elif case == "started-plan":
            await runtime.call("runPlan.start", run_plan_id=runtime.plan_id)
        elif case == "started-ticket":
            await runtime.call(
                "tracker.updateTicket", ticket_key=key, patch_json={"status": "in-progress"}
            )
        elif case == "rebind":
            await runtime.call("tracker.updateTicket", ticket_key=key, patch_json=patch)
        elif case == "mirror":
            key = f"workflow-{runtime.plan_id}-work"
        elif case == "attempt":
            await runtime.call(
                "run.start",
                kind="skill-run",
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": items["stalled"],
                        "owner_ref": "prior-owner",
                        "epoch": 1,
                        "scope_digest": SCOPE,
                    }
                },
            )
        elif case == "dry-run":
            dry_run = True
        with Session(runtime.engine) as session:
            before = (
                TrackerRepository(session)
                .brief(project_id=runtime.project_id, ticket_key=key)
                .ticket
            )
            grants = session.get(RunPlan, runtime.plan_id).grant_snapshot_json
        with pytest.raises((ConflictError, ValidationError)):
            await runtime.call(
                "tracker.updateTicket", ticket_key=key, patch_json=patch, dry_run=dry_run
            )
        with Session(runtime.engine) as session:
            after = (
                TrackerRepository(session)
                .brief(project_id=runtime.project_id, ticket_key=key)
                .ticket
            )
            assert after == before
            assert session.get(RunPlan, runtime.plan_id).grant_snapshot_json == grants

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["stopped-owner", "foreign-owner", "crowded"])
def test_item_local_fences_do_not_stop_independent_capacity(tmp_path, monkeypatch, case):
    import stackos.actions.codex_worker as codex

    def forbidden(*args, **kwargs):
        raise AssertionError("real process/network forbidden")

    monkeypatch.setattr(codex.subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    profile = fixture_profile(tmp_path / "pins")
    births = []

    def acquire(argv, **kwargs):
        births.append(argv)
        return Process()

    runtime = CanonicalRuntime(
        tmp_path / "runtime",
        profile,
        CodexProcessTransport(profile, process_factory=acquire, io=PerProcessPipes()),
    )

    async def scenario():
        items = await prepare(runtime)
        if case == "stopped-owner":
            continuity = {**fixture_packet()["continuity"], "stopped": True}
            await runtime.call(
                "tracker.updateTicket",
                controlled=True,
                ticket_key="stalled",
                patch_json={"metadata_json": {"ownership_continuity": continuity}},
            )
        elif case == "foreign-owner":
            await runtime.call(
                "run.ownerAcquire",
                controlled=True,
                canonical_item_id=items["stalled"],
                owner_ref="other-live-owner",
                scope_digest=SCOPE,
            )
        else:
            for index in range(51):
                await runtime.call(
                    "tracker.createTicket",
                    controlled=True,
                    task_key="accepted-work",
                    key=f"outside-{index}",
                    title="Out of admitted scope",
                    priority_key="p0",
                )
        loop = AgentDispatchLoop(
            engine=runtime.engine,
            settings=runtime.settings,
            services=runtime.services,
            scope=make_scope(runtime, capacity=1 if case == "stopped-owner" else 2),
        )
        loop.start()
        try:
            await observe_until(
                lambda: receipts(runtime)["independent"].get("state") == "review_ready", loop
            )
            assert len(births) == (2 if case == "crowded" else 1)
            states = receipts(runtime)
            assert not states["capacity-wait"]
            if case == "stopped-owner":
                assert states["stalled"]["state"] == "admission_rejected"
            elif case == "foreign-owner":
                assert not states["stalled"]
            assert not any(key.startswith("dispatch-rca") for key in states)
            with Session(runtime.engine) as session:
                owners = list(session.exec(select(WorkItemOwnership)))
                assert {owner.canonical_item_id for owner in owners} <= set(items.values())
        finally:
            await loop.stop()
            await asyncio.gather(*loop._launches.values(), return_exceptions=True)
            await runtime.owner.shutdown()

    asyncio.run(scenario())


def test_inactive_step_fails_closed_without_unscoped_fallback(tmp_path):
    runtime = CanonicalRuntime(tmp_path / "runtime", None)

    async def scenario():
        await prepare(runtime)
        await runtime.call(
            "runPlan.recordStep",
            controlled=True,
            run_plan_id=runtime.plan_id,
            step_id="work",
            status="blocked",
            error="explicit fixture approval wait",
        )
        loop = AgentDispatchLoop(
            engine=runtime.engine,
            settings=runtime.settings,
            services=runtime.services,
            scope=make_scope(runtime),
        )
        await asyncio.wait_for(loop.start(), timeout=2)
        assert loop.last_error == "ToolNotGrantedError"
        with Session(runtime.engine) as session:
            assert not list(session.exec(select(ActionCall)))
            assert not list(session.exec(select(WorkItemOwnership)))
            assert (
                session.exec(select(RunPlanStep).where(RunPlanStep.id == runtime.step_id))
                .one()
                .status
                == "blocked"
            )
        await loop.stop()

    asyncio.run(scenario())
