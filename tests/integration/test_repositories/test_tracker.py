from __future__ import annotations

import pytest
from sqlmodel import Session, select

from stackos.db.models import RunPlanStepStatus, TaskTracker, TrackerItemStatus
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

    running_ticket = tracker.brief(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-prepare",
    ).ticket
    assert running_ticket.status == TrackerItemStatus.IN_PROGRESS
    assert running_ticket.assignee == "codex"
    assert running_ticket.run_id == started.run_id

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
