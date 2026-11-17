from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session, select

from stackos.db.models import ProjectEvent, RunPlanStepStatus, TrackerRevision
from stackos.repositories.base import ValidationError
from stackos.repositories.tracker import TrackerRepository
from stackos.task_delivery import _update_delivery


def seed_inspection(session: Session, project_id: int) -> TrackerRepository:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="inspect", title="Same title")
    repo.create_task(project_id=project_id, key="other", title="Same title")
    repo.create_ticket(project_id=project_id, task_key="inspect", key="child", title="Same title")
    repo.update_ticket(
        project_id=project_id, ticket_key="child", patch_json={"status": "in-progress"}
    )
    return repo


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "bad",
        {"code": "not-a-code", "next_action": "/private/tmp/native"},
        {"code": "unknown", "evidence_refs": ["/private/tmp/native"]},
        {"code": "unknown", "phase": {}},
        {"code": "unknown", "facts": [42, {}]},
        {
            "code": "native_failure",
            "phase": "review",
            "next_owner": "main_agent",
            "next_action": "inspect_retained_evidence",
            "resume_condition": "explicit_rehandoff",
            "evidence_refs": ["tracker-ticket:1:123:attempt:2:review"],
            "extra": {"prompt": "private prompt", "path": "/private/tmp/native"},
        },
    ],
)
def test_stop_context_full_light_and_activity_are_safe_read_only(session, project_id, raw):
    from stackos.repositories.tracker.schema import TaskDeliveryStopContextOut

    repo = seed_inspection(session, project_id)
    agent = {"name": "fixture", "instructions": "private frozen prompt"}
    _update_delivery(
        repo,
        project_id,
        "inspect",
        status="running",
        job_ref="fixture",
        delivery_agent=agent,
        review_agent=agent,
    )
    _update_delivery(
        repo, project_id, "inspect", status="stopped", reason="Legacy reason", stop_context=raw
    )
    # Emulate historical malformed optional data without routing it through the
    # new writer, which intentionally validates and discards unknown fields.
    from stackos.db.models import TrackerTask

    row = session.exec(select(TrackerTask).where(TrackerTask.key == "inspect")).one()
    row.metadata_json = {"delivery": {**row.metadata_json["delivery"], "stop_context": raw}}
    session.add(row)
    session.commit()
    before = session.connection().exec_driver_sql("SELECT total_changes()").scalar()
    expected = TaskDeliveryStopContextOut.from_value(raw)
    for _ in range(2):
        full = repo.get(project_id=project_id, task_key="inspect", include_graph=False)
        light = repo.get(
            project_id=project_id, task_key="inspect", projection="ui", include_graph=False
        )
        assert full.tasks[0].delivery == light.tasks[0].delivery
        assert light.tasks[0].delivery.stop_context == expected
        safe = light.model_dump_json()
        assert "private frozen prompt" not in safe and "/private/tmp/native" not in safe
        activity = repo.task_activity(project_id=project_id, task_key="inspect")
        assert "/private/tmp/native" not in activity.model_dump_json()
        assert "private frozen prompt" not in activity.model_dump_json()
    assert session.connection().exec_driver_sql("SELECT total_changes()").scalar() == before


@pytest.mark.parametrize("status", ["queued", "running", "complete", "stopped"])
def test_stop_context_clears_current_facts_and_retains_revision_history(
    session, project_id, status
):
    repo = seed_inspection(session, project_id)
    agent = {"name": "fixture", "instructions": "Never execute"}
    _update_delivery(
        repo,
        project_id,
        "inspect",
        status="stopped",
        job_ref="fixture",
        delivery_agent=agent,
        review_agent=agent,
        stop_context={"code": "agent_blocked", "next_action": "resolve_blocker"},
    )
    _update_delivery(repo, project_id, "inspect", status=status)
    full = repo.get(project_id=project_id, task_key="inspect", include_graph=False).tasks[0]
    if status == "stopped":
        assert full.delivery.stop_context.code == "unknown"
        assert full.delivery.stop_context.next_action is None
    else:
        assert "stop_context" not in full.metadata_json["delivery"]
        assert full.delivery.stop_context is None
    history = repo.task_activity(project_id=project_id, task_key="inspect")
    assert any(
        item.details.get("stop_context", {}).get("code") == "agent_blocked"
        for item in history.items
        if item.details.get("stop_context")
    )


