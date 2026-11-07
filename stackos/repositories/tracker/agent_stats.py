"""One safe projection of existing automatic-delivery invocation receipts."""

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlmodel import Session, col

from stackos.db.models import Project, TaskTracker, TrackerTicket
from stackos.host_mcp.result import usage_snapshot
from stackos.repositories.tracker.schema import (
    AgentStatsCoverageOut,
    AgentStatsDayOut,
    AgentStatsFieldCoverageOut,
    AgentStatsMetricsOut,
    AgentStatsRowOut,
    AgentStatsUsageOut,
    TrackerAgentStatsOut,
)
from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY


def invocation_receipts(evidence: Any) -> list[dict[str, Any]]:
    """Deduplicate current/archive by attempt and phase within one ticket.

    Read only the canonical receipt locations, never tracker history, sessions,
    configuration or nested result packets. Current receipts take precedence.
    Legacy missing attempt identity remains unknown, rather than an inferred run.
    """
    if not isinstance(evidence, dict):
        return []
    automatic = evidence.get("automatic_delivery")
    if not isinstance(automatic, dict):
        return []
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
            if isinstance(receipt, dict) and isinstance(receipt.get("launch"), dict):
                unique[attempt, phase] = receipt
    return list(unique.values())


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


def summarize_receipts(receipts: Iterable[dict[str, Any]]) -> AgentStatsMetricsOut:
    """Summarize deduplicated ticket receipts for every statistics projection."""
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
            role in ("delivery_agent", "review_agent")
            and "validated_outcome" in receipt
            and outcome in (None, "success", "repair", "blocked", "failed")
        ):
            classified += 1
            rejects += int(role == "review_agent" and outcome == "repair")
            if role == "review_agent":
                review_outcomes[outcome or "validation_failed"] += 1
    return AgentStatsMetricsOut(
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
    session: Session, *, tracker_id: int, task_ids: set[int]
) -> dict[int, AgentStatsMetricsOut]:
    """Batch whole-task totals, independent of visible tickets or graph filters."""
    if not task_ids:
        return {}
    receipts_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    statement = select(
        col(TrackerTicket.task_id), col(TrackerTicket.completion_evidence_json)
    ).where(
        col(TrackerTicket.tracker_id) == tracker_id,
        col(TrackerTicket.task_id).in_(task_ids),
    )
    for task_id, evidence in session.execute(statement):
        # Deduplication is ticket-local; never add parent/group subtotals.
        receipts_by_task[task_id].extend(invocation_receipts(evidence))
    return {task_id: summarize_receipts(receipts_by_task[task_id]) for task_id in task_ids}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


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
            r.get("selected_role")
            if r.get("selected_role") in ("delivery_agent", "review_agent")
            else None
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
    statement = (
        select(
            col(Project.id),
            col(Project.name),
            col(Project.slug),
            col(TrackerTicket.id),
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
    for pid, name, slug, _ticket_id, evidence in session.execute(statement):
        for receipt in invocation_receipts(evidence):
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
