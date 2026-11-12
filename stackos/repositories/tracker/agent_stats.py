"""One safe projection of existing automatic-delivery invocation receipts."""

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, true
from sqlmodel import Session, col

from stackos.db.models import Project, TaskTracker, TrackerTask, TrackerTicket
from stackos.host_mcp import service as host_service
from stackos.host_mcp.result import usage_snapshot
from stackos.host_mcp.service import ProvisionalUsage, active_execution, provisional_usage
from stackos.repositories.tracker.schema import (
    AgentStatsCoverageOut,
    AgentStatsDayOut,
    AgentStatsFieldCoverageOut,
    AgentStatsMetricsOut,
    AgentStatsRowOut,
    AgentStatsUsageOut,
    ExecutionIdentityOut,
    TrackerAgentStatsOut,
    UsageSampleOut,
    WorkDurationOut,
)
from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY


def invocation_receipts(evidence: Any, *, require_launch: bool = True) -> list[dict[str, Any]]:
    """Deduplicate current/archive by attempt and phase within one ticket.

    Read only the canonical receipt locations, never tracker history, sessions,
    configuration or nested result packets. Current receipts take precedence.
    Legacy missing attempt identity remains unknown, rather than an inferred run.
    """
    return list(_receipt_items(evidence, require_launch=require_launch).values())


def _receipt_items(
    evidence: Any, *, require_launch: bool = True
) -> dict[tuple[int | None, str], dict[str, Any]]:
    if not isinstance(evidence, dict):
        return {}
    automatic = evidence.get("automatic_delivery")
    if not isinstance(automatic, dict):
        return {}
    archive = automatic.get("attempts")
    occurrences = [*(archive if isinstance(archive, list) else []), automatic]
    unique = {}
    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            continue
        attempt = occurrence.get("attempt")
        attempt = attempt if type(attempt) is int and attempt > 0 else None
        for phase in ("delivery", "review"):
            receipt = occurrence.get(phase)
            if isinstance(receipt, dict) and (
                not require_launch or isinstance(receipt.get("launch"), dict)
            ):
                unique[attempt, phase] = receipt
    return unique


def work_duration(
    sources: Iterable[tuple[str, Any]],
    *,
    live_calls: list[dict[str, Any]],
    observed_at: datetime,
) -> WorkDurationOut:
    """Union ticket-local deduplicated receipts; missing data is never zero-filled.

    Live state must match the current receipt's actual identity. Persisted
    execution/usage observations alone cannot manufacture an open endpoint.
    Final receipts win even if an earlier registry snapshot is still present.
    """
    intervals: list[tuple[datetime, datetime]] = []
    live: dict[str, ExecutionIdentityOut] = {}
    issues: set[str] = set()
    missing = 0
    for ticket_key, evidence in sources:
        automatic = evidence.get("automatic_delivery", {}) if isinstance(evidence, dict) else {}
        if not isinstance(automatic, dict):
            automatic = {}
        current = [automatic.get(phase) for phase in ("delivery", "review")]
        for (attempt, phase), receipt in _receipt_items(evidence, require_launch=False).items():
            launch = receipt.get("launch")
            start = _instant(launch.get("started_at")) if isinstance(launch, dict) else None
            end = _instant(receipt.get("finished_at"))
            reason = None
            owned = None
            if start is None:
                reason = "missing_start"
            elif "finished_at" in receipt or "exit_code" in receipt or "result" in receipt:
                if end is None:
                    reason = "missing_finish"
            else:
                execution = receipt.get("execution")
                if isinstance(execution, dict) and any(receipt is item for item in current):
                    owned = next(
                        (
                            call
                            for call in live_calls
                            if call.get("ticket_key") == ticket_key
                            and call.get("attempt") == attempt
                            and call.get("phase") == phase
                            and isinstance(call.get("invocation_id"), str)
                            and bool(call["invocation_id"])
                            and all(
                                call.get(key) == execution.get(key)
                                for key in (
                                    "invocation_id",
                                    "started_at",
                                    "ticket_key",
                                    "agent",
                                    "host_key",
                                    "model",
                                )
                            )
                        ),
                        None,
                    )
                if owned is None:
                    reason = "stale_live_state" if execution else "missing_finish"
                else:
                    actual_start = _instant(owned.get("started_at"))
                    if actual_start is None or actual_start < start:
                        reason = "invalid_order"
                    elif actual_start > observed_at:
                        reason = "future_endpoint"
                    end = _instant(owned.get("finished_at")) or observed_at
            if reason is None and start is not None and end is not None:
                if end < start:
                    reason = "invalid_order"
                elif end > observed_at:
                    reason = "future_endpoint"
                else:
                    intervals.append((start, end))
                    if owned is not None and owned.get("finished_at") is None:
                        live[owned["invocation_id"]] = ExecutionIdentityOut.model_validate(owned)
            if reason:
                missing += 1
                issues.add(reason)
    total = 0.0
    endpoint = None
    for start, end in sorted(intervals):
        total += max(0, (end - max(start, endpoint or start)).total_seconds())
        endpoint = max(endpoint or end, end)
    if not intervals and not missing:
        issues.add("no_recorded_invocations")
    return WorkDurationOut(
        total_seconds=total if intervals else None,
        observed_at=(
            observed_at.isoformat() if live else endpoint.isoformat() if endpoint else None
        ),
        endpoint_at=endpoint.isoformat() if endpoint else None,
        coverage=("partial" if missing else "complete") if intervals else "unavailable",
        recorded_intervals=len(intervals),
        unavailable_intervals=missing,
        issues=sorted(issues),
        live=list(live.values()),
    )