def test_stop_context_clears_on_retired_workflow_completion(session, project_id):
    from stackos.repositories.run_plans import RunPlanRepository

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "stop-completion",
            "title": "Completion fixture",
            "steps": [{"id": "finish", "title": "Finish"}],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id).data
    repo = TrackerRepository(session)
    agent = {"name": "fixture", "instructions": "Never execute"}
    _update_delivery(
        repo,
        project_id,
        f"workflow-{plan.id}",
        status="stopped",
        job_ref="fixture",
        delivery_agent=agent,
        review_agent=agent,
        slot_reserved=False,
        stop_context={"code": "agent_blocked", "next_action": "resolve_blocker"},
    )
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="finish")
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="finish",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Accepted completion"},
    )
    task = repo.get(project_id=project_id, task_key=f"workflow-{plan.id}").tasks[0]
    assert task.metadata_json["delivery"]["status"] == "complete"
    assert "stop_context" not in task.metadata_json["delivery"]
    assert task.delivery.stop_context is None
    assert any(
        item.details.get("stop_context", {}).get("code") == "agent_blocked"
        for item in repo.task_activity(project_id=project_id, task_key=task.key).items
        if item.details.get("stop_context")
    )


def test_stop_reason_masks_paths_and_bounds_legacy_text(session, project_id):
    repo = seed_inspection(session, project_id)
    agent = {"name": "fixture", "instructions": "Never execute"}
    _update_delivery(
        repo,
        project_id,
        "inspect",
        status="stopped",
        job_ref="fixture",
        delivery_agent=agent,
        review_agent=agent,
        reason="Native failed at /private/tmp/native.json " + "x" * 700,
    )
    full = repo.get(project_id=project_id, task_key="inspect").tasks[0]
    light = repo.get(project_id=project_id, task_key="inspect", projection="ui").tasks[0]
    assert full.delivery == light.delivery
    assert len(full.delivery.reason) == 500
    assert "[local path]" in full.delivery.reason
    assert "/private/tmp/native.json" not in full.delivery.model_dump_json()
    assert "/private/tmp/native.json" in full.metadata_json["delivery"]["reason"]
    assert (
        "/private/tmp/native.json"
        not in repo.task_activity(project_id=project_id, task_key="inspect").model_dump_json()
    )


@pytest.mark.parametrize(
    "path",
    [
        "file:///private/tmp/native-result.json",
        "file:/private/tmp/native-result.json",
        "file://localhost/private/tmp/native-result.json",
        "file://workstation/share/native-result.json",
        "FILE:///C:/Users/operator/native-result.json",
        "C:/Users/operator/native-result.json",
        r"C:\Users\operator\native-result.json",
        r"\\workstation\share\native-result.json",
        "/private/tmp/native-result.json",
    ],
)
def test_stop_reason_local_representations_agree_across_readers(session, project_id, path):
    from stackos.agent_responses import compact_tracker_task

    repo = seed_inspection(session, project_id)
    agent = {"name": "fixture", "instructions": "Never execute"}
    link = "https://docs.example.test/help?q=stop#details"
    reason = f'Native failed at "{path}" (again {path}). See {link}'
    expected = f'Native failed at "[local path]" (again [local path]). See {link}'
    _update_delivery(
        repo,
        project_id,
        "inspect",
        status="stopped",
        job_ref="fixture",
        delivery_agent=agent,
        review_agent=agent,
        reason=reason,
    )
    writes_before = session.connection().exec_driver_sql("SELECT total_changes()").scalar()
    full = repo.get(project_id=project_id, task_key="inspect", include_graph=False).tasks[0]
    light = repo.get(
        project_id=project_id, task_key="inspect", projection="ui", include_graph=False
    ).tasks[0]
    compact = compact_tracker_task(full.model_dump(mode="json"))
    activity = repo.task_activity(project_id=project_id, task_key="inspect")
    assert (
        full.delivery.reason == light.delivery.reason == compact["delivery"]["reason"] == expected
    )
    assert [item.details["reason"] for item in activity.items if item.details.get("reason")] == [
        expected
    ]
    assert full.metadata_json["delivery"]["reason"] == reason
    assert session.connection().exec_driver_sql("SELECT total_changes()").scalar() == writes_before


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (None, None),
        ([], None),
        ({"reason": "old"}, None),
        (42, None),
        ("  ", None),
        (" Legacy stop ", "Legacy stop"),
        (
            "http://example.test/help https://example.test/C:/docs",
            "http://example.test/help https://example.test/C:/docs",
        ),
        (
            "https://example.test/help?next=/docs&example=C:/docs",
            "https://example.test/help?next=/docs&example=C:/docs",
        ),
        (
            "api_key=fixture-secret; file:///private/tmp/native.json https://example.test/help?token=fixture-signed&ok=1",
            "api_key=[redacted]; [local path] https://example.test/help?token=[redacted]&ok=1",
        ),
    ],
)
def test_stop_reason_preserves_safe_legacy_and_secret_controls(reason, expected):
    from stackos.repositories.tracker.schema import TaskDeliveryStateOut

    assert TaskDeliveryStateOut.safe_reason(reason) == expected


