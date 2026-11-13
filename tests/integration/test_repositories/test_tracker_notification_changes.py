"""Notification reads project real atomic revisions without hydrating evidence."""

from sqlalchemy import event

from stackos.repositories.tracker import TrackerRepository
from stackos.task_delivery import _update_delivery


def handoff():
    return {
        "status": "running",
        "job_ref": "fixture-job",
        "delivery_agent": {"name": "delivery", "instructions": "private prompt"},
        "review_agent": {"name": "review", "instructions": "private prompt"},
    }


def test_notification_projection_baselines_pages_and_preserves_raw(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="work",
        title="Work",
        metadata_json={
            "delivery": handoff(),
            "private": "do not expose",
        },
    )
    baseline = repo.changed(project_id=project_id, projection="notifications")
    assert baseline.changes == []
    assert baseline.current_rev > 0
    _update_delivery(repo, project_id, "work", status="stopped", reason="private error")
    _update_delivery(repo, project_id, "work", status="stopped", reason="same incident")
    _update_delivery(repo, project_id, "work", status="running")
    _update_delivery(repo, project_id, "work", status="stopped")
    repo.update_task(project_id=project_id, task_key="work", patch_json={"status": "failed"})
    cursor = baseline.current_rev
    changes = []
    while True:
        page = repo.changed(
            project_id=project_id, since_rev=cursor, limit=2, projection="notifications"
        )
        changes.extend(page.changes)
        if page.next_cursor is None:
            break
        assert page.next_cursor > cursor
        cursor = page.next_cursor
    assert len(changes) == 5
    assert [(c.before_delivery_status, c.after_delivery_status) for c in changes[:4]] == [
        ("running", "stopped"),
        ("stopped", "stopped"),
        ("stopped", "running"),
        ("running", "stopped"),
    ]
    assert changes[-1].before_status == "not-started"
    assert changes[-1].after_status == "failed"
    assert all(c.task_key == "work" for c in changes)
    assert "private" not in page.model_dump_json()
    assert repo.changed(project_id=project_id, since_rev=baseline.current_rev).changes[0].after_json


def test_notification_projection_skips_deleted_foreign_and_invalid_handoffs(session, project_id):
    from stackos.db.models import TrackerRevision, TrackerTask
    from stackos.repositories.projects import ProjectRepository

    repo = TrackerRepository(session)
    other = (
        ProjectRepository(session)
        .create(slug="foreign", name="Foreign", domain="foreign.example")
        .data.id
    )
    assert other is not None
    task = repo.create_task(project_id=other, key="work", title="Foreign work").data.task
    repo.create_task(project_id=project_id, key="work", title="Local work")
    baseline = repo.changed(project_id=project_id, projection="notifications")
    _update_delivery(repo, project_id, "work", status="stopped", reason="not a valid handoff")
    page = repo.changed(
        project_id=project_id, since_rev=baseline.current_rev, projection="notifications"
    )
    assert page.changes[0].after_delivery_status is None
    tracker = repo._tracker_or_none(project_id=project_id)
    session.add(
        TrackerRevision(
            tracker_id=tracker.id,
            project_id=project_id,
            rev=tracker.rev + 1,
            entity_kind="task",
            entity_id=task.id,
            entity_key="work",
            change_kind="update",
            summary="foreign reference",
            before_json={"status": "in-progress"},
            after_json={"status": "complete"},
        )
    )
    tracker.rev += 1
    session.add(tracker)
    session.commit()
    page = repo.changed(
        project_id=project_id, since_rev=page.current_rev, projection="notifications"
    )
    assert page.changes == []
    local = repo._task_by_key(tracker.id, "work")
    session.delete(local)
    session.commit()
    assert (
        repo.changed(
            project_id=project_id, since_rev=baseline.current_rev, projection="notifications"
        ).changes
        == []
    )
    assert session.get(TrackerTask, task.id) is not None


def test_notification_projection_reads_only_bounded_scalar_columns(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id, key="work", title="Work", context_json={"prompt": "x" * 100_000}
    )
    baseline = repo.changed(project_id=project_id, projection="notifications")
    for index in range(8):
        repo.update_task(project_id=project_id, task_key="work", patch_json={"title": str(index)})
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = repo.changed(
            project_id=project_id,
            since_rev=baseline.current_rev,
            limit=2,
            projection="notifications",
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(page.changes) == 2
    assert page.next_cursor == page.changes[-1].rev
    assert len(page.model_dump_json()) < 2000
    assert not any("context_json" in sql or "patch_json" in sql for sql in statements)
    assert not any("count(" in sql.lower() for sql in statements)


def test_parent_completion_and_failure_use_real_ticket_aggregation(session, project_id):
    repo = TrackerRepository(session)
    for key in ("complete-parent", "failed-parent"):
        repo.create_task(project_id=project_id, key=key, title=key)
        repo.create_ticket(project_id=project_id, task_key=key, key=key, title=key)
        repo.update_ticket(
            project_id=project_id, ticket_key=key, patch_json={"status": "in-progress"}
        )
    baseline = repo.changed(project_id=project_id, projection="notifications")
    repo.update_ticket(
        project_id=project_id, ticket_key="complete-parent", patch_json={"status": "complete"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="failed-parent", patch_json={"status": "failed"}
    )
    changes = repo.changed(
        project_id=project_id, since_rev=baseline.current_rev, projection="notifications"
    ).changes
    assert [(c.task_key, c.before_status, c.after_status) for c in changes] == [
        ("complete-parent", "in-progress", "complete"),
        ("failed-parent", "in-progress", "failed"),
    ]


def test_workflow_terminal_mirror_retains_the_real_transition(session, project_id):
    from stackos.db.models import RunPlanStepStatus
    from stackos.repositories.run_plans import RunPlanRepository

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "notification-fixture",
            "title": "Notification fixture",
            "steps": [{"id": "finish", "title": "Finish"}],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id).data
    repo = TrackerRepository(session)
    baseline = repo.changed(project_id=project_id, projection="notifications")
    plans.claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="finish", claimed_by="fixture"
    )
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="finish",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Done"},
    )
    changes = repo.changed(
        project_id=project_id, since_rev=baseline.current_rev, projection="notifications"
    ).changes
    assert [(c.task_key, c.before_status, c.after_status) for c in changes] == [
        (f"workflow-{plan.id}", "in-progress", "complete"),
    ]
