from __future__ import annotations

import pytest
from sqlmodel import Session, select

from stackos.db.models import RunPlanStep, RunPlanStepStatus, TaskTracker, TrackerItemStatus
from stackos.repositories.base import ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository


def test_tracker_reads_do_not_create_default_tracker(session: Session, project_id: int) -> None:
    repo = TrackerRepository(session)

    status = repo.status(project_id=project_id)
    snapshot = repo.get(project_id=project_id)
    next_work = repo.next(project_id=project_id)

    assert status.rev == 0
    assert snapshot.tracker.id == 0
    assert snapshot.tasks == []
    assert snapshot.graph is not None
    assert next_work.tickets == []
    row = session.exec(select(TaskTracker).where(TaskTracker.project_id == project_id)).first()
    assert row is None


def test_manual_task_ticket_lifecycle_and_graph(session: Session, project_id: int) -> None:
    repo = TrackerRepository(session)

    task_env = repo.create_task(
        project_id=project_id,
        key="manual-comms",
        title="Manual communications work",
        goal="Track a direct agent task with child tickets.",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="manual-comms",
        key="manual-discovery",
        title="Discovery",
        definition_of_done_json=["Constraints are written down."],
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="manual-comms",
        key="manual-delivery",
        title="Delivery",
        dependency_keys=["manual-discovery"],
        definition_of_done_json=["Delivery is verified."],
        created_by="codex",
    )

    next_before = repo.next(project_id=project_id)
    assert [ticket.key for ticket in next_before.tickets] == ["manual-discovery"]
    assert next_before.blocked[0].key == "manual-delivery"
    assert repo.status(project_id=project_id).blocked_ticket_count == 1

    picked = repo.pick(project_id=project_id, ticket_key="manual-discovery", assignee="codex").data
    assert picked.ticket is not None
    assert picked.ticket.status == TrackerItemStatus.IN_PROGRESS

    repo.update_ticket(
        project_id=project_id,
        ticket_key="manual-discovery",
        patch_json={"status": "complete", "outcome": "Discovery complete."},
        actor="codex",
    )
    after_first_ticket = repo.brief(project_id=project_id, ticket_key="manual-delivery")
    assert after_first_ticket.task.status == TrackerItemStatus.IN_PROGRESS
    next_after = repo.next(project_id=project_id)
    assert [ticket.key for ticket in next_after.tickets] == ["manual-delivery"]

    repo.pick(project_id=project_id, ticket_key="manual-delivery", assignee="codex")
    completed = repo.update_ticket(
        project_id=project_id,
        ticket_key="manual-delivery",
        patch_json={"status": "complete", "outcome": "Delivery complete."},
        actor="codex",
    ).data
    assert completed.task is not None
    assert completed.task.status == TrackerItemStatus.COMPLETE
    assert completed.task.completed_at is not None
    assert completed.ticket is not None
    assert completed.ticket.completed_at is not None

    reopened = repo.update_ticket(
        project_id=project_id,
        ticket_key="manual-delivery",
        patch_json={"status": "in-progress"},
        actor="codex",
    ).data
    assert reopened.task is not None
    assert reopened.task.status == TrackerItemStatus.IN_PROGRESS
    assert reopened.task.completed_at is None
    assert reopened.ticket is not None
    assert reopened.ticket.status == TrackerItemStatus.IN_PROGRESS
    assert reopened.ticket.completed_at is None

    completed = repo.update_ticket(
        project_id=project_id,
        ticket_key="manual-delivery",
        patch_json={"status": "complete", "outcome": "Delivery complete again."},
        actor="codex",
    ).data
    assert completed.task is not None
    assert completed.task.status == TrackerItemStatus.COMPLETE

    snapshot = repo.get(project_id=project_id)
    assert task_env.data.task is not None
    assert snapshot.graph is not None
    assert {node.id for node in snapshot.graph.nodes} >= {
        "task:manual-comms",
        "ticket:manual-discovery",
        "ticket:manual-delivery",
    }
    assert any(edge.type == "dependency" for edge in snapshot.graph.edges)


