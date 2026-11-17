"""Continuation uses the real tracker, queue and runner; only the native edge is controlled."""

import json
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from sqlmodel import Session

from stackos import task_delivery
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.base import ConflictError
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_managed_upgrade import IDENTITY, prepare
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    generic_result,
    git,
    git_project,  # noqa: F401
    native_source_fixtures,  # noqa: F401
    prepared_task,
    submit,
    use_codex_native,
)
from tests.integration.test_repositories.test_task_delivery import (
    delivery_engine as delivery_engine,
)


def finish(session, scheduler, revision=2, **changes):
    return task_delivery.finish_managed_upgrade(
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
        upgrade_id=IDENTITY["upgrade_id"],
        target_identity=IDENTITY["target_identity"],
        expected_revision=revision,
        postflight={
            "target_identity": IDENTITY["target_identity"],
            "daemon_generation": task_delivery._admission_id(scheduler),
            "health_ok": True,
            "doctor_exit_code": 0,
            "doctor_evidence_ref": "fixture:doctor:exit-0",
        }
        | changes,
    )


def test_finish_rejoins_queue_once_and_fences_original_callback(session, project_id, tmp_path):
    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    callback, options = old.jobs[0]
    prepare(session, old)
    repo = TrackerRepository(session)
    metadata = deepcopy(repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json)
    # Historical queued snapshots may retain prior stop facts. The continuation
    # writer must clear them without changing its frozen checkpoint or sessions.
    metadata["delivery"]["stop_context"] = {
        "code": "agent_blocked",
        "next_action": "resolve_blocker",
    }
    repo.update_task(
        project_id=project_id, task_key="deliver", patch_json={"metadata_json": metadata}
    )
    new = QueuedScheduler()
    result = finish(session, new)
    assert result["status"] == "continued" and not result["fenced"]
    assert len(new.jobs) == 1
    session.expire_all()
    assert (
        "stop_context"
        not in repo.get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert new.jobs[0][1]["kwargs"]["handoff_id"] != options["kwargs"]["handoff_id"]
    callback(**options["kwargs"])
    assert finish(session, new) == result
    assert len(new.jobs) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"target_identity": "wrong-target"},
        {"health_ok": False},
        {"doctor_exit_code": 9},
        {"doctor_exit_code": False},
        {"doctor_evidence_ref": ""},
        {"daemon_generation": "stale"},
    ],
)
def test_failed_postflight_keeps_fence_and_never_dispatches(session, project_id, tmp_path, change):
    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    before = prepare(session, old)
    new = QueuedScheduler()
    with pytest.raises(ConflictError, match="postflight"):
        finish(session, new, **change)
    from stackos.repositories.managed_upgrades import ManagedUpgradeRepository

    session.expire_all()
    assert ManagedUpgradeRepository(session).status(IDENTITY["upgrade_id"]) == before
    assert new.jobs == []


@pytest.mark.parametrize(
    "damage", ["settings", "authority", "source", "binding", "route", "retirement"]
)
def test_unsafe_checkpoint_is_held_with_repair_context(session, project_id, tmp_path, damage):
    from stackos.db.models import TrackerTask, WorkspaceBinding

    prepared_task(session, project_id)
    old = QueuedScheduler()
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "commit", "--allow-empty", "-m", "baseline")
    submit(session, project_id, root, old)
    prepare(session, old)
    session.expire_all()
    repo = TrackerRepository(session)
    out = repo.get(project_id=project_id, task_key="deliver").tasks[0]
    task = session.get(TrackerTask, out.id)
    metadata = deepcopy(task.metadata_json)
    if damage == "settings":
        metadata["delivery"]["agents"]["delivery_agent"]["model"] = "changed-model"
    elif damage == "authority":
        task.context_json = {"contract_revision": 900}
    elif damage == "source":
        (root / "foreign.txt").write_text("unattributed change after quiescence")
    elif damage == "binding":
        from sqlmodel import select

        binding = session.exec(
            select(WorkspaceBinding).where(WorkspaceBinding.last_known_root == str(root))
        ).one()
        binding.last_known_root = str(tmp_path / "foreign")
        session.add(binding)
    elif damage == "route":
        metadata["delivery"]["mcp_config_ref"] = {
            "path": str(tmp_path / "missing.toml"),
            "sha256": "0" * 64,
        }
    elif damage == "retirement":
        cp = metadata["delivery"]["upgrade_checkpoint"]
        cp["invocations"] = [{"invocation_id": "missing-retirement"}]
    task.metadata_json = metadata
    session.add(task)
    session.commit()
    new = QueuedScheduler()
    result = finish(session, new)
    assert result["status"] == "partial" and not result["fenced"]
    assert result["checkpoints"][0]["status"] == "held"
    assert result["checkpoints"][0]["issues"]
    assert not new.jobs


