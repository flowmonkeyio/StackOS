from __future__ import annotations

import json
from time import perf_counter

import pytest
from sqlalchemy import event
from sqlmodel import Session

from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.schema import TrackerMetricDemand


def test_ui_summary_demand_and_full_detail(session, project_id, monkeypatch):
    repo = TrackerRepository(session)
    for key in ("visible", "hidden"):
        repo.create_task(
            project_id=project_id, key=key, title=key, context_json={"prompt": "x" * 20000}
        )
        repo.create_ticket(project_id=project_id, task_key=key, key=f"{key}-ticket", title=key)
    session.commit()
    session.expunge_all()
    from stackos.repositories.tracker.agent_stats import ReceiptProjection

    loads = []
    original = ReceiptProjection.load

    def load(self, **kwargs):
        loads.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(ReceiptProjection, "load", load)
    result = repo.get(
        project_id=project_id,
        projection="ui",
        task_index_only=True,
        include_graph=False,
        metric_demand=TrackerMetricDemand(task_keys=["visible"]),
    )
    assert {task.key for task in result.tasks} == {"visible", "hidden"}
    assert result.scope_complete and result.projection == "ui"
    visible, hidden = sorted(result.tasks, key=lambda task: task.key, reverse=True)
    assert visible.key == "visible" and visible.agent_stats is not None
    assert hidden.agent_stats is None and hidden.work_duration is None
    assert all("context_json" not in task.model_dump() for task in result.tasks)
    assert {tid for call in loads for tid in call.get("task_ids", [])} == {visible.id}
    full = repo.get(project_id=project_id, task_key="hidden", include_graph=False)
    assert full.tasks[0].context_json == {"prompt": "x" * 20000}
    assert full.projection == "full"


def test_selected_graph_reads_only_selected_scalar_records(session, project_id):
    repo = TrackerRepository(session)
    for key in ("selected", "unrelated"):
        repo.create_task(
            project_id=project_id, key=key, title=key, context_json={"large": "x" * 10000}
        )
        repo.create_ticket(
            project_id=project_id,
            task_key=key,
            key=f"{key}-ticket",
            title=key,
            context_json={"evidence": "y" * 10000},
        )
    session.commit()
    statements = []
    engine = session.get_bind()

    def observe(_conn, _cursor, sql, params, _context, _many):
        statements.append((sql, params))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with Session(engine) as reader:
            result = TrackerRepository(reader).get(
                project_id=project_id, task_key="selected", projection="ui"
            )
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert [task.key for task in result.tasks] == ["selected"]
    assert [ticket.key for ticket in result.tickets] == ["selected-ticket"]
    assert all(node.data.keys() <= {"key", "item_id"} for node in result.graph.nodes)
    assert all("context_json" not in sql for sql, _ in statements)
    with pytest.raises(NotFoundError):
        repo.get(project_id=project_id, task_key="missing", projection="ui")
    with pytest.raises(ValidationError):
        repo.get(
            project_id=project_id,
            projection="ui",
            include_graph=False,
            metric_demand=TrackerMetricDemand(task_keys=["missing"]),
        )


def test_demand_is_explicit_and_bounded():
    assert TrackerMetricDemand().task_keys == []
    with pytest.raises(ValueError):
        TrackerMetricDemand(task_keys=[str(i) for i in range(201)])