def test_terminal_status_patches_force_done_lane(session: Session, project_id: int) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="terminal-lane-invariant",
        title="Terminal lane invariant",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="terminal-lane-invariant",
        key="terminal-lane-ticket",
        title="Terminal lane ticket",
        created_by="codex",
    )

    task_update = repo.update_task(
        project_id=project_id,
        task_key="terminal-lane-invariant",
        patch_json={"status": "aborted", "lane_key": "implementation"},
        actor="codex",
    ).data
    ticket_update = repo.update_ticket(
        project_id=project_id,
        ticket_key="terminal-lane-ticket",
        patch_json={"status": "complete", "lane_key": "implementation"},
        actor="codex",
    ).data

    assert task_update.task is not None
    assert task_update.task.status == TrackerItemStatus.ABORTED
    assert task_update.task.lane_key == "done"
    assert ticket_update.ticket is not None
    assert ticket_update.ticket.status == TrackerItemStatus.COMPLETE
    assert ticket_update.ticket.lane_key == "done"


def test_tracker_reject_task_cascades_all_child_tickets(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="reject-cascade",
        title="Reject cascade",
        created_by="codex",
    )
    for key in ("reject-open", "reject-picked", "reject-complete"):
        repo.create_ticket(
            project_id=project_id,
            task_key="reject-cascade",
            key=key,
            title=key,
            blocker_reason="Waiting on input." if key == "reject-open" else None,
            created_by="codex",
        )
    repo.pick(project_id=project_id, ticket_key="reject-picked", assignee="codex")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="reject-complete",
        patch_json={"status": "complete", "outcome": "Already done."},
        actor="codex",
    )

    rejected = repo.reject_task(
        project_id=project_id,
        task_key="reject-cascade",
        reason="Operator api_key=sk-secret parked this task.",
        actor="codex",
    ).data
    snapshot = repo.get(project_id=project_id, task_key="reject-cascade", include_graph=False)

    assert rejected.task is not None
    assert rejected.task.status == TrackerItemStatus.ABORTED
    assert rejected.task.completion_evidence_json["decision"] == "rejected"
    assert rejected.task.completion_evidence_json["reason"] == (
        "Operator api_key=[redacted] parked this task."
    )
    assert "sk-secret" not in str(rejected.model_dump())
    assert [result.action for result in rejected.results] == [
        "rejected",
        "rejected",
        "rejected",
    ]
    assert {ticket.status for ticket in snapshot.tickets} == {TrackerItemStatus.ABORTED}
    assert {ticket.lane_key for ticket in snapshot.tickets} == {"done"}
    assert {ticket.blocker_reason for ticket in snapshot.tickets} == {None}
    assert all(
        "Operator api_key=[redacted] parked this task." in (ticket.outcome or "")
        for ticket in snapshot.tickets
    )
    assert all(ticket.metadata_json["parent_task_rejected"] is True for ticket in snapshot.tickets)


def test_tracker_graph_includes_conservative_advisory_warnings(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="graph-warning-flow",
        title="Graph warning flow",
        created_by="codex",
    )
    for key in (
        "warning-impl-a",
        "warning-impl-b",
        "warning-isolated-a",
        "warning-isolated-b",
        "warning-isolated-c",
    ):
        repo.create_ticket(
            project_id=project_id,
            task_key="graph-warning-flow",
            key=key,
            title=key,
            created_by="codex",
        )
    repo.create_ticket(
        project_id=project_id,
        task_key="graph-warning-flow",
        key="warning-review-gate",
        title="Manual review gate before implementation",
        lane_key="review",
        dependency_keys=["warning-impl-a", "warning-impl-b"],
        created_by="codex",
    )

    snapshot = repo.get(project_id=project_id, task_key="graph-warning-flow")

    assert snapshot.graph is not None
    assert snapshot.graph.warnings == [
        (
            "Task graph-warning-flow has 6 nonterminal tickets but only 2 dependency "
            "relations; review whether the plan is missing blocking edges."
        ),
        (
            "Task graph-warning-flow has 3 nonterminal tickets without dependency links "
            "(warning-isolated-a, warning-isolated-b, warning-isolated-c); review isolated "
            "work before implementation."
        ),
        (
            "Review/gate ticket warning-review-gate depends on 2 tickets; confirm dependency "
            "direction if this gate should unblock implementation work."
        ),
    ]