@pytest.mark.parametrize("malformed", [[], {"unexpected": "value"}])
def test_activity_retains_task_events_with_malformed_optional_ticket_metadata(
    session, project_id, malformed
):
    from stackos.context.repository import ContextRepository

    repo = seed_inspection(session, project_id)
    task = repo.get(project_id=project_id, task_key="inspect", include_graph=False).tasks[0]
    event = ContextRepository(session)._record_event(
        project_id=project_id,
        source_type="tracker_task",
        source_id=task.id,
        event_type="tracker.task.note",
        title="Persisted task note",
        metadata_json={"ticket_key": malformed},
    )
    session.commit()
    writes_before = session.connection().exec_driver_sql("SELECT total_changes()").scalar()

    page = repo.task_activity(project_id=project_id, task_key="inspect")

    item = next(
        item
        for item in page.items
        if item.source_kind == "project_event" and item.source_id == event.id
    )
    assert item.task_key == "inspect"
    assert item.ticket_key is None
    assert item.summary == "Persisted task note"
    assert item.details["source_type"] == "tracker_task"
    assert item.details["source_id"] == task.id
    assert item.details["metadata_json"]["ticket_key"] == malformed
    assert any(item.kind == "tracker.ticket.status_changed" for item in page.items)
    assert session.connection().exec_driver_sql("SELECT total_changes()").scalar() == writes_before
    session.refresh(event)
    assert event.metadata_json["ticket_key"] == malformed


def test_activity_uses_real_writers_filters_before_paging_and_never_writes(session, project_id):
    repo = seed_inspection(session, project_id)
    _update_delivery(repo, project_id, "inspect", status="running", ticket_key="child")
    _update_delivery(repo, project_id, "inspect", status="stopped", reason="Required proof failed")
    stopped = session.exec(select(TrackerRevision).order_by(TrackerRevision.id.desc())).first()
    repo.update_task(
        project_id=project_id, task_key="inspect", patch_json={"description": "edited"}
    )
    for _index in range(65):
        session.add(
            ProjectEvent(
                project_id=project_id,
                source_type="other",
                event_type="unrelated",
                title="Same title",
                metadata_json={"nested": {"task_key": "inspect"}},
            )
        )
    session.commit()
    before = list(session.exec(select(TrackerRevision)).all())
    writes_before = session.connection().exec_driver_sql("SELECT total_changes()").scalar()
    first = repo.task_activity(project_id=project_id, task_key="inspect", limit=2)
    assert first.task_key == "inspect"
    assert first.next_cursor
    items = list(first.items)
    cursor = first.next_cursor
    while cursor:
        page = repo.task_activity(project_id=project_id, task_key="inspect", limit=2, cursor=cursor)
        items.extend(page.items)
        cursor = page.next_cursor
    assert len({(x.source_kind, x.source_id) for x in items}) == len(items)
    assert any(
        x.source_kind == "tracker_revision" and x.source_id == stopped.id and "stopped" in x.summary
        for x in items
    )
    assert all(x.kind != "unrelated" for x in items)
    assert list(session.exec(select(TrackerRevision)).all()) == before
    assert not session.dirty
    assert session.connection().exec_driver_sql("SELECT total_changes()").scalar() == writes_before
    with pytest.raises(ValidationError):
        repo.task_activity(project_id=project_id, task_key="other", cursor=first.next_cursor)


