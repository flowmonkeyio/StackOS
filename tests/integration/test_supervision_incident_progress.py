"""Opt-in incident/progress and changing-diff proofs, never a runtime classifier.

Freshness is an explicitly selected fixture policy. Existing MCP operations own
bookkeeping, grants and evidence; only the original native W owns its deadline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, col, select

from stackos import controlled_process_clock as clock
from stackos.db.models import (
    ActionCall,
    Decision,
    DurableActionAttempt,
    DurableActionItem,
    DurableActionJob,
    Project,
    ProjectEvent,
    Run,
    WorkItemOwnership,
)
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.run_plans import RunPlanRepository
from tests.integration import test_controlled_command_loaded as loaded
from tests.integration.test_controlled_publication import git
from tests.integration.test_supervision_loaded_deadline import (
    call as rest_call,
)
from tests.integration.test_supervision_loaded_deadline import (
    original_rows,
    verify_deadline,
)
from tests.support.loaded_deadline_fixture import prepare_copy, setup_case
from tests.support.supervision_loaded import fence, require_denied, require_ok, run_scenario, sdk

pytestmark = pytest.mark.skipif(
    os.environ.get("STACKOS_RUN_LOADED_SUPERVISION_PROOF") != "1",
    reason="Requires an explicitly admitted owned-daemon/native proof",
)

CADENCE_SECONDS = 0.1
STALE_AFTER_NS = 750_000_000
OBSERVATION_GAP_SECONDS = 1.0
OBLIGATION = "MYB100.incident-progress.v1"
VERIFIER_ID = "stackos.test.progress-observation.v1"
PROGRESS_FIELDS = {
    "candidate_sha256",
    "source_sha256",
    "captured_at",
    "evidence_ref",
    "canonical_item_id",
    "epoch",
    "obligation",
    "verifier",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _continuous() -> int:
    sample = clock._sample_clock()
    assert clock._valid_clock(sample) and sample.continuous_ns is not None
    return sample.continuous_ns


def _verify_observation(evidence: dict, *, expected: dict, last_good_ns: int, now_ns: int) -> None:
    """Pinned criterion verifier for these fixtures, with no effect authority."""
    assert evidence.keys() >= PROGRESS_FIELDS, "incomplete progress observation"
    assert all(type(evidence[key]) is str and evidence[key] for key in PROGRESS_FIELDS - {"epoch"})
    assert type(evidence["epoch"]) is int and evidence["epoch"] == expected["epoch"]
    assert evidence["canonical_item_id"] == expected["canonical_item_id"], "progress item mismatch"
    assert evidence["candidate_sha256"] == expected["candidate_sha256"], (
        "progress candidate mismatch"
    )
    assert evidence["source_sha256"] == expected["source_sha256"], "progress source mismatch"
    assert evidence["obligation"] == expected["obligation"] == OBLIGATION
    assert evidence["verifier"] == expected["verifier"] == VERIFIER_ID
    assert evidence["captured_at"] == expected["captured_at"]
    captured = datetime.fromisoformat(evidence["captured_at"])
    assert captured.tzinfo is not None and captured.utcoffset().total_seconds() == 0
    assert type(last_good_ns) is int and type(now_ns) is int
    assert 0 <= now_ns - last_good_ns < STALE_AFTER_NS, "stale progress observation"


def _effects(engine, project: int) -> dict:
    with Session(engine) as session:
        return {
            "calls": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(ActionCall)
                    .where(ActionCall.project_id == project)
                    .order_by(ActionCall.id)
                ).all()
            ],
            "children": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(Run)
                    .where(Run.project_id == project, col(Run.parent_run_id).is_not(None))
                    .order_by(Run.id)
                ).all()
            ],
            "owners": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(WorkItemOwnership)
                    .where(WorkItemOwnership.project_id == project)
                    .order_by(WorkItemOwnership.id)
                ).all()
            ],
            "jobs": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(DurableActionJob)
                    .where(DurableActionJob.project_id == project)
                    .order_by(DurableActionJob.id)
                ).all()
            ],
            "items": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(DurableActionItem).order_by(DurableActionItem.id)
                ).all()
            ],
            "attempts": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(DurableActionAttempt).order_by(DurableActionAttempt.id)
                ).all()
            ],
        }


def _seed_manual_request(engine):
    # Explicit trusted synthetic/manual source, not provider authentication.
    with Session(engine) as session:
        project = Project(
            slug="incident-proof", name="Incident proof", domain="example.test", locale="en-US"
        )
        session.add(project)
        session.commit()
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project.id,
                request_key="synthetic:incident-original",
                title="Synthetic incident fixture",
                source_provider="synthetic",
                source_kind="manual-request",
                source_message_ref="synthetic:incident-message",
            )
            .data
        )
        return project.id, request.id


def _observer(engine, project: int):
    """Separate controller with exactly one write grant: decision.record."""
    with Session(engine) as session:
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "synthetic.incident-observer",
                "title": "Synthetic observer bookkeeping only",
                "grants": {"mcp_tool_grants": [{"step_id": "observe", "tool": "decision.record"}]},
                "steps": [{"id": "observe", "title": "Record observation"}],
            },
        ).data
        started = plans.start(plan.id, project_id=project).data
        plans.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="observe",
            claimed_by="synthetic:observer",
        )
        return started


async def _route(call, engine, project: int, observer, diagnosis: dict) -> dict:
    before = _effects(engine, project)
    with Session(engine) as session:
        before_decisions = len(session.exec(select(Decision)).all())
        before_events = len(
            session.exec(
                select(ProjectEvent).where(ProjectEvent.event_type == "decision.record")
            ).all()
        )
    routed_at = _utc()
    record = require_ok(
        await call(
            "decision.record",
            {
                "project_id": project,
                "run_token": observer.run_token,
                "run_id": observer.run_id,
                "title": diagnosis["cause"],
                "decision": "Hold effect dispatch; refresh or repair the named bookkeeping",
                "rationale": json.dumps(diagnosis, sort_keys=True),
                "evidence_json": diagnosis,
                "decided_by": "synthetic:observer",
                "tags": ["synthetic-incident-proof"],
                "response_mode": "raw",
            },
        )
    )["data"]
    # Default fields include rationale; raw mode preserves its full diagnostic JSON.
    page = require_ok(
        await call(
            "decision.query",
            {
                "project_id": project,
                "tags": ["synthetic-incident-proof"],
                "limit": 10,
                "response_mode": "raw",
            },
        )
    )
    item = next(item for item in page["items"] if item["id"] == record["id"])
    assert json.loads(item["fields"]["rationale"]) == diagnosis
    assert record["evidence_json"] == diagnosis
    assert _effects(engine, project) == before
    read_back_at = _utc()
    assert diagnosis["detected_at"] <= routed_at <= read_back_at
    with Session(engine) as session:
        assert len(session.exec(select(Decision)).all()) == before_decisions + 1
        assert (
            len(
                session.exec(
                    select(ProjectEvent).where(ProjectEvent.event_type == "decision.record")
                ).all()
            )
            == before_events + 1
        )
    return {
        "decision_id": record["id"],
        "routing_started_at": routed_at,
        "routing_read_back_at": read_back_at,
        "diagnosis": diagnosis,
    }


def test_loaded_missing_binding_and_stale_incomplete_progress_route_without_dispatch(tmp_path):
    async def scenario(settings, engine, base, env, work, audit, daemon_owner):
        project, request_id = _seed_manual_request(engine)
        observer = _observer(engine, project)
        async with sdk(settings, base) as call:
            task = require_ok(
                await call(
                    "tracker.createTask",
                    {
                        "project_id": project,
                        "key": "incident-work",
                        "title": "Incident work",
                        "response_mode": "raw",
                    },
                )
            )["data"]["task"]
            item = f"task:{task['id']}"
            prepared = require_ok(
                await call(
                    "agentRequest.prepareRunPlan",
                    {
                        "project_id": project,
                        "request_id": request_id,
                        "claimed_by": "synthetic:lead",
                        "idempotency_key": "incident:prepare",
                        "run_plan_json": {
                            "schema_version": "stackos.run-plan.v1",
                            "key": "synthetic.incident-work",
                            "title": "Planning only",
                            "metadata_json": {"execution_authorized": False},
                            "grants": {
                                "mcp_tool_grants": [
                                    {"step_id": "work", "tool": name, "targets": [item]}
                                    for name in ["run.ownerAcquire", "run.ownerCheckpoint"]
                                ]
                            },
                            "steps": [{"id": "work", "title": "Work"}],
                        },
                        "response_mode": "raw",
                    },
                )
            )["data"]
            plan = prepared["run_plan"]
            step = plan["steps"][0]
            continuity = dict(
                scope_digest=loaded.SCOPE,
                budget_ref="synthetic:budget",
                stop_scope_ref="synthetic:stop",
                history_ref="synthetic:history",
                stopped=False,
                denied=False,
                attempts_used=0,
                failures_used=0,
                attempt_limit=1,
                failure_limit=1,
                effect_identity="effect:incident:no-dispatch",
            )
            native_source = {"run_plan_id": plan["id"], "run_plan_step_id": step["id"]}
            require_ok(
                await call(
                    "tracker.patch",
                    {
                        "project_id": project,
                        "patch_json": {
                            "tasks": {
                                task["key"]: {
                                    "source_json": native_source,
                                    "metadata_json": {"ownership_continuity": continuity},
                                }
                            }
                        },
                        "actor": "synthetic:fixture-bootstrap",
                        "response_mode": "raw",
                    },
                )
            )
            observed_at = _utc()
            current = require_ok(
                await call("runPlan.get", {"run_plan_id": plan["id"], "response_mode": "raw"})
            )
            tracker = require_ok(
                await call(
                    "tracker.get",
                    {
                        "project_id": project,
                        "task_key": task["key"],
                        "include_graph": False,
                        "response_mode": "raw",
                    },
                )
            )
            assert current["run_id"] is None and current["status"] == "draft"
            assert current["metadata_json"]["execution_authorized"] is False
            assert tracker["tasks"][0]["source_json"] == native_source
            acquire_args = dict(
                project_id=project,
                canonical_item_id=item,
                owner_ref="synthetic:lead",
                scope_digest=loaded.SCOPE,
                response_mode="raw",
            )
            before = _effects(engine, project)
            denied = await call("run.ownerAcquire", acquire_args)
            require_denied(denied)
            assert (
                denied.value["code"] == -32007 and denied.value["message"] == "ToolNotGrantedError"
            )
            assert _effects(engine, project) == before
            assert not before["calls"] and not before["children"] and not before["owners"]
            routes = [
                await _route(
                    call,
                    engine,
                    project,
                    observer,
                    {
                        "occurrence": "synthetic:incident:missing-binding",
                        "canonical_item_id": item,
                        "source_request_id": request_id,
                        "project_id": project,
                        "run_plan_id": plan["id"],
                        "step_id": step["id"],
                        "current_run_id": None,
                        "attempt_id": None,
                        "epoch": None,
                        "obligation": OBLIGATION,
                        "verifier": VERIFIER_ID,
                        "scope_digest": loaded.SCOPE,
                        "cause": "missing_controller_bookkeeping",
                        "detected_at": observed_at,
                        "raw_denial": denied.value,
                        "last_good_read_at": observed_at,
                        "next_actor": "main_agent",
                        "required_input": "explicit start/claim authority",
                        "next_operations": [
                            "runPlan.start",
                            "runPlan.claimStep",
                            "run.ownerAcquire",
                        ],
                        "execution_authorized": False,
                        "hang_asserted": False,
                    },
                )
            ]
            # Recording a diagnosis cannot start the affected draft or authorize work.
            assert (
                require_ok(
                    await call("runPlan.get", {"run_plan_id": plan["id"], "response_mode": "raw"})
                )["run_id"]
                is None
            )
            # Explicitly admitted bookkeeping correction, with no worker/action grant.
            started = require_ok(
                await call(
                    "runPlan.start",
                    {
                        "project_id": project,
                        "run_plan_id": plan["id"],
                        "response_mode": "raw",
                    },
                )
            )["data"]
            require_ok(
                await call(
                    "runPlan.claimStep",
                    {
                        "run_plan_id": plan["id"],
                        "step_id": "work",
                        "run_token": started["run_token"],
                    },
                )
            )
            acquire_args["run_token"] = started["run_token"]
            acquired = require_ok(await call("run.ownerAcquire", acquire_args))["data"]["ownership"]
            candidate = work / "candidate.py"
            candidate.write_bytes(b"value = 1\n")
            expected_progress = dict(
                candidate_sha256=_sha(candidate.read_bytes()),
                source_sha256=_sha(Path(loaded.__file__).read_bytes()),
                captured_at=_utc(),
                canonical_item_id=item,
                epoch=acquired["epoch"],
                obligation=OBLIGATION,
                verifier=VERIFIER_ID,
            )
            progress = expected_progress | {"evidence_ref": "synthetic:incident-progress-v1"}
            _verify_observation(
                progress,
                expected=expected_progress,
                last_good_ns=time.monotonic_ns(),
                now_ns=time.monotonic_ns(),
            )
            checkpoint = require_ok(
                await call(
                    "run.ownerCheckpoint",
                    acquire_args
                    | fence(acquired)
                    | {
                        "checkpoint_ref": "synthetic-progress:"
                        + _sha(json.dumps(progress, sort_keys=True).encode())
                    },
                )
            )["data"]["ownership"]
            read_args = {"project_id": project, "canonical_item_id": item, "response_mode": "raw"}
            last_good = require_ok(await call("run.ownerGet", read_args))
            last_good_ns = time.monotonic_ns()
            progress_scope = {
                "project_id": project,
                "run_plan_id": plan["id"],
                "step_id": step["id"],
                "current_run_id": started["run_id"],
                "attempt_id": None,
                "scope_digest": loaded.SCOPE,
                "source_request_id": request_id,
                "candidate_sha256": progress["candidate_sha256"],
                "source_sha256": progress["source_sha256"],
                "capture_time": progress["captured_at"],
                "epoch": acquired["epoch"],
                "obligation": OBLIGATION,
                "verifier": VERIFIER_ID,
            }
            await asyncio.sleep(OBSERVATION_GAP_SECONDS)
            with pytest.raises(AssertionError, match="stale progress observation"):
                _verify_observation(
                    progress,
                    expected=expected_progress,
                    last_good_ns=last_good_ns,
                    now_ns=time.monotonic_ns(),
                )
            routes.append(
                await _route(
                    call,
                    engine,
                    project,
                    observer,
                    {
                        **progress_scope,
                        "occurrence": "synthetic:incident:stale-progress",
                        "canonical_item_id": item,
                        "cause": "stale_observer_read",
                        "detected_at": _utc(),
                        "last_good_read_at": last_good["status"]["captured_at"],
                        "threshold_ns": STALE_AFTER_NS,
                        "evidence_ref": checkpoint["checkpoint_ref"],
                        "next_actor": "main_agent",
                        "required_input": "fresh canonical readback",
                        "next_operations": ["run.ownerGet"],
                        "hang_asserted": False,
                    },
                )
            )
            incomplete_path = work / "incomplete-progress.json"
            incomplete = deepcopy(progress)
            incomplete.pop("source_sha256")
            incomplete_path.write_text(json.dumps(incomplete, sort_keys=True))
            with pytest.raises(AssertionError, match="incomplete progress observation"):
                _verify_observation(
                    json.loads(incomplete_path.read_text()),
                    expected=expected_progress,
                    last_good_ns=time.monotonic_ns(),
                    now_ns=time.monotonic_ns(),
                )
            unchanged_checkpoint = _effects(engine, project)
            for field, wrong, reason in [
                ("source_sha256", "0" * 64, "progress source mismatch"),
                ("canonical_item_id", "task:999999", "progress item mismatch"),
            ]:
                contradictory = progress | {field: wrong}
                with pytest.raises(AssertionError, match=reason):
                    _verify_observation(
                        contradictory,
                        expected=expected_progress,
                        last_good_ns=time.monotonic_ns(),
                        now_ns=time.monotonic_ns(),
                    )
                assert _effects(engine, project) == unchanged_checkpoint
            routes.append(
                await _route(
                    call,
                    engine,
                    project,
                    observer,
                    {
                        **progress_scope,
                        "occurrence": "synthetic:incident:incomplete-progress",
                        "canonical_item_id": item,
                        "cause": "incomplete_progress_evidence",
                        "detected_at": _utc(),
                        "last_good_read_at": last_good["status"]["captured_at"],
                        "evidence_sha256": _sha(incomplete_path.read_bytes()),
                        "missing_field": "source_sha256",
                        "next_actor": "agent",
                        "required_input": (
                            "complete source/candidate/capture evidence before checkpoint"
                        ),
                        "next_operations": ["run.ownerGet", "run.ownerCheckpoint"],
                        "hang_asserted": False,
                    },
                )
            )
            fresh = require_ok(await call("run.ownerGet", read_args))
            assert (
                fresh["ownership"] == checkpoint
                and fresh["status"]["acceptance_state"] == "not_evaluated"
            )
            assert fresh["status"]["freshness"] == "current_at_snapshot"
            assert fresh["status"]["captured_at"] > last_good["status"]["captured_at"]
            final = _effects(engine, project)
            assert not final["calls"] and not final["children"] and not final["jobs"]
            assert final["owners"][0]["continuity_json"]["attempts_used"] == 0
            audit.update(
                status="passed",
                routes=routes,
                threshold_ns=STALE_AFTER_NS,
                operation_scope="synthetic owner-progress observation only",
                unintended_dispatches=0,
                unintended_effects=0,
                work_accepted=False,
                flag_is_authority=False,
                affected_plan=plan["id"],
                observer_plan=observer.plan.id,
            )

    loaded.run_loaded_scenario(tmp_path, scenario)


@pytest.mark.parametrize("weakened", [False, True])
def test_loaded_changing_tracked_diff_is_activity_not_acceptance_and_cannot_renew_deadline(
    tmp_path, monkeypatch, weakened
):
    async def scenario(settings, engine, base, env, work, audit, daemon_owner):
        copied, binding = prepare_copy(work, weaken_deadline=weakened)
        from stackos.actions import controlled_command as fixed

        script = copied / fixed._REPORT_SCRIPT
        raw = script.read_text()
        loop = "    while time.monotonic() < end:\n        pass\n"
        assert raw.count(loop) == 1
        replacement = (
            "    activity = directory.parent / (directory.name + '.activity')\n"
            "    tick = 0\n"
            "    while time.monotonic() < end:\n"
            "        tick += 1\n"
            "        (activity / 'candidate.py').write_text(f'value = {tick}\\n')\n"
            f"        time.sleep({CADENCE_SECONDS})\n"
        )
        script.write_text(raw.replace(loop, replacement))
        binding["activity_script_sha256"] = _sha(script.read_bytes())
        # Whole copied runtime is fixed before daemon import, setup and approval.
        binding["files"] = [
            {"path": p.relative_to(copied).as_posix(), "sha256": _sha(p.read_bytes())}
            for p in sorted(copied.rglob("*"))
            if p.is_file() and not p.is_symlink()
        ]
        (work / "frozen-runtime-manifest.json").write_text(json.dumps(binding, sort_keys=True))
        daemon_owner.stop()
        env["PYTHONPATH"] = str(copied)
        daemon_owner.start()
        async with (
            sdk(settings, base) as call,
            httpx.AsyncClient(
                headers={"Authorization": "Bearer " + settings.token_path.read_text().strip()},
                transport=httpx.AsyncHTTPTransport(retries=0),
                trust_env=False,
                follow_redirects=False,
                timeout=15,
            ) as action_client,
        ):
            case = setup_case(settings, engine, copied, monkeypatch)
            activity = case.command.report_directory.parent / (
                case.command.report_directory.name + ".activity"
            )
            activity.parent.mkdir(mode=0o700, exist_ok=True)
            activity.mkdir(mode=0o700)
            (activity / "candidate.py").write_bytes(b"value = 0\n")
            git(activity, "init", "--quiet")
            git(activity, "add", "candidate.py")
            git(activity, "commit", "--quiet", "-m", "Synthetic tracked baseline")
            original_policy = deepcopy(case.policy)
            # Existing loaded-deadline REST path owns the finite6s action wait;
            # SDK MCP independently observes and heartbeats the same controller.
            pending = asyncio.create_task(
                rest_call(action_client, base, "action.execute", case.args)
            )
            samples, diff_hashes = [], set()
            gap_done = False
            try:
                while not pending.done():
                    now = _continuous()
                    assert now < original_policy["cleanup_deadline_continuous_ns"]
                    if case.marker.exists():
                        diff = git(activity, "diff", "--", "candidate.py").encode()
                        if diff:
                            diff_hashes.add(_sha(diff))
                        read_started_ns = _continuous()
                        read = require_ok(
                            await call(
                                "run.ownerGet",
                                {
                                    "project_id": case.project,
                                    "canonical_item_id": case.item,
                                    "response_mode": "raw",
                                },
                            )
                        )
                        read_completed_ns = _continuous()
                        require_ok(
                            await call(
                                "run.heartbeat",
                                {
                                    "project_id": case.project,
                                    "run_id": case.run,
                                    "run_token": case.args["run_token"],
                                    "response_mode": "raw",
                                },
                            )
                        )
                        assert read["ownership"]["checkpoint_revision"] == 0
                        assert (
                            read["ownership"]["epoch"] == case.args["input_json"]["expected_epoch"]
                        )
                        assert read["status"]["acceptance_state"] == "not_evaluated"
                        if read_completed_ns < original_policy["deadline_continuous_ns"]:
                            assert read["status"]["state"] == "running"
                            assert read["ownership"]["continuity_json"]["stopped"] is False
                        samples.append(
                            {
                                "read_started_ns": read_started_ns,
                                "read_completed_ns": read_completed_ns,
                                "crossed_deadline_during_read": (
                                    read_started_ns
                                    < original_policy["deadline_continuous_ns"]
                                    <= read_completed_ns
                                ),
                                "diff_sha256": _sha(diff),
                                "captured_at": read["status"]["captured_at"],
                                "state": read["status"]["state"],
                                "acceptance": "not_evaluated",
                            }
                        )
                        if len(diff_hashes) >= 3 and not gap_done:
                            # Fresh readback after a genuine observation gap grants no time.
                            expected_progress = {
                                "candidate_sha256": _sha((activity / "candidate.py").read_bytes()),
                                "source_sha256": _sha(script.read_bytes()),
                                "captured_at": read["status"]["captured_at"],
                                "canonical_item_id": case.item,
                                "epoch": read["ownership"]["epoch"],
                                "obligation": OBLIGATION,
                                "verifier": VERIFIER_ID,
                            }
                            evidence = expected_progress | {
                                "evidence_ref": "synthetic:tracked-candidate-progress"
                            }
                            read_ns = time.monotonic_ns()
                            _verify_observation(
                                evidence,
                                expected=expected_progress,
                                last_good_ns=read_ns,
                                now_ns=time.monotonic_ns(),
                            )
                            await asyncio.sleep(OBSERVATION_GAP_SECONDS)
                            with pytest.raises(AssertionError, match="stale progress observation"):
                                _verify_observation(
                                    evidence,
                                    expected=expected_progress,
                                    last_good_ns=read_ns,
                                    now_ns=time.monotonic_ns(),
                                )
                            fresh = require_ok(
                                await call(
                                    "run.ownerGet",
                                    {
                                        "project_id": case.project,
                                        "canonical_item_id": case.item,
                                        "response_mode": "raw",
                                    },
                                )
                            )
                            assert fresh["status"]["captured_at"] > evidence["captured_at"]
                            assert (
                                fresh["ownership"]["epoch"]
                                == case.args["input_json"]["expected_epoch"]
                            )
                            expected_progress = expected_progress | {
                                "candidate_sha256": _sha((activity / "candidate.py").read_bytes()),
                                "source_sha256": _sha(script.read_bytes()),
                                "captured_at": fresh["status"]["captured_at"],
                            }
                            evidence = evidence | expected_progress
                            fresh_ns = time.monotonic_ns()
                            _verify_observation(
                                evidence,
                                expected=expected_progress,
                                last_good_ns=fresh_ns,
                                now_ns=time.monotonic_ns(),
                            )
                            gap_done = True
                    await asyncio.sleep(CADENCE_SECONDS)
                result = (await pending).json()
                assert len(diff_hashes) >= 3 and gap_done
                rows = original_rows(engine, case)
                report_path = (
                    settings.state_dir
                    / "controlled-process"
                    / f"{case.project}-{rows[1]['id']}"
                    / "watchdog-observation.json"
                )
                report = json.loads(report_path.read_bytes())
                if weakened:
                    with pytest.raises(AssertionError, match="I4 deadline exceeded"):
                        verify_deadline(case, result, rows, report)
                else:
                    verify_deadline(case, result, rows, report)
                    assert all(
                        event["continuous_ns"] >= original_policy["deadline_continuous_ns"]
                        for event in report["events"]
                        if event["event"] == "signal"
                    )
                effects = _effects(engine, case.project)
                assert len(effects["calls"]) == len(effects["children"]) == 1
                assert not effects["jobs"] and not effects["items"] and not effects["attempts"]
                assert effects["owners"][0]["continuity_json"]["attempts_used"] == 1
                assert case.policy == original_policy
                assert (
                    rows[1]["metadata_json"]["native_recovery_private"]["policy"] == original_policy
                )
                audit.update(
                    status="passed",
                    fixture=binding,
                    samples=samples,
                    distinct_tracked_diff_hashes=sorted(diff_hashes),
                    weak_deadline=weakened,
                    hard_deadline_renewed=False,
                    work_accepted=False,
                    hang_asserted=False,
                    policy=original_policy,
                    declared_gap_seconds=OBSERVATION_GAP_SECONDS,
                    stale_after_ns=STALE_AFTER_NS,
                    cadence_seconds=CADENCE_SECONDS,
                )
            finally:
                if not pending.done():
                    await pending  # retain the original request; no second action/kill/retry

    run_scenario(tmp_path, monkeypatch, scenario)