def test_finish_transaction_and_dispatch_gap_are_retry_safe(
    session, project_id, tmp_path, monkeypatch
):
    from stackos.repositories.managed_upgrades import ManagedUpgradeRepository

    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    before = prepare(session, old)
    new = QueuedScheduler()
    original = task_delivery._checkpoint_task

    def crash(*_args, **_kwargs):
        raise RuntimeError("fixture transaction crash")

    # Fail after checkpoint validation but before commit; no half-admission survives.
    update = TrackerRepository.update_task
    monkeypatch.setattr(TrackerRepository, "update_task", crash)
    with pytest.raises(RuntimeError, match="transaction crash"):
        finish(session, new)
    monkeypatch.setattr(TrackerRepository, "update_task", update)
    session.expire_all()
    assert ManagedUpgradeRepository(session).status(IDENTITY["upgrade_id"]) == before
    drain = task_delivery._request_delivery_drain
    monkeypatch.setattr(task_delivery, "_request_delivery_drain", lambda *_args: None)
    result = finish(session, new)
    assert not new.jobs and result["status"] == "continued"
    monkeypatch.setattr(task_delivery, "_request_delivery_drain", drain)
    assert finish(session, new) == result
    assert len(new.jobs) == 1
    assert task_delivery._checkpoint_task is original


def test_partial_finish_retries_only_held_work_and_keeps_queue_capacity(
    session, project_id, tmp_path
):
    from stackos.db.models import TaskDeliveryCapacity

    repo = TrackerRepository(session)
    old = QueuedScheduler()
    roots = []
    for key in ("unsafe", "safe"):
        root = tmp_path / key
        root.mkdir()
        git(root, "init")
        git(root, "config", "user.name", "Fixture")
        git(root, "config", "user.email", "fixture@example.test")
        git(root, "commit", "--allow-empty", "-m", "baseline")
        repo.create_task(project_id=project_id, key=key, title=key)
        repo.create_ticket(project_id=project_id, task_key=key, key=f"{key}-ticket", title=key)
        submit(session, project_id, root, old, task_key=key)
        roots.append(root)
    prepare(session, old)
    (roots[0] / "foreign.txt").write_text("changed after retirement")
    session.add(TaskDeliveryCapacity(project_id=project_id, concurrent_tasks=1))
    session.commit()
    new = QueuedScheduler()
    result = finish(session, new)
    assert result["status"] == "partial"
    assert [(c["task_key"], c["status"]) for c in result["checkpoints"]] == [
        ("unsafe", "held"),
        ("safe", "consumed"),
    ]
    assert len(new.jobs) == 1
    safe_job = new.jobs[0][1]["kwargs"].copy()
    assert safe_job["task_key"] == "safe"
    (roots[0] / "foreign.txt").unlink()
    result = finish(session, new, result["revision"])
    assert result["status"] == "continued"
    assert len(new.jobs) == 1 and new.jobs[0][1]["kwargs"] == safe_job
    capacity = task_delivery.delivery_capacity(session, new)["projects"][0]
    assert (capacity["configured_limit"], capacity["running_count"], capacity["queued_count"]) == (
        1,
        1,
        1,
    )
    # Retrying does not evict the admitted safe task after lowering capacity.
    assert finish(session, new) == result
    assert new.jobs[0][1]["kwargs"] == safe_job


def test_queued_continuation_rechecks_source_before_native_launch(
    session, project_id, tmp_path, monkeypatch
):
    launches = []
    monkeypatch.setattr(task_delivery, "run_agent", lambda *_a, **_k: launches.append(True))
    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    prepare(session, old)
    new = QueuedScheduler()
    finish(session, new)
    (tmp_path / "foreign.txt").write_text("changed while queued")
    new.run()
    session.expire_all()
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["status"] == "stopped"
    assert state["upgrade_checkpoint"]["status"] == "held"
    assert "fingerprint" in state["reason"]
    assert not launches


