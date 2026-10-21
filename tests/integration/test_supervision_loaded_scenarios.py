"""Remaining loaded supervision scenarios. Source only until Main dispatch.

Uses the reviewed owned-daemon fixture and real SDK; no bridge, providers,
automatic action retry, production monkeypatch or second runtime controller.
"""

import asyncio
import os
from copy import deepcopy

import pytest
from sqlmodel import Session, select

from stackos import controlled_process_clock as clock
from stackos.db.models import ActionCall, ApprovalRequest, RunPlan, TrackerTask, WorkItemOwnership
from tests.integration import test_controlled_command_loaded as loaded
from tests.support.supervision_loaded import (
    fence,
    occurrence_snapshot,
    ownership_case,
    require_denied,
    require_ok,
    run_scenario,
    sdk,
    verify_occurrence,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("STACKOS_RUN_LOADED_SUPERVISION_PROOF") != "1",
    reason="Explicit owned-daemon/native supervision proof requires opt-in",
)


def test_loaded_trusted_occurrence_race_status_and_preservation(tmp_path, monkeypatch):
    async def scenario(settings, engine, base, env, work, audit, owner):
        case = ownership_case(engine)
        sentinel = work / "preserved-evidence"
        sentinel.write_bytes(b"preserved synthetic evidence\n")
        async with sdk(settings, base) as call:
            replies = await asyncio.gather(
                call("run.ownerAcquire", case.args), call("run.ownerAcquire", case.args)
            )
            values = [require_ok(reply)["data"] for reply in replies]
            assert sorted(value["acquired"] for value in values) == [False, True]
            snapshot = occurrence_snapshot(engine, case)
            verify_occurrence(snapshot, case, sentinel)
            read = dict(project_id=case.project, canonical_item_id=case.item, response_mode="raw")
            first = require_ok(await call("run.ownerGet", read))
            second = require_ok(await call("run.ownerGet", read))
            assert loaded._without_capture(first) == loaded._without_capture(second)
            assert occurrence_snapshot(engine, case) == snapshot
            assert first["status"]["acceptance_state"] == "not_evaluated"
        audit.update(
            status="passed",
            scenario="T2/T5/T7",
            occurrence=snapshot["events"],
            same_revision=True,
            preserved=True,
        )

    run_scenario(tmp_path, monkeypatch, scenario)


@pytest.mark.parametrize("continuity", ["stopped", "exhausted"])
@pytest.mark.parametrize("control", ["intact", "reset-continuity"])
def test_loaded_explicit_restart_preserves_original_continuity(
    tmp_path, monkeypatch, continuity, control
):
    async def scenario(settings, engine, base, env, work, audit, owner):
        case = ownership_case(engine)
        sentinel = work / "original-evidence"
        sentinel.write_bytes(b"preserved synthetic evidence\n")
        async with sdk(settings, base) as call:
            acquired = require_ok(await call("run.ownerAcquire", case.args))["data"]["ownership"]
            operation = "run.ownerStop" if continuity == "stopped" else "run.ownerConsume"
            extra = (
                {"reason_ref": "synthetic:original-stop"}
                if continuity == "stopped"
                else {"attempts_delta": 2, "evidence_ref": "synthetic:original-budget"}
            )
            require_ok(await call(operation, case.args | fence(acquired) | extra))
            original = occurrence_snapshot(engine, case)
            require_denied(await call("run.ownerAcquire", case.args))
        # Every launch/reap is explicit and uses the same retained owner.
        await asyncio.to_thread(owner.restart)
        assert audit["daemon_launches"] == 2
        if control == "reset-continuity":
            # Disposable persisted-control mutant, after healthy valid baseline.
            # The real loaded ownerGet producer and invariant verifier are unchanged.
            with Session(engine) as session:
                task = session.get(TrackerTask, case.task)
                native = session.exec(
                    select(WorkItemOwnership).where(WorkItemOwnership.project_id == case.project)
                ).one()
                reset = {"stopped": False} if continuity == "stopped" else {"attempts_used": 0}
                task.metadata_json = task.metadata_json | {
                    "ownership_continuity": task.metadata_json["ownership_continuity"] | reset
                }
                native.continuity_json = native.continuity_json | reset
                session.add_all([task, native])
                session.commit()
        async with sdk(settings, base) as call:
            status = require_ok(
                await call(
                    "run.ownerGet",
                    {
                        "project_id": case.project,
                        "canonical_item_id": case.item,
                        "response_mode": "raw",
                    },
                )
            )
            assert status["ownership"]["canonical_item_id"] == case.item
            current = occurrence_snapshot(engine, case)

            def verify():
                if continuity == "stopped":
                    verify_occurrence(current, case, sentinel)
                assert current["events"] == original["events"]
                for key in (
                    "stopped",
                    "attempts_used",
                    "failures_used",
                    "budget_ref",
                    "history_ref",
                    "stop_scope_ref",
                ):
                    assert (
                        current["owner"]["continuity_json"][key]
                        == original["owner"]["continuity_json"][key]
                    ), "original continuity reset across restart"

            if control == "reset-continuity":
                with pytest.raises(AssertionError, match="original continuity reset"):
                    verify()
            else:
                verify()
                require_denied(await call("run.ownerAcquire", case.args))
                assert occurrence_snapshot(engine, case) == current
            assert sentinel.read_bytes() == b"preserved synthetic evidence\n"
        audit.update(
            status="passed",
            scenario="T4/I6",
            continuity=continuity,
            control=control,
            explicit_restarts=1,
        )

    run_scenario(tmp_path, monkeypatch, scenario)