def test_tracker_graph_allows_post_delivery_review_dependencies(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="graph-release-review-flow",
        title="Graph release review flow",
        created_by="codex",
    )
    for key in ("delivery-complete", "verification-complete"):
        repo.create_ticket(
            project_id=project_id,
            task_key="graph-release-review-flow",
            key=key,
            title=key,
            created_by="codex",
        )
    repo.create_ticket(
        project_id=project_id,
        task_key="graph-release-review-flow",
        key="release-review",
        title="Review delivery after verification",
        lane_key="review",
        dependency_keys=["delivery-complete", "verification-complete"],
        created_by="codex",
    )

    snapshot = repo.get(project_id=project_id, task_key="graph-release-review-flow")

    assert snapshot.graph is not None
    assert snapshot.graph.warnings == []


def test_tracker_status_ignores_terminal_blocker_notes(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="terminal-notes",
        title="Terminal blocker notes",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="terminal-notes",
        key="terminal-open-blocked",
        title="Open blocked",
        blocker_reason="Waiting on input.",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="terminal-notes",
        key="terminal-deferred-note",
        title="Deferred with historical note",
        blocker_reason="Deferred by product decision.",
        created_by="codex",
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="terminal-deferred-note",
        patch_json={"status": "deferred"},
        actor="codex",
    )

    status = repo.status(project_id=project_id)
    blockers = repo.blockers(project_id=project_id)

    assert status.blocked_ticket_count == 1
    assert [ticket.key for ticket in blockers.blocked] == ["terminal-open-blocked"]


def test_tracker_patch_rejects_unsupported_shapes(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="patch-shape",
        title="Patch shape",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="patch-shape",
        key="patch-ticket",
        title="Patch ticket",
        created_by="codex",
    )

    with pytest.raises(ValidationError, match=r"patch_json\.tickets must be an object"):
        repo.patch(
            project_id=project_id,
            patch_json={
                "tickets": [
                    {"key": "patch-ticket", "status": "complete"},
                ],
            },
            actor="codex",
        )


def test_tracker_atomic_updates_reject_unknown_patch_fields(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="patch-field-validation",
        title="Patch field validation",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="patch-field-validation",
        key="field-validation-ticket",
        title="Field validation ticket",
        created_by="codex",
    )

    with pytest.raises(ValidationError, match="unsupported tracker ticket patch fields"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="field-validation-ticket",
            patch_json={"stauts": "complete"},
            actor="codex",
        )
    with pytest.raises(ValidationError, match="unsupported tracker ticket patch fields"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="field-validation-ticket",
            patch_json={"stauts": "complete"},
            dry_run=True,
            actor="codex",
        )
    with pytest.raises(ValidationError, match="unsupported tracker task patch fields"):
        repo.update_task(
            project_id=project_id,
            task_key="patch-field-validation",
            patch_json={"titel": "Typo"},
            actor="codex",
        )
    with pytest.raises(ValidationError, match="unsupported tracker ticket patch fields"):
        repo.patch(
            project_id=project_id,
            patch_json={
                "tickets": {
                    "field-validation-ticket": {"stauts": "complete"},
                },
            },
            actor="codex",
        )

    invalid = repo.update_ticket_list(
        project_id=project_id,
        updates_json=[
            {
                "ticket_key": "field-validation-ticket",
                "patch_json": {"stauts": "complete"},
            }
        ],
        dry_run=True,
        actor="codex",
    ).data
    snapshot = repo.get(
        project_id=project_id,
        task_key="patch-field-validation",
        include_graph=False,
    )

    assert invalid.valid is False
    assert invalid.results[0].action == "error"
    assert invalid.results[0].error == "unsupported tracker ticket patch fields: stauts"
    assert snapshot.tickets[0].status == TrackerItemStatus.NOT_STARTED