def test_activity_same_timestamp_ties_and_insertion(session, project_id):
    repo = seed_inspection(session, project_id)
    stamp = datetime(2026, 1, 1)
    _update_delivery(repo, project_id, "inspect", status="stopped")
    revision = session.exec(select(TrackerRevision).order_by(TrackerRevision.id.desc())).first()
    revision.created_at = stamp
    session.add(revision)
    for index in range(59):
        session.add(
            ProjectEvent(
                project_id=project_id,
                source_type="tracker_ticket",
                source_id=None,
                event_type="tracker.execution.changed",
                occurred_at=stamp,
                metadata_json={
                    "task_key": "inspect",
                    "ticket_key": "child",
                    "active": True,
                    "invocation_id": str(index),
                },
            )
        )
    session.commit()
    page = repo.task_activity(project_id=project_id, task_key="inspect", limit=3)
    seen = list(page.items)
    session.add(
        ProjectEvent(
            project_id=project_id,
            source_type="tracker_ticket",
            source_id=None,
            event_type="tracker.execution.changed",
            metadata_json={"task_key": "inspect", "ticket_key": "child"},
        )
    )
    session.commit()
    while page.next_cursor:
        page = repo.task_activity(
            project_id=project_id, task_key="inspect", limit=3, cursor=page.next_cursor
        )
        seen.extend(page.items)
    assert len({(x.source_kind, x.source_id) for x in seen}) == len(seen)
    tied = [x for x in seen if x.occurred_at == stamp]
    assert len(tied) == 60
    assert [x.source_kind for x in tied] == ["tracker_revision"] + ["project_event"] * 59


def test_results_require_explicit_provenance(session, project_id):
    from stackos.db.models import Artifact

    repo = seed_inspection(session, project_id)
    linked = Artifact(
        project_id=project_id,
        kind="file",
        uri="file:///tmp/result.txt",
        provenance_json={"task_key": "inspect", "ticket_key": "child"},
    )
    session.add(linked)
    session.add(
        Artifact(
            project_id=project_id,
            kind="file",
            uri="file:///tmp/unlinked.txt",
            name="inspect",
            metadata_json={"task_key": "inspect"},
        )
    )
    session.add(
        Artifact(
            project_id=project_id,
            kind="file",
            uri="file:///tmp/nested.txt",
            provenance_json={"nested": {"task_key": "inspect"}},
        )
    )
    session.commit()
    page = repo.task_results(project_id=project_id, task_key="inspect")
    assert [(x.source_kind, x.source_id) for x in page.items] == [("artifact", linked.id)]
    assert {x.kind for x in page.items[0].associations} == {
        "provenance.task_key",
        "provenance.ticket_key",
    }
    assert page.items[0].data.id == linked.id
    assert page.coverage