def test_later_upgrade_preserves_prior_checkpoint_status(session, project_id, tmp_path):
    from stackos.repositories.managed_upgrades import ManagedUpgradeRepository

    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    prepare(session, old)
    new = QueuedScheduler()
    first = finish(session, new)
    prepare(
        session,
        new,
        upgrade_id="second-upgrade",
        source_identity="target-build",
        target_identity="next-build",
    )
    session.expire_all()
    assert ManagedUpgradeRepository(session).status(IDENTITY["upgrade_id"]) == first


def test_external_route_bytes_are_verified_before_continuation(
    session, project_id, tmp_path, monkeypatch
):
    from hashlib import sha256

    from stackos.host_mcp.adapters import codex

    prepared_task(session, project_id)
    fragment = tmp_path.parent / f"{tmp_path.name}-route.toml"
    fragment.write_text('[mcp_servers.serena]\nenv = {SERENA_PROJECT_NAME = "original-route"}')
    ref = {"path": str(fragment), "sha256": sha256(fragment.read_bytes()).hexdigest()}
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/native/codex")
    monkeypatch.setattr(
        codex,
        "_run_codex",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [],
            0,
            json.dumps([{"name": "serena", "transport": {"type": "stdio", "command": "python3"}}]),
            "",
        ),
    )
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old, mcp_config_ref=ref)
    prepare(session, old)
    fragment.write_text('[mcp_servers.serena]\nenv = {SERENA_PROJECT_NAME = "foreign-route"}')
    new = QueuedScheduler()
    result = finish(session, new)
    assert result["status"] == "partial"
    assert "digest mismatch" in str(result["checkpoints"][0]["issues"])
    assert not new.jobs


def test_completed_ticket_between_calls_advances_without_delivery_replay(
    session, project_id, tmp_path, monkeypatch
):
    repo = TrackerRepository(session)
    prepared_task(session, project_id)
    old = QueuedScheduler()
    submit(session, project_id, tmp_path, old)
    head = git(tmp_path, "rev-parse", "HEAD")
    task_delivery._save_evidence(
        repo,
        project_id,
        "first",
        {
            "attempt": 1,
            "repair_count": 0,
            "candidate": {"base_commit_sha": head, "commit_sha": head},
            "delivery": {"validated_outcome": "success"},
            "review": {"validated_outcome": "success"},
        },
        status="complete",
    )
    task_delivery._update_delivery(repo, project_id, "deliver", ticket_key="first")
    before = repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json
    prepare(session, old)
    launches = []

    def native(_directory, _instructions, prompt, _schema, *, on_session, **_kwargs):
        packet = json.loads(prompt)
        launches.append((packet["brief"]["ticket"]["key"], packet["phase"]))
        thread = f"second-{packet['phase']}"
        on_session(thread)
        return AgentRunResult(
            0, thread_id=thread, result=generic_result(outputs={"commit_sha": head})
        )

    use_codex_native(monkeypatch, native)
    new = QueuedScheduler()
    assert finish(session, new)["status"] == "continued"
    new.run()
    session.expire_all()
    assert launches == [("second", "delivery"), ("second", "review")]
    assert (
        repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json
        == before
    )
    assert repo.get(project_id=project_id, task_key="deliver").tasks[0].status == "complete"


@pytest.mark.parametrize("damage", ["grant", "approval", "step"])
def test_current_workflow_authority_is_rechecked(session, project_id, tmp_path, damage):
    from stackos.db.models import ApprovalRequest, ApprovalRequestStatus, RunPlan, RunPlanStep
    from stackos.repositories.run_plans import RunPlanRepository

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "authority-check",
            "title": "Fixture",
            "steps": [{"id": "deliver", "title": "Deliver"}],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="deliver")
    old = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        old,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="deliver",
    )
    prepare(session, old)
    step = plans.get_step(plan.id, "deliver")
    if damage == "grant":
        row = session.get(RunPlan, plan.id)
        row.grant_snapshot_json = {"changed": True}
        session.add(row)
    elif damage == "approval":
        session.add(
            ApprovalRequest(
                project_id=project_id,
                run_plan_id=plan.id,
                run_plan_step_id=step.id,
                approval_key="new-required",
                title="Approval",
                status=ApprovalRequestStatus.PENDING,
            )
        )
    else:
        row = session.get(RunPlanStep, step.id)
        row.status = "blocked"
        session.add(row)
    session.commit()
    new = QueuedScheduler()
    result = finish(session, new)
    assert result["status"] == "partial"
    assert result["checkpoints"][0]["status"] == "held"
    assert "authorization" in str(result["checkpoints"][0]["issues"])
    assert not new.jobs