def test_tracker_ticket_list_import_review_update_and_evidence(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="ticket-list-flow",
        title="Ticket list flow",
        completion_evidence_json={"self_test": ["browser review planned"]},
        created_by="codex",
    )

    ticket_list_json = {
        "task_key": "ticket-list-flow",
        "tickets": [
            {
                "key": "ticket-list-schema",
                "title": "Add ticket list schema",
                "completion_evidence_json": {"changed_files": ["stackos/repositories/tracker.py"]},
            },
            {
                "key": "ticket-list-ui",
                "title": "Expose ticket list UI",
                "dependency_keys": ["ticket-list-schema"],
            },
        ],
        "created_by": "codex",
    }

    dry_run = repo.validate_ticket_list(project_id=project_id, ticket_list_json=ticket_list_json)
    assert dry_run.dry_run is True
    assert dry_run.valid is True
    assert [result.action for result in dry_run.results] == ["validated", "validated"]
    assert repo.get(project_id=project_id, task_key="ticket-list-flow").tickets == []

    imported = repo.create_ticket_list(
        project_id=project_id,
        ticket_list_json=ticket_list_json,
        actor="codex",
    ).data
    assert imported.valid is True
    assert [ticket.key for ticket in imported.tickets] == ["ticket-list-schema", "ticket-list-ui"]
    assert imported.dependencies[0].ticket_key == "ticket-list-ui"
    assert imported.dependencies[0].depends_on_ticket_key == "ticket-list-schema"
    assert imported.tickets[0].completion_evidence_json == {
        "changed_files": ["stackos/repositories/tracker.py"]
    }

    review = repo.get(
        project_id=project_id,
        task_key="ticket-list-flow",
        ticket_keys=["ticket-list-ui"],
        include_graph=False,
    )
    assert [ticket.key for ticket in review.tickets] == ["ticket-list-ui"]
    assert review.graph is None

    ticket_id = imported.tickets[1].id
    updated = repo.update_ticket_list(
        project_id=project_id,
        updates_json=[
            {
                "ticket_key": "ticket-list-schema",
                "patch_json": {
                    "status": "complete",
                    "completion_evidence_json": {
                        "changed_files": ["stackos/repositories/tracker.py"],
                        "summary": "List import contract persisted.",
                    },
                },
            },
            {"ticket_id": ticket_id, "patch_json": {"assignee": "codex"}},
        ],
        actor="codex",
    ).data
    assert updated.valid is True
    assert [result.action for result in updated.results] == ["updated", "updated"]
    assert {tuple(result.changed_fields) for result in updated.results} == {
        ("status", "completion_evidence_json"),
        ("assignee",),
    }

    snapshot = repo.get(project_id=project_id, task_key="ticket-list-flow", include_graph=False)
    by_key = {ticket.key: ticket for ticket in snapshot.tickets}
    assert by_key["ticket-list-schema"].status == TrackerItemStatus.COMPLETE
    assert by_key["ticket-list-schema"].completion_evidence_json == {
        "changed_files": ["stackos/repositories/tracker.py"],
        "summary": "List import contract persisted.",
    }
    assert by_key["ticket-list-ui"].assignee == "codex"


def test_tracker_dependency_patch_add_remove_preserves_unspecified_fields(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="dependency-patch-flow",
        title="Dependency patch flow",
        created_by="codex",
    )
    for key in ("dependency-a", "dependency-b", "dependency-c"):
        repo.create_ticket(
            project_id=project_id,
            task_key="dependency-patch-flow",
            key=key,
            title=key,
            created_by="codex",
        )
    repo.create_ticket(
        project_id=project_id,
        task_key="dependency-patch-flow",
        key="dependency-target",
        title="Dependency target",
        assignee="codex",
        dependency_keys=["dependency-a"],
        created_by="codex",
    )

    added = repo.update_ticket(
        project_id=project_id,
        ticket_key="dependency-target",
        patch_json={"add_dependency_keys": ["dependency-b", "dependency-b"]},
        actor="codex",
    ).data.ticket
    assert added is not None
    assert added.assignee == "codex"
    assert added.dependency_keys == ["dependency-a", "dependency-b"]

    removed = repo.update_ticket(
        project_id=project_id,
        ticket_key="dependency-target",
        patch_json={"remove_dependency_keys": ["dependency-a"]},
        actor="codex",
    ).data.ticket
    assert removed is not None
    assert removed.dependency_keys == ["dependency-b"]

    replaced = repo.update_ticket(
        project_id=project_id,
        ticket_key="dependency-target",
        patch_json={"dependency_keys": ["dependency-c"]},
        actor="codex",
    ).data.ticket
    assert replaced is not None
    assert replaced.dependency_keys == ["dependency-c"]

    with pytest.raises(ValidationError, match="dependency_keys cannot be combined"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="dependency-target",
            patch_json={
                "dependency_keys": ["dependency-a"],
                "add_dependency_keys": ["dependency-b"],
            },
            actor="codex",
        )

    with pytest.raises(ValidationError, match="ticket dependency edge does not exist"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="dependency-target",
            patch_json={"remove_dependency_keys": ["dependency-a"]},
            actor="codex",
        )


