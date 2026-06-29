from stackos.agent_responses import compact_tracker_status
from stackos.operations.tracker.schemas import (
    TrackerUpdateTaskInput,
    TrackerUpdateTicketInput,
)


def test_compact_tracker_status_includes_pulse_summary() -> None:
    compact = compact_tracker_status(
        {
            "tracker": {"id": 7, "project_id": 2, "rev": 11},
            "task_counts": {
                "not-started": 1,
                "in-progress": 2,
                "complete": 3,
                "deferred": 1,
            },
            "ticket_counts": {
                "not-started": 4,
                "in-progress": 5,
                "complete": 6,
                "failed": 1,
                "skipped": 2,
            },
            "ready_ticket_count": 3,
            "blocked_ticket_count": 1,
            "in_progress_ticket_count": 5,
        }
    )

    assert compact["summary"]["tasks"] == {
        "total": 7,
        "active": 3,
        "done": 4,
        "not_started": 1,
        "in_progress": 2,
        "complete": 3,
        "deferred": 1,
    }
    assert compact["summary"]["tickets"] == {
        "total": 18,
        "active": 9,
        "done": 9,
        "not_started": 4,
        "in_progress": 5,
        "complete": 6,
        "failed": 1,
        "skipped": 2,
        "ready": 3,
        "blocked": 1,
    }
    assert compact["task_counts"]["not-started"] == 1
    assert compact["ticket_counts"]["failed"] == 1


def test_tracker_update_schemas_describe_terminal_status_and_closeout_fields() -> None:
    task_patch = TrackerUpdateTaskInput.model_json_schema()["properties"]["patch_json"]
    ticket_patch = TrackerUpdateTicketInput.model_json_schema()["properties"]["patch_json"]

    for patch_schema in (task_patch, ticket_patch):
        description = patch_schema["description"]
        assert "skipped = intentionally not executed" in description
        assert "deferred = valid work postponed for later" in description
        assert "aborted = stopped, cancelled, or rejected" in description
        assert "completion_evidence_json" in description