def provisional_receipt(
    project_id: int, ticket_key: str, evidence: Any, provisional: ProvisionalUsage
) -> list[dict[str, Any]]:
    """Pure merge of a pre-query counter copy and fresh receipts, exactly once."""
    receipts = invocation_receipts(evidence)
    automatic = evidence.get("automatic_delivery", {}) if isinstance(evidence, dict) else {}
    for attempt, phase, usage in provisional.get((project_id, ticket_key), []):
        if automatic.get("attempt") != attempt or phase is None:
            continue
        current = automatic.get(phase)
        # Recorded usage includes the native-exit handoff. It wins over an older
        # counter copy even before full result validation adds exit_code/result.
        if (
            not isinstance(current, dict)
            or "exit_code" in current
            or "result" in current
            or _measured(current) is not None
        ):
            continue
        receipts = [receipt for receipt in receipts if receipt is not current]
        receipts.append(
            {
                **current,
                "usage": {key: value for key, value in usage.items() if key != "execution"},
                **({"execution": usage["execution"]} if "execution" in usage else {}),
            }
        )
    return receipts


def _coverage(known: int, total: int) -> Literal["empty", "complete", "partial", "unavailable"]:
    if not total:
        return "empty"
    if known == total:
        return "complete"
    return "partial" if known else "unavailable"


def _eligible(receipt: dict[str, Any]) -> bool:
    """Statistics-only exclusion; operational inventory and snapshots stay intact."""
    usage = receipt.get("usage")
    return not (
        isinstance(usage, dict)
        and usage.get("status") == "unavailable"
        and usage.get("reason") == "operator_reset"
        and "measured" in usage
        and usage["measured"] is None
    )