def test_tracker_dependency_patch_dry_run_reports_diff_without_writing(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="dependency-preview-flow",
        title="Dependency preview flow",
        created_by="codex",
    )
    for key in ("preview-a", "preview-b", "preview-c"):
        repo.create_ticket(
            project_id=project_id,
            task_key="dependency-preview-flow",
            key=key,
            title=key,
            created_by="codex",
        )
    repo.create_ticket(
        project_id=project_id,
        task_key="dependency-preview-flow",
        key="preview-target",
        title="Preview target",
        dependency_keys=["preview-a", "preview-b"],
        created_by="codex",
    )
    before = repo.get(
        project_id=project_id,
        task_key="dependency-preview-flow",
        include_graph=False,
    ).tracker.rev

    preview = repo.update_ticket(
        project_id=project_id,
        ticket_key="preview-target",
        patch_json={
            "add_dependency_keys": ["preview-c"],
            "remove_dependency_keys": ["preview-a"],
        },
        dry_run=True,
        actor="codex",
    ).data

    assert preview.dry_run is True
    assert preview.rev == before
    assert preview.dependency_preview is not None
    assert preview.dependency_preview.current_dependency_keys == ["preview-a", "preview-b"]
    assert preview.dependency_preview.final_dependency_keys == ["preview-b", "preview-c"]
    assert preview.dependency_preview.added_dependency_keys == ["preview-c"]
    assert preview.dependency_preview.removed_dependency_keys == ["preview-a"]
    assert preview.dependency_preview.kept_dependency_keys == ["preview-b"]
    assert preview.results[0].action == "validated"
    assert preview.results[0].dependency_preview == preview.dependency_preview

    after = repo.get(
        project_id=project_id,
        task_key="dependency-preview-flow",
        include_graph=False,
    )
    assert after.tracker.rev == before
    by_key = {ticket.key: ticket for ticket in after.tickets}
    assert by_key["preview-target"].dependency_keys == ["preview-a", "preview-b"]


def test_tracker_ticket_list_update_add_remove_dependency_keys(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="bulk-dependency-patch-flow",
        title="Bulk dependency patch flow",
        created_by="codex",
    )
    repo.create_ticket_list(
        project_id=project_id,
        ticket_list_json={
            "task_key": "bulk-dependency-patch-flow",
            "tickets": [
                {"key": "bulk-dep-a", "title": "Dependency A"},
                {"key": "bulk-dep-b", "title": "Dependency B"},
                {
                    "key": "bulk-target",
                    "title": "Target",
                    "dependency_keys": ["bulk-dep-a"],
                },
            ],
        },
        actor="codex",
    )

    updated = repo.update_ticket_list(
        project_id=project_id,
        updates_json=[
            {
                "ticket_key": "bulk-target",
                "patch_json": {
                    "add_dependency_keys": ["bulk-dep-b"],
                    "remove_dependency_keys": ["bulk-dep-a"],
                },
            }
        ],
        actor="codex",
    ).data
    assert updated.valid is True
    assert updated.results[0].changed_fields == [
        "add_dependency_keys",
        "remove_dependency_keys",
    ]
    assert updated.tickets[0].dependency_keys == ["bulk-dep-b"]


