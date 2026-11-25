"""Bounded statistics reads retain canonical receipt and source-time semantics."""

import json
from copy import deepcopy
from datetime import date, datetime

import pytest
from sqlalchemy import event, update
from sqlmodel import Session, select

from stackos.db.models import Project, TrackerReceiptProjection, TrackerTicket
from stackos.repositories.tracker import TrackerRepository, delivery_stats
from stackos.repositories.tracker import agent_stats as stats
from stackos.repositories.tracker import receipt_projection as index
from stackos.repositories.tracker.agent_stats import query_agent_stats
from tests.helpers.agent_statistics import seed_statistics_workload
from tests.integration.test_repositories.test_agent_statistics import _receipt, _store


def test_home_read_token_tracks_live_counters_and_scope_without_exposing_them(
    session, project_id, monkeypatch
):
    calls = [{"project_id": project_id, "invocation_id": "owned-call", "live_usage": {"total": 10}}]
    monkeypatch.setattr(
        stats.host_service,
        "usage_executions",
        lambda pid: calls if pid in (None, project_id) else [],
    )
    before = stats.usage_read_token(session, project_id)
    assert before == stats.usage_read_token(session, project_id)
    assert before != stats.usage_read_token(session, None)
    calls[0]["live_usage"]["total"] = 11
    assert stats.usage_read_token(session, project_id) != before
    assert len(before) == 64 and "owned-call" not in before


def test_global_home_read_token_ignores_other_owners(session, project_id, monkeypatch):
    from uuid import uuid4

    from stackos.auth import UserPrincipal
    from stackos.repositories.projects import ProjectRepository

    own = session.get(Project, project_id)
    own.owner_user_id = str(uuid4())
    own.environment_id = "read-token-test"
    session.add(own)
    session.commit()
    foreign = (
        ProjectRepository(session)
        .create(slug="foreign-token", name="Foreign", domain="example.test")
        .data.id
    )
    session.info["user_principal"] = UserPrincipal(own.owner_user_id, own.environment_id)
    calls = [{"project_id": foreign, "invocation_id": "foreign-call", "live_usage": {"total": 10}}]
    monkeypatch.setattr(stats.host_service, "usage_executions", lambda _: calls)
    before = stats.usage_read_token(session, None)
    calls[0]["live_usage"]["total"] = 11
    row = session.get(Project, foreign)
    row.is_active = not row.is_active
    session.add(row)
    session.commit()
    assert stats.usage_read_token(session, None) == before
    own.is_active = not own.is_active
    session.add(own)
    session.commit()
    assert stats.usage_read_token(session, None) != before


