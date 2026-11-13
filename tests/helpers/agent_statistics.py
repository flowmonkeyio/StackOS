"""Deterministic disposable receipt workloads shared by local and assembled proof."""

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from stackos.db.models import (
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketKind,
)
from stackos.repositories.tracker import TrackerRepository


def seed_statistics_workload(
    session: Session, project_id: int, *, tasks: int = 34, receipts: int = 591, days: int = 365
) -> list[int]:
    """No production state; each receipt contributes exactly 11 tokens and one launch."""
    tracker = TrackerRepository(session).ensure_tracker(project_id=project_id)
    ticket_ids = []
    for index in range(tasks):
        task = TrackerTask(
            tracker_id=tracker.id,
            project_id=project_id,
            key=f"perf-task-{index}",
            title=f"Performance task {index}",
            status=TrackerItemStatus.COMPLETE,
            source_kind=TrackerSourceKind.MANUAL,
        )
        session.add(task)
        session.flush()
        archive = []
        for number in range(index, receipts, tasks):
            stamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=number % days)
            archive.append(
                {
                    "attempt": number + 1,
                    "delivery": {
                        "launch": {
                            "agent": f"agent-{number % 3}",
                            "model": f"model-{number % 2}",
                            "host_key": "codex",
                            "started_at": stamp.isoformat(),
                        },
                        "finished_at": (stamp + timedelta(seconds=10)).isoformat(),
                        "selected_role": "delivery_agent",
                        "validated_outcome": "success",
                        "usage": {
                            "unit": "tokens",
                            "status": "measured",
                            "measured": {
                                "input": 10,
                                "output": 1,
                                "total": 11,
                                "cache_read": 4,
                            },
                        },
                        "result": {"status": "success", "summary": "private evidence " * 256},
                    },
                }
            )
        ticket = TrackerTicket(
            tracker_id=tracker.id,
            project_id=project_id,
            task_id=task.id,
            key=f"perf-ticket-{index}",
            title=f"Performance ticket {index}",
            status=TrackerItemStatus.COMPLETE,
            kind=TrackerTicketKind.TICKET,
            source_kind=TrackerSourceKind.MANUAL,
            completion_evidence_json={
                "automatic_delivery": {
                    **archive[-1],
                    "attempts": archive,
                }
            }
            if archive
            else None,
        )
        session.add(ticket)
        session.flush()
        assert ticket.id is not None
        ticket_ids.append(ticket.id)
    session.commit()
    return ticket_ids