def test_loaded_read_activity_cannot_renew_original_admission_deadline(tmp_path, monkeypatch):
    async def scenario(settings, engine, base, env, work, audit, owner):
        case = loaded._setup_case(settings, engine)
        with Session(engine) as session:
            decision = deepcopy(session.exec(select(ApprovalRequest)).one().decision_json)
        policy = decision["controlled_host"]
        read = dict(project_id=case.project, canonical_item_id=case.item, response_mode="raw")
        async with sdk(settings, base) as call:
            first = require_ok(await call("run.ownerGet", read))
            sample = clock._sample_clock()
            assert sample.status == "known" and sample.boot_uuid == policy["boot_uuid"]
            remaining = max(0, policy["deadline_continuous_ns"] - sample.continuous_ns)
            await asyncio.sleep(remaining / 1e9)
            assert clock._sample_clock().continuous_ns >= policy["deadline_continuous_ns"]
            second = require_ok(await call("run.ownerGet", read))
            assert loaded._without_capture(first) == loaded._without_capture(second)
            assert second["status"]["acceptance_state"] == "not_evaluated"
            before = loaded._rows(engine)
            require_denied(await call("action.execute", case.args))
            after = loaded._rows(engine)
            assert after["Run"] == before["Run"]
            assert after["WorkItemOwnership"] == before["WorkItemOwnership"]
            assert len(after["ActionCall"]) <= len(before["ActionCall"]) + 1
            for row in after["ActionCall"]:
                if row["response_json"] is not None:
                    assert row["response_json"]["host_dispatched"] is False
            assert not case.command.report_directory.exists()
        with Session(engine) as session:
            assert session.exec(select(ApprovalRequest)).one().decision_json == decision
        audit.update(
            status="passed",
            scenario="T5/I5",
            scope=(
                "actual pre-admission ceiling; "
                "independent in-flight watchdog proof reused separately"
            ),
            deadline_unchanged=True,
        )

    run_scenario(tmp_path, monkeypatch, scenario)


@pytest.mark.parametrize("denial", ["stale-epoch", "stale-checkpoint", "current-grant"])
def test_loaded_checkpoint_requires_current_fence_and_grant(tmp_path, monkeypatch, denial):
    async def scenario(settings, engine, base, env, work, audit, owner):
        case = ownership_case(engine)
        async with sdk(settings, base) as call:
            acquired = require_ok(await call("run.ownerAcquire", case.args))["data"]["ownership"]
            arguments = (
                case.args | fence(acquired) | {"checkpoint_ref": "synthetic:observed-candidate"}
            )
            baseline = require_ok(await call("run.ownerCheckpoint", arguments))
            before = occurrence_snapshot(engine, case)
            denied = arguments | {"checkpoint_ref": "synthetic:stale-candidate"}
            if denial == "stale-epoch":
                denied["expected_epoch"] += 1
            elif denial == "current-grant":
                denied.update(fence(before["owner"]))
                with Session(engine) as session:
                    plan = session.get(RunPlan, case.plan)
                    snapshot = deepcopy(plan.grant_snapshot_json)
                    snapshot["mcp_tool_grants"] = [
                        grant
                        for grant in snapshot["mcp_tool_grants"]
                        if grant["tool"] != "run.ownerCheckpoint"
                    ]
                    plan.grant_snapshot_json = snapshot
                    session.add(plan)
                    session.commit()
            require_denied(await call("run.ownerCheckpoint", denied))
            assert occurrence_snapshot(engine, case) == before
        audit.update(status="passed", scenario="T3", denial=denial, baseline=baseline)

    run_scenario(tmp_path, monkeypatch, scenario)


def test_loaded_duplicate_report_admission_has_one_original_attempt(tmp_path, monkeypatch):
    async def scenario(settings, engine, base, env, work, audit, owner):
        case = loaded._setup_case(settings, engine)
        async with sdk(settings, base) as call:
            replies = await asyncio.gather(
                call("action.execute", case.args), call("action.execute", case.args)
            )
            successes = [reply.value for reply in replies if not reply.error]
            assert successes and any(
                value["data"]["output_json"]["status"] == "succeeded" for value in successes
            )
            with Session(engine) as session:
                calls = session.exec(
                    select(ActionCall).where(ActionCall.project_id == case.project)
                ).all()
                owner = session.exec(
                    select(WorkItemOwnership).where(WorkItemOwnership.project_id == case.project)
                ).one()
                assert len(calls) == 1
                assert owner.continuity_json["attempts_used"] == 1
                assert len(owner.continuity_json["launch_reservations"]) == 1
            before = loaded._rows(engine)
            replay = require_ok(await call("action.execute", case.args))
            assert replay["data"]["replayed"] is True
            assert loaded._rows(engine) == before
            assert replay["data"]["output_json"]["work_accepted"] is False
        audit.update(
            status="passed", scenario="T2/I1", one_original_call=True, one_charged_attempt=True
        )

    run_scenario(tmp_path, monkeypatch, scenario)
