"""The fixed delivery loop uses real tracker state and a controlled harness."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from stackos.host_mcp.result import AgentRunResult
from stackos.mcp.context import MCPContext
from stackos.operations.tracker.schemas import TrackerStartDeliveryInput
from stackos.operations.tracker.write_handlers import tracker_start_delivery
from stackos.repositories.base import ValidationError
from stackos.repositories.tracker import TrackerRepository


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


def submit(session: Session, project_id: int, tmp_path: Path, scheduler: QueuedScheduler):
    arguments = TrackerStartDeliveryInput(
        project_id=project_id,
        task_key="deliver",
        project_dir=str(tmp_path),
        host_key="codex",
        delivery_agent={"name": "project-builder", "instructions": "Build the ticket."},
        review_agent={"name": "project-reviewer", "instructions": "Review the ticket."},
    )
    context = MCPContext(
        session=session,
        request_id="task-delivery-test",
        project_id=project_id,
        extras={"session_factory": lambda: Session(session.get_bind()), "scheduler": scheduler},
    )
    return asyncio.run(tracker_start_delivery(arguments, context, None)).data


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
        assert "delivery" not in (packet["brief"]["task"]["metadata_json"] or {})
        assert host == "codex" and Path(project_dir) == tmp_path
        with Session(session.get_bind()) as check:
            repo = TrackerRepository(check)
            brief = repo.brief(project_id=project_id, ticket_key=ticket_key)
            assert brief.ticket.status == "in-progress"
            if ticket_key == "second":
                assert (
                    repo.brief(project_id=project_id, ticket_key="first").ticket.status
                    == "complete"
                )
        calls.append((ticket_key, instructions, sandbox))
        result = (
            {"status": "complete", "summary": "Implemented"}
            if sandbox == "workspace-write"
            else {"decision": "approve", "summary": "Verified"}
        )
        return AgentRunResult(0, thread_id=f"thread-{len(calls)}", result=result)

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    first = submit(session, project_id, tmp_path, scheduler)
    again = submit(session, project_id, tmp_path, scheduler)
    assert first == again and first["status"] == "queued"
    assert calls == [] and len(scheduler.jobs) == 1
    scheduler.run()
    assert [(key, sandbox) for key, _, sandbox in calls] == [
        ("first", "workspace-write"),
        ("first", "read-only"),
        ("second", "workspace-write"),
        ("second", "read-only"),
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
        calls.append(sandbox)
        if failure == "delivery" or (failure == "review" and sandbox == "read-only"):
            return AgentRunResult(1, error="Harness failed")
        if sandbox == "workspace-write":
            return AgentRunResult(
                0,
                result={
                    "status": "blocked" if failure == "blocked" else "complete",
                    "summary": "Delivery result",
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
    with pytest.raises(ValidationError, match="independent"):
        submit(session, project_id, tmp_path, QueuedScheduler())