@pytest.mark.parametrize(
    "phase", ["delivery", "review", "repair", "mirror", "review-mirror", "prevented"]
)
def test_original_phase_session_and_attempt_resume(
    delivery_engine, project_id, tmp_path, monkeypatch, phase
):
    from stackos.repositories.run_plans import RunPlanRepository

    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "commit", "--allow-empty", "-m", "baseline")
    head = git(root, "rev-parse", "HEAD")
    ready = Event()
    launches = []
    interrupted = False
    continued = False

    def execute(_directory, _instructions, prompt, _schema, *, on_session, process_control, **kw):
        nonlocal interrupted
        packet = json.loads(prompt)
        current = packet["phase"]
        launches.append((current, kw.get("resume_thread_id"), packet["occurrence"]))
        thread = f"original-{current}"
        if phase == "prevented" and not continued:
            ready.set()
            assert process_control.cancelled.wait(5)
            process_control.spawn([sys.executable, "-c", "raise SystemExit('must never launch')"])
        on_session(thread)
        if not continued:
            if phase in {"review", "repair"} and current == "delivery" and not interrupted:
                return AgentRunResult(
                    0, thread_id=thread, result=generic_result(outputs={"commit_sha": head})
                )
            if phase == "repair" and current == "review":
                interrupted = True
                return AgentRunResult(
                    0,
                    thread_id=thread,
                    result=generic_result("repair", outputs={"commit_sha": head}),
                )
            process = process_control.spawn(
                [sys.executable, "-c", "import time; print('ready',flush=True); time.sleep(30)"],
                cwd=root,
                stdout=subprocess.PIPE,
                text=True,
            )
            assert process.stdout.readline().strip() == "ready"
            process.stdout.close()
            if current == "delivery":
                (root / "owned.txt").write_text("interrupted native work")
            ready.set()
            process.wait(timeout=10)
            return AgentRunResult(1, thread_id=thread, error="upgrade interruption")
        if current == "delivery" and git(root, "status", "--porcelain", "--", "owned.txt"):
            git(root, "add", "owned.txt")
            git(root, "commit", "-m", "finish original work")
        return AgentRunResult(
            0,
            thread_id=thread,
            result=generic_result(outputs={"commit_sha": git(root, "rev-parse", "HEAD")}),
        )

    use_codex_native(monkeypatch, execute)
    with Session(delivery_engine) as session:
        repo = TrackerRepository(session)
        old = QueuedScheduler()
        scope = {}
        task_key, ticket_key = "deliver", "first"
        if "mirror" in phase:
            plans = RunPlanRepository(session)
            plan = plans.create(
                project_id=project_id,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": "continuation-mirror",
                    "title": "Fixture",
                    "steps": [{"id": "deliver", "title": "Deliver"}],
                },
            ).data
            started = plans.start(plan.id, project_id=project_id)
            plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="deliver")
            task_key, ticket_key = f"workflow-{plan.id}", f"workflow-{plan.id}-deliver"
            scope = {"run_plan_id": plan.id, "step_id": "deliver"}
            if phase == "review-mirror":
                scope["agent_assignments"] = {ticket_key: "review_agent"}
        else:
            prepared_task(session, project_id)
        submit(session, project_id, root, old, task_key=task_key, **scope)
        with ThreadPoolExecutor() as workers:
            running = workers.submit(old.run)
            assert ready.wait(5)
            receipt = prepare(session, old)
            running.result(timeout=5)
        session.expire_all()
        before = deepcopy(repo.brief(project_id=project_id, ticket_key=ticket_key).ticket)
        old_evidence = before.completion_evidence_json["automatic_delivery"]
        resumed_phase = "review" if phase in {"review", "review-mirror"} else "delivery"
        expected_count = len(launches)
        continued = True
        new = QueuedScheduler()
        result = finish(session, new, receipt["revision"])
        assert result["status"] == "continued", result
        new.run()
        session.expire_all()
        after = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
        evidence = after.completion_evidence_json["automatic_delivery"]
        assert after.status == "complete", after.outcome
        resumed = launches[expected_count]
        assert resumed[0:2] == (
            resumed_phase,
            None if phase == "prevented" else f"original-{resumed_phase}",
        )
        assert resumed[2]["attempt"] == old_evidence["attempt"]
        if "mirror" not in phase:
            assert evidence["repair_count"] == old_evidence["repair_count"]
        assert all(evidence["sessions"][p] == s for p, s in old_evidence["sessions"].items())
        if phase == "review":
            assert evidence["delivery"] == old_evidence["delivery"]
            assert sum(p == "delivery" for p, _, _ in launches) == 2  # first and second tickets
        assert evidence["upgrade_interruptions"][-1]["phase"] == resumed_phase