def test_tracker_ticket_list_update_dry_run_reports_dependency_diffs_and_errors(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="bulk-dependency-preview-flow",
        title="Bulk dependency preview flow",
        created_by="codex",
    )
    repo.create_ticket_list(
        project_id=project_id,
        ticket_list_json={
            "task_key": "bulk-dependency-preview-flow",
            "tickets": [
                {"key": "bulk-preview-a", "title": "Dependency A"},
                {"key": "bulk-preview-b", "title": "Dependency B"},
                {"key": "bulk-preview-c", "title": "Dependency C"},
                {"key": "bulk-preview-d", "title": "Dependency D"},
                {"key": "bulk-preview-target", "title": "Target"},
            ],
            "dependencies": [
                {
                    "ticket_key": "bulk-preview-target",
                    "depends_on_ticket_key": "bulk-preview-a",
                },
                {
                    "ticket_key": "bulk-preview-target",
                    "depends_on_ticket_key": "bulk-preview-b",
                },
                {
                    "ticket_key": "bulk-preview-target",
                    "depends_on_ticket_key": "bulk-preview-c",
                },
                {
                    "ticket_key": "bulk-preview-target",
                    "depends_on_ticket_key": "bulk-preview-d",
                },
            ],
        },
        actor="codex",
    )
    before = repo.get(
        project_id=project_id,
        task_key="bulk-dependency-preview-flow",
        include_graph=False,
    ).tracker.rev

    preview = repo.update_ticket_list(
        project_id=project_id,
        updates_json=[
            {
                "ticket_key": "bulk-preview-target",
                "patch_json": {
                    "remove_dependency_keys": [
                        "bulk-preview-a",
                        "bulk-preview-b",
                        "bulk-preview-c",
                        "bulk-preview-d",
                    ],
                },
            },
            {
                "ticket_key": "bulk-preview-target",
                "patch_json": {"remove_dependency_keys": ["bulk-preview-a"]},
            },
        ],
        dry_run=True,
        actor="codex",
    ).data

    assert preview.dry_run is True
    assert preview.rev == before
    assert preview.valid is True
    assert [result.action for result in preview.results] == ["validated", "validated"]
    assert preview.results[0].dependency_preview is not None
    assert preview.results[0].dependency_preview.removed_dependency_keys == [
        "bulk-preview-a",
        "bulk-preview-b",
        "bulk-preview-c",
        "bulk-preview-d",
    ]
    assert preview.results[0].dependency_preview.final_dependency_keys == []
    assert preview.warnings == [
        "dependency preview removes 5 edges and adds 0; review direction before applying"
    ]

    after = repo.get(
        project_id=project_id,
        task_key="bulk-dependency-preview-flow",
        include_graph=False,
    )
    assert after.tracker.rev == before
    by_key = {ticket.key: ticket for ticket in after.tickets}
    assert by_key["bulk-preview-target"].dependency_keys == [
        "bulk-preview-a",
        "bulk-preview-b",
        "bulk-preview-c",
        "bulk-preview-d",
    ]

    invalid = repo.update_ticket_list(
        project_id=project_id,
        updates_json=[
            {
                "ticket_key": "bulk-preview-target",
                "patch_json": {"add_dependency_keys": ["missing-preview-ticket"]},
            }
        ],
        dry_run=True,
        actor="codex",
    ).data
    assert invalid.valid is False
    assert invalid.errors[0].message == "tracker ticket not found"


def test_run_plan_lifecycle_mirrors_tracker(session: Session, project_id: int) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "tracker.workflow.run",
                "title": "Tracker Workflow",
                "steps": [
                    {"id": "prepare", "title": "Prepare", "success_criteria": ["Prepared"]},
                    {
                        "id": "deliver",
                        "title": "Deliver",
                        "depends_on": ["prepare"],
                        "success_criteria": ["Delivered"],
                    },
                ],
            },
            created_by="codex",
        )
        .data
    )

    tracker = TrackerRepository(session)
    snapshot = tracker.get(project_id=project_id, run_plan_id=plan.id)
    assert len(snapshot.tasks) == 1
    assert snapshot.tasks[0].key == f"workflow-{plan.id}"
    assert {ticket.key for ticket in snapshot.tickets} == {
        f"workflow-{plan.id}-prepare",
        f"workflow-{plan.id}-deliver",
    }
    assert snapshot.dependencies[0].depends_on_ticket_key == f"workflow-{plan.id}-prepare"

    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data
    claimed = (
        RunPlanRepository(session)
        .claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="prepare",
            claimed_by="codex",
        )
        .data
    )
    assert claimed.status == "running"

    running_brief = tracker.brief(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-prepare",
    )
    running_ticket = running_brief.ticket
    assert running_ticket.status == TrackerItemStatus.IN_PROGRESS
    assert running_ticket.assignee == "codex"
    assert running_ticket.run_id == started.run_id
    assert running_brief.workflow_handoff is not None
    assert running_brief.workflow_handoff.run_plan_id == plan.id
    assert running_brief.workflow_handoff.run_id == started.run_id
    assert running_brief.workflow_handoff.step_id == "prepare"
    assert "runPlan.claimStep" in running_brief.workflow_handoff.next_operations
    assert any("Workflow ticket" in item for item in running_brief.suggested_next_actions)

    RunPlanRepository(session).record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Prepared"},
    )
    complete_ticket = tracker.brief(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-prepare",
    ).ticket
    assert complete_ticket.status == TrackerItemStatus.COMPLETE
    assert complete_ticket.outcome == "Prepared"


