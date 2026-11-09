"""Receipt oracles for bounded lifetime trends, independent of acceptance."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult, normalize_usage, usage_snapshot
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.agent_stats import (
    provisional_receipt,
    summarize_receipts,
    usage_refresh,
)
from stackos.task_delivery import _record_execution_transition


def receipt(index, counts, *, outcome="success", role="delivery_agent", samples=None, ticket="one"):
    stamp = (datetime(2026, 11, 8, tzinfo=UTC) + timedelta(seconds=index)).isoformat()
    usage = normalize_usage(
        usage_snapshot("fixture", "invocation", *counts[:2], cache_read=counts[2]),
        session_id=f"native-{index}",
        resumed=False,
    )
    return {
        "agent": f"agent-{index}",
        "selected_role": role,
        "launch": {
            "agent": f"agent-{index}",
            "host_key": "codex",
            "model": f"model-{index}",
            "started_at": stamp,
        },
        "execution": {
            "invocation_id": f"call-{index}",
            "agent": f"agent-{index}",
            "host_key": "codex",
            "model": f"model-{index}",
            "started_at": stamp,
            "ticket_key": ticket,
            "live_usage": {
                "usage": {"unit": "tokens", "status": "measured", "measured": usage["measured"]},
                "measured_at": stamp,
                "samples": samples if samples is not None else [{"at": stamp, **usage["measured"]}],
            },
        },
        "usage": usage,
        "exit_code": 0,
        "validated_outcome": outcome,
    }


def test_lifetime_oracle_reopen_all_outcomes_scope_and_zero_idle_readers(session, project_id):
    repo = TrackerRepository(session)
    for task, tickets in (("task-a", ("one", "two")), ("task-b", ("other",))):
        repo.create_task(project_id=project_id, key=task, title=task)
        for ticket in tickets:
            repo.create_ticket(project_id=project_id, task_key=task, key=ticket, title=ticket)
    # 10/8/1 + 20/unknown/2 + 0/0/0 + 30/15/3 + 40/20/4 + 50/25/5.
    # Cache and non-cache subtotals only use paired evidence; Output is independent.
    calls = [
        receipt(1, (10, 1, 8)),
        receipt(2, (20, 2, None), outcome="repair", role="review_agent"),
        receipt(3, (0, 0, 0), outcome="failed"),
        receipt(4, (30, 3, 15), outcome="blocked"),
        receipt(5, (40, 4, 20), outcome=None),  # cancelled/invalid result still measured
        receipt(6, (50, 5, 25)),
    ]
    automatic = {
        "attempt": 3,
        "attempts": [
            {"attempt": 1, "delivery": calls[0], "review": calls[1]},
            {"attempt": 2, "delivery": calls[2], "review": calls[3]},
            {"attempt": 3, "delivery": calls[4], "review": calls[5]},
        ],
        "delivery": calls[4],
        "review": calls[5],  # duplicate archived current occurrence counts once
    }
    for key, data in (
        ("one", automatic),
        ("two", {"attempt": 1, "delivery": receipt(7, (7, 1, 3), ticket="two")}),
        ("other", {"attempt": 1, "delivery": receipt(8, (999, 9, 900), ticket="other")}),
    ):
        repo.update_ticket(
            project_id=project_id,
            ticket_key=key,
            patch_json={"completion_evidence_json": {"automatic_delivery": data}},
        )
    with Session(session.get_bind()) as reopened:
        fresh = TrackerRepository(reopened).brief(project_id=project_id, ticket_key="one")
        own = fresh.ticket.agent_stats
        assert own.launch_attempts == 6
        assert (
            own.reported_usage.input,
            own.reported_usage.cache_read,
            own.reported_usage.remaining_input,
            own.reported_usage.output,
        ) == (150, 68, 62, 15)
        assert [(p.input, p.cache_read, p.remaining_input, p.output) for p in own.samples] == [
            (10, 8, 2, 1),
            (30, 8, 2, 3),
            (30, 8, 2, 3),
            (60, 23, 17, 6),
            (100, 43, 37, 10),
            (150, 68, 62, 15),
        ]
        assert own.last_execution.model == "model-6"
        assert fresh.ticket.active_execution is None
        assert fresh.task.agent_stats.reported_usage.total == 173
        assert fresh.task.agent_stats.last_execution.model == "model-7"
        assert fresh.task.agent_stats.last_execution.ticket_key == "two"
        last = fresh.task.agent_stats.samples[-1]
        assert (last.input, last.cache_read, last.remaining_input, last.output) == (157, 71, 66, 16)
        other = TrackerRepository(reopened).brief(project_id=project_id, ticket_key="other")
        assert other.task.agent_stats.reported_usage.total == 1008
        assert other.task.agent_stats.last_execution.ticket_key == "other"
        assert other.task.agent_stats.samples[-1].remaining_input == 99
        refresh = usage_refresh(reopened, project_id, None, task_key="task-a")
        assert {t["key"] for t in refresh["tasks"]} == {"task-a"}
        assert {t["key"] for t in refresh["tickets"]} == {"one", "two"}
        assert refresh["tickets"][0]["agent_stats"]["samples"]
    assert not service.usage_executions(project_id)


def test_bounded_measured_points_and_missing_cache_leave_supported_output():
    samples = []
    for i in range(1, 151):
        samples.append(
            {
                "at": (datetime(2026, 11, 8, tzinfo=UTC) + timedelta(seconds=i)).isoformat(),
                "input": i,
                "output": i,
                "total": i * 2,
            }
        )
    value = receipt(150, (150, 150, None), samples=samples)
    result = summarize_receipts([value], include_history=True)
    assert len(result.samples) == 120
    assert result.samples[0].input == 31
    assert result.samples[-1].output == 150
    assert result.samples[-1].remaining_input is None
    assert result.samples[-1].cache_read is None


def test_terminal_flush_saves_history_before_release_and_reopen(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="handoff", title="Handoff")
    repo.create_ticket(project_id=project_id, task_key="handoff", key="flush", title="Flush")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="flush",
        patch_json={
            "completion_evidence_json": {
                "automatic_delivery": {"attempt": 1, "delivery": {"launch": {"agent": "actual"}}}
            }
        },
    )
    call = service.ActiveInvocation(
        "flush-id",
        service.AgentExecutionScope(project_id, "handoff", "flush", "actual", 1, "delivery"),
        "codex",
        "actual-model",
        datetime.now(UTC).isoformat(),
    )
    service._active_invocations[call.invocation_id] = call
    try:
        call.usage = normalize_usage(
            usage_snapshot("fixture", "invocation", 10, 2, cache_read=5),
            session_id="native",
            resumed=False,
        )
        call.measured_at = call.started_at
        call.samples.append({"at": call.started_at, **call.usage["measured"]})
        stale = service.provisional_usage(project_id)
        service._finish(
            call,
            AgentRunResult(
                1,
                thread_id="native",
                usage=usage_snapshot("fixture", "invocation", 20, 4, cache_read=10),
            ),
        )
        assert call.reader is None and call.finished
        assert len(call.samples) == 2
        _record_execution_transition(lambda: Session(session.get_bind()), call, False)
        saved = deepcopy(call.projection())
    finally:
        service._unregister(call)
    with Session(session.get_bind()) as reopened:
        item = TrackerRepository(reopened).brief(project_id=project_id, ticket_key="flush").ticket
        assert item.completion_evidence_json["automatic_delivery"]["delivery"]["execution"] == saved
        assert item.agent_stats.samples[-1].output == 4
        assert item.agent_stats.last_execution.invocation_id == "flush-id"
        assert item.active_execution is None
        reconciled = summarize_receipts(
            provisional_receipt(project_id, "flush", item.completion_evidence_json, stale),
            include_history=True,
        )
        assert reconciled.samples[-1].output == 4
        assert reconciled.reported_usage.total == 24
    assert not call.samples and not service.provisional_usage(project_id)


def test_resume_points_are_invocation_deltas_and_overlap_order_is_independent():
    first = receipt(1, (100, 10, 80))
    second = receipt(2, (50, 5, 20))
    baseline = normalize_usage(
        usage_snapshot("codex.turn", "session", 100, 10, cache_read=80),
        session_id="same",
        resumed=False,
    )
    second["usage"] = normalize_usage(
        usage_snapshot("codex.turn", "session", 150, 15, cache_read=100),
        session_id="same",
        resumed=True,
        previous=baseline,
    )
    result = summarize_receipts([second, first], include_history=True)
    assert [(p.input, p.cache_read, p.remaining_input, p.output) for p in result.samples] == [
        (100, 80, 20, 10),
        (150, 100, 50, 15),
    ]
    assert result.reported_usage.total == 165
    assert result.last_execution.model == "model-2"


def test_legacy_missing_time_never_fabricates_history_or_active_identity():
    value = receipt(1, (0, 0, 0))
    value.pop("execution")
    value["launch"].pop("started_at")
    result = summarize_receipts([value], include_history=True)
    assert result.reported_usage.total == 0
    assert result.samples == [] and result.last_execution is None


@pytest.mark.parametrize("prior_call", [False, True])
def test_preflight_rejection_cannot_invent_or_replace_last_execution_after_reload(
    session, project_id, tmp_path, prior_call
):
    result = service.run_agent("unsupported-identity-fixture", tmp_path, "fixture", "fixture", {})
    assert result.error and result.exit_code is result.thread_id is result.result is None
    rejected = {
        "launch": {
            "agent": "Never started",
            "host_key": "unsupported-identity-fixture",
            "model": "configured-only",
            "started_at": "2026-11-08T12:00:00+00:00",
        },
        **asdict(result),
    }
    previous = receipt(1, (10, 1, 8), ticket="identity-child")
    automatic = {
        "attempt": 2,
        "attempts": [{"attempt": 1, "delivery": previous}] if prior_call else [],
        "delivery": rejected,
    }
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="identity", title="Identity")
    repo.create_ticket(
        project_id=project_id, task_key="identity", key="identity-child", title="Child"
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="identity-child",
        patch_json={"completion_evidence_json": {"automatic_delivery": automatic}},
    )
    with Session(session.get_bind()) as reopened:
        brief = TrackerRepository(reopened).brief(
            project_id=project_id, ticket_key="identity-child"
        )
        for metrics in (brief.ticket.agent_stats, brief.task.agent_stats):
            assert metrics.launch_attempts == 1 + int(prior_call)
            identity = metrics.last_execution
            if prior_call:
                assert identity.invocation_id == "call-1"
                assert identity.agent == "agent-1" and identity.model == "model-1"
            else:
                assert identity is None
        assert brief.ticket.active_execution is brief.task.active_execution is None
        updates = usage_refresh(reopened, project_id, None, task_key="identity")
        for projection in (updates["tasks"][0], updates["tickets"][0]):
            assert projection["agent_stats"]["last_execution"] == (
                brief.ticket.agent_stats.last_execution.model_dump() if prior_call else None
            )


@pytest.mark.parametrize(
    "evidence,executed",
    [
        ({"exit_code": None, "thread_id": None, "result": None}, False),
        ({"exit_code": False}, False),
        ({"error": "preflight rejected"}, False),
        ({"thread_id": "  "}, False),
        ({"exit_code": 0}, True),
        ({"exit_code": 1}, True),
        ({"exit_code": -9}, True),
        ({"thread_id": "native-session"}, True),
        ({"result": {}}, True),
    ],
)
def test_legacy_last_identity_requires_positive_execution_evidence(evidence, executed):
    legacy = receipt(1, (10, 1, 8))
    legacy.pop("execution")
    legacy.pop("exit_code")
    legacy["ticket_key"] = "one"
    legacy.update(evidence)
    metrics = summarize_receipts([legacy], include_history=True)
    assert metrics.launch_attempts == 1
    assert (metrics.last_execution is not None) is executed