def _measured(receipt: dict[str, Any]) -> dict[str, Any] | None:
    usage = receipt.get("usage")
    if (
        not isinstance(usage, dict)
        or usage.get("status") != "measured"
        or usage.get("unit") != "tokens"
    ):
        return None
    measured = usage.get("measured")
    if not isinstance(measured, dict) or any(
        type(measured.get(k)) is not int or measured[k] < 0 for k in ("input", "output", "total")
    ):
        return None
    if measured["input"] + measured["output"] != measured["total"]:
        return None
    # Reuse the producer's subset validation; never inspect native/raw counters.
    normalized = usage_snapshot(
        "receipt",
        "invocation",
        measured["input"],
        measured["output"],
        measured["total"],
        cache_read=measured.get("cache_read"),
        cache_write=measured.get("cache_write"),
        reasoning=measured.get("reasoning"),
    )
    return normalized["snapshot"] if normalized else None


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(UTC) if stamp.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _last_execution(receipts: list[dict[str, Any]]) -> ExecutionIdentityOut | None:
    candidates = []
    for receipt in receipts:
        native_session = receipt.get("thread_id")
        if not receipt.get("execution") and not (
            type(receipt.get("exit_code")) is int
            or (isinstance(native_session, str) and bool(native_session.strip()))
            or isinstance(receipt.get("result"), dict)
        ):
            continue
        execution = receipt.get("execution") or receipt.get("launch") or {}
        stamp = _instant(execution.get("started_at"))
        fields = {
            key: execution.get(key)
            for key in ("agent", "host_key", "model", "started_at", "invocation_id")
        }
        fields["ticket_key"] = execution.get("ticket_key") or receipt.get("ticket_key")
        if stamp is not None and all(
            isinstance(fields[key], str) and fields[key]
            for key in ("agent", "host_key", "ticket_key")
        ):
            candidates.append((stamp, ExecutionIdentityOut.model_validate(fields)))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _history(receipts: list[dict[str, Any]]) -> list[UsageSampleOut]:
    """Replay observations once per receipt, replacing its cumulative contribution.

    A receipt's first retained checkpoint includes its earlier usage. Missing
    checkpoints create no zero prefix. Equal timestamps are one observation of
    the combined scope, independent of receipt/finalization order.
    """
    events: dict[datetime, dict[int, dict[str, Any]]] = defaultdict(dict)
    for index, receipt in enumerate(receipts):
        if not _eligible(receipt):
            continue
        final = _measured(receipt)
        if final is None:
            continue
        live = (receipt.get("execution") or {}).get("live_usage") or {}
        final_at = _instant(receipt.get("finished_at")) or _instant(live.get("measured_at"))
        points = live.get("samples")
        dated = {}
        for point in points[-120:] if isinstance(points, list) else []:
            if not isinstance(point, dict):
                continue
            at = _instant(point.get("at"))
            measured = _measured(
                {"usage": {"unit": "tokens", "status": "measured", "measured": point}}
            )
            if (
                at is not None
                and (final_at is None or at <= final_at)
                and measured is not None
                and measured["input"] <= final["input"]
                and measured["output"] <= final["output"]
            ):
                dated[at] = measured
        if final is not None and final_at is not None:
            dated[final_at] = final
        if final is not None and not dated:
            # A legacy total without an observation time cannot be placed on a
            # lifetime timeline. Keep the numbers; do not draw a narrower scope.
            return []
        for at, measured in dated.items():
            events[at][index] = measured
    current: dict[int, dict[str, Any]] = {}
    samples = []
    ordered = sorted(events.items())
    for position, (at, updates) in enumerate(ordered):
        current.update(updates)
        if position < len(ordered) - 120:
            continue
        totals = summarize_receipts(
            [
                {"usage": {"unit": "tokens", "status": "measured", "measured": measured}}
                for measured in current.values()
            ]
        ).reported_usage
        samples.append(
            UsageSampleOut(
                at=at.isoformat(), **totals.model_dump(exclude={"unit", "input_with_unknown_cache"})
            )
        )
    return samples[-120:]


def summarize_receipts(
    receipts: Iterable[dict[str, Any]], *, include_history: bool = False
) -> AgentStatsMetricsOut:
    """Summarize deduplicated ticket receipts for every statistics projection."""
    receipts = list(receipts)
    total = measured_count = classified = rejects = 0
    fields = ("input", "output", "total", "cache_read", "cache_write", "reasoning")
    usage = dict.fromkeys(fields, 0)
    known = dict.fromkeys(fields, 0)
    remaining = unknown_cache_input = 0
    review_outcomes: dict[str, int] = defaultdict(int)
    for receipt in receipts:
        if not _eligible(receipt):
            continue
        total += 1
        measured = _measured(receipt)
        if measured is not None:
            measured_count += 1
            for key in usage:
                if measured[key] is not None:
                    usage[key] += measured[key]
                    known[key] += 1
            if measured["cache_read"] is not None:
                remaining += measured["input"] - measured["cache_read"]
            else:
                unknown_cache_input += measured["input"]
        role = receipt.get("selected_role")
        # Explicit null records failed producer validation; absence is legacy/unfinished.
        outcome = receipt.get("validated_outcome")
        if (
            isinstance(role, str)
            and bool(role)
            and "validated_outcome" in receipt
            and outcome in (None, "success", "repair", "blocked", "failed")
        ):
            classified += 1
            rejects += int(role == "review_agent" and outcome == "repair")
            if role == "review_agent":
                review_outcomes[outcome or "validation_failed"] += 1
    return AgentStatsMetricsOut(
        samples=_history(receipts) if include_history else [],
        last_execution=_last_execution(receipts) if include_history else None,
        launch_attempts=total,
        recorded_reviewer_rejects=rejects,
        review_outcomes=dict(review_outcomes),
        reported_usage=AgentStatsUsageOut.model_validate(
            {
                **{key: usage[key] if known[key] or not total else None for key in fields},
                "remaining_input": remaining if known["cache_read"] or not total else None,
                "input_with_unknown_cache": unknown_cache_input
                if measured_count or not total
                else None,
            }
        ),
        coverage=AgentStatsCoverageOut(
            usage_measured_attempts=measured_count,
            usage_unavailable_attempts=total - measured_count,
            classification_known_attempts=classified,
            classification_unknown_attempts=total - classified,
            usage_status=_coverage(measured_count, total),
            classification_status=_coverage(classified, total),
            fields={
                key: AgentStatsFieldCoverageOut(
                    measured_attempts=known[key],
                    unavailable_attempts=total - known[key],
                    status=_coverage(known[key], total),
                )
                for key in fields
            },
        ),
    )