def test_workflow_step_ticket_status_cannot_bypass_run_plan_lifecycle(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "tracker.workflow-guard.run",
                "title": "Tracker Workflow Guard",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
            created_by="codex",
        )
        .data
    )
    RunPlanRepository(session).start(plan.id, project_id=project_id)

    with pytest.raises(ValidationError) as exc_info:
        TrackerRepository(session).update_ticket(
            project_id=project_id,
            ticket_key=f"workflow-{plan.id}-prepare",
            patch_json={"status": "complete", "outcome": "Done outside runPlan.recordStep."},
            actor="codex",
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["step_id"] == "prepare"
    assert "runPlan.claimStep" in exc_info.value.data["next_operations"]
    assert exc_info.value.data["requested_status"] == "complete"

    with pytest.raises(ValidationError) as failed_exc:
        TrackerRepository(session).update_ticket(
            project_id=project_id,
            ticket_key=f"workflow-{plan.id}-prepare",
            patch_json={"status": "failed", "outcome": "Failed outside runPlan.recordStep."},
            actor="codex",
        )

    assert failed_exc.value.data["requested_status"] == "failed"
    assert "runPlan.recordStep" in failed_exc.value.data["next_operations"]


def test_workflow_child_ticket_creation_cannot_bypass_run_plan_lifecycle(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "tracker.workflow-create-guard.run",
                "title": "Tracker Workflow Create Guard",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
            created_by="codex",
        )
        .data
    )
    RunPlanRepository(session).start(plan.id, project_id=project_id)
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None

    with pytest.raises(ValidationError) as exc_info:
        TrackerRepository(session).create_ticket(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            key="workflow-child-active",
            title="Workflow child active",
            status=TrackerItemStatus.IN_PROGRESS,
            run_plan_id=plan.id,
            run_plan_step_id=step.id,
            created_by="codex",
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["step_id"] == "prepare"
    assert exc_info.value.data["requested_status"] == "in-progress"


def test_workflow_child_ticket_can_complete_inside_running_step(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-child-active-complete.run",
            "title": "Tracker Workflow Child Active Complete",
            "steps": [{"id": "prepare", "title": "Prepare"}],
        },
        created_by="codex",
    ).data
    started = run_plans.start(plan.id, project_id=project_id)
    run_plans.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        claimed_by="codex",
    )
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None

    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        key="workflow-child-active-delivery",
        title="Workflow child active delivery",
        status=TrackerItemStatus.IN_PROGRESS,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        created_by="codex",
    )
    completed = tracker.update_ticket(
        project_id=project_id,
        ticket_key="workflow-child-active-delivery",
        patch_json={"status": "complete", "outcome": "Done inside active run-plan step."},
        actor="codex",
    ).data
    snapshot = tracker.get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )

    assert completed.ticket is not None
    assert completed.ticket.status == TrackerItemStatus.COMPLETE
    assert completed.ticket.lane_key == "done"
    assert snapshot.tasks[0].status == TrackerItemStatus.IN_PROGRESS


