"""One safe projection of existing automatic-delivery invocation receipts."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlmodel import Session, col

from stackos.db.models import Project, TaskTracker, TrackerTicket
from stackos.repositories.tracker.schema import (
    AgentStatsCoverageOut,
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


def _measured(receipt: dict[str, Any]) -> dict[str, int] | None:
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
    return measured


def summarize_receipts(receipts: Iterable[dict[str, Any]]) -> AgentStatsMetricsOut:
    """Summarize deduplicated ticket receipts for every statistics projection."""
    total = measured_count = classified = rejects = 0
    usage = dict.fromkeys(("input", "output", "total"), 0)
    for receipt in receipts:
        total += 1
        measured = _measured(receipt)
        if measured is not None:
            measured_count += 1
            for key in usage:
                usage[key] += measured[key]
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
    return AgentStatsMetricsOut(
        launch_attempts=total,
        recorded_reviewer_rejects=rejects,
        reported_usage=AgentStatsUsageOut(
            input=usage["input"] if measured_count or not total else None,
            output=usage["output"] if measured_count or not total else None,
            total=usage["total"] if measured_count or not total else None,
        ),
        coverage=AgentStatsCoverageOut(
            usage_measured_attempts=measured_count,
            usage_unavailable_attempts=total - measured_count,
            classification_known_attempts=classified,
            classification_unknown_attempts=total - classified,
            usage_status=_coverage(measured_count, total),
            classification_status=_coverage(classified, total),
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


def query_agent_stats(
    session: Session, *, project_id: int | None = None, is_active: bool | None = None
) -> TrackerAgentStatsOut:
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
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    all_receipts = []
    for pid, name, slug, _ticket_id, evidence in session.execute(statement):
        for receipt in invocation_receipts(evidence):
            launch = receipt["launch"]
            role = receipt.get("selected_role")
            identity = (
                pid,
                name,
                slug,
                _text(launch.get("agent")) or _text(receipt.get("agent")),
                role if role in ("delivery_agent", "review_agent") else None,
                _text(launch.get("host_key")),
                _text(launch.get("model")),
            )
            groups[identity].append(receipt)
            all_receipts.append(receipt)
    rows = []
    for identity, receipts in sorted(
        groups.items(), key=lambda item: tuple(str(v or "") for v in item[0])
    ):
        pid, name, slug, agent, role, host, model = identity
        rows.append(
            AgentStatsRowOut(
                project_id=pid,
                project_name=name,
                project_slug=slug,
                agent_name=agent,
                selected_role=role,
                host_key=host,
                model=model,
                **summarize_receipts(receipts).model_dump(),
            )
        )
    return TrackerAgentStatsOut(
        project_id=project_id,
        is_active=is_active,
        as_of=datetime.now(UTC),
        totals=summarize_receipts(all_receipts),
        rows=rows,
    )