def test_summary_retains_canonical_queued_stopped_terminal_and_filter_facts(session, project_id):
    from tests.integration.test_repositories.test_work_duration import receipt

    repo = TrackerRepository(session)
    agent = {"name": "fixture", "instructions": "Never execute"}
    for key, status, delivery, invoked in [
        ("new-queue", "in-progress", "queued", False),
        ("started-queue", "in-progress", "queued", True),
        ("stopped", "in-progress", "stopped", False),
        ("terminal", "complete", "stopped", False),
        ("deferred", "deferred", "stopped", False),
    ]:
        repo.create_task(
            project_id=project_id,
            key=key,
            title=key,
            status=status,
            source_json={"template_key": "fixture", "private": "must not return"},
            metadata_json={
                "delivery": {
                    "status": delivery,
                    "job_ref": "inert",
                    "delivery_agent": agent,
                    "review_agent": agent,
                }
            },
        )
        repo.create_ticket(
            project_id=project_id,
            task_key=key,
            key=f"{key}-child",
            title=key,
            assignee="visible-filter",
        )
        if invoked:
            repo.update_ticket(
                project_id=project_id,
                ticket_key=f"{key}-child",
                patch_json={
                    "completion_evidence_json": {
                        "automatic_delivery": {"attempt": 1, "delivery": receipt(0, 60)}
                    },
                },
            )
        # Child creation/rollup may reopen terminal parents; establish the intended final state.
        repo.update_task(project_id=project_id, task_key=key, patch_json={"status": status})
    raw = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
    ui = repo.get(project_id=project_id, projection="ui", task_index_only=True, include_graph=False)
    assert (
        {task.key: task.board_column for task in ui.tasks}
        == {
            "new-queue": "todo",
            "started-queue": "in-progress",
            "stopped": "stopped",
            "terminal": "done",
            "deferred": "backlog",
        }
        == {task.key: task.board_column for task in raw.tasks}
    )
    for task, full in zip(ui.tasks, raw.tasks, strict=True):
        assert task.ticket_summary == full.ticket_summary
        assert task.source_json == {"template_key": "fixture"}
        assert "private" not in task.model_dump_json()
        assert task.agent_stats is None


@pytest.mark.parametrize("instructions", ["\t\n\u2003", "\x00"])
def test_summary_handoff_validation_matches_canonical_text(session, project_id, instructions):
    repo = TrackerRepository(session)
    agent = {"name": "fixture", "instructions": instructions}
    repo.create_task(
        project_id=project_id,
        key="validation",
        title="Validation",
        metadata_json={
            "delivery": {
                "status": "stopped",
                "job_ref": "inert",
                "delivery_agent": agent,
                "review_agent": agent,
            },
        },
    )
    full = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
    ui = repo.get(project_id=project_id, task_index_only=True, include_graph=False, projection="ui")
    assert ui.tasks[0].board_column == full.tasks[0].board_column


def test_empty_execution_object_does_not_make_queued_work_started(session, project_id):
    repo = TrackerRepository(session)
    agent = {"name": "fixture", "instructions": "Never execute"}
    repo.create_task(
        project_id=project_id,
        key="queued",
        title="Queued",
        metadata_json={
            "delivery": {
                "status": "queued",
                "job_ref": "inert",
                "delivery_agent": agent,
                "review_agent": agent,
            },
        },
    )
    repo.create_ticket(project_id=project_id, task_key="queued", key="pending", title="Pending")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="pending",
        patch_json={
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 1,
                    "delivery": {
                        "launch": {
                            "agent": "fixture",
                            "host_key": "codex",
                            "started_at": "2026-11-01T00:00:00Z",
                        },
                        "execution": {},
                    },
                }
            },
        },
    )
    ui = repo.get(project_id=project_id, task_index_only=True, include_graph=False, projection="ui")
    assert ui.tasks[0].board_column == "todo"