def test_completed_workflow_suppresses_historical_topology_warnings(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-historical-topology.run",
            "title": "Tracker Workflow Historical Topology",
            "steps": [{"id": "prepare", "title": "Prepare"}],
        },
        created_by="codex",
    ).data
    started = run_plans.start(plan.id, project_id=project_id).data
    run_plans.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        claimed_by="codex",
    )
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None

    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        key="workflow-child-historical-delivery",
        title="Workflow child historical delivery",
        status=TrackerItemStatus.IN_PROGRESS,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        created_by="codex",
    )
    tracker.update_ticket(
        project_id=project_id,
        ticket_key="workflow-child-historical-delivery",
        patch_json={"status": "complete", "outcome": "Completed during the active step."},
        actor="codex",
    )
    run_plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Step completed."},
    )

    snapshot = tracker.get(project_id=project_id, task_key=f"workflow-{plan.id}")
    assert snapshot.graph is not None
    assert snapshot.graph.warnings == []

    step_verify = tracker.verify(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-prepare",
    )
    check_keys = {check["key"] for check in step_verify.checks}
    assert "workflow-step-child-bridge" not in check_keys
    assert "workflow-step-children-reachable" not in check_keys


def test_workflow_child_ticket_creation_rejects_terminal_run_plan(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-terminal-create-guard.run",
            "title": "Tracker Workflow Terminal Create Guard",
            "steps": [{"id": "prepare", "title": "Prepare"}],
        },
        created_by="codex",
    ).data
    repo.start(plan.id, project_id=project_id)
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None
    repo.abort(run_plan_id=plan.id, project_id=project_id, reason="terminal create guard")

    with pytest.raises(ValidationError) as exc_info:
        TrackerRepository(session).create_ticket(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            key="workflow-child-after-terminal",
            title="Workflow child after terminal",
            run_plan_id=plan.id,
            run_plan_step_id=step.id,
            created_by="codex",
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["run_plan_status"] == "aborted"
    assert "runPlan.checkConsistency" in exc_info.value.data["next_operations"]


def test_workflow_task_status_cannot_bypass_run_plan_lifecycle(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "tracker.workflow-task-guard.run",
                "title": "Tracker Workflow Task Guard",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
            created_by="codex",
        )
        .data
    )

    with pytest.raises(ValidationError) as exc_info:
        TrackerRepository(session).update_task(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            patch_json={"status": "complete"},
            actor="codex",
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["requested_status"] == "complete"
    assert "runPlan.recordStep" in exc_info.value.data["next_operations"]


def test_workflow_ticket_metadata_update_preserves_terminal_run_plan_task_status(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-terminal-evidence.run",
            "title": "Tracker Workflow Terminal Evidence",
            "steps": [
                {"id": "prepare", "title": "Prepare"},
                {"id": "verify", "title": "Verify"},
            ],
        },
        created_by="codex",
    ).data
    started = run_plans.start(plan.id, project_id=project_id).data
    run_plans.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        project_id=project_id,
    )
    run_plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "first step done"},
        project_id=project_id,
    )
    run_plans.abort(
        run_plan_id=plan.id,
        project_id=project_id,
        reason="terminal evidence guard",
        actor="codex",
    )

    repo = TrackerRepository(session)
    before = repo.get(project_id=project_id, task_key=f"workflow-{plan.id}", include_graph=False)
    assert before.tasks[0].status == TrackerItemStatus.ABORTED

    updated = repo.update_ticket(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-verify",
        patch_json={
            "outcome": "Evidence recorded after abort.",
            "completion_evidence_json": {"manual_test_plan": "visible"},
        },
        actor="codex",
    ).data

    assert updated.task is not None
    assert updated.task.status == TrackerItemStatus.ABORTED
    assert updated.ticket is not None
    assert updated.ticket.status == TrackerItemStatus.ABORTED


def test_workflow_ticket_list_update_dry_run_validates_run_plan_lifecycle(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "tracker.workflow-dry-run-guard.run",
                "title": "Tracker Workflow Dry Run Guard",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
            created_by="codex",
        )
        .data
    )
    RunPlanRepository(session).start(plan.id, project_id=project_id)
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None
    TrackerRepository(session).create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        key="workflow-child-draft",
        title="Workflow child draft",
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        created_by="codex",
    )

    preview = (
        TrackerRepository(session)
        .update_ticket_list(
            project_id=project_id,
            updates_json=[
                {
                    "ticket_key": "workflow-child-draft",
                    "patch_json": {"status": "complete"},
                }
            ],
            dry_run=True,
        )
        .data
    )

    assert preview.dry_run is True
    assert preview.valid is False
    assert preview.results[0].action == "error"
    assert "terminal status requires the owning run-plan step to be running" in (
        preview.results[0].error or ""
    )
