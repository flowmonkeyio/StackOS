"""Six delivery trends within the existing scoped statistics read.

Revision reductions retain first-completion provenance. Receipt selection uses
the canonical derived index; no widget reads or replays ticket evidence.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, literal, null, or_, select, union_all
from sqlmodel import Session, col

from stackos.db.models import (
    Project,
    TaskTracker,
    TrackerReceiptProjection,
    TrackerRevision,
    TrackerTask,
    TrackerTicket,
)
from stackos.repositories.tracker.schema import (
    DeliveryCountOut,
    DeliveryMetricCoverageOut,
    DeliveryTrendDayOut,
    DeliveryTrendMetricsOut,
    DeliveryTrendsOut,
    DeliveryWorkTimeOut,
    FirstReviewAcceptanceOut,
)
from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY


def _coverage(recorded: int, unavailable: int = 0, *issues: str) -> DeliveryMetricCoverageOut:
    return DeliveryMetricCoverageOut(
        status=("partial" if recorded else "unavailable")
        if unavailable
        else ("complete" if recorded else "empty"),
        recorded=recorded,
        unavailable=unavailable,
        issues=sorted(set(issues)),
    )


def _scope(statement: Any, project_id: int | None, is_active: bool | None) -> Any:
    statement = statement.where(col(TaskTracker.key) == DEFAULT_TRACKER_KEY)
    if project_id is not None:
        statement = statement.where(col(Project.id) == project_id)
    if is_active is not None:
        statement = statement.where(col(Project.is_active).is_(is_active))
    return statement


def _join_scope(statement: Any, model: Any) -> Any:
    return statement.join(TaskTracker, col(TaskTracker.id) == col(model.tracker_id)).join(
        Project, col(Project.id) == col(model.project_id)
    )


def query_delivery_trends(
    session: Session,
    *,
    project_id: int | None,
    is_active: bool | None,
    start_date: date | None,
    end_date: date | None,
    zone: ZoneInfo,
    timing: list[dict[str, Any]],
) -> DeliveryTrendsOut:
    from stackos.repositories.tracker.agent_stats import union_intervals, work_intervals
    from stackos.repositories.tracker.receipt_projection import decode_receipt

    observed = datetime.now(UTC)
    days = (
        []
        if start_date is None or end_date is None
        else [start_date + timedelta(days=n) for n in range((end_date - start_date).days + 1)]
    )
    bounds = {}
    for day in days:
        try:
            left = datetime.combine(day, time.min, zone).astimezone(UTC)
        except OverflowError:
            left = datetime.min.replace(tzinfo=UTC)
        try:
            right = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
        except OverflowError:
            right = datetime.max.replace(tzinfo=UTC)
        bounds[day] = left, right
    start = bounds[days[0]][0] if days else None
    end = bounds[days[-1]][1] if days else None
    counts: dict[str, dict[date | None, int]] = defaultdict(lambda: defaultdict(int))
    coverage: dict[str, DeliveryMetricCoverageOut] = {}

    def add(metric: str, at: datetime) -> None:
        at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        day = at.astimezone(zone).date()
        if start_date is not None and end_date is not None:
            if not start_date <= day <= end_date:
                return
            counts[metric][day] += 1
        counts[metric][None] += 1

    # Reduce lifetime transitions in SQL before selecting a period. Recompletion
    # in the period cannot conceal an earlier completion outside it.
    task_count = 0
    task_baseline_gaps = 0
    for kind, model, metric in (
        ("task", TrackerTask, "completed_tasks"),
        ("ticket", TrackerTicket, "completed_tickets"),
    ):
        rev = TrackerRevision
        before = col(rev.before_json)["status"].as_string()
        after = col(rev.after_json)["status"].as_string()
        transition = and_(before.is_not(None), before != "complete", after == "complete")
        imported_complete = or_(
            before == "complete", and_(col(rev.change_kind) == "create", after == "complete")
        )
        history: Any = (
            select(
                col(rev.entity_id).label("entity_id"),
                col(rev.tracker_id).label("tracker_id"),
                func.min(case((transition, col(rev.created_at)))).label("first_at"),
                func.min(case((transition, col(rev.rev)))).label("first_rev"),
                func.min(case((imported_complete, col(rev.rev)))).label("prior_complete"),
                func.max(case((col(rev.change_kind) == "create", 1), else_=0)).label("created"),
            )
            .where(col(rev.entity_kind) == kind)
            .group_by(col(rev.entity_id), col(rev.tracker_id))
        )
        history = _scope(_join_scope(history, rev), project_id, is_active).subquery()
        statement = _join_scope(
            select(
                col(model.id),
                col(model.status),
                history.c.first_at,
                history.c.first_rev,
                history.c.prior_complete,
                history.c.created,
            ).select_from(model),
            model,
        ).outerjoin(
            history,
            and_(
                history.c.entity_id == col(model.id), history.c.tracker_id == col(model.tracker_id)
            ),
        )
        known = missing = 0
        for _id, status, first_at, first_rev, prior, created in session.execute(
            _scope(statement, project_id, is_active)
        ):
            if kind == "task":
                task_count += 1
                task_baseline_gaps += not bool(created)
            if prior is not None and (first_rev is None or prior < first_rev):
                missing += 1
            elif first_at is not None:
                known += 1
                add(metric, first_at)
            elif status == "complete":
                missing += 1
            else:
                # An entity that has never completed is known zero only with
                # retained creation provenance; imported history is unknown.
                known += bool(created)
                missing += not bool(created)
        coverage[metric] = _coverage(
            known, missing, *(["first_completion_unknown"] if missing else [])
        )

    rev = TrackerRevision
    before_stop = col(rev.before_json)["metadata_json"]["delivery"]["status"].as_string()
    after_stop = col(rev.after_json)["metadata_json"]["delivery"]["status"].as_string()
    stops = _join_scope(
        select(col(rev.created_at))
        .select_from(rev)
        .join(
            TrackerTask,
            and_(
                col(TrackerTask.id) == col(rev.entity_id),
                col(TrackerTask.tracker_id) == col(rev.tracker_id),
            ),
        ),
        TrackerTask,
    ).where(
        col(rev.entity_kind) == "task",
        func.json_type(col(rev.before_json)) == "object",
        func.coalesce(before_stop, "") != "stopped",
        after_stop == "stopped",
        col(rev.change_kind) != "create",
    )
    if start is not None and end is not None:
        stops = stops.where(
            col(rev.created_at) >= start.replace(tzinfo=None),
            col(rev.created_at) < end.replace(tzinfo=None),
        )
    for (at,) in session.execute(_scope(stops, project_id, is_active)):
        add("stops", at)
    coverage["stops"] = _coverage(
        task_count - task_baseline_gaps,
        task_baseline_gaps,
        *(["retained_revision_history_only"] if task_baseline_gaps else []),
    )

    table = TrackerReceiptProjection
    # Position is the canonical archive/current occurrence order. Blocked/native
    # failures never enter the quality denominator, including before a success.
    reviews = (
        _scope(
            _join_scope(
                select(
                    col(table.ticket_id).label("ticket_id"),
                    col(table.attempt).label("attempt"),
                    col(table.review_outcome).label("outcome"),
                    col(table.finished_at).label("at"),
                    func.row_number()
                    .over(
                        partition_by=col(table.ticket_id),
                        order_by=(col(table.position), col(table.phase)),
                    )
                    .label("first"),
                )
                .select_from(table)
                .join(TrackerTicket, col(TrackerTicket.id) == col(table.ticket_id)),
                TrackerTicket,
            ),
            project_id,
            is_active,
        )
        .where(
            col(table.inventory) == "usage",
            col(table.review_outcome).in_(("success", "repair")),
        )
        .subquery()
    )
    selected = select(reviews)
    if start is not None and end is not None:
        selected = selected.where(
            or_(
                reviews.c.first == 1,
                reviews.c.at.is_(None),
                and_(
                    reviews.c.outcome == "repair",
                    reviews.c.at >= start.replace(tzinfo=None),
                    reviews.c.at < end.replace(tzinfo=None),
                ),
            )
        )
    first_known = first_missing = 0
    for _tid, attempt, outcome, at, first in session.execute(selected):
        valid = bool(attempt and at is not None and at <= observed.replace(tzinfo=None))
        if first == 1:
            if valid:
                first_known += 1
                add("reviewed", at)
                if outcome == "success":
                    add("accepted", at)
            else:
                first_missing += 1
        if outcome == "repair" and valid:
            add("changes_requested", at)
    coverage["first_review_acceptance"] = _coverage(
        first_known,
        first_missing,
        *(["quality_verdict_identity_or_finish_unknown"] if first_missing else []),
    )

    # Closed out-of-range intervals need no JSON decode. A cheap scalar count
    # preserves the distinction between an empty period and unavailable history.
    valid_closed = and_(
        col(table.timing).is_(True),
        col(table.closed).is_(True),
        col(table.launch_at).is_not(None),
        col(table.finished_at).is_not(None),
        col(table.finished_at) >= col(table.launch_at),
        col(table.finished_at) <= observed.replace(tzinfo=None),
    )
    repair = and_(col(table.inventory) == "usage", col(table.review_outcome) == "repair")
    usable_verdict = and_(
        col(table.attempt) > 0,
        col(table.finished_at).is_not(None),
        col(table.finished_at) <= observed.replace(tzinfo=None),
    )
    inventory_branch = _scope(
        _join_scope(
            select(
                literal(0).label("row_kind"),
                col(TrackerTicket.id).label("ticket_id"),
                col(TrackerTicket.task_id).label("task_id"),
                func.sum(case((col(table.timing).is_(True), 1), else_=0)).label("timing_count"),
                func.sum(case((valid_closed, 1), else_=0)).label("valid_count"),
                func.sum(
                    case(
                        (
                            or_(
                                and_(
                                    col(table.inventory) == "usage",
                                    col(table.review_outcome) == "unclassified",
                                ),
                                and_(
                                    col(table.inventory) == "timing",
                                    col(table.review_outcome).in_(("success", "repair")),
                                ),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("unclassified"),
                func.sum(case((and_(repair, usable_verdict), 1), else_=0)).label("known_repairs"),
                func.sum(case((and_(repair, ~usable_verdict), 1), else_=0)).label(
                    "missing_repairs"
                ),
                null().label("project_id"),
                null().label("ticket_key"),
                null().label("attempt"),
                null().label("phase"),
                null().label("observation_key"),
                null().label("current"),
                null().label("receipt_json"),
                null().label("counted"),
            )
            .select_from(TrackerTicket)
            .outerjoin(
                table,
                col(table.ticket_id) == col(TrackerTicket.id),
            ),
            TrackerTicket,
        ),
        project_id,
        is_active,
    ).group_by(col(TrackerTicket.id))
    selected_timing = _scope(
        _join_scope(
            select(
                literal(1).label("row_kind"),
                col(TrackerTicket.id).label("ticket_id"),
                col(TrackerTicket.task_id).label("task_id"),
                null().label("timing_count"),
                null().label("valid_count"),
                null().label("unclassified"),
                null().label("known_repairs"),
                null().label("missing_repairs"),
                col(TrackerTicket.project_id).label("project_id"),
                col(TrackerTicket.key).label("ticket_key"),
                col(table.attempt).label("attempt"),
                col(table.phase).label("phase"),
                col(table.observation_key).label("observation_key"),
                col(table.current).label("current"),
                col(table.receipt_json).label("receipt_json"),
                valid_closed.label("counted"),
            )
            .select_from(table)
            .join(TrackerTicket, col(TrackerTicket.id) == col(table.ticket_id)),
            TrackerTicket,
        ),
        project_id,
        is_active,
    ).where(col(table.timing).is_(True))
    if start is not None and end is not None:
        selected_timing = selected_timing.where(
            or_(
                ~valid_closed,
                and_(
                    col(table.launch_at) < end.replace(tzinfo=None),
                    col(table.finished_at) >= start.replace(tzinfo=None),
                ),
            )
        )
    evidence: dict[int, dict[str, Any]] = {}
    identities = {}
    already_counted: dict[int, int] = defaultdict(int)
    # Coverage totals and selected receipts must share one SQL snapshot. UNION ALL
    # keeps one atomic read while avoiding SQLite's O(N*M) unindexed outer join.
    timing_rows = session.execute(union_all(inventory_branch, selected_timing)).all()
    # A real native exit can land during the earlier report reductions. Validate
    # fresh final rows against the time after their read; the earlier owned live
    # copy still bounds any invocation without a durable finish.
    observed = datetime.now(UTC)
    recorded = missing = 0
    ticket_tasks = set()
    unknown_classification = 0
    repair_known = repair_missing = 0
    for (
        row_kind,
        tid,
        task_id,
        count,
        valid,
        unclassified,
        known_repairs,
        missing_repairs,
        pid,
        key,
        attempt,
        phase,
        observation,
        current,
        source,
        counted,
    ) in timing_rows:
        if row_kind == 0:
            ticket_tasks.add(task_id)
            recorded += valid or 0
            unknown_classification += unclassified or 0
            repair_known += known_repairs or 0
            repair_missing += missing_repairs or 0
            if not count:
                missing += 1
            continue
        identities[tid] = task_id, pid, key
        already_counted[tid] += bool(counted)
        automatic = evidence.setdefault(tid, {"automatic_delivery": {"attempts": []}})[
            "automatic_delivery"
        ]
        # Equal cached values must not alias current/archive object identity.
        receipt = dict(decode_receipt(source))
        if observation:
            automatic.setdefault("upgrade_interruptions", []).append(
                {"attempt": attempt or None, "phase": phase, "receipt": receipt}
            )
        elif current:
            automatic["attempt"] = attempt or None
            automatic[phase] = receipt
        else:
            automatic["attempts"].append({"attempt": attempt or None, phase: receipt})
    missing += task_count - len(ticket_tasks)
    coverage["changes_requested"] = _coverage(
        repair_known,
        repair_missing,
        *(["quality_verdict_identity_or_finish_unknown"] if repair_missing else []),
    )
    if unknown_classification:
        for name, known, unavailable in (
            ("first_review_acceptance", first_known, first_missing),
            ("changes_requested", repair_known, repair_missing),
        ):
            coverage[name] = _coverage(
                known,
                unavailable + unknown_classification,
                *coverage[name].issues,
                "unclassified_review_history",
            )
    by_task: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    issues = {"no_recorded_invocations"} if missing else set()
    timing_by_project: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for call in timing:
        timing_by_project[call.get("project_id")].append(call)
    for tid, source in evidence.items():
        task_id, pid, key = identities[tid]
        intervals, _live, absent, invalid = work_intervals(
            [(key, source)],
            live_calls=timing_by_project.get(pid) or [],
            observed_at=observed,
        )
        by_task[task_id].extend(intervals)
        missing += invalid
        issues.update(absent)
        # Closed intervals were counted in SQL, including out-of-range support.
        recorded += len(intervals) - already_counted[tid]
    seconds: dict[date | None, float] = defaultdict(float)
    for intervals in by_task.values():
        for left, right in union_intervals(intervals):
            if start is None or end is None:
                seconds[None] += (right - left).total_seconds()
                continue
            left, right = max(left, start), min(right, end)
            if right < left:
                continue
            # Only intersected local days, including DST, receive work.
            day = max(days[0], left.astimezone(zone).date())
            last = min(days[-1], right.astimezone(zone).date())
            while day <= last:
                lo, hi = bounds[day]
                seconds[day] += max(0, (min(right, hi) - max(left, lo)).total_seconds())
                if day == last:
                    break
                day += timedelta(days=1)
    if days:
        seconds[None] = sum(seconds[day] for day in days)
    coverage["work_time"] = _coverage(recorded, missing, *issues)

    def metrics(day: date | None) -> DeliveryTrendMetricsOut:
        def count(name: str) -> DeliveryCountOut:
            return DeliveryCountOut(total=counts[name][day], coverage=coverage[name])

        accepted, reviewed = counts["accepted"][day], counts["reviewed"][day]
        return DeliveryTrendMetricsOut(
            completed_tasks=count("completed_tasks"),
            completed_tickets=count("completed_tickets"),
            first_review_acceptance=FirstReviewAcceptanceOut(
                accepted=accepted,
                reviewed=reviewed,
                rate=accepted / reviewed if reviewed else None,
                coverage=coverage["first_review_acceptance"],
            ),
            changes_requested=count("changes_requested"),
            stops=count("stops"),
            work_time=DeliveryWorkTimeOut(
                total_seconds=seconds[day] if recorded or not task_count else None,
                coverage=coverage["work_time"],
            ),
        )

    return DeliveryTrendsOut(
        totals=metrics(None),
        daily=[DeliveryTrendDayOut(date=day, **metrics(day).model_dump()) for day in days],
    )
