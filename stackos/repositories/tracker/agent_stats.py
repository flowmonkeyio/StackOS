"""One safe projection of existing automatic-delivery invocation receipts."""

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cached_property, lru_cache
from hashlib import sha256
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select, true
from sqlmodel import Session, col

from stackos.db.models import (
    Project,
    TaskTracker,
    TrackerReceiptProjection,
    TrackerTask,
    TrackerTicket,
)
from stackos.host_mcp import service as host_service
from stackos.host_mcp.result import normalize_model_observation, usage_snapshot
from stackos.host_mcp.service import ProvisionalUsage, active_execution, provisional_usage
from stackos.repositories.tracker.receipt_projection import decode_receipt, receipt_input
from stackos.repositories.tracker.schema import (
    AgentStatsCoverageOut,
    AgentStatsDayOut,
    AgentStatsFieldCoverageOut,
    AgentStatsMetricsOut,
    AgentStatsRowOut,
    AgentStatsUsageOut,
    ExecutionIdentityOut,
    TrackerAgentStatsOut,
    TrackerMetricDemand,
    UsageIntervalOut,
    UsageSampleOut,
    WorkDurationOut,
)
from stackos.repositories.tracker.utils import DEFAULT_TRACKER_KEY


def usage_read_token(session: Session, project_id: int | None) -> str | None:
    """Equality fence for Home reads and the stream's initial baseline only.

    Keep the existing durable event position (including non-reused project
    identity) and bounded native counter/identity owner; never retain either.
    Take this before the report's reads. A matching stable stream baseline can
    acknowledge that read without another full aggregation.
    """
    from stackos.context import ContextRepository
    from stackos.repositories.base import NotFoundError

    live = host_service.usage_executions(project_id)
    try:
        position = ContextRepository(session).event_position(project_id=project_id)
    except NotFoundError:
        # The pure aggregate also serves deleted-source reconciliation. Its
        # existing empty result is useful, but cannot acknowledge a live scope.
        return None
    scopes = session.execute(
        select(
            col(Project.id),
            col(Project.updated_at),
            col(Project.is_active),
            col(Project.cleanup_json),
            col(TaskTracker.rev),
        )
        .outerjoin(
            TaskTracker,
            (col(TaskTracker.project_id) == col(Project.id))
            & (col(TaskTracker.key) == DEFAULT_TRACKER_KEY),
        )
        .where(col(Project.id) == project_id if project_id is not None else true())
        .order_by(col(Project.id))
    ).all()
    return sha256(
        json.dumps(
            [project_id, position, [tuple(row) for row in scopes], live],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def invocation_receipts(evidence: Any, *, require_launch: bool = True) -> list[dict[str, Any]]:
    """Deduplicate canonical receipts and retain distinct interrupted invocations.

    Read only the canonical receipt locations, never tracker history, sessions,
    configuration or nested result packets. Current receipts take precedence.
    Legacy missing attempt identity remains unknown, rather than an inferred run.
    """
    return list(_receipt_items(evidence, require_launch=require_launch).values())


def _receipt_items(
    evidence: Any, *, require_launch: bool = True
) -> dict[tuple[int | None, str, str], dict[str, Any]]:
    if not isinstance(evidence, dict):
        return {}
    automatic = evidence.get("automatic_delivery")
    if not isinstance(automatic, dict):
        return {}
    archive = automatic.get("attempts")
    occurrences = [*(archive if isinstance(archive, list) else []), automatic]
    normal = {}
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
                # Review-only recovery retains the successful delivery receipt;
                # it is still the original invocation, not another delivery.
                receipt_attempt = (
                    occurrence.get("delivery_reused_from_attempt", attempt)
                    if phase == "delivery"
                    else attempt
                )
                normal[receipt_attempt, phase] = receipt
    # Normal current/archive precedence remains unchanged, including legacy
    # copies without invocation IDs. Retained observations have explicit source
    # locations and may share an attempt/phase with a resumed call.
    interruptions = automatic.get("upgrade_interruptions")
    observations = [
        (f"upgrade_interruptions:{position}", item, "receipt")
        for position, item in enumerate(interruptions if isinstance(interruptions, list) else [])
    ]
    interruptions = automatic.get("account_interruptions")
    observations.extend(
        (f"account_interruptions:{position}", item, "receipt")
        for position, item in enumerate(interruptions if isinstance(interruptions, list) else [])
    )
    corrections = automatic.get("reporting_corrections")
    observations.extend(
        (f"reporting_corrections:{position}:{receipt_key}", item, receipt_key)
        for position, item in enumerate(corrections if isinstance(corrections, list) else [])
        for receipt_key in ("original_receipt", "corrected_receipt")
    )
    retained: dict[tuple[int | None, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    normal_ids = set()
    for (attempt, phase), receipt in normal.items():
        execution = receipt.get("execution")
        if isinstance(execution, dict) and isinstance(execution.get("invocation_id"), str):
            normal_ids.add((attempt, phase, execution["invocation_id"]))
    for locator, item, receipt_key in observations:
        if not isinstance(item, dict) or item.get("phase") not in {"delivery", "review"}:
            continue
        receipt = item.get(receipt_key)
        if not isinstance(receipt, dict) or (
            require_launch and not isinstance(receipt.get("launch"), dict)
        ):
            continue
        attempt = item.get("attempt")
        attempt = attempt if type(attempt) is int and attempt > 0 else None
        phase = item["phase"]
        execution = receipt.get("execution")
        invocation = execution.get("invocation_id") if isinstance(execution, dict) else None
        if isinstance(invocation, str) and invocation:
            if (attempt, phase, invocation) in normal_ids:
                continue  # Still-current/archive copies are the same invocation.
            observation = f"invocation:{invocation}"
        else:
            current = normal.get((attempt, phase))
            launch = receipt.get("launch")
            if (
                receipt_key != "receipt"
                and isinstance(current, dict)
                and isinstance(launch, dict)
                and isinstance(launch.get("started_at"), str)
                and launch["started_at"]
                and launch == current.get("launch")
            ):
                continue  # A known reporting copy of the same saved launch.
            # A source locator distinguishes an incomplete observation without
            # inventing a native invocation identity, timestamp or usage.
            observation = locator
        retained[attempt, phase][observation] = receipt
    unique = {}
    for (attempt, phase), receipt in normal.items():
        for observation, interrupted in retained.pop((attempt, phase), {}).items():
            unique[attempt, phase, observation] = interrupted
        unique[attempt, phase, ""] = receipt
    for (attempt, phase), receipts in retained.items():
        for observation, receipt in receipts.items():
            unique[attempt, phase, observation] = receipt
    return unique


def invocation_timing_facts(evidence: Any) -> dict[tuple[int | None, str, str], tuple[Any, ...]]:
    """Durable duration inputs for stream invalidation, without a moving clock or usage."""
    facts = {}
    for identity, receipt in _receipt_items(evidence, require_launch=False).items():
        launch = receipt.get("launch")
        execution = receipt.get("execution")
        facts[identity] = (
            launch.get("started_at") if isinstance(launch, dict) else None,
            receipt.get("finished_at"),
            any(key in receipt for key in ("finished_at", "exit_code", "result")),
            tuple(execution.get(key) for key in ExecutionIdentityOut.model_fields)
            if isinstance(execution, dict)
            else None,
        )
    return facts


def work_intervals(
    sources: Iterable[tuple[str, Any]],
    *,
    live_calls: list[dict[str, Any]],
    observed_at: datetime,
) -> tuple[list[tuple[datetime, datetime]], dict[str, ExecutionIdentityOut], set[str], int]:
    """Validate ticket-local deduplicated intervals; missing data is never zero-filled.

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
        receipts = _receipt_items(evidence, require_launch=False)
        if not receipts:
            # Preserve this included ticket's unknown history in a task union.
            # It contributes no invented interval and is never evidence of zero.
            issues.add("no_recorded_invocations")
        for (attempt, phase, _observation), receipt in receipts.items():
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
                    # The request owns this pre-SQL registry copy. Never extend
                    # it past its actual observation when the call can retire
                    # while SQL reads the final receipt. Durable finishes win.
                    end = (
                        _instant(owned.get("finished_at"))
                        or _instant(owned.get("observed_at"))
                        or observed_at
                    )
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
    return intervals, live, issues, missing


def union_intervals(
    intervals: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = result[-1][0], max(result[-1][1], end)
        else:
            result.append((start, end))
    return result


def work_duration(
    sources: Iterable[tuple[str, Any]],
    *,
    live_calls: list[dict[str, Any]],
    observed_at: datetime,
) -> WorkDurationOut:
    """Project the union using the same interval validator as scoped reporting."""
    intervals, live, issues, missing = work_intervals(
        sources, live_calls=live_calls, observed_at=observed_at
    )
    total = 0.0
    endpoint = None
    for start, end in union_intervals(intervals):
        total += max(0, (end - max(start, endpoint or start)).total_seconds())
        endpoint = max(endpoint or end, end)
    if not intervals and not missing:
        issues.add("no_recorded_invocations")
    return WorkDurationOut(
        total_seconds=total if intervals else None,
        observed_at=endpoint.isoformat() if endpoint else None,
        endpoint_at=endpoint.isoformat() if endpoint else None,
        coverage=("partial" if missing or "no_recorded_invocations" in issues else "complete")
        if intervals
        else "unavailable",
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


def classification_known(receipt: dict[str, Any]) -> bool:
    # A positively fenced invocation has no validated decision. Other explicit
    # nulls still mean failed validation, not an inferred cancellation.
    if receipt.get("validated_outcome") is None and (
        receipt.get("cancelled") is True or receipt.get("upgrade_interruption")
    ):
        return False
    role = receipt.get("selected_role")
    return (
        isinstance(role, str)
        and bool(role)
        and "validated_outcome" in receipt
        and receipt.get("validated_outcome") in (None, "success", "repair", "blocked", "failed")
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


@dataclass
class ReceiptFact:
    """One immutable-by-convention normalized contribution, shared by read projections."""

    receipt: dict[str, Any]
    measured: dict[str, Any] | None
    eligible: bool

    @cached_property
    def events(self) -> list[tuple[datetime, dict[str, Any], UsageIntervalOut]] | None:
        return _receipt_events(self)


@lru_cache(maxsize=8192)
def _normalized_receipt(source_revision: str, ticket_key: str | None) -> ReceiptFact:
    receipt = {**decode_receipt(source_revision)}
    if ticket_key is not None:
        receipt["ticket_key"] = ticket_key
    return ReceiptFact(receipt, _measured(receipt), _eligible(receipt))


def _fact(receipt: dict[str, Any] | ReceiptFact) -> ReceiptFact:
    if isinstance(receipt, ReceiptFact):
        return receipt
    source = json.dumps(receipt_input(receipt), sort_keys=True, separators=(",", ":"))
    return _normalized_receipt(source, receipt.get("ticket_key"))


def _receipt_events(
    fact: ReceiptFact,
) -> list[tuple[datetime, dict[str, Any], UsageIntervalOut]] | None:
    """Replay observations once per receipt, replacing its cumulative contribution.

    A receipt's first retained checkpoint includes its earlier usage. Missing
    checkpoints create no zero prefix. Equal timestamps are one observation of
    the combined scope, independent of receipt/finalization order.
    """
    events = []
    receipt = fact.receipt
    if fact.eligible and fact.measured is not None:
        final = fact.measured
        live = (receipt.get("execution") or {}).get("live_usage") or {}
        final_at = _instant(live.get("measured_at")) or _instant(receipt.get("finished_at"))
        if _measured({"usage": live.get("usage")}) != final:
            final_at = _instant(receipt.get("finished_at")) or final_at
        points = live.get("samples")
        dated: dict[datetime, dict[str, Any]] = {}
        sources: dict[datetime, str | None] = {}
        conflicts: set[datetime] = set()
        for point in points[-120:] if isinstance(points, list) else []:
            if not isinstance(point, dict):
                continue
            at = _instant(point.get("at"))
            measured = _measured(
                {"usage": {"unit": "tokens", "status": "measured", "measured": point}}
            )
            if at is not None and (final_at is None or at <= final_at) and measured is not None:
                if at in dated and dated[at] != measured:
                    conflicts.add(at)
                dated[at] = measured
                sources[at] = point.get("source", "native")
        if final is not None and final_at is not None:
            if dated.get(final_at) != final:
                sources[final_at] = "final"
            dated[final_at] = final
        if final is not None and not dated:
            # A legacy total without an observation time cannot be placed on a
            # lifetime timeline. Keep the numbers; do not draw a narrower scope.
            return None
        previous: tuple[datetime, dict[str, Any]] | None = None
        for at, measured in sorted(dated.items()):
            # Pair optional detail before differencing. A newly available cache
            # split is coverage, not newly consumed non-cache Input.
            values = {
                **measured,
                "remaining_input": (
                    measured["input"] - measured["cache_read"]
                    if measured.get("cache_read") is not None
                    else None
                ),
            }
            current_values = values
            kind: Literal["baseline", "checkpoint", "batched", "reset"] = "baseline"
            start_at = None
            if previous is not None:
                prior_at, prior = previous
                start_at = prior_at.isoformat()
                kind = "checkpoint"
                if (at - prior_at).total_seconds() > 60 or sources.get(at) == "final":
                    kind = "batched"
                if at in conflicts or any(
                    measured[key] < prior[key] for key in ("input", "output", "total")
                ):
                    kind = "reset"
                values = {
                    key: value - prior[key]
                    if kind != "reset"
                    and value is not None
                    and prior.get(key) is not None
                    and value >= prior[key]
                    else None
                    for key, value in values.items()
                }
                if kind != "reset":
                    delta = _measured(
                        {"usage": {"unit": "tokens", "status": "measured", "measured": values}}
                    )
                    assert delta is not None
                    values = {
                        **delta,
                        "remaining_input": (
                            delta["input"] - delta["cache_read"]
                            if delta.get("cache_read") is not None
                            else None
                        ),
                    }
            if at in conflicts:
                kind = "reset"
                values = {key: None for key in values}
            events.append((at, measured, UsageIntervalOut(kind=kind, start_at=start_at, **values)))
            previous = None if at in conflicts else (at, current_values)
    return events


def _history(receipts: list[ReceiptFact]) -> list[UsageSampleOut]:
    events: dict[datetime, dict[int, dict[str, Any]]] = defaultdict(dict)
    intervals: dict[datetime, list[UsageIntervalOut]] = defaultdict(list)
    for index, receipt in enumerate(receipts):
        points = receipt.events
        if points is None:
            return []
        for at, measured, interval in points:
            events[at][index] = measured
            intervals[at].append(interval.model_copy(deep=True))
    current: dict[int, dict[str, Any]] = {}
    fields = ("input", "output", "total", "cache_read", "cache_write", "reasoning")
    sums = dict.fromkeys(fields, 0)
    known = dict.fromkeys(fields, 0)
    remaining = 0
    samples = []
    ordered = sorted(events.items())
    for position, (at, updates) in enumerate(ordered):
        # Replace changed contributions instead of summing every retained
        # invocation again at each checkpoint of the combined timeline.
        for index, measured in updates.items():
            for sign, values in ((-1, current.get(index)), (1, measured)):
                if values is None:
                    continue
                for key in fields:
                    if values.get(key) is not None:
                        sums[key] += sign * values[key]
                        known[key] += sign
                if values.get("cache_read") is not None:
                    remaining += sign * (values["input"] - values["cache_read"])
            current[index] = measured
        if position < len(ordered) - 120:
            continue
        sample_values: dict[str, Any] = {key: sums[key] if known[key] else None for key in fields}
        samples.append(
            UsageSampleOut(
                at=at.isoformat(),
                intervals=intervals[at],
                **sample_values,
                remaining_input=remaining if known["cache_read"] else None,
            )
        )
    return samples[-120:]


class ReceiptMetrics:
    """Add normalized facts to multiple requested buckets in one source pass."""

    def __init__(self) -> None:
        self.total = self.measured_count = self.classified = self.rejects = 0
        self.usage = dict.fromkeys(
            ("input", "output", "total", "cache_read", "cache_write", "reasoning"),
            0,
        )
        self.known = dict.fromkeys(self.usage, 0)
        self.remaining = self.unknown_cache_input = 0
        self.review_outcomes: dict[str, int] = defaultdict(int)

    def add(self, fact: ReceiptFact) -> None:
        receipt = fact.receipt
        if not fact.eligible:
            return
        self.total += 1
        measured = fact.measured
        if measured is not None:
            self.measured_count += 1
            for key in self.usage:
                if measured[key] is not None:
                    self.usage[key] += measured[key]
                    self.known[key] += 1
            if measured["cache_read"] is not None:
                self.remaining += measured["input"] - measured["cache_read"]
            else:
                self.unknown_cache_input += measured["input"]
        role = receipt.get("selected_role")
        outcome = receipt.get("validated_outcome")
        if classification_known(receipt):
            self.classified += 1
            self.rejects += int(role == "review_agent" and outcome == "repair")
            if role == "review_agent":
                self.review_outcomes[outcome or "validation_failed"] += 1

    def output(self) -> AgentStatsMetricsOut:
        return AgentStatsMetricsOut(
            launch_attempts=self.total,
            recorded_reviewer_rejects=self.rejects,
            review_outcomes=dict(self.review_outcomes),
            reported_usage=AgentStatsUsageOut.model_validate(
                {
                    **{
                        key: value if self.known[key] or not self.total else None
                        for key, value in self.usage.items()
                    },
                    "remaining_input": self.remaining
                    if self.known["cache_read"] or not self.total
                    else None,
                    "input_with_unknown_cache": self.unknown_cache_input
                    if self.measured_count or not self.total
                    else None,
                }
            ),
            coverage=AgentStatsCoverageOut(
                usage_measured_attempts=self.measured_count,
                usage_unavailable_attempts=self.total - self.measured_count,
                classification_known_attempts=self.classified,
                classification_unknown_attempts=self.total - self.classified,
                usage_status=_coverage(self.measured_count, self.total),
                classification_status=_coverage(self.classified, self.total),
                fields={
                    key: AgentStatsFieldCoverageOut(
                        measured_attempts=known,
                        unavailable_attempts=self.total - known,
                        status=_coverage(known, self.total),
                    )
                    for key, known in self.known.items()
                },
            ),
        )


def summarize_receipts(
    receipts: Iterable[dict[str, Any] | ReceiptFact], *, include_history: bool = False
) -> AgentStatsMetricsOut:
    """Summarize deduplicated ticket receipts for every statistics projection."""
    facts = [_fact(receipt) for receipt in receipts]
    metrics = ReceiptMetrics()
    for fact in facts:
        metrics.add(fact)
    output = metrics.output()
    if include_history:
        output.samples = _history(facts)
        output.last_execution = _last_execution([fact.receipt for fact in facts])
    return output


@dataclass
class TicketReceipts:
    task_id: int
    project_id: int
    key: str
    task_key: str
    evidence: dict[str, Any]
    receipts: list[ReceiptFact]
    tracker_id: int
    durable_receipts: list[ReceiptFact]


class ReceiptProjection:
    """One request's fresh scope, provisional snapshot and normalized receipt inventory."""

    def __init__(
        self,
        session: Session,
        *,
        provisional: ProvisionalUsage | None = None,
        timing_snapshot: list[dict[str, Any]] | None = None,
    ):
        self.session = session
        # Copy live state before fresh scalar SQL, preserving native-exit handoff.
        self.provisional = provisional_usage() if provisional is None else provisional
        self.timing = (
            host_service.invocation_timings() if timing_snapshot is None else timing_snapshot
        )
        self.tickets: dict[int, TicketReceipts] = {}
        self.task_scopes: set[tuple[int | None, int]] = set()
        self.summaries: dict[tuple[int, ...], AgentStatsMetricsOut] = {}

    def load(
        self,
        *,
        task_ids: set[int] | None = None,
        ticket_ids: set[int] | None = None,
        tracker_id: int | None = None,
    ) -> None:
        missing_tasks = {tid for tid in task_ids or () if (tracker_id, tid) not in self.task_scopes}
        missing_tickets = (ticket_ids or set()) - self.tickets.keys()
        if not missing_tasks and not missing_tickets:
            return
        table = TrackerReceiptProjection
        statement = (
            select(  # type: ignore[call-overload]
                TrackerTicket.id,
                TrackerTicket.task_id,
                TrackerTicket.project_id,
                TrackerTicket.key,
                TrackerTask.key,
                table.attempt,
                table.phase,
                table.observation_key,
                table.current,
                table.receipt_json,
                table.inventory,
                table.timing,
                TrackerTicket.tracker_id,
            )
            .join(TrackerTask, col(TrackerTask.id) == col(TrackerTicket.task_id))
            .outerjoin(
                table,
                col(table.ticket_id) == col(TrackerTicket.id),
            )
            .where(
                or_(
                    col(TrackerTicket.task_id).in_(missing_tasks),
                    col(TrackerTicket.id).in_(missing_tickets),
                )
            )
        )
        if tracker_id is not None:
            statement = statement.where(col(TrackerTicket.tracker_id) == tracker_id)
        rows = self.session.execute(statement.order_by(TrackerTicket.id, table.position)).all()
        ids = {row[0] for row in rows} - self.tickets.keys()
        evidence: dict[int, dict[str, Any]] = {tid: {"automatic_delivery": {}} for tid in ids}
        archives: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
        usage: dict[int, list[ReceiptFact]] = defaultdict(list)
        durable: dict[int, list[ReceiptFact]] = defaultdict(list)
        if ids:
            for (
                tid,
                _task,
                pid,
                key,
                _task_key,
                attempt,
                phase,
                observation,
                current,
                source,
                inventory,
                timing,
                _tracker,
            ) in rows:
                if tid not in ids or source is None:
                    continue
                # Current/archived object identity is part of work_duration's
                # ownership check, even when their projected values are equal.
                receipt = dict(decode_receipt(source))
                if inventory == "usage":
                    durable_fact = _normalized_receipt(source, key)
                    durable[tid].append(durable_fact)
                    if current and (pid, key) in self.provisional:
                        selected = provisional_receipt(
                            pid,
                            key,
                            {
                                "automatic_delivery": {
                                    "attempt": attempt or None,
                                    phase: receipt,
                                }
                            },
                            self.provisional,
                        )
                        usage[tid].extend(_fact({**r, "ticket_key": key}) for r in selected)
                    else:
                        usage[tid].append(durable_fact)
                if not timing:
                    continue
                automatic = evidence[tid]["automatic_delivery"]
                if observation:
                    automatic.setdefault("upgrade_interruptions", []).append(
                        {"attempt": attempt or None, "phase": phase, "receipt": receipt}
                    )
                elif current:
                    automatic["attempt"] = attempt or None
                    automatic[phase] = receipt
                else:
                    occurrence = archives[tid].setdefault(attempt, {"attempt": attempt or None})
                    occurrence[phase] = receipt
            for tid in ids:
                evidence[tid]["automatic_delivery"]["attempts"] = list(archives[tid].values())
        for tid, task_id, project_id, key, task_key, *tail in rows:
            if tid in self.tickets:
                continue
            source = evidence[tid]
            self.tickets[tid] = TicketReceipts(
                task_id,
                project_id,
                key,
                task_key,
                source,
                usage[tid],
                tail[-1],
                durable[tid],
            )
        self.task_scopes.update((tracker_id, tid) for tid in missing_tasks)

    def metrics(self, receipts: list[ReceiptFact]) -> AgentStatsMetricsOut:
        identity = tuple(id(receipt) for receipt in receipts)
        if identity not in self.summaries:
            self.summaries[identity] = summarize_receipts(receipts, include_history=True)
        # Consumers may mutate their output; never expose a cached mutable model.
        return self.summaries[identity].model_copy(deep=True)


def query_task_agent_stats(
    session: Session,
    *,
    tracker_id: int | None,
    task_ids: set[int],
    provisional: ProvisionalUsage | None = None,
    work_durations: dict[int, WorkDurationOut] | None = None,
    task_keys: dict[int, str] | None = None,
    timing_snapshot: list[dict[str, Any]] | None = None,
    projection: ReceiptProjection | None = None,
) -> dict[int, AgentStatsMetricsOut]:
    """Batch whole-task totals, independent of visible tickets or graph filters."""
    if not task_ids:
        return {}
    projection = projection or ReceiptProjection(
        session,
        provisional=provisional,
        timing_snapshot=timing_snapshot,
    )
    projection.load(task_ids=task_ids, tracker_id=tracker_id)
    receipts_by_task: dict[int, list[ReceiptFact]] = defaultdict(list)
    duration_sources: dict[int, list[tuple[str, Any]]] = defaultdict(list)
    projects: dict[int, int] = {}
    for ticket in projection.tickets.values():
        task_id = ticket.task_id
        if task_id not in task_ids or (tracker_id is not None and ticket.tracker_id != tracker_id):
            continue
        duration_sources[task_id].append((ticket.key, ticket.evidence))
        projects[task_id] = ticket.project_id
        # Deduplication is ticket-local; never add parent/group subtotals.
        receipts_by_task[task_id].extend(ticket.receipts)
    if work_durations is not None:
        # Share the fresh scalar receipt read with usage, but never its
        # token eligibility filter or provisional usage substitutions.
        observed_at = datetime.now(UTC)
        for task_id in task_ids:
            work_durations[task_id] = work_duration(
                duration_sources[task_id],
                live_calls=[
                    call
                    for call in projection.timing
                    if call.get("project_id") == projects.get(task_id)
                    and call.get("task_key") == (task_keys or {}).get(task_id)
                ],
                observed_at=observed_at,
            )
    return {task_id: projection.metrics(receipts_by_task[task_id]) for task_id in task_ids}


def query_ticket_agent_stats(
    session: Session,
    *,
    ticket_ids: set[int],
    provisional: ProvisionalUsage | None = None,
    work_durations: dict[int, WorkDurationOut] | None = None,
    timing_snapshot: list[dict[str, Any]] | None = None,
    projection: ReceiptProjection | None = None,
) -> dict[int, AgentStatsMetricsOut]:
    """Fresh scalar evidence avoids stale ORM rows loaded before native exit."""
    if not ticket_ids:
        return {}
    projection = projection or ReceiptProjection(
        session,
        provisional=provisional,
        timing_snapshot=timing_snapshot,
    )
    projection.load(ticket_ids=ticket_ids)
    observed_at = datetime.now(UTC)
    metrics = {}
    for ticket_id, ticket in projection.tickets.items():
        if ticket_id not in ticket_ids:
            continue
        metrics[ticket_id] = projection.metrics(ticket.receipts)
        if work_durations is not None:
            work_durations[ticket_id] = work_duration(
                [(ticket.key, ticket.evidence)],
                live_calls=[
                    call
                    for call in projection.timing
                    if call.get("project_id") == ticket.project_id
                    and call.get("task_key") == ticket.task_key
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
    metric_demand: TrackerMetricDemand | None = None,
    projection: ReceiptProjection | None = None,
) -> dict[str, Any]:
    """Safe initial/reconnect snapshot, then minimal affected Work projections."""
    if metric_demand is not None:
        if task_key is not None:
            from stackos.repositories.base import ValidationError

            raise ValidationError("UI metric demand uses complete project scope")
        from stackos.repositories.tracker import TrackerRepository
        from stackos.repositories.tracker.ui_projection import ui_snapshot

        snapshot = ui_snapshot(
            TrackerRepository(session),
            project_id=project_id,
            task_key=task_key,
            task_index_only=True,
            include_graph=False,
            task_statuses=None,
            workflow_key=None,
            demand=metric_demand,
            receipt_projection=projection,
        )
        return {
            "projection": "ui",
            "scope_complete": True,
            "metric_scope": "demand",
            "project_id": project_id,
            "rev": snapshot.tracker.rev,
            "metric_demand": metric_demand.model_dump(mode="json"),
            "tasks": [task.model_dump(mode="json") for task in snapshot.tasks],
            "tickets": [ticket.model_dump(mode="json") for ticket in snapshot.tickets],
            "replace": True,
        }
    # Snapshot before SQL: removal implies the receipt commit already happened;
    # otherwise this request owns its counter copy through projection. No DB I/O
    # runs under the registry lock and no finished-call history is retained.
    provisional = provisional_usage(project_id)
    timing_snapshot = host_service.invocation_timings()
    projection = ReceiptProjection(
        session, provisional=provisional, timing_snapshot=timing_snapshot
    )
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
                timing_snapshot=timing_snapshot,
                projection=projection,
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
        timing_snapshot=timing_snapshot,
        projection=projection,
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


@dataclass
class GroupMetrics:
    metrics: ReceiptMetrics = field(default_factory=ReceiptMetrics)
    projects: set[tuple[int, str, str]] = field(default_factory=set)
    roles: set[str | None] = field(default_factory=set)
    hosts: set[str | None] = field(default_factory=set)
    models: set[str | None] = field(default_factory=set)
    model_bases: set[Literal["observed", "selected"] | None] = field(default_factory=set)


def _model_identity(
    receipt: dict[str, Any],
) -> tuple[str | None, Literal["observed", "selected"] | None]:
    """Prefer exact native evidence; ambiguity forbids selection fallback."""
    launch = receipt.get("launch") or {}
    execution = receipt.get("execution") or {}
    observations = [
        (owner["model_observation"], owner.get("thread_id"), owner.get("host_key"))
        for owner in (receipt, execution)
        if owner.get("model_observation") is not None
    ]
    if not observations:
        selected = _text(launch.get("model"))
        return selected, "selected" if selected else None
    if any(host is not None and host != launch.get("host_key") for _, _, host in observations):
        return None, None
    normalized = [
        normalize_model_observation(value, session_id=sid, host_key=launch.get("host_key"))
        for value, sid, _ in observations
    ]
    observed = normalized[0]
    if (
        observed is None
        or observed["status"] != "observed"
        or any(other != observed for other in normalized)
        or (receipt.get("thread_id") is not None and receipt["thread_id"] != observed["session_id"])
    ):
        return None, None
    return observed["model"], "observed"


def _add_group(
    groups: dict[tuple, GroupMetrics],
    group_by: Literal["agent", "model", "agent_model"],
    pid: int,
    name: str,
    slug: str,
    fact: ReceiptFact,
) -> None:
    receipt = fact.receipt
    launch = receipt["launch"]
    agent = _text(launch.get("agent")) or _text(receipt.get("agent"))
    model, model_basis = _model_identity(receipt)
    identity: tuple[Any, ...] = (model,) if group_by == "model" else (pid, agent)
    if group_by == "agent_model":
        identity += (model,)
    group = groups.get(identity)
    if group is None:
        group = groups[identity] = GroupMetrics()
    group.metrics.add(fact)
    group.projects.add((pid, name, slug))
    role = receipt.get("selected_role")
    group.roles.add(role if isinstance(role, str) else None)
    group.hosts.add(_text(launch.get("host_key")))
    group.models.add(model)
    group.model_bases.add(model_basis)


def _group_rows(
    groups: dict[tuple, GroupMetrics],
    group_by: Literal["agent", "model", "agent_model"],
) -> list[AgentStatsRowOut]:
    rows = []
    for identity, group in sorted(
        groups.items(), key=lambda item: tuple(str(v or "") for v in item[0])
    ):
        projects = sorted(group.projects)
        single_project = projects[0] if len(projects) == 1 else None
        rows.append(
            AgentStatsRowOut(
                group_key=json.dumps([group_by, *identity], separators=(",", ":")),
                project_ids=[p[0] for p in projects],
                project_id=single_project[0] if single_project else None,
                project_name=single_project[1] if single_project else None,
                project_slug=single_project[2] if single_project else None,
                agent_name=identity[1] if group_by != "model" else None,
                selected_role=next(iter(group.roles)) if len(group.roles) == 1 else None,
                host_key=next(iter(group.hosts)) if len(group.hosts) == 1 else None,
                model=next(iter(group.models)) if len(group.models) == 1 else None,
                model_basis=(
                    next(iter(group.model_bases)) if len(group.model_bases) == 1 else "mixed"
                ),
                **group.metrics.output().model_dump(),
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
    read_token = usage_read_token(session, project_id)
    zone = stats_timezone(start_date, end_date, timezone)
    if group_by not in ("agent", "model", "agent_model"):
        raise ValueError("group_by must be agent, model or agent_model")
    provisional = provisional_usage(project_id)
    timing = host_service.invocation_timings()
    table = TrackerReceiptProjection
    statement = (
        select(  # type: ignore[call-overload]
            col(Project.id),
            col(Project.name),
            col(Project.slug),
            col(TrackerTicket.key),
            col(table.attempt),
            col(table.phase),
            col(table.current),
            col(table.receipt_json),
        )
        .select_from(table)
        .join(TrackerTicket, col(TrackerTicket.id) == col(table.ticket_id))
        .join(TaskTracker, col(TaskTracker.id) == col(TrackerTicket.tracker_id))
        .join(Project, col(Project.id) == col(TrackerTicket.project_id))
        .where(col(TaskTracker.key) == DEFAULT_TRACKER_KEY)
        .where(col(table.inventory) == "usage")
    )
    if project_id is not None:
        statement = statement.where(col(TrackerTicket.project_id) == project_id)
    if is_active is not None:
        statement = statement.where(col(Project.is_active).is_(is_active))
    selected: Any = statement
    if start_date is not None and end_date is not None:
        try:
            start = datetime.combine(start_date, datetime.min.time(), zone).astimezone(UTC)
        except OverflowError:
            start = datetime.min.replace(tzinfo=UTC)
        try:
            end = datetime.combine(
                end_date + timedelta(days=1), datetime.min.time(), zone
            ).astimezone(UTC)
        except OverflowError:
            end = None
        # Separate disjoint ranges keep both scoped and global date indexes
        # searchable; undated disclosure deliberately remains lifetime-scoped.
        selected = statement.where(
            col(table.launch_at) >= start.replace(tzinfo=None),
            col(table.launch_at) < end.replace(tzinfo=None) if end is not None else true(),
        ).union_all(statement.where(col(table.launch_at).is_(None)))
    totals = ReceiptMetrics()
    groups: dict[tuple, GroupMetrics] = {}
    by_day: dict[date, ReceiptMetrics] = defaultdict(ReceiptMetrics)
    daily_groups: dict[tuple[date, str], dict[tuple, GroupMetrics]] = defaultdict(dict)
    undated = ReceiptMetrics()
    for pid, name, slug, ticket_key, attempt, phase, current, source in session.execute(selected):
        receipt = decode_receipt(source)
        receipts = [receipt]
        if current and (pid, ticket_key) in provisional:
            receipts = provisional_receipt(
                pid,
                ticket_key,
                {
                    "automatic_delivery": {
                        "attempt": attempt or None,
                        phase: receipt,
                    }
                },
                provisional,
            )
        for item in receipts:
            fact = _normalized_receipt(source, None) if item is receipt else _fact(item)
            receipt = fact.receipt
            if not isinstance(receipt.get("launch"), dict) or not fact.eligible:
                continue
            day = _launch_day(receipt, zone)
            if day is None:
                undated.add(fact)
            if start_date is not None and end_date is not None:
                if day is None or not start_date <= day <= end_date:
                    continue
                by_day[day].add(fact)
                for grouping in ("agent", "model", "agent_model"):
                    _add_group(daily_groups[day, grouping], grouping, pid, name, slug, fact)
            totals.add(fact)
            _add_group(groups, group_by, pid, name, slug, fact)
    daily = []
    if start_date is not None and end_date is not None:
        for offset in range((end_date - start_date).days + 1):
            day = start_date + timedelta(days=offset)
            daily.append(
                AgentStatsDayOut(
                    date=day,
                    by_agent=_group_rows(daily_groups[day, "agent"], "agent"),
                    by_model=_group_rows(daily_groups[day, "model"], "model"),
                    by_agent_model=_group_rows(daily_groups[day, "agent_model"], "agent_model"),
                    **by_day[day].output().model_dump(),
                )
            )
    from stackos.repositories.tracker.delivery_stats import query_delivery_trends

    delivery_trends = query_delivery_trends(
        session,
        project_id=project_id,
        is_active=is_active,
        start_date=start_date,
        end_date=end_date,
        zone=zone,
        timing=timing,
    )
    return TrackerAgentStatsOut(
        read_token=read_token,
        project_id=project_id,
        is_active=is_active,
        as_of=datetime.now(UTC),
        totals=totals.output(),
        rows=_group_rows(groups, group_by),
        group_by=group_by,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        daily=daily,
        undated=undated.output(),
        delivery_trends=delivery_trends,
    )
