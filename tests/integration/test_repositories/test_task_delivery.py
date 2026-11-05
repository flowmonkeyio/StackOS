"""The fixed delivery loop uses real tracker state and a controlled harness."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as SchemaValidationError
from sqlmodel import Session

from stackos.db.models import RunPlanStepStatus
from stackos.host_mcp.result import AgentRunResult
from stackos.mcp.context import MCPContext
from stackos.operations.tracker.schemas import TrackerStartDeliveryInput
from stackos.operations.tracker.write_handlers import tracker_start_delivery
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository


def git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(directory: Path, name: str) -> str:
    (directory / name).write_text(name)
    git(directory, "add", name)
    git(directory, "commit", "-m", name)
    return git(directory, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def git_project(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Delivery Test")
    git(tmp_path, "config", "user.email", "delivery@example.test")
    commit(tmp_path, "baseline")


class QueuedScheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, dict[str, Any]]] = []

    def add_job(self, function, **options) -> None:
        assert options["jobstore"] == "memory"
        assert options["executor"] == "long"
        self.jobs.append((function, options))

    def run(self) -> None:
        function, options = self.jobs.pop(0)
        function(**options["kwargs"])


def prepared_task(session: Session, project_id: int) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="unrelated", title="Unrelated")
    repo.create_ticket(project_id=project_id, task_key="unrelated", key="other", title="Other")
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.update_task(
        project_id=project_id, task_key="deliver", patch_json={"metadata_json": {"keep": "yes"}}
    )
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    repo.create_ticket(
        project_id=project_id,
        task_key="deliver",
        key="second",
        title="Second",
        dependency_keys=["first"],
    )


def submit(session: Session, project_id: int, tmp_path: Path, scheduler: QueuedScheduler, **scope):
    task_key = scope.pop("task_key", "deliver")
    arguments = TrackerStartDeliveryInput(
        project_id=project_id,
        task_key=task_key,
        project_dir=str(tmp_path),
        host_key=scope.pop("host_key", None),
        delivery_agent=scope.pop(
            "delivery_agent", {"name": "project-builder", "instructions": "Build the ticket."}
        ),
        review_agent=scope.pop(
            "review_agent", {"name": "project-reviewer", "instructions": "Review the ticket."}
        ),
        **scope,
    )
    context = MCPContext(
        session=session,
        request_id="task-delivery-test",
        project_id=project_id,
        extras={"session_factory": lambda: Session(session.get_bind()), "scheduler": scheduler},
    )
    return asyncio.run(tracker_start_delivery(arguments, context, None)).data


def test_role_host_model_provider_reach_launch_and_receipt(
    session: Session, project_id: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackos.plugins.builtin_engineering import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    calls = []

    def run_agent(host, directory, instructions, prompt, schema, *, sandbox, **selection):
        calls.append((host, selection))
        key = json.loads(prompt)["brief"]["ticket"]["key"]
        delivery = schema == task_delivery().DELIVERY_SCHEMA
        sha = commit(tmp_path, key) if delivery else git(tmp_path, "rev-parse", "HEAD")
        value = {"status": "complete"} if delivery else {"decision": "approve", "followups": []}
        return AgentRunResult(
            0,
            thread_id=f"{key}-{delivery}",
            result={**value, "summary": "Checked", "commit_sha": sha},
        )

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        delivery_agent={
            "name": "builder",
            "instructions": "Build",
            "host_key": "hermes",
            "model": "provider/model",
            "provider": "openrouter",
            "profile": "work",
        },
        review_agent={
            "name": "reviewer",
            "instructions": "Review",
            "host_key": "claude-code",
            "model": "claude-opus-5-5",
        },
    )
    scheduler.run()
    assert (
        calls
        == [
            ("hermes", {"model": "provider/model", "provider": "openrouter", "profile": "work"}),
            ("claude-code", {"model": "claude-opus-5-5"}),
        ]
        * 2
    )
    session.expire_all()
    evidence = (
        TrackerRepository(session)
        .brief(project_id=project_id, ticket_key="first")
        .ticket.completion_evidence_json["automatic_delivery"]
    )
    assert evidence["delivery"]["launch"]["provider"] == "openrouter"
    assert evidence["delivery"]["launch"]["profile"] == "work"
    assert evidence["review"]["launch"]["host_key"] == "claude-code"
    assert evidence["review"]["launch"]["model"] == "claude-opus-5-5"


def test_unsupported_reviewer_rejects_before_scheduling(session, project_id, tmp_path):
    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="does not support headless"):
        submit(
            session,
            project_id,
            tmp_path,
            scheduler,
            review_agent={"name": "review", "instructions": "Review", "host_key": "claude-desktop"},
        )
    assert not scheduler.jobs
    task = TrackerRepository(session).get(project_id=project_id, task_key="deliver").tasks[0]
    assert task.metadata_json == {"keep": "yes"}


def test_project_defaults_merge_by_role_and_freeze_before_launch(
    session, project_id, tmp_path, monkeypatch
):
    from stackos.plugins.builtin_engineering import task_delivery

    prepared_task(session, project_id)
    plugins = PluginRepository(session)
    defaults = {
        "host_key": "claude-code",
        "delivery_agent": {"name": "builder", "instructions": "Saved build instructions"},
        "review_agent": {
            "name": "reviewer",
            "instructions": "Saved review instructions",
            "host_key": "gemini-cli",
            "model": "gemini-2.5-pro",
        },
    }
    plugins.enable(
        project_id=project_id, plugin_slug="engineering", config_json={"delivery": defaults}
    )
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        delivery_agent=None,
        review_agent={"model": "gemini-3.1-pro-preview"},
    )
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json
    )
    assert state["keep"] == "yes"
    assert state["delivery"]["delivery_agent"] == {
        **defaults["delivery_agent"],
        "host_key": "claude-code",
    }
    assert state["delivery"]["review_agent"] == {
        **defaults["review_agent"],
        "model": "gemini-3.1-pro-preview",
    }
    # An edit after submission cannot change the effective task snapshot.
    plugins.enable(
        project_id=project_id,
        plugin_slug="engineering",
        config_json={"delivery": {"review_agent": {"instructions": "Changed later"}}},
    )
    duplicate = submit(
        session, project_id, tmp_path, scheduler, delivery_agent=None, review_agent=None
    )
    assert duplicate["status"] == "queued" and len(scheduler.jobs) == 1
    calls = []

    def run_agent(host, directory, instructions, prompt, schema, *, sandbox, **selection):
        calls.append((host, instructions, sandbox, selection))
        key = json.loads(prompt)["brief"]["ticket"]["key"]
        if schema == task_delivery().DELIVERY_SCHEMA:
            value = {"status": "complete", "commit_sha": commit(tmp_path, key)}
        else:
            value = {
                "decision": "approve",
                "followups": [],
                "commit_sha": git(tmp_path, "rev-parse", "HEAD"),
            }
        return AgentRunResult(
            0, thread_id=f"{key}-{sandbox}", result={**value, "summary": "Checked"}
        )

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    scheduler.run()
    assert (
        calls
        == [
            ("claude-code", "Saved build instructions", "workspace-write", {}),
            (
                "gemini-cli",
                "Saved review instructions",
                "normal",
                {"model": "gemini-3.1-pro-preview"},
            ),
        ]
        * 2
    )
    assert not (tmp_path / ".codex").exists()


@pytest.mark.parametrize(
    "defaults,override,reason",
    [
        ({}, None, "Resolved delivery_agent is invalid"),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build"}},
            None,
            "Resolved review_agent is invalid",
        ),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build", "model": "unknown"}},
            None,
            "native model catalog",
        ),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build"}},
            {"instructions": None},
            "Resolved delivery_agent is invalid",
        ),
    ],
)
def test_incomplete_or_invalid_resolved_defaults_never_schedule(
    session, project_id, tmp_path, defaults, override, reason
):
    prepared_task(session, project_id)
    PluginRepository(session).enable(
        project_id=project_id, plugin_slug="engineering", config_json={"delivery": defaults}
    )
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match=reason):
        submit(session, project_id, tmp_path, scheduler, delivery_agent=override, review_agent=None)
    assert not scheduler.jobs
    task = TrackerRepository(session).get(project_id=project_id, task_key="deliver").tasks[0]
    assert task.metadata_json == {"keep": "yes"}


@pytest.mark.parametrize("host", ["gemini-cli", "hermes"])
@pytest.mark.parametrize("failure", [None, "reject", "native", "mutation"])
def test_normal_review_receipts_and_same_task_failure(
    session, project_id, tmp_path, monkeypatch, host, failure
):
    from stackos.plugins.builtin_engineering import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    calls = []

    def run_agent(selected_host, directory, instructions, prompt, schema, *, sandbox, **selection):
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        calls.append((key, selected_host, sandbox))
        if sandbox == "workspace-write":
            value = {"status": "complete", "commit_sha": commit(tmp_path, key)}
        else:
            assert sandbox == "normal" and selected_host == host
            assert "Do not change files or commits" in packet["assignment"]
            assert schema == task_delivery().REVIEW_SCHEMA
            if failure == "native":
                return AgentRunResult(1, error="Native review access denied")
            value = {
                "decision": "reject" if failure == "reject" else "approve",
                "commit_sha": packet["candidate"]["commit_sha"],
                "followups": [],
            }
            if failure == "mutation":
                (tmp_path / "review-edit").write_text("unauthorized review change")
        return AgentRunResult(
            0,
            thread_id=None if failure == "reject" and sandbox == "normal" else f"{key}-{sandbox}",
            result={**value, "summary": "Reviewed"},
        )

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    review = {"name": "reviewer", "instructions": "Independent review only", "host_key": host}
    if host == "hermes":
        review.update(provider="openrouter", model="provider/model")
    submit(session, project_id, tmp_path, scheduler, review_agent=review)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    first = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = first.completion_evidence_json["automatic_delivery"]
    assert evidence["review"]["launch"]["sandbox"] == "normal"
    assert evidence["review"]["launch"]["host_key"] == host
    assert first.status.value == ("failed" if failure else "complete")
    state = repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json["delivery"]
    assert state["status"] == ("stopped" if failure else "complete")
    assert len(calls) == (2 if failure else 4)
    if failure:
        assert (
            repo.brief(project_id=project_id, ticket_key="second").ticket.status.value
            == "not-started"
        )
    assert (
        repo.brief(project_id=project_id, ticket_key="other").ticket.status.value == "not-started"
    )


def test_two_tickets_run_in_order_after_submission_returns(
    session: Session, project_id: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackos.plugins.builtin_engineering import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    calls = []

    def run_agent(host, project_dir, instructions, prompt, schema, *, sandbox):
        packet = json.loads(prompt)
        ticket_key = packet["brief"]["ticket"]["key"]
        phase = "delivery" if schema == task_delivery().DELIVERY_SCHEMA else "review"
        assert "delivery" not in (packet["brief"]["task"]["metadata_json"] or {})
        assert host == "codex" and Path(project_dir) == tmp_path
        with Session(session.get_bind()) as check:
            repo = TrackerRepository(check)
            brief = repo.brief(project_id=project_id, ticket_key=ticket_key)
            assert brief.ticket.status == "in-progress"
            evidence = brief.ticket.completion_evidence_json["automatic_delivery"]
            assert evidence[phase]["launch"]["sandbox"] == sandbox
            if phase == "review":
                assert evidence["candidate"]["commit_sha"] == git(tmp_path, "rev-parse", "HEAD")
            if ticket_key == "second":
                assert (
                    repo.brief(project_id=project_id, ticket_key="first").ticket.status
                    == "complete"
                )
        calls.append((ticket_key, instructions, phase))
        sha = (
            commit(tmp_path, ticket_key)
            if phase == "delivery"
            else git(tmp_path, "rev-parse", "HEAD")
        )
        if phase == "review":
            assert packet["candidate"]["commit_sha"] == sha
        result = (
            {"status": "complete", "summary": "Implemented"}
            if phase == "delivery"
            else {"decision": "approve", "summary": "Verified", "followups": []}
        )
        result["commit_sha"] = sha
        return AgentRunResult(0, thread_id=f"thread-{len(calls)}", result=result)

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    first = submit(session, project_id, tmp_path, scheduler)
    again = submit(session, project_id, tmp_path, scheduler)
    assert first == again and first["status"] == "queued"
    assert calls == [] and len(scheduler.jobs) == 1
    scheduler.run()
    assert [(key, sandbox) for key, _, sandbox in calls] == [
        ("first", "delivery"),
        ("first", "review"),
        ("second", "delivery"),
        ("second", "review"),
    ]
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert snapshot.tasks[0].status == "complete"
    metadata = snapshot.tasks[0].metadata_json
    assert metadata["keep"] == "yes"
    assert metadata["delivery"]["status"] == "complete"
    assert metadata["delivery"]["delivery_agent"]["name"] == "project-builder"
    for ticket in snapshot.tickets:
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert evidence["review"]["result"]["decision"] == "approve"
        assert evidence["delivery"]["thread_id"] != evidence["review"]["thread_id"]
        assert evidence["candidate"]["commit_sha"] == evidence["review"]["result"]["commit_sha"]
        assert evidence["candidate"]["base_commit_sha"] != evidence["candidate"]["commit_sha"]
    assert (
        TrackerRepository(session).brief(project_id=project_id, ticket_key="other").ticket.status
        == "not-started"
    )


@pytest.mark.parametrize("failure", ["delivery", "review", "reject", "blocked"])
def test_failure_stops_before_next_ticket(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from stackos.plugins.builtin_engineering import task_delivery

    prepared_task(session, project_id)
    calls = []

    def run_agent(*args, sandbox, **kwargs):
        delivery = args[4] == task_delivery().DELIVERY_SCHEMA
        calls.append(delivery)
        if failure == "delivery" or (failure == "review" and not delivery):
            return AgentRunResult(1, error="Harness failed")
        if delivery:
            return AgentRunResult(
                0,
                thread_id="delivery",
                result={
                    "status": "blocked" if failure == "blocked" else "complete",
                    "summary": "Delivery result",
                    "commit_sha": commit(tmp_path, "delivered"),
                },
            )
        return AgentRunResult(0, result={"decision": "reject", "summary": "Needs repair"})

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert len(calls) == (1 if failure in {"delivery", "blocked"} else 2)


def test_ready_filters_task_and_pending_before_limit(session: Session, project_id: int) -> None:
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    assert repo.next(project_id=project_id, limit=1).tickets[0].key == "other"
    assert (
        repo.next(project_id=project_id, task_key="deliver", pending_only=True, limit=1)
        .tickets[0]
        .key
        == "first"
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    assert repo.next(project_id=project_id, task_key="deliver", pending_only=True).tickets == []
    assert repo.next(project_id=project_id, task_key="deliver").tickets[0].key == "first"


def test_workflow_task_is_not_a_second_run_plan_controller(
    session: Session, project_id: int, tmp_path: Path
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Workflow", source_kind="workflow")
    with pytest.raises(ValidationError, match=r"run_plan_id.*step_id"):
        submit(session, project_id, tmp_path, QueuedScheduler())


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "stale",
        "unknown",
        "wrong",
        "dirty",
        "review-sha",
        "review-mutation",
        "review-new-sha",
    ],
)
def test_commit_identity_failure_stops_same_task(
    session, project_id, tmp_path, monkeypatch, failure
):
    from stackos.plugins.builtin_engineering import task_delivery

    prepared_task(session, project_id)
    base = git(tmp_path, "rev-parse", "HEAD")
    calls = []

    def run_agent(*args, sandbox, **kwargs):
        delivery = args[4] == task_delivery().DELIVERY_SCHEMA
        calls.append(delivery)
        if delivery:
            sha = commit(tmp_path, "first")
            result = {"status": "complete", "summary": "Delivered", "commit_sha": sha}
            if failure == "missing":
                result.pop("commit_sha")
            elif failure == "stale":
                result["commit_sha"] = base
            elif failure == "unknown":
                result["commit_sha"] = "0" * 40
            elif failure == "wrong":
                commit(tmp_path, "unexpected")
            elif failure == "dirty":
                (tmp_path / "first").write_text("Uncommitted change")
        else:
            sha = git(tmp_path, "rev-parse", "HEAD")
            result = {
                "decision": "approve",
                "summary": "Reviewed",
                "commit_sha": base if failure == "review-sha" else sha,
                "followups": [],
            }
            if failure in {"review-mutation", "review-new-sha"}:
                new_sha = commit(tmp_path, "review-mutation")
                if failure == "review-new-sha":
                    result["commit_sha"] = new_sha
        return AgentRunResult(0, thread_id=f"thread-{delivery}", result=result)

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    if failure == "missing":
        assert "Invalid codex result" in evidence["delivery"]["error"]
    else:
        assert evidence["delivery"]["result"]["status"] == "complete"
    assert len(calls) == (2 if failure.startswith("review-") else 1)


def workflow_task(session, project_id, *, running=True):
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "delivery-scope",
            "title": "Delivery scope",
            "steps": [
                {"id": "deliver", "title": "Deliver"},
                {"id": "later", "title": "Later", "depends_on": ["deliver"]},
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    if running:
        plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="deliver")
    repo = TrackerRepository(session)
    for key, step_id, kind in [
        ("other-step", "later", "ticket"),
        ("group", "deliver", "group"),
        ("selected", "deliver", "ticket"),
    ]:
        context = repo.workflow_step_ticket_context(
            project_id=project_id, run_plan_id=plan.id, step_id=step_id
        )
        repo.create_ticket(
            project_id=project_id,
            task_key=context["task_key"],
            key=key,
            title=key,
            kind=kind,
            parent_ticket_key=context["parent_ticket_key"],
            run_plan_id=plan.id,
            run_plan_step_id=context["run_plan_step_id"],
        )
    return plan.id


def test_workflow_delivers_only_selected_children_without_completing_step(
    session, project_id, tmp_path, monkeypatch
):
    from stackos.plugins.builtin_engineering import task_delivery

    plan_id = workflow_task(session, project_id)
    calls = []

    def run_agent(*args, sandbox, **kwargs):
        packet = json.loads(args[3])
        calls.append(packet["brief"]["ticket"]["key"])
        delivery = args[4] == task_delivery().DELIVERY_SCHEMA
        sha = commit(tmp_path, "selected") if delivery else git(tmp_path, "rev-parse", "HEAD")
        return AgentRunResult(
            0,
            thread_id=f"selected-{delivery}",
            result={
                "status" if delivery else "decision": "complete" if delivery else "approve",
                "summary": "Done",
                "commit_sha": sha,
                **({} if delivery else {"followups": []}),
            },
        )

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
    )
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key=f"workflow-{plan_id}")
    assert calls == ["selected", "selected"]
    assert snapshot.tasks[0].status == "in-progress"
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "complete"
    statuses = {ticket.key: ticket.status for ticket in snapshot.tickets}
    assert statuses[f"workflow-{plan_id}-deliver"] == "in-progress"
    assert statuses["other-step"] == statuses["group"] == "not-started"


@pytest.mark.parametrize("failure", ["pending", "task", "step", "partial"])
def test_workflow_selector_rejected_before_scheduling(session, project_id, tmp_path, failure):
    plan_id = workflow_task(session, project_id, running=failure != "pending")
    scope = {"task_key": f"workflow-{plan_id}", "run_plan_id": plan_id, "step_id": "deliver"}
    if failure == "task":
        prepared_task(session, project_id)
        scope["task_key"] = "deliver"
    elif failure == "step":
        scope["step_id"] = "missing"
    elif failure == "partial":
        scope.pop("step_id")
    scheduler = QueuedScheduler()
    with pytest.raises((ValidationError, NotFoundError, SchemaValidationError)):
        submit(session, project_id, tmp_path, scheduler, **scope)
    assert scheduler.jobs == []


def test_queued_workflow_stops_when_step_is_no_longer_running(
    session, project_id, tmp_path, monkeypatch
):
    from stackos.plugins.builtin_engineering import task_delivery

    plan_id = workflow_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
    )
    plans = RunPlanRepository(session)
    plan = plans.get(plan_id)
    plans.record_step(
        run_plan_id=plan_id,
        run_id=plan.run_id,
        step_id="deliver",
        status=RunPlanStepStatus.BLOCKED,
        error="Operator paused delivery",
    )
    monkeypatch.setattr(
        task_delivery(), "run_agent", lambda *a, **kw: pytest.fail("must not launch")
    )
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key=f"workflow-{plan_id}")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert "running workflow step" in snapshot.tasks[0].metadata_json["delivery"]["reason"]
    assert (
        next(ticket for ticket in snapshot.tickets if ticket.key == "selected").status
        == "not-started"
    )


def feedback_harness(session, project_id, tmp_path, monkeypatch, decisions, *, fault=None):
    """Real commits and durable tracker reads around each controlled native response."""
    from stackos.plugins.builtin_engineering import task_delivery

    calls = []
    base = git(tmp_path, "rev-parse", "HEAD")

    def run_agent(host, directory, instructions, prompt, schema, *, sandbox, **selection):
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        phase = "delivery" if schema == task_delivery().DELIVERY_SCHEMA else "review"
        resume = selection.get("resume_thread_id")
        previous = [call for call in calls if call["key"] == key and call["phase"] == phase]
        assert resume == (f"{key}-{phase}" if previous else None)
        assert sandbox == "workspace-write"  # Both Codex roles execute focused specs.
        assert "SIDE NOTE" not in prompt
        assert packet["brief"]["task"]["context_json"]["operator_inputs"] == ["Keep input"]
        if previous and phase == "delivery":
            with Session(session.get_bind()) as check:
                durable = TrackerRepository(check).brief(project_id=project_id, ticket_key=key)
                evidence = durable.ticket.completion_evidence_json["automatic_delivery"]
                assert evidence["repair_pass_used"] is True
                assert evidence["attempts"][-1]["review"]["result"]["decision"] == "reject"
            assert packet["review_result"]["result"]["summary"] == "Scoped repair"
        calls.append({"key": key, "phase": phase, "resume": resume, "packet": packet})
        identity = f"{key}-{phase}"
        if phase == "delivery":
            sha = commit(tmp_path, f"{key}-{len(calls)}")
            value = {"status": "complete", "summary": "Checked specs", "commit_sha": sha}
            if previous and fault == "stale-repair":
                git(tmp_path, "reset", "--hard", packet["candidate"]["base_commit_sha"])
                value["commit_sha"] = packet["candidate"]["base_commit_sha"]
        else:
            value = {
                "decision": decisions.pop(0),
                "summary": "Scoped repair",
                "commit_sha": packet["candidate"]["commit_sha"],
                "followups": [{"text": f"SIDE NOTE {key} {len(calls)}"}],
            }
            if fault == "stale-review":
                value["commit_sha"] = base
            elif fault == "wrong-review":
                value["commit_sha"] = "0" * 40
            elif fault == "dirty-review":
                (tmp_path / "uncommitted").write_text("reviewer edit")
            elif fault == "mutating-review":
                value["commit_sha"] = commit(tmp_path, "reviewer-commit")
            elif fault == "schema":
                value.pop("followups")
            elif fault == "native":
                return AgentRunResult(1, thread_id=identity, error="Required check unavailable")
        if fault == "missing-id" or (fault == "missing-review-id" and phase == "review"):
            identity = None
        elif resume and (
            (fault == "wrong-id" and phase == "delivery")
            or (fault == "wrong-review-id" and phase == "review")
        ):
            identity = "wrong-session"
        elif fault == "shared-id" and phase == "review":
            identity = f"{key}-delivery"
        return AgentRunResult(0, thread_id=identity, result=value)

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    return calls


def feedback_task(session, project_id):
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    repo.update_task(
        project_id=project_id,
        task_key="deliver",
        patch_json={
            "context_json": {
                "operator_inputs": ["Keep input"],
                "other": "preserved",
                "followups": [
                    {
                        "text": "SIDE NOTE existing",
                        "source_ticket_key": "older",
                        "source_attempt": 1,
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        },
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={
            "completion_evidence_json": {
                "unrelated": {"keep": True},
                "prior_review": {"deep": {"followups": [{"text": "SIDE NOTE nested"}]}},
            }
        },
    )


def test_feedback_reject_repair_approve_preserves_sessions_attempts_and_notes(
    session, project_id, tmp_path, monkeypatch
):
    feedback_task(session, project_id)
    calls = feedback_harness(
        session, project_id, tmp_path, monkeypatch, ["reject", "approve", "approve"]
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    assert [(call["key"], call["phase"], call["resume"]) for call in calls] == [
        ("first", "delivery", None),
        ("first", "review", None),
        ("first", "delivery", "first-delivery"),
        ("first", "review", "first-review"),
        ("second", "delivery", None),
        ("second", "review", None),
    ]
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    first, second = snapshot.tickets
    evidence = first.completion_evidence_json["automatic_delivery"]
    assert first.completion_evidence_json["unrelated"] == {"keep": True}
    assert "SIDE NOTE nested" in json.dumps(first.completion_evidence_json["prior_review"])
    prior = evidence["attempts"][0]
    assert prior["review"]["result"]["decision"] == "reject"
    assert prior["candidate"]["commit_sha"] != evidence["candidate"]["commit_sha"]
    assert evidence["candidate"]["base_commit_sha"] == prior["candidate"]["commit_sha"]
    assert evidence["review"]["result"]["commit_sha"] == evidence["candidate"]["commit_sha"]
    assert evidence["sessions"]["delivery"]["thread_id"] == "first-delivery"
    assert evidence["sessions"]["review"]["thread_id"] == "first-review"
    assert (
        second.completion_evidence_json["automatic_delivery"]["sessions"]["review"]["thread_id"]
        == "second-review"
    )
    context = snapshot.tasks[0].context_json
    assert context["operator_inputs"] == ["Keep input"] and context["other"] == "preserved"
    notes = context["followups"]
    assert len(notes) == 4
    assert [(n["source_ticket_key"], n["source_attempt"]) for n in notes[1:]] == [
        ("first", 1),
        ("first", 2),
        ("second", 1),
    ]
    assert all(n["created_at"] for n in notes)
    assert snapshot.tasks[0].metadata_json["keep"] == "yes"
    assert len(snapshot.tickets) == 2 and second.dependency_keys == ["first"]


@pytest.mark.parametrize("decision", ["approve", "blocked", "reject"])
def test_feedback_followup_only_blocked_and_second_reject(
    session, project_id, tmp_path, monkeypatch, decision
):
    feedback_task(session, project_id)
    decisions = ["approve", "approve"] if decision == "approve" else [decision, decision]
    calls = feedback_harness(session, project_id, tmp_path, monkeypatch, decisions)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert snapshot.tickets[0].status == ("complete" if decision == "approve" else "failed")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == (
        "complete" if decision == "approve" else "stopped"
    )
    assert len(calls) == (2 if decision == "blocked" else 4)
    if decision != "approve":
        assert snapshot.tickets[1].status == "not-started"


@pytest.mark.parametrize(
    "fault,decision,expected_calls",
    [
        ("missing-id", "approve", 1),
        ("missing-review-id", "approve", 2),
        ("shared-id", "approve", 2),
        ("wrong-id", "reject", 3),
        ("wrong-review-id", "reject", 4),
        ("stale-repair", "reject", 3),
        ("stale-review", "reject", 2),
        ("wrong-review", "reject", 2),
        ("dirty-review", "reject", 2),
        ("mutating-review", "reject", 2),
        ("schema", "approve", 2),
        ("native", "approve", 2),
    ],
)
def test_feedback_identity_schema_and_check_failures_stop(
    session, project_id, tmp_path, monkeypatch, fault, decision, expected_calls
):
    feedback_task(session, project_id)
    calls = feedback_harness(
        session, project_id, tmp_path, monkeypatch, [decision, "approve"], fault=fault
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert len(calls) == expected_calls
    assert len(snapshot.tasks[0].context_json["followups"]) == (2 if expected_calls >= 3 else 1)


def test_feedback_requeue_preserves_consumed_repair_budget(
    session, project_id, tmp_path, monkeypatch
):
    feedback_task(session, project_id)
    calls = feedback_harness(
        session, project_id, tmp_path, monkeypatch, ["reject", "reject", "reject"]
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    assert len(calls) == 4
    session.expire_all()
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    assert len(calls) == 6
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["repair_pass_used"] is True
    assert len(evidence["attempts"]) == 2
    assert [a["review"]["result"]["decision"] for a in evidence["attempts"]] == ["reject", "reject"]