def query_task_agent_stats(
    session: Session,
    *,
    tracker_id: int,
    task_ids: set[int],
    provisional: ProvisionalUsage | None = None,
    work_durations: dict[int, WorkDurationOut] | None = None,
    task_keys: dict[int, str] | None = None,
) -> dict[int, AgentStatsMetricsOut]:
    """Batch whole-task totals, independent of visible tickets or graph filters."""
    if not task_ids:
        return {}
    if provisional is None:
        provisional = provisional_usage()
    receipts_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    duration_sources: dict[int, list[tuple[str, Any]]] = defaultdict(list)
    projects: dict[int, int] = {}
    statement = select(
        col(TrackerTicket.task_id),
        col(TrackerTicket.project_id),
        col(TrackerTicket.key),
        col(TrackerTicket.completion_evidence_json),
    ).where(
        col(TrackerTicket.tracker_id) == tracker_id,
        col(TrackerTicket.task_id).in_(task_ids),
    )
    rows = session.execute(statement).all()
    for task_id, project_id, ticket_key, evidence in rows:
        duration_sources[task_id].append((ticket_key, evidence))
        projects[task_id] = project_id
        # Deduplication is ticket-local; never add parent/group subtotals.
        receipts_by_task[task_id].extend(
            {**receipt, "ticket_key": ticket_key}
            for receipt in provisional_receipt(project_id, ticket_key, evidence, provisional)
        )
    if work_durations is not None:
        # Share the fresh scalar receipt read with usage, but never its
        # token eligibility filter or provisional usage substitutions.
        timings = host_service.invocation_timings()
        observed_at = datetime.now(UTC)
        for task_id in task_ids:
            work_durations[task_id] = work_duration(
                duration_sources[task_id],
                live_calls=[
                    call
                    for call in timings
                    if call.get("project_id") == projects.get(task_id)
                    and call.get("task_key") == (task_keys or {}).get(task_id)
                ],
                observed_at=observed_at,
            )
    return {
        task_id: summarize_receipts(receipts_by_task[task_id], include_history=True)
        for task_id in task_ids
    }


