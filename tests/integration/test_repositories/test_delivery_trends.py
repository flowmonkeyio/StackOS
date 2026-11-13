"""Independent ledgers for the six shared Home delivery trends."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlmodel import select

from stackos.db.models import TrackerRevision, TrackerTask, TrackerTicket
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_agent_statistics import _receipt, _store


def _revision(session, tracker, kind, entity, before, after, at):
    tracker.rev += 1
    session.add(tracker)
    session.add(
        TrackerRevision(
            tracker_id=tracker.id,
            project_id=tracker.project_id,
            rev=tracker.rev,
            entity_kind=kind,
            entity_id=entity.id,
            entity_key=entity.key,
            change_kind="update",
            before_json=before,
            after_json=after,
            created_at=datetime.fromisoformat(at).replace(tzinfo=None),
        )
    )
    session.flush()


def _call(role, outcome, start, finish):
    receipt = _receipt(role, outcome)
    receipt["launch"]["started_at"] = start
    receipt["finished_at"] = finish
    return receipt


def ledger(session, pid):
    repo = TrackerRepository(session)
    # LA: Nov 1 is 25 hours. A span across midnight must be clipped, not
    # attributed wholesale to its launch or completion day.
    first = {
        "attempt": 1,
        "delivery": _call(
            "review_agent", "blocked", "2026-11-01T06:30:00+00:00", "2026-11-01T07:30:00+00:00"
        ),
    }
    second = {
        "attempt": 2,
        "review": _call(
            "review_agent", "repair", "2026-11-01T07:00:00+00:00", "2026-11-01T08:00:00+00:00"
        ),
    }
    third = {
        "attempt": 3,
        "review": _call(
            "review_agent", "success", "2026-11-02T07:30:00+00:00", "2026-11-02T08:30:00+00:00"
        ),
    }
    _store(session, pid, "ledger-a", {**third, "attempts": [first, second, deepcopy(third)]})
    _store(
        session,
        pid,
        "ledger-b",
        {
            "attempt": 1,
            "delivery": _call(
                "review_agent", "success", "2026-11-01T07:30:00+00:00", "2026-11-01T09:00:00+00:00"
            ),
        },
    )
    tracker = repo.ensure_tracker(project_id=pid)
    task = session.exec(select(TrackerTask).where(TrackerTask.tracker_id == tracker.id)).one()
    tickets = session.exec(
        select(TrackerTicket).where(TrackerTicket.tracker_id == tracker.id)
    ).all()
    for kind, entity in [("task", task), *[("ticket", ticket) for ticket in tickets]]:
        for before, after, at in [
            ("in-progress", "complete", "2026-11-01T07:20:00+00:00"),
            ("complete", "in-progress", "2026-11-02T08:10:00+00:00"),
            ("in-progress", "complete", "2026-11-02T08:20:00+00:00"),
        ]:
            _revision(session, tracker, kind, entity, {"status": before}, {"status": after}, at)
    for before, after, at in [
        ("running", "stopped", "2026-11-01T07:25:00+00:00"),
        ("stopped", "stopped", "2026-11-01T07:26:00+00:00"),
        ("stopped", "running", "2026-11-01T07:27:00+00:00"),
        ("running", "stopped", "2026-11-02T08:25:00+00:00"),
    ]:

        def state(value):
            return {"metadata_json": {"delivery": {"status": value}}}

        _revision(session, tracker, "task", task, state(before), state(after), at)
    session.commit()


FILTERS = dict(
    start_date=date(2026, 11, 1), end_date=date(2026, 11, 2), timezone="America/Los_Angeles"
)


def test_independent_completion_review_stop_and_interval_ledger(session, project_id):
    ledger(session, project_id)
    trends = (
        TrackerRepository(session).agent_stats(project_id=project_id, **FILTERS).delivery_trends
    )
    assert trends.totals.completed_tasks.total == 1
    assert trends.totals.completed_tickets.total == 2
    assert trends.totals.changes_requested.total == 1
    assert trends.totals.stops.total == 2
    rate = trends.totals.first_review_acceptance
    assert (rate.accepted, rate.reviewed, rate.rate) == (1, 2, 0.5)
    # Per-task union: Nov1 07:00-09:00 + Nov2 07:30-08:00 = 2.5h;
    # Nov2 08:00-08:30 = .5h. No duplicate task/ticket or archive totals.
    assert trends.totals.work_time.total_seconds == 10800
    assert [day.work_time.total_seconds for day in trends.daily] == [9000, 1800]
    assert [day.completed_tasks.total for day in trends.daily] == [1, 0]
    assert [day.stops.total for day in trends.daily] == [1, 1]
    assert [day.first_review_acceptance.reviewed for day in trends.daily] == [2, 0]
    later = (
        TrackerRepository(session)
        .agent_stats(project_id=project_id, **{**FILTERS, "start_date": date(2026, 11, 2)})
        .delivery_trends
    )
    assert later.totals.completed_tasks.total == 0
    assert later.totals.first_review_acceptance.reviewed == 0
    assert later.totals.work_time.total_seconds == 1800


def test_global_project_visibility_and_weighted_rate(session, project_id):
    ledger(session, project_id)
    projects = ProjectRepository(session)
    other = projects.create(slug="trend-other", name="Other", domain="test.invalid").data.id
    archive = projects.create(slug="trend-archive", name="Archive", domain="test.invalid").data.id
    for pid in (other, archive):
        _store(
            session,
            pid,
            "other-success",
            {
                "attempt": 1,
                "delivery": _call(
                    "review_agent", "success", "2026-11-02T08:00:00Z", "2026-11-02T09:00:00Z"
                ),
            },
        )
    projects.update(archive, is_active=False)
    repo = TrackerRepository(session)
    active = repo.agent_stats_all(**FILTERS).delivery_trends
    all_projects = repo.agent_stats_all(is_active=None, **FILTERS).delivery_trends
    assert active.totals.first_review_acceptance.rate == pytest.approx(2 / 3)
    assert all_projects.totals.first_review_acceptance.rate == 0.75
    assert active.totals.work_time.total_seconds == 14400
    assert all_projects.totals.work_time.total_seconds == 18000
    assert (
        repo.agent_stats(
            project_id=project_id, **FILTERS
        ).delivery_trends.totals.work_time.total_seconds
        == 10800
    )
    assert (
        repo.agent_stats(
            project_id=archive, **FILTERS
        ).delivery_trends.totals.work_time.total_seconds
        == 3600
    )
    for field in ("completed_tasks", "completed_tickets", "changes_requested", "stops"):
        assert (
            sum(getattr(day, field).total for day in active.daily)
            == getattr(active.totals, field).total
        )
    assert sum(day.first_review_acceptance.accepted for day in active.daily) == 2
    assert sum(day.first_review_acceptance.reviewed for day in active.daily) == 3


def test_unknown_completion_verdict_and_duration_are_explicit(session, project_id):
    receipt = _call("review_agent", "repair", "2026-11-01T07:00:00Z", "offset-free")
    _store(session, project_id, "unknown", {"attempt": 1, "review": receipt})
    repo = TrackerRepository(session)
    tracker = repo.ensure_tracker(project_id=project_id)
    task = session.exec(select(TrackerTask).where(TrackerTask.tracker_id == tracker.id)).one()
    # First retained evidence already says complete; later recompletion cannot
    # invent the missing first completion instant.
    _revision(
        session,
        tracker,
        "task",
        task,
        {"status": "complete"},
        {"status": "in-progress"},
        "2026-11-01T07:00:00Z",
    )
    _revision(
        session,
        tracker,
        "task",
        task,
        {"status": "in-progress"},
        {"status": "complete"},
        "2026-11-01T08:00:00Z",
    )
    session.commit()
    trends = repo.agent_stats(project_id=project_id, **FILTERS).delivery_trends
    assert trends.totals.completed_tasks.total == 0
    assert trends.totals.completed_tasks.coverage.unavailable == 1
    assert trends.totals.first_review_acceptance.rate is None
    assert trends.totals.first_review_acceptance.coverage.unavailable == 1
    assert trends.totals.changes_requested.total == 0
    assert trends.totals.changes_requested.coverage.unavailable == 1
    assert trends.totals.work_time.total_seconds is None
    assert trends.totals.work_time.coverage.issues == ["missing_finish"]
    assert all(day.work_time.total_seconds is None for day in trends.daily)


def test_short_range_keeps_lifetime_review_coverage_without_decoding_history(session, project_id):
    archive = [
        {
            "attempt": attempt,
            "review": _call(
                "review_agent", "repair", f"2026-10-{day}T07:00:00Z", f"2026-10-{day}T08:00:00Z"
            ),
        }
        for attempt, day in [(1, 28), (2, 29), (3, 30), (None, 31)]
    ]
    _store(session, project_id, "quality-coverage", {"attempts": archive})
    report = TrackerRepository(session).agent_stats(project_id=project_id, **FILTERS)
    changes = report.delivery_trends.totals.changes_requested
    assert changes.total == 0
    assert changes.coverage.recorded == 3
    assert changes.coverage.unavailable == 1
    assert changes.coverage.status == "partial"


def test_empty_period_is_zero_with_known_intervals_and_lifetime_has_no_series(session, project_id):
    ledger(session, project_id)
    repo = TrackerRepository(session)
    empty = repo.agent_stats(
        project_id=project_id,
        **{**FILTERS, "start_date": date(2026, 11, 5), "end_date": date(2026, 11, 6)},
    ).delivery_trends
    assert empty.totals.work_time.total_seconds == 0
    assert all(day.work_time.total_seconds == 0 for day in empty.daily)
    assert empty.totals.first_review_acceptance.rate is None
    assert empty.totals.completed_tickets.total == 0
    lifetime = repo.agent_stats(project_id=project_id).delivery_trends
    assert lifetime.daily == []
    assert lifetime.totals.work_time.total_seconds == 12600


def test_corrected_receipts_invalidate_finish_bounds_quality_and_clear_history(session, project_id):
    receipt = _call("review_agent", "repair", "2026-10-31T22:00:00Z", "2026-11-01T08:00:00Z")
    _store(session, project_id, "corrected", {"attempt": 1, "review": receipt})
    repo = TrackerRepository(session)
    first = repo.agent_stats(project_id=project_id, **FILTERS).delivery_trends
    assert first.totals.changes_requested.total == 1
    assert first.totals.work_time.total_seconds == 3600
    ticket = session.exec(select(TrackerTicket).where(TrackerTicket.key == "corrected")).one()
    fixed = deepcopy(ticket.completion_evidence_json)
    fixed["automatic_delivery"]["review"].update(
        validated_outcome="success", finished_at="2026-11-02T08:00:00Z"
    )
    ticket.completion_evidence_json = fixed
    session.commit()
    current = repo.agent_stats(project_id=project_id, **FILTERS).delivery_trends
    assert current.totals.changes_requested.total == 0
    assert current.totals.first_review_acceptance.rate == 1
    assert [day.first_review_acceptance.reviewed for day in current.daily] == [0, 1]
    assert current.totals.work_time.total_seconds == 90000  # DST's 25-hour day
    ticket.completion_evidence_json = None
    session.commit()
    cleared = repo.agent_stats(project_id=project_id, **FILTERS).delivery_trends
    assert cleared.totals.first_review_acceptance.reviewed == 0
    assert cleared.totals.work_time.total_seconds is None


def test_short_range_decodes_only_overlapping_receipts_in_one_shared_read(
    session, project_id, monkeypatch
):
    from stackos.repositories.tracker import delivery_stats, receipt_projection
    from tests.helpers.agent_statistics import seed_statistics_workload

    seed_statistics_workload(session, project_id, tasks=10, receipts=365)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 1, 1, tzinfo=tz)

    monkeypatch.setattr(delivery_stats, "datetime", FixedDatetime)
    decoded, statements = [], []
    original = receipt_projection.decode_receipt

    def decode(source):
        decoded.append(source)
        return original(source)

    monkeypatch.setattr(receipt_projection, "decode_receipt", decode)

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        report = TrackerRepository(session).agent_stats(
            project_id=project_id, start_date=date(2026, 11, 1), end_date=date(2026, 11, 7)
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert report.totals.reported_usage.total == 77
    assert report.delivery_trends.totals.work_time.total_seconds == 70
    assert len(decoded) == 7
    assert len(statements) == 8  # Includes the repository's legitimate project lookup.
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert not any("completion_evidence_json" in statement for statement in statements)


@pytest.mark.parametrize("handoff", ["after_copy", "before_copy", "before_commit"])
def test_report_uses_canonical_live_final_handoff(session, project_id, monkeypatch, handoff):
    from sqlmodel import Session

    from stackos.host_mcp import service
    from stackos.task_delivery import _record_execution_transition

    start = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    finish = (datetime.fromisoformat(start) + timedelta(seconds=9)).isoformat()
    call = service.ActiveInvocation(
        "trend-handoff",
        service.AgentExecutionScope(project_id, "overview", "racing", "worker", 1, "delivery"),
        "codex",
        None,
        start,
    )
    _store(
        session,
        project_id,
        "racing",
        {
            "attempt": 1,
            "delivery": {"launch": {"started_at": start}, "execution": call.projection()},
        },
    )
    service._active_invocations[call.invocation_id] = call
    original = service.invocation_timings

    def crossing():
        before = original()
        service._finish(call, None, finished_at=finish)
        monkeypatch.setattr(service, "invocation_timings", original)
        if handoff != "before_commit":
            _record_execution_transition(lambda: Session(session.get_bind()), call, False)
            service._unregister(call)
        return before if handoff == "after_copy" else original()

    monkeypatch.setattr(service, "invocation_timings", crossing)
    try:
        report = TrackerRepository(session).agent_stats(project_id=project_id).delivery_trends
        assert report.totals.work_time.total_seconds == 9
        assert report.totals.work_time.coverage.status == "complete"
        if handoff == "before_commit":
            _record_execution_transition(lambda: Session(session.get_bind()), call, False)
            service._unregister(call)
            assert (
                TrackerRepository(session).agent_stats(project_id=project_id).delivery_trends
                == report
            )
    finally:
        service._unregister(call)


def test_final_finish_during_the_report_replaces_the_earlier_live_copy(session, project_id):
    from sqlmodel import Session

    from stackos.host_mcp import service
    from stackos.task_delivery import _record_execution_transition

    start = datetime.now(UTC) - timedelta(seconds=10)
    call = service.ActiveInvocation(
        "trend-late-finish",
        service.AgentExecutionScope(project_id, "overview", "late-finish", "worker", 1, "delivery"),
        "codex",
        None,
        start.isoformat(),
    )
    _store(
        session,
        project_id,
        "late-finish",
        {
            "attempt": 1,
            "delivery": {
                "launch": {"started_at": start.isoformat()},
                "execution": call.projection(),
            },
        },
    )
    service._active_invocations[call.invocation_id] = call
    finishes = []
    engine = session.get_bind()

    def finish_before_fresh_rows(_conn, _cursor, statement, *_args):
        if " AS counted" not in statement or finishes:
            return
        finish = datetime.now(UTC)
        finishes.append(finish)
        service._finish(call, None, finished_at=finish.isoformat())
        _record_execution_transition(lambda: Session(engine), call, False)
        service._unregister(call)

    event.listen(engine, "before_cursor_execute", finish_before_fresh_rows)
    try:
        report = TrackerRepository(session).agent_stats(project_id=project_id)
        assert finishes
        assert report.delivery_trends.totals.work_time.total_seconds == pytest.approx(
            (finishes[0] - start).total_seconds()
        )
        assert report.delivery_trends.totals.work_time.coverage.status == "complete"
        assert report.as_of >= finishes[0]
    finally:
        event.remove(engine, "before_cursor_execute", finish_before_fresh_rows)
        service._unregister(call)


def test_equal_archive_values_do_not_become_current_and_zero_is_measured(
    session, project_id, monkeypatch
):
    from stackos.host_mcp import service

    execution = {
        "invocation_id": "same-source",
        "ticket_key": "same",
        "agent": "worker",
        "host_key": "codex",
        "model": "recorded",
        "started_at": "2026-11-01T07:00:00Z",
    }
    receipt = {"launch": execution, "execution": execution}
    _store(
        session,
        project_id,
        "same",
        {
            "attempt": 2,
            "delivery": deepcopy(receipt),
            "attempts": [{"attempt": 1, "delivery": deepcopy(receipt)}],
        },
    )
    monkeypatch.setattr(
        service,
        "invocation_timings",
        lambda: [
            {
                **execution,
                "project_id": project_id,
                "attempt": 1,
                "phase": "delivery",
                "observed_at": "2026-11-01T07:00:10Z",
            }
        ],
    )
    repo = TrackerRepository(session)
    duration = repo.agent_stats(project_id=project_id, **FILTERS).delivery_trends.totals.work_time
    assert duration.total_seconds is None
    assert duration.coverage.unavailable == 2
    ticket = session.exec(select(TrackerTicket).where(TrackerTicket.key == "same")).one()
    ticket.completion_evidence_json = {
        "automatic_delivery": {
            "attempt": 2,
            "delivery": {
                "launch": execution,
                "finished_at": execution["started_at"],
                "validated_outcome": "success",
            },
        }
    }
    session.commit()
    duration = repo.agent_stats(project_id=project_id, **FILTERS).delivery_trends.totals.work_time
    assert duration.total_seconds == 0
    assert duration.coverage.status == "complete"