def test_full_year_retains_daily_groups_exact_totals_and_bounded_reads(
    session, project_id, monkeypatch
):
    seed_statistics_workload(session, project_id, tasks=4, receipts=365)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 1, 1, tzinfo=tz)

    monkeypatch.setattr(delivery_stats, "datetime", FixedDatetime)
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = TrackerRepository(session).agent_stats(
            project_id=project_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    # Existing seven reads plus event-owner validation/position and one compact
    # project/revision vector; none scales with day/card count.
    assert len(statements) == 10
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert not any("completion_evidence_json" in statement for statement in statements)
    assert len(result.daily) == len(result.delivery_trends.daily) == 365
    assert result.totals.launch_attempts == 365
    assert result.totals.reported_usage.total == 4015
    assert result.totals.reported_usage.remaining_input == 2190
    assert result.totals.reported_usage.cache_read == 1460
    assert result.totals.reported_usage.output == 365
    assert result.totals.coverage.usage_measured_attempts == 365
    assert result.totals.coverage.usage_unavailable_attempts == 0
    assert sum(row.reported_usage.total for row in result.rows) == 4015
    assert result.delivery_trends.totals.work_time.total_seconds == 3650
    for day in result.daily:
        assert day.reported_usage.total == 11
        assert day.coverage.usage_measured_attempts == 1
        for groups in (day.by_agent, day.by_model, day.by_agent_model):
            assert sum(row.reported_usage.total for row in groups) == 11


@pytest.mark.parametrize("writer", ["orm", "sqlite", "rebuild"])
def test_cancelled_current_and_archive_inputs_survive_each_projection_writer(
    session, project_id, writer
):
    archive = []
    for attempt, outcome, total in ((1, "repair", 15), (2, None, 20), (3, None, 30)):
        receipt = _receipt("review_agent", outcome, total)
        receipt["launch"]["started_at"] = f"2026-01-01T0{attempt}:00:00Z"
        receipt["finished_at"] = f"2026-01-01T0{attempt}:10:00Z"
        archive.append({"attempt": attempt, "review": receipt})
    _store(session, project_id, "cancel-projection", {**archive[-1], "attempts": archive})
    ticket = session.exec(
        select(TrackerTicket).where(TrackerTicket.key == "cancel-projection")
    ).one()
    previous = deepcopy(ticket.completion_evidence_json)
    evidence = deepcopy(previous)
    automatic = evidence["automatic_delivery"]
    automatic["review"]["cancelled"] = True
    for occurrence in automatic["attempts"][1:]:
        occurrence["review"]["cancelled"] = True
    if writer == "orm":
        ticket.completion_evidence_json = evidence
        session.commit()
    else:
        connection = session.connection()
        connection.execute(
            update(TrackerTicket)
            .where(TrackerTicket.id == ticket.id)
            .values(completion_evidence_json=evidence)
        )
        if writer == "sqlite":
            index.sync_sqlite_receipt_projection(
                connection.connection.driver_connection, ticket.id, evidence, previous
            )
        else:
            index.initialize_receipt_projection(connection)
        session.commit()
    session.expire_all()
    assert session.get(TrackerTicket, ticket.id).completion_evidence_json == evidence
    rows = session.exec(
        select(TrackerReceiptProjection).where(TrackerReceiptProjection.ticket_id == ticket.id)
    ).all()
    assert len(rows) == 3
    assert sorted(row.review_outcome for row in rows) == ["repair", "unclassified", "unclassified"]
    assert all(json.loads(row.receipt_json)["cancelled"] is True for row in rows if row.attempt > 1)
    report = TrackerRepository(session).agent_stats(project_id=project_id)
    assert report.totals.launch_attempts == 3
    assert report.totals.reported_usage.total == 65
    assert report.totals.review_outcomes == {"repair": 1}
    assert report.totals.recorded_reviewer_rejects == 1
    assert report.totals.coverage.classification_unknown_attempts == 2
    durations = {}
    stats.query_ticket_agent_stats(session, ticket_ids={ticket.id}, work_durations=durations)
    assert durations[ticket.id].total_seconds == 1800


def test_mixed_runtime_and_account_observations_keep_native_order_and_deduplicate(
    session, project_id
):
    def receipt(index):
        value = _receipt("review_agent", None, 10 * index)
        value["launch"]["started_at"] = f"2026-11-22T00:00:0{index}Z"
        value["execution"] = {"invocation_id": f"native-{index}"}
        return value

    first, runtime, account, current = [receipt(i) for i in range(1, 5)]
    automatic = {
        "attempt": 1,
        "review": current,
        "attempts": [{"attempt": 1, "review": deepcopy(current)}],
        "runtime_interruptions": [
            {"attempt": 1, "phase": "review", "receipt": runtime},
            {"attempt": 1, "phase": "review", "receipt": deepcopy(first)},
        ],
        "account_interruptions": [
            {"attempt": 1, "phase": "review", "receipt": first},
            {"attempt": 1, "phase": "review", "receipt": account},
        ],
    }
    # The same inventory supplies run_delivery's immediately preceding baseline.
    receipts = stats.invocation_receipts({"automatic_delivery": automatic})
    assert [r["execution"]["invocation_id"] for r in receipts] == [
        f"native-{i}" for i in range(1, 5)
    ]
    _store(session, project_id, "mixed-interruptions", automatic)
    original = TrackerRepository(session).agent_stats(project_id=project_id).totals
    assert original.launch_attempts == 4 and original.reported_usage.total == 100
    index.initialize_receipt_projection(session.connection())
    session.commit()
    assert TrackerRepository(session).agent_stats(project_id=project_id).totals == original


def test_short_report_does_not_read_full_evidence(session: Session, project_id: int, monkeypatch):
    seed_statistics_workload(session, project_id, tasks=4, receipts=365)
    statements = []
    selected = []
    original = stats._normalized_receipt

    def normalize(*args):
        selected.append(args)
        return original(*args)

    monkeypatch.setattr(stats, "_normalized_receipt", normalize)

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = query_agent_stats(
            session,
            project_id=project_id,
            start_date=date(2026, 12, 25),
            end_date=date(2026, 12, 31),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result.totals.launch_attempts == 7
    assert result.totals.reported_usage.total == 77
    assert [day.reported_usage.total for day in result.daily] == [11] * 7
    assert not any("completion_evidence_json" in sql for sql in statements)
    assert len(selected) == 7


@pytest.mark.parametrize("tasks", [5, 500])
def test_fixed_visible_scope_does_not_normalize_hidden_history(
    session, project_id, monkeypatch, tasks
):
    ids = seed_statistics_workload(session, project_id, tasks=tasks, receipts=tasks * 6)
    selected = []
    original = stats._normalized_receipt

    def normalize(*args):
        selected.append(args)
        return original(*args)

    monkeypatch.setattr(stats, "_normalized_receipt", normalize)
    visible = session.get(TrackerTicket, ids[0])
    result = stats.query_task_agent_stats(
        session, tracker_id=visible.tracker_id, task_ids={visible.task_id}
    )
    assert result[visible.task_id].reported_usage.total == 66
    assert len(selected) == 6


@pytest.mark.parametrize("entity", ["ticket", "task", "project"])
def test_source_deletion_removes_index_and_cached_visibility(session, project_id, entity):
    from stackos.db.models import TrackerTask

    ids = seed_statistics_workload(session, project_id, tasks=1, receipts=3)
    assert stats.query_agent_stats(session, project_id=project_id).totals.launch_attempts == 3
    ticket = session.get(TrackerTicket, ids[0])
    source = {
        "ticket": ticket,
        "task": session.get(TrackerTask, ticket.task_id),
        "project": session.get(Project, project_id),
    }[entity]
    session.delete(source)
    session.commit()
    assert session.exec(select(TrackerReceiptProjection)).all() == []
    assert stats.query_agent_stats(session, project_id=project_id).totals.launch_attempts == 0


def test_request_and_unchanged_reads_share_contributions(session, project_id, monkeypatch):
    ids = seed_statistics_workload(session, project_id, tasks=4, receipts=16)
    tickets = session.exec(select(TrackerTicket)).all()
    task_ids = {ticket.task_id for ticket in tickets}
    stats._normalized_receipt.cache_clear()
    index.decode_receipt.cache_clear()
    prepared = []
    original = stats._receipt_events

    def prepare(fact):
        prepared.append(fact)
        return original(fact)

    monkeypatch.setattr(stats, "_receipt_events", prepare)
    projection = stats.ReceiptProjection(session)
    tasks = stats.query_task_agent_stats(
        session, tracker_id=None, task_ids=task_ids, projection=projection
    )
    assert len(prepared) == 16
    original_tickets = stats.query_ticket_agent_stats(
        session, ticket_ids=set(ids), projection=projection
    )
    assert len(prepared) == 16
    # A later request reads fresh scoped rows but reuses each unchanged contribution.
    assert stats.query_ticket_agent_stats(session, ticket_ids=set(ids)) == original_tickets
    assert len(prepared) == 16
    assert sum(metric.reported_usage.total for metric in tasks.values()) == 176
    # Request outputs cannot corrupt reused facts or another consumer's model.
    original_tickets[ids[0]].samples[0].intervals[0].kind = "reset"
    assert (
        stats.query_ticket_agent_stats(session, ticket_ids={ids[0]})[ids[0]]
        .samples[0]
        .intervals[0]
        .kind
        == "baseline"
    )
    # The tracker selector remains part of request scope even after prior loading.
    denied = stats.query_task_agent_stats(
        session, tracker_id=-1, task_ids=task_ids, projection=projection
    )
    assert all(metric.launch_attempts == 0 for metric in denied.values())


def test_one_changed_receipt_preserves_unchanged_contributions(session, project_id, monkeypatch):
    ids = seed_statistics_workload(session, project_id, tasks=4, receipts=16)
    stats.query_ticket_agent_stats(session, ticket_ids=set(ids))
    ticket = session.get(TrackerTicket, ids[0])
    before = deepcopy(ticket.completion_evidence_json)
    updated = deepcopy(before)
    # Change one archived contribution; the current receipt and other archive members stay fixed.
    receipt = updated["automatic_delivery"]["attempts"][0]["delivery"]
    receipt["usage"]["measured"].update(input=20, total=21)
    rewritten = []
    normalized = index.receipt_input

    def project(source):
        rewritten.append(source)
        return normalized(source)

    monkeypatch.setattr(index, "receipt_input", project)
    ticket.completion_evidence_json = updated
    session.add(ticket)
    session.commit()
    assert len(rewritten) == 1
    prepared = []
    original = stats._receipt_events

    def prepare(fact):
        prepared.append(fact)
        return original(fact)

    monkeypatch.setattr(stats, "_receipt_events", prepare)
    actual = stats.query_ticket_agent_stats(session, ticket_ids=set(ids))
    assert sum(metric.reported_usage.total for metric in actual.values()) == 186
    assert len(prepared) == 1
    assert actual[ids[0]] == stats.summarize_receipts(
        [{**receipt, "ticket_key": ticket.key} for receipt in stats.invocation_receipts(updated)],
        include_history=True,
    )
    # Rollback cannot leave a projected receipt from an uncommitted source write.
    ticket.completion_evidence_json = before
    session.flush()
    session.rollback()
    assert stats.query_ticket_agent_stats(session, ticket_ids={ids[0]}) == {ids[0]: actual[ids[0]]}


def test_repair_cleanup_and_project_scope_are_fresh(session, project_id):
    ids = seed_statistics_workload(session, project_id, tasks=2, receipts=8)
    repo = TrackerRepository(session)
    assert repo.agent_stats(project_id=project_id).totals.reported_usage.total == 88
    ticket = session.get(TrackerTicket, ids[0])
    current = deepcopy(ticket.completion_evidence_json["automatic_delivery"])
    next_receipt = deepcopy(current["delivery"])
    next_receipt.update(selected_role="review_agent", validated_outcome="repair")
    ticket.completion_evidence_json = {
        "automatic_delivery": {
            "attempt": 9,
            "review": next_receipt,
            "attempts": [
                *current["attempts"],
                {"attempt": current["attempt"], "delivery": current["delivery"]},
            ],
        }
    }
    session.commit()
    assert repo.agent_stats(project_id=project_id).totals.launch_attempts == 9
    assert repo.agent_stats(project_id=project_id).totals.recorded_reviewer_rejects == 1
    project = session.get(Project, project_id)
    project.is_active = False
    session.commit()
    assert repo.agent_stats_all(is_active=True).totals.launch_attempts == 0
    assert repo.agent_stats_all(is_active=False).totals.launch_attempts == 9
    # Clear/reopen history only when the authoritative source actually removes it.
    ticket.completion_evidence_json = None
    session.commit()
    assert repo.agent_stats(project_id=project_id).totals.launch_attempts == 4
    session.delete(session.get(TrackerTicket, ids[1]))
    session.commit()
    assert session.exec(select(TrackerReceiptProjection)).all() == []
    assert repo.agent_stats(project_id=project_id).totals.launch_attempts == 0


def test_usage_and_timing_keep_their_distinct_legacy_inventory(session, project_id):
    ids = seed_statistics_workload(session, project_id, tasks=1, receipts=1)
    ticket = session.get(TrackerTicket, ids[0])
    automatic = deepcopy(ticket.completion_evidence_json["automatic_delivery"])
    # Current malformed launch supersedes timing, while token inventory retains
    # the same identity's valid archived launch under its existing require_launch rule.
    automatic["delivery"] = {"finished_at": "2026-01-01T00:00:20+00:00"}
    ticket.completion_evidence_json = {"automatic_delivery": automatic}
    session.commit()
    durations = {}
    result = stats.query_ticket_agent_stats(session, ticket_ids=set(ids), work_durations=durations)
    assert result[ids[0]].reported_usage.total == 11
    assert durations[ids[0]].unavailable_intervals == 1
    assert durations[ids[0]].issues == ["missing_start"]
    assert stats.query_agent_stats(session, project_id=project_id).totals.reported_usage.total == 11


def test_sqlite_writer_preserves_exact_midnight_date_selection(session, project_id):
    ids = seed_statistics_workload(session, project_id, tasks=1, receipts=1)
    ticket = session.get(TrackerTicket, ids[0])
    previous = deepcopy(ticket.completion_evidence_json)
    evidence = deepcopy(previous)
    # Replace both copies of the same occurrence, through the existing raw-SQL
    # writer's transaction boundary, at an exact inclusive date boundary.
    for receipt in [
        evidence["automatic_delivery"]["delivery"],
        evidence["automatic_delivery"]["attempts"][0]["delivery"],
    ]:
        receipt["usage"]["measured"].update(input=20, total=21)
    connection = session.connection()
    connection.execute(
        update(TrackerTicket)
        .where(TrackerTicket.id == ids[0])
        .values(
            completion_evidence_json=evidence,
        )
    )
    index.sync_sqlite_receipt_projection(
        connection.connection.driver_connection, ids[0], evidence, previous
    )
    session.commit()
    result = stats.query_agent_stats(
        session, project_id=project_id, start_date=date(2026, 1, 1), end_date=date(2026, 1, 1)
    )
    assert result.totals.reported_usage.total == 21


@pytest.mark.parametrize("day,timezone", [(date.min, "Pacific/Kiritimati"), (date.max, "UTC")])
def test_valid_calendar_limits_do_not_overflow_query_bounds(session, project_id, day, timezone):
    result = stats.query_agent_stats(
        session, project_id=project_id, start_date=day, end_date=day, timezone=timezone
    )
    assert result.totals.launch_attempts == 0


def test_equal_receipt_values_do_not_make_archived_live_timing_current(session, project_id):
    ids = seed_statistics_workload(session, project_id, tasks=1, receipts=1)
    ticket = session.get(TrackerTicket, ids[0])
    execution = {
        "invocation_id": "same-source-value",
        "ticket_key": ticket.key,
        "agent": "worker",
        "host_key": "codex",
        "model": "recorded",
        "started_at": "2026-01-01T00:00:00Z",
    }
    receipt = {"launch": execution, "execution": execution}
    ticket.completion_evidence_json = {
        "automatic_delivery": {
            "attempt": 2,
            "delivery": deepcopy(receipt),
            "attempts": [{"attempt": 1, "delivery": deepcopy(receipt)}],
        }
    }
    session.commit()
    live = [
        {
            **execution,
            "project_id": project_id,
            "task_key": "perf-task-0",
            "attempt": 1,
            "phase": "delivery",
            "observed_at": "2026-01-01T00:00:10Z",
        }
    ]
    durations = {}
    stats.query_ticket_agent_stats(
        session, ticket_ids=set(ids), provisional={}, timing_snapshot=live, work_durations=durations
    )
    assert durations[ids[0]].total_seconds is None
    assert durations[ids[0]].unavailable_intervals == 2


def test_delivery_trends_timing_query_avoids_nested_loop_scan(session, project_id):
    seed_statistics_workload(session, project_id, tasks=4, receipts=24)
    engine = session.get_bind()
    plans: list[str] = []

    def observe(connection, _cursor, sql, params, _execution, _many):
        if sql.lstrip().upper().startswith("EXPLAIN"):
            return
        if "UNION ALL" in sql or "timing_selection" in sql:
            rows = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params).all()
            plans.extend(row[3] for row in rows)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        result = TrackerRepository(session).agent_stats(
            project_id=project_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert plans, "expected compound timing query plan"
    assert any("UNION ALL" in detail for detail in plans), plans
    assert not any("SCAN timing_selection" in detail for detail in plans), plans
    assert result.delivery_trends.totals.work_time.total_seconds == 240
    assert result.delivery_trends.totals.work_time.coverage.status == "complete"