@pytest.mark.parametrize("phase", ["delivery", "review", "repair"])
def test_interrupted_history_and_cumulative_usage_reach_existing_projections(
    delivery_engine, project_id, tmp_path, monkeypatch, phase
):
    from stackos.host_mcp.result import usage_snapshot
    from stackos.repositories.tracker.agent_stats import invocation_receipts

    native_result = AgentRunResult
    calls = defaultdict(int)

    def measured_result(*args, **kwargs):
        thread = kwargs["thread_id"]
        calls[thread] += 1
        kwargs["usage"] = usage_snapshot(
            "fixture-native", "session", 10 * calls[thread], 5 * calls[thread]
        )
        return native_result(*args, **kwargs)

    monkeypatch.setattr(sys.modules[__name__], "AgentRunResult", measured_result)
    test_original_phase_session_and_attempt_resume(
        delivery_engine, project_id, tmp_path, monkeypatch, phase
    )
    with Session(delivery_engine) as session:
        repo = TrackerRepository(session)
        brief = repo.brief(project_id=project_id, ticket_key="first")
        evidence = brief.ticket.completion_evidence_json
        automatic = evidence["automatic_delivery"]
        interrupted = automatic["upgrade_interruptions"][-1]
        receipts = invocation_receipts(evidence)
        assert interrupted["receipt"]["execution"]["invocation_id"] in {
            r["execution"]["invocation_id"] for r in receipts
        }
        expected = 5 if phase == "repair" else 3
        assert len(receipts) == brief.ticket.agent_stats.launch_attempts == expected
        assert brief.ticket.work_duration.recorded_intervals == expected
        assert brief.ticket.work_duration.coverage == "complete"
        usage = automatic[interrupted["phase"]]["usage"]
        assert usage["status"] == "measured" and usage["measured"]["total"] == 15
        assert brief.ticket.agent_stats.reported_usage.total == expected * 15
        assert brief.task.agent_stats.launch_attempts == expected + 2
        assert brief.task.status == "complete"
        report = repo.agent_stats(project_id=project_id)
        assert report.totals.launch_attempts == expected + 2
        assert report.delivery_trends.totals.work_time.total_seconds == pytest.approx(
            brief.task.work_duration.total_seconds
        )


def test_missing_interruption_usage_cannot_bridge_an_older_repair_baseline(
    delivery_engine, project_id, tmp_path, monkeypatch
):
    from stackos.host_mcp.result import usage_snapshot

    native_result = AgentRunResult
    calls = defaultdict(int)

    def measured_result(*args, **kwargs):
        thread = kwargs["thread_id"]
        calls[thread] += 1
        if thread != "original-delivery" or calls[thread] != 2:
            kwargs["usage"] = usage_snapshot(
                "fixture-native", "session", 10 * calls[thread], 5 * calls[thread]
            )
        return native_result(*args, **kwargs)

    monkeypatch.setattr(sys.modules[__name__], "AgentRunResult", measured_result)
    test_original_phase_session_and_attempt_resume(
        delivery_engine, project_id, tmp_path, monkeypatch, "repair"
    )
    with Session(delivery_engine) as session:
        evidence = (
            TrackerRepository(session)
            .brief(project_id=project_id, ticket_key="first")
            .ticket.completion_evidence_json["automatic_delivery"]
        )
        usage = evidence["delivery"]["usage"]
        assert usage["status"] == "unavailable" and usage["reason"] == "missing_baseline"
        assert usage["snapshot"]["total"] == 45 and usage["measured"] is None