def test_results_all_core_relations_and_inactive_contexts(session, project_id):
    from stackos.db.models import (
        Artifact,
        ContextSnapshot,
        Decision,
        ExecutionContext,
        ExecutionContextArtifact,
        ExecutionContextLink,
        Experiment,
        Learning,
        Plugin,
        Resource,
        ResourceRecord,
        Run,
        RunPlan,
        RunPlanStatus,
        RunStatus,
        TrackerLinkKind,
        TrackerTicketLink,
    )

    repo = seed_inspection(session, project_id)
    task = repo.get(project_id=project_id, task_key="inspect", include_graph=False).tasks[0]
    run = Run(project_id=project_id, kind="maintenance", status=RunStatus.RUNNING)
    session.add(run)
    session.flush()
    plan = RunPlan(
        project_id=project_id,
        run_id=run.id,
        key="linked-plan",
        title="Linked",
        status=RunPlanStatus.STARTED,
    )
    session.add(plan)
    session.flush()
    repo.link_run_plan(project_id=project_id, task_key="inspect", run_plan_id=plan.id)
    context = ExecutionContext(
        project_id=project_id, context_ref="old-context", name="Old", status="archived"
    )
    session.add(context)
    session.flush()
    context_link = ExecutionContextLink(
        project_id=project_id, context_id=context.id, link_type="task", link_ref="inspect"
    )
    session.add(context_link)
    plugin = session.exec(select(Plugin)).first()
    resource = Resource(plugin_id=plugin.id, key="inspection-record", name="Inspection")
    session.add(resource)
    session.flush()
    record = ResourceRecord(
        project_id=project_id,
        resource_id=resource.id,
        data_json={"answer": 42},
        provenance_json={"ticket_key": "child"},
    )
    session.add(record)
    snapshot = ContextSnapshot(project_id=project_id, run_id=run.id)
    session.add(snapshot)
    session.flush()
    files = [
        Artifact(project_id=project_id, kind="file", uri=f"file:///tmp/{i}", **fields)
        for i, fields in enumerate(
            [
                {"resource_record_id": record.id},
                {},
                {"provenance_json": {"run_plan_id": plan.id}},
                {"provenance_json": {"run_id": run.id}},
                {"provenance_json": {"context_ref": context.context_ref}},
            ]
        )
    ]
    session.add_all(files)
    session.flush()
    session.add(
        ExecutionContextArtifact(
            project_id=project_id, context_id=context.id, artifact_id=files[1].id
        )
    )
    session.add(
        TrackerTicketLink(
            project_id=project_id,
            tracker_id=task.tracker_id,
            task_id=task.id,
            link_kind=TrackerLinkKind.ARTIFACT,
            artifact_id=files[1].id,
        )
    )
    decision = Decision(project_id=project_id, run_id=run.id, decision="Decided")
    learning = Learning(project_id=project_id, source_snapshot_id=snapshot.id, statement="Learned")
    experiment = Experiment(
        project_id=project_id, hypothesis="Tested", linked_run_ids_json=[run.id]
    )
    session.add_all([decision, learning, experiment])
    session.commit()
    writes_before = session.connection().exec_driver_sql("SELECT total_changes()").scalar()
    page = repo.task_results(project_id=project_id, task_key="inspect", limit=2)
    items = list(page.items)
    while page.next_cursor:
        page = repo.task_results(
            project_id=project_id, task_key="inspect", limit=2, cursor=page.next_cursor
        )
        items.extend(page.items)
    assert {(x.source_kind, x.source_id) for x in items} == {
        ("resource_record", record.id),
        ("decision", decision.id),
        ("learning", learning.id),
        ("experiment", experiment.id),
        *(("artifact", artifact.id) for artifact in files),
    }
    item = next(x for x in items if x.source_kind == "artifact" and x.source_id == files[1].id)
    assert {r.kind for r in item.associations} == {"tracker_link", "execution_context"}
    assert str(context_link.id) in next(
        r.path for r in item.associations if r.kind == "execution_context"
    )
    assert all(x.associations for x in items)
    assert len(items) == 9
    assert session.connection().exec_driver_sql("SELECT total_changes()").scalar() == writes_before
    artifacts = repo.task_results(
        project_id=project_id, task_key="inspect", source_kind="artifact", limit=1
    )
    assert artifacts.next_cursor
    with pytest.raises(ValidationError):
        repo.task_results(project_id=project_id, task_key="inspect", cursor=artifacts.next_cursor)