def test_five_visible_metrics_do_not_read_five_thousand_hidden_histories(
    session, project_id, monkeypatch, record_property
):
    from stackos.db.models import TrackerTask, TrackerTicket
    from stackos.repositories.tracker import agent_stats
    from tests.integration.test_repositories.test_agent_statistics import _receipt

    tracker = TrackerRepository(session).ensure_tracker(project_id=project_id)
    session.flush()
    tracker_id = tracker.id
    payload = {"prompt": "private" * 600}

    def seed(start, count):
        tasks = [
            TrackerTask(
                tracker_id=tracker_id,
                project_id=project_id,
                key=f"work-{i}",
                title=f"Work {i}",
                status="in-progress" if i < 5 else "complete",
                source_kind="manual",
                context_json=payload,
                order_index=i,
            )
            for i in range(start, start + count)
        ]
        session.add_all(tasks)
        session.flush()
        for i, task in enumerate(tasks, start):
            receipt = _receipt(total=i + 10)
            receipt["launch"].update(started_at="2026-11-01T00:00:00Z", ticket_key=f"child-{i}")
            receipt.update(finished_at="2026-11-01T00:01:00Z", exit_code=0)
            session.add(
                TrackerTicket(
                    tracker_id=tracker_id,
                    project_id=project_id,
                    task_id=task.id,
                    key=f"child-{i}",
                    title=f"Child {i}",
                    kind="ticket",
                    status=task.status,
                    source_kind="manual",
                    context_json=payload,
                    completion_evidence_json={
                        "automatic_delivery": {
                            "attempt": 1,
                            "delivery": receipt,
                        }
                    },
                )
            )
        session.commit()
        session.expunge_all()

    seed(0, 5)
    original = agent_stats.decode_receipt
    decoded = []
    statements = []

    def decode(source):
        value = original(source)
        decoded.append(value["launch"]["ticket_key"])
        return value

    def observe(_conn, _cursor, sql, params, _context, _many):
        statements.append((sql, params))

    monkeypatch.setattr(agent_stats, "decode_receipt", decode)
    engine = session.get_bind()

    def read(keys):
        agent_stats._normalized_receipt.cache_clear()
        decoded.clear()
        statements.clear()
        event.listen(engine, "before_cursor_execute", observe)
        started = perf_counter()
        try:
            with Session(engine) as reader:
                result = TrackerRepository(reader).get(
                    project_id=project_id,
                    projection="ui",
                    task_index_only=True,
                    include_graph=False,
                    metric_demand=TrackerMetricDemand(task_keys=keys),
                )
                encoded = result.model_dump_json()
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        heavy = [task for task in result.tasks if task.agent_stats is not None]
        receipt_queries = [(sql, params) for sql, params in statements if "receipt_json" in sql]
        counters = {
            "tasks": len(result.tasks),
            "demand": len(keys),
            "decoded_keys": sorted(set(decoded)),
            "decodes": len(decoded),
            "receipt_queries": len(receipt_queries),
            "bytes": len(encoded.encode()),
            "metric_bytes": len(
                json.dumps([task.agent_stats.model_dump(mode="json") for task in heavy])
            ),
            "ms": round((perf_counter() - started) * 1000, 2),
        }
        assert len(receipt_queries) == 1
        assert set(decoded) == {f"child-{key.split('-')[1]}" for key in keys}
        assert all(
            "context_json" not in sql and "completion_evidence_json" not in sql
            for sql, _ in statements
        )
        assert len(heavy) == len(keys)
        return result, counters

    keys = [f"work-{i}" for i in range(5)]
    _, before = read(keys)
    seed(5, 5000)
    result, after = read(keys)
    assert len(result.tasks) == 5005
    assert sum(task.ticket_summary.total_count for task in result.tasks) == 5005
    assert before["decodes"] == after["decodes"]
    assert before["metric_bytes"] == after["metric_bytes"]
    _, revealed = read([*keys, "work-5"])
    assert revealed["decoded_keys"] == [f"child-{i}" for i in range(6)]
    record_property(
        "demand_counters", json.dumps({"five": before, "hidden_5000": after, "reveal": revealed})
    )
    print(
        "DEMAND_COUNTERS=" + json.dumps({"five": before, "hidden_5000": after, "reveal": revealed})
    )


def test_ui_metrics_match_full_canonical_duration_usage_and_replacement(session, project_id):
    from stackos.repositories.tracker.agent_stats import usage_refresh
    from tests.integration.test_repositories.test_agent_statistics import _receipt

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="owned", title="Owned")
    repo.create_ticket(project_id=project_id, task_key="owned", key="child", title="Child")
    receipt = _receipt(total=71)
    receipt["launch"].update(started_at="2026-11-01T00:00:00Z", ticket_key="child")
    receipt.update(finished_at="2026-11-01T00:01:00Z", exit_code=0)
    repo.update_ticket(
        project_id=project_id,
        ticket_key="child",
        patch_json={
            "completion_evidence_json": {"automatic_delivery": {"attempt": 1, "delivery": receipt}},
        },
    )
    full = repo.get(project_id=project_id, task_key="owned", include_graph=False)
    demanded = usage_refresh(
        session,
        project_id,
        None,
        metric_demand=TrackerMetricDemand(task_keys=["owned"], ticket_keys=["child"]),
    )
    for field in ("agent_stats", "work_duration"):
        assert demanded["tasks"][0][field] == full.tasks[0].model_dump(mode="json")[field]
        assert demanded["tickets"][0][field] == full.tickets[0].model_dump(mode="json")[field]
    cleared = usage_refresh(session, project_id, [], metric_demand=TrackerMetricDemand())
    assert cleared["replace"] and cleared["scope_complete"]
    assert cleared["tasks"][0]["agent_stats"] is None
    assert cleared["tickets"] == []
    assert len(usage_refresh(session, project_id, None)["tickets"]) == 1
    assert len(usage_refresh(session, project_id, None, task_key="owned")["tasks"]) == 1