def query_ticket_agent_stats(
    session: Session,
    *,
    ticket_ids: set[int],
    provisional: ProvisionalUsage | None = None,
    work_durations: dict[int, WorkDurationOut] | None = None,
) -> dict[int, AgentStatsMetricsOut]:
    """Fresh scalar evidence avoids stale ORM rows loaded before native exit."""
    if not ticket_ids:
        return {}
    if provisional is None:
        provisional = provisional_usage()
    statement = (
        select(
            col(TrackerTicket.id),
            col(TrackerTicket.project_id),
            col(TrackerTicket.key),
            col(TrackerTicket.completion_evidence_json),
            col(TrackerTask.key),
        )
        .join(TrackerTask, col(TrackerTask.id) == col(TrackerTicket.task_id))
        .where(col(TrackerTicket.id).in_(ticket_ids))
    )
    rows = session.execute(statement).all()
    timings = host_service.invocation_timings() if work_durations is not None else []
    observed_at = datetime.now(UTC)
    metrics = {}
    for ticket_id, project_id, key, evidence, task_key in rows:
        metrics[ticket_id] = summarize_receipts(
            [
                {**receipt, "ticket_key": key}
                for receipt in provisional_receipt(project_id, key, evidence, provisional)
            ],
            include_history=True,
        )
        if work_durations is not None:
            work_durations[ticket_id] = work_duration(
                [(key, evidence)],
                live_calls=[
                    call
                    for call in timings
                    if call.get("project_id") == project_id and call.get("task_key") == task_key
                ],
                observed_at=observed_at,
            )
    return metrics


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def usage_refresh(
    session: Session,
    project_id: int,
    calls: list[dict[str, Any]] | None,
    *,
    task_key: str | None = None,
) -> dict[str, Any]:
    """Safe initial/reconnect snapshot, then minimal affected Work projections."""
    # Snapshot before SQL: removal implies the receipt commit already happened;
    # otherwise this request owns its counter copy through projection. No DB I/O
    # runs under the registry lock and no finished-call history is retained.
    provisional = provisional_usage(project_id)
    task_keys = {call["task_key"] for call in calls or []}
    ticket_keys = {call["ticket_key"] for call in calls or []}
    tasks = (
        list(
            session.execute(
                select(TrackerTask)
                .join(TaskTracker, col(TaskTracker.id) == col(TrackerTask.tracker_id))
                .where(
                    col(TrackerTask.project_id) == project_id,
                    col(TaskTracker.key) == DEFAULT_TRACKER_KEY,
                    true() if task_key is None else col(TrackerTask.key) == task_key,
                    true() if calls is None else col(TrackerTask.key).in_(task_keys),
                )
            )
            .scalars()
            .all()
        )
        if calls is None or task_keys
        else []
    )
    task_stats = {}
    task_durations: dict[int, WorkDurationOut] = {}
    for tracker_id in {task.tracker_id for task in tasks}:
        task_stats.update(
            query_task_agent_stats(
                session,
                tracker_id=tracker_id,
                task_ids={
                    task.id
                    for task in tasks
                    if task.id is not None and task.tracker_id == tracker_id
                },
                provisional=provisional,
                work_durations=task_durations,
                task_keys={task.id: task.key for task in tasks if task.id is not None},
            )
        )
    task_by_id = {task.id: task for task in tasks}
    tickets = (
        list(
            session.execute(
                select(TrackerTicket)
                .join(TaskTracker, col(TaskTracker.id) == col(TrackerTicket.tracker_id))
                .where(
                    col(TrackerTicket.project_id) == project_id,
                    col(TaskTracker.key) == DEFAULT_TRACKER_KEY,
                    col(TrackerTicket.task_id).in_(task_by_id),
                    true() if calls is None else col(TrackerTicket.key).in_(ticket_keys),
                )
            )
            .scalars()
            .all()
        )
        if calls is None or ticket_keys
        else []
    )
    ticket_durations: dict[int, WorkDurationOut] = {}
    ticket_stats = query_ticket_agent_stats(
        session,
        ticket_ids={ticket.id for ticket in tickets if ticket.id is not None},
        provisional=provisional,
        work_durations=ticket_durations,
    )
    return {
        "tasks": [
            {
                "key": task.key,
                "agent_stats": task_stats[task.id].model_dump(mode="json"),
                "work_duration": task_durations[task.id].model_dump(mode="json"),
                "active_execution": active_execution(project_id, task.key),
            }
            for task in tasks
        ],
        "tickets": [
            {
                "key": ticket.key,
                "agent_stats": ticket_stats[ticket.id].model_dump(mode="json"),
                "work_duration": ticket_durations[ticket.id].model_dump(mode="json"),
                "active_execution": active_execution(
                    project_id, task_by_id[ticket.task_id].key, ticket.key
                ),
            }
            for ticket in tickets
            if ticket.task_id in task_by_id
        ],
    }


def stats_timezone(start_date: date | None, end_date: date | None, timezone: str) -> ZoneInfo:
    """Validate the bounded calendar contract for operations and direct readers."""
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("timezone must be an IANA timezone") from exc
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be supplied together")
    if (
        start_date is not None
        and end_date is not None
        and not 0 <= (end_date - start_date).days < 366
    ):
        raise ValueError("date range must be ordered and contain at most 366 days")
    return zone


def _launch_day(receipt: dict[str, Any], zone: ZoneInfo) -> date | None:
    started = receipt["launch"].get("started_at")
    if not isinstance(started, str):
        return None
    try:
        stamp = datetime.fromisoformat(started)
        if stamp.utcoffset() is None:
            return None
        return stamp.astimezone(zone).date()
    except (ValueError, OverflowError):
        return None