def test_foreign_and_malformed_relations_never_associate(session, project_id):
    from stackos.db.models import (
        Artifact,
        ExecutionContext,
        ExecutionContextArtifact,
        ExecutionContextLink,
    )
    from stackos.repositories.projects import ProjectRepository

    repo = seed_inspection(session, project_id)
    foreign = (
        ProjectRepository(session)
        .create(slug="foreign", name="Foreign", domain="example.net")
        .data.id
    )
    other_repo = TrackerRepository(session)
    other_repo.create_task(project_id=foreign, key="inspect", title="Same title")
    context = ExecutionContext(project_id=foreign, context_ref="foreign", name="Foreign")
    session.add(context)
    session.flush()
    session.add(
        ExecutionContextLink(
            project_id=project_id, context_id=context.id, link_type="task", link_ref="inspect"
        )
    )
    artifact = Artifact(project_id=project_id, kind="file", uri="file:///tmp/forged")
    session.add(artifact)
    session.flush()
    session.add(
        ExecutionContextArtifact(
            project_id=project_id, context_id=context.id, artifact_id=artifact.id
        )
    )
    for fields in [
        {"project_id": foreign, "provenance_json": {"task_key": "inspect"}},
        {"provenance_json": {"task_key": ["inspect"]}},
        {"provenance_json": {"run_id": True}},
        {"provenance_json": {"run_id": "1"}},
        {"provenance_json": {"context_ref": "foreign"}},
        {"provenance_json": {"ticket_key": "missing"}},
    ]:
        session.add(
            Artifact(
                kind="file", uri="file:///tmp/unrelated", **{"project_id": project_id, **fields}
            )
        )
    session.commit()
    assert repo.task_results(project_id=project_id, task_key="inspect").items == []


def test_activity_real_invocation_and_review_receipt_sources(session, project_id):
    from stackos.db.models import TrackerTicket
    from stackos.host_mcp.service import ActiveInvocation, AgentExecutionScope
    from stackos.task_delivery import _record_execution_transition, _save_evidence

    repo = seed_inspection(session, project_id)
    # The same persisted receipt writer used by delivery, with unrelated launch
    # instructions deliberately omitted from the projected details.
    automatic = {
        "attempt": 1,
        "review": {"launch": {"started_at": "2026-01-01T00:00:00Z", "instructions": "PRIVATE"}},
    }
    _save_evidence(repo, project_id, "child", automatic)
    launch_revision = session.exec(
        select(TrackerRevision).order_by(TrackerRevision.id.desc())
    ).first()
    call = ActiveInvocation(
        invocation_id="native-call",
        host_key="codex",
        model="test",
        started_at="2026-01-01T00:00:00Z",
        scope=AgentExecutionScope(
            project_id=project_id,
            agent="reviewer",
            task_key="inspect",
            ticket_key="child",
            phase="review",
            attempt=1,
        ),
    )
    _record_execution_transition(lambda: Session(session.get_bind()), call, True)
    session.expire_all()
    automatic["review"]["result"] = {"status": "success", "summary": "PRIVATE"}
    automatic["review"]["validated_outcome"] = "success"
    _save_evidence(repo, project_id, "child", automatic)
    finish_revision = session.exec(
        select(TrackerRevision).order_by(TrackerRevision.id.desc())
    ).first()
    _record_execution_transition(lambda: Session(session.get_bind()), call, False)
    session.expire_all()
    repo.update_ticket(project_id=project_id, ticket_key="child", patch_json={"status": "complete"})
    activity = repo.task_activity(project_id=project_id, task_key="inspect")
    assert "PRIVATE" not in activity.model_dump_json()
    revisions = [x for x in activity.items if x.source_kind == "tracker_revision"]
    assert {x.source_id for x in revisions} == {launch_revision.id, finish_revision.id}
    assert (
        len(next(x for x in revisions if x.source_id == finish_revision.id).details["changes"]) == 1
    )
    assert {x.summary for x in activity.items if x.kind == "tracker.execution.changed"} == {
        "Agent call started",
        "Agent call finished",
    }
    ticket = session.exec(select(TrackerTicket).where(TrackerTicket.key == "child")).first()
    completion = next(
        x
        for x in activity.items
        if x.kind == "tracker.ticket.status_changed"
        and x.details["metadata_json"]["new_status"] == "complete"
    )
    assert completion.details["source_id"] == ticket.id