def _group_rows(
    occurrences: list[tuple[int, str, str, dict[str, Any]]],
    group_by: Literal["agent", "model", "agent_model"],
) -> list[AgentStatsRowOut]:
    groups: dict[tuple, list[tuple[int, str, str, dict[str, Any]]]] = defaultdict(list)
    for pid, name, slug, receipt in occurrences:
        launch = receipt["launch"]
        agent = _text(launch.get("agent")) or _text(receipt.get("agent"))
        model = _text(launch.get("model"))
        identity: tuple[Any, ...] = (model,) if group_by == "model" else (pid, agent)
        if group_by == "agent_model":
            identity += (model,)
        groups[identity].append((pid, name, slug, receipt))
    rows = []
    for identity, members in sorted(
        groups.items(), key=lambda item: tuple(str(v or "") for v in item[0])
    ):
        projects = sorted({(pid, name, slug) for pid, name, slug, _ in members})
        receipts = [receipt for _, _, _, receipt in members]
        roles = {
            r.get("selected_role") if isinstance(r.get("selected_role"), str) else None
            for r in receipts
        }
        hosts = {_text(r["launch"].get("host_key")) for r in receipts}
        models = {_text(r["launch"].get("model")) for r in receipts}
        single_project = projects[0] if len(projects) == 1 else None
        rows.append(
            AgentStatsRowOut(
                group_key=json.dumps([group_by, *identity], separators=(",", ":")),
                project_ids=[p[0] for p in projects],
                project_id=single_project[0] if single_project else None,
                project_name=single_project[1] if single_project else None,
                project_slug=single_project[2] if single_project else None,
                agent_name=identity[1] if group_by != "model" else None,
                selected_role=next(iter(roles)) if len(roles) == 1 else None,
                host_key=next(iter(hosts)) if len(hosts) == 1 else None,
                model=next(iter(models)) if len(models) == 1 else None,
                **summarize_receipts(receipts).model_dump(),
            )
        )
    return rows


def query_agent_stats(
    session: Session,
    *,
    project_id: int | None = None,
    is_active: bool | None = None,
    group_by: Literal["agent", "model", "agent_model"] = "agent_model",
    start_date: date | None = None,
    end_date: date | None = None,
    timezone: str = "UTC",
) -> TrackerAgentStatsOut:
    zone = stats_timezone(start_date, end_date, timezone)
    if group_by not in ("agent", "model", "agent_model"):
        raise ValueError("group_by must be agent, model or agent_model")
    provisional = provisional_usage(project_id)
    statement = (
        select(
            col(Project.id),
            col(Project.name),
            col(Project.slug),
            col(TrackerTicket.id),
            col(TrackerTicket.key),
            col(TrackerTicket.completion_evidence_json),
        )
        .select_from(TrackerTicket)
        .join(TaskTracker, col(TaskTracker.id) == col(TrackerTicket.tracker_id))
        .join(Project, col(Project.id) == col(TrackerTicket.project_id))
        .where(col(TaskTracker.key) == DEFAULT_TRACKER_KEY)
        .order_by(col(TrackerTicket.id))
    )
    if project_id is not None:
        statement = statement.where(col(TrackerTicket.project_id) == project_id)
    if is_active is not None:
        statement = statement.where(col(Project.is_active).is_(is_active))
    occurrences = []
    by_day: dict[date, list[tuple[int, str, str, dict[str, Any]]]] = defaultdict(list)
    undated = []
    for pid, name, slug, _ticket_id, ticket_key, evidence in session.execute(statement):
        for receipt in provisional_receipt(pid, ticket_key, evidence, provisional):
            if not _eligible(receipt):
                continue
            day = _launch_day(receipt, zone)
            if day is None:
                undated.append(receipt)
            if start_date is not None and end_date is not None:
                if day is None or not start_date <= day <= end_date:
                    continue
                by_day[day].append((pid, name, slug, receipt))
            occurrences.append((pid, name, slug, receipt))
    daily = []
    if start_date is not None and end_date is not None:
        for offset in range((end_date - start_date).days + 1):
            day = start_date + timedelta(days=offset)
            members = by_day[day]
            daily.append(
                AgentStatsDayOut(
                    date=day,
                    by_agent=_group_rows(members, "agent"),
                    by_model=_group_rows(members, "model"),
                    by_agent_model=_group_rows(members, "agent_model"),
                    **summarize_receipts(r for _, _, _, r in members).model_dump(),
                )
            )
    return TrackerAgentStatsOut(
        project_id=project_id,
        is_active=is_active,
        as_of=datetime.now(UTC),
        totals=summarize_receipts(r for _, _, _, r in occurrences),
        rows=_group_rows(occurrences, group_by),
        group_by=group_by,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        daily=daily,
        undated=summarize_receipts(undated),
    )
