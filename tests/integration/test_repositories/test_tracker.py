from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy import event
from sqlmodel import Session, select

from stackos.context.repository import ContextRepository
from stackos.db.models import (
    RunPlanStep,
    RunPlanStepStatus,
    TaskTracker,
    TaskTrackerLane,
    TaskTrackerPriority,
    TrackerItemStatus,
    TrackerRevision,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketDependency,
    TrackerTicketKind,
    TrackerTicketLink,
    TrackerTicketReference,
)
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.events import (
    TRACKER_TASK_STATUS_CHANGED,
    TRACKER_TICKET_STATUS_CHANGED,
)
from stackos.repositories.tracker.workflow import workflow_step_ticket_key


def _ticket_create_state(engine, project_id: int) -> dict:
    """Read every create-owned row from a fresh session, including revision/rollup fields."""
    with Session(engine) as reader:
        return {
            model.__name__: [
                row.model_dump(mode="json")
                for row in reader.exec(
                    select(model).where(model.project_id == project_id).order_by(model.id)
                ).all()
            ]
            for model in (
                TaskTracker,
                TrackerTask,
                TrackerTicket,
                TrackerTicketDependency,
                TrackerTicketReference,
                TrackerTicketLink,
                TrackerRevision,
            )
        }


def _seed_ticket_create_state(session: Session, project_id: int) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="atomic", title="Client research")
    repo.create_ticket(
        project_id=project_id,
        task_key="atomic",
        key="existing",
        title="Existing research",
        references_json=[{"ref": "existing-source", "title": "Keep this"}],
    )


@pytest.mark.parametrize("as_list", [False, True])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", None),
        ("title", []),
        ("effort", {}),
        ("goal", None),
        ("title", "x" * 301),
        ("effort", "x" * 41),
        ("source_json", []),
        ("definition_of_done_json", None),
        ("constraints_json", None),
        ("expected_changes_json", None),
        ("allowed_paths_json", None),
        ("completion_evidence_json", []),
        ("context_json", []),
        ("metadata_json", []),
        ("constraints_json", ["Valid", {}]),
        ("metadata_json", {"unsupported": {1, 2}}),
    ],
)
def test_ticket_patch_value_constraints_reject_before_mutation(
    session, project_id, as_list, field, value
):
    _seed_ticket_create_state(session, project_id)
    repo = TrackerRepository(session)
    before = _ticket_create_state(session.get_bind(), project_id)
    events_before = ContextRepository(session).timeline(project_id=project_id).model_dump()
    patch = {"status": "in-progress", field: value}
    errors = []
    for dry_run in (True, False):
        if as_list:
            result = repo.update_ticket_list(
                project_id=project_id,
                updates_json=[{"ticket_key": "existing", "patch_json": patch}],
                dry_run=dry_run,
            ).data
            assert result.valid is False
            errors.append(result.results[0].error_data)
        else:
            with pytest.raises(ValidationError) as exc:
                repo.update_ticket(
                    project_id=project_id, ticket_key="existing", patch_json=patch, dry_run=dry_run
                )
            errors.append(exc.value.data)
        session.commit()
        assert _ticket_create_state(session.get_bind(), project_id) == before
        assert (
            ContextRepository(session).timeline(project_id=project_id).model_dump() == events_before
        )
    assert errors[0] == errors[1]
    assert errors[0]["field"] == field
    # The multi-entity caller shares the same pre-mutation value checks.
    with pytest.raises(ValidationError) as exc:
        repo.patch(project_id=project_id, patch_json={"tickets": {"existing": patch}})
    assert exc.value.data == errors[0]
    assert not session.new and not session.dirty and not session.deleted
    session.commit()
    assert _ticket_create_state(session.get_bind(), project_id) == before


@pytest.mark.parametrize("as_list", [False, True])
def test_ticket_patch_value_constraints_preserve_nullable_and_cleaned_values(
    session, project_id, as_list
):
    _seed_ticket_create_state(session, project_id)
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id,
        ticket_key="existing",
        patch_json={
            "assignee": "agent",
            "blocker_reason": "Waiting",
            "outcome": "Pending",
            "effort": "M",
        },
    )
    patch = {
        "title": " " + "x" * 300 + " ",
        "goal": "",
        "priority_key": "p2",
        "lane_key": "review",
        "assignee": None,
        "blocker_reason": None,
        "outcome": None,
        "effort": None,
        "source_json": None,
        "definition_of_done_json": [],
        "constraints_json": [],
        "expected_changes_json": [],
        "allowed_paths_json": [],
        "completion_evidence_json": None,
        "context_json": None,
        "metadata_json": None,
    }
    before = _ticket_create_state(session.get_bind(), project_id)
    for dry_run in (True, False):
        if as_list:
            result = repo.update_ticket_list(
                project_id=project_id,
                updates_json=[{"ticket_key": "existing", "patch_json": patch}],
                dry_run=dry_run,
            ).data
        else:
            result = repo.update_ticket(
                project_id=project_id, ticket_key="existing", patch_json=patch, dry_run=dry_run
            ).data
        assert result.valid
        if dry_run:
            assert _ticket_create_state(session.get_bind(), project_id) == before
    ticket = repo.brief(project_id=project_id, ticket_key="existing").ticket
    for field, value in patch.items():
        assert getattr(ticket, field) == (value.strip() if isinstance(value, str) else value)


@pytest.mark.parametrize("caller", ["single", "list", "patch"])
@pytest.mark.parametrize(
    ("field", "value"), [("metadata_json", []), ("definition_of_done_json", None)]
)
def test_ticket_json_rejection_survives_caller_commit(session, project_id, caller, field, value):
    _seed_ticket_create_state(session, project_id)
    repo = TrackerRepository(session)
    before = _ticket_create_state(session.get_bind(), project_id)
    patch = {"status": "in-progress", field: value}
    error = None
    result = None
    try:
        if caller == "single":
            repo.update_ticket(project_id=project_id, ticket_key="existing", patch_json=patch)
        elif caller == "list":
            result = repo.update_ticket_list(
                project_id=project_id,
                updates_json=[{"ticket_key": "existing", "patch_json": patch}],
            ).data
        else:
            repo.patch(project_id=project_id, patch_json={"tickets": {"existing": patch}})
    except Exception as exc:
        error = exc  # A real caller may catch a failure and later commit its other work.
    session.commit()
    assert _ticket_create_state(session.get_bind(), project_id) == before
    if caller == "list":
        assert result is not None and result.valid is False
        assert result.results[0].error_data["field"] == field
    else:
        assert isinstance(error, ValidationError)
        assert error.data["field"] == field


@pytest.mark.parametrize("caller", ["single", "list", "patch"])
def test_ticket_json_patch_validates_normalized_values(session, project_id, caller):
    class Metadata(BaseModel):
        observed_at: datetime

    _seed_ticket_create_state(session, project_id)
    repo = TrackerRepository(session)
    observed_at = datetime(2026, 11, 12, tzinfo=UTC)
    patch = {
        "source_json": {1: ("source", observed_at)},
        "definition_of_done_json": ("Required",),
        "constraints_json": ("Preserve",),
        "expected_changes_json": ("Updated",),
        "allowed_paths_json": ("docs/task-tracker.md",),
        "completion_evidence_json": {},
        "context_json": {},
        "metadata_json": Metadata(observed_at=observed_at),
    }
    before = _ticket_create_state(session.get_bind(), project_id)
    preview = repo.update_ticket(
        project_id=project_id, ticket_key="existing", patch_json=patch, dry_run=True
    ).data
    assert preview.valid
    assert _ticket_create_state(session.get_bind(), project_id) == before
    if caller == "single":
        repo.update_ticket(project_id=project_id, ticket_key="existing", patch_json=patch)
    elif caller == "list":
        result = repo.update_ticket_list(
            project_id=project_id,
            updates_json=[{"ticket_key": "existing", "patch_json": patch}],
        ).data
        assert result.valid
    else:
        repo.patch(project_id=project_id, patch_json={"tickets": {"existing": patch}})
    ticket = repo.brief(project_id=project_id, ticket_key="existing").ticket
    assert ticket.source_json == {"1": ["source", observed_at.isoformat()]}
    assert ticket.definition_of_done_json == ["Required"]
    assert ticket.constraints_json == ["Preserve"]
    assert ticket.expected_changes_json == ["Updated"]
    assert ticket.allowed_paths_json == ["docs/task-tracker.md"]
    assert ticket.completion_evidence_json == ticket.context_json == {}
    assert ticket.metadata_json == {"observed_at": "2026-11-12T00:00:00Z"}
    revision = session.exec(select(TrackerRevision).order_by(TrackerRevision.rev.desc())).first()
    assert revision.patch_json["source_json"] == ticket.source_json
    assert revision.patch_json["metadata_json"] == ticket.metadata_json


@pytest.mark.parametrize("case", ["correction", "serialization"])
def test_ticket_json_patch_rejects_normalization_errors(session, project_id, case):
    class Context(BaseModel):
        correction: dict[str, str | int]

    class Metadata(BaseModel):
        value: object

    _seed_ticket_create_state(session, project_id)
    before = _ticket_create_state(session.get_bind(), project_id)
    patch = {"status": "in-progress"}
    if case == "correction":
        patch["context_json"] = Context(
            correction={
                "work_intent": "invalid",
                "candidate_commit_sha": "a" * 40,
                "target_attempt": 1,
            }
        )
        message = "Invalid context_json.correction"
    else:
        patch["metadata_json"] = Metadata(value=object())
        message = "invalid tracker ticket patch value"
    for dry_run in (True, False):
        with pytest.raises(ValidationError, match=message):
            TrackerRepository(session).update_ticket(
                project_id=project_id, ticket_key="existing", patch_json=patch, dry_run=dry_run
            )
        session.commit()
        assert _ticket_create_state(session.get_bind(), project_id) == before


@pytest.mark.parametrize("dry_run", [True, False])
def test_missing_tracker_ticket_lookup_never_initializes(session, project_id, dry_run):
    repo = TrackerRepository(session)
    before = _ticket_create_state(session.get_bind(), project_id)
    config_before = {
        model: [row.model_dump() for row in session.exec(select(model)).all()]
        for model in (TaskTrackerLane, TaskTrackerPriority)
    }
    assert before["TaskTracker"] == []
    with pytest.raises(NotFoundError, match="tracker ticket not found") as exc:
        repo.update_ticket(
            project_id=project_id,
            ticket_key="missing",
            patch_json={"status": "in-progress"},
            dry_run=dry_run,
        )
    assert exc.value.data == {"ticket_key": "missing"}
    assert not session.new and not session.dirty and not session.deleted
    session.commit()
    assert _ticket_create_state(session.get_bind(), project_id) == before
    with Session(session.get_bind()) as reader:
        assert {
            model: [row.model_dump() for row in reader.exec(select(model)).all()]
            for model in (TaskTrackerLane, TaskTrackerPriority)
        } == config_before


@pytest.mark.parametrize("as_list", [False, True])
@pytest.mark.parametrize(
    ("current", "requested", "legal"),
    [
        ("skipped", "aborted", False),
        ("skipped", "in-progress", True),
        ("complete", "aborted", False),
        ("failed", "aborted", True),
        ("deferred", "skipped", True),
        ("skipped", "skipped", True),
    ],
)
def test_ticket_preview_apply_transition_parity(
    session: Session, project_id: int, as_list: bool, current: str, requested: str, legal: bool
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="parity", title="Parity")
    repo.create_ticket(project_id=project_id, task_key="parity", key="dep", title="Dependency")
    repo.create_ticket(
        project_id=project_id, task_key="parity", key="target", title="Target", status=current
    )
    tracker = session.exec(select(TaskTracker).where(TaskTracker.project_id == project_id)).one()
    before = _ticket_create_state(session.get_bind(), project_id)
    events_before = ContextRepository(session).timeline(project_id=project_id).model_dump()
    patch = {"status": requested, "title": "Changed", "add_dependency_keys": ["dep"]}

    def update(dry_run):
        if as_list:
            return repo.update_ticket_list(
                project_id=project_id,
                updates_json=[{"ticket_key": "target", "patch_json": patch}],
                dry_run=dry_run,
            ).data
        return repo.update_ticket(
            project_id=project_id, ticket_key="target", patch_json=patch, dry_run=dry_run
        ).data

    if not legal and not as_list:
        with pytest.raises(ConflictError) as preview_error:
            update(True)
        with pytest.raises(ConflictError) as apply_error:
            update(False)
        assert preview_error.value.data == apply_error.value.data
        assert preview_error.value.data["current"] == current
        assert preview_error.value.data["attempted"] == requested
    else:
        preview = update(True)
        assert preview.valid is legal
        assert _ticket_create_state(session.get_bind(), project_id) == before
        assert (
            ContextRepository(session).timeline(project_id=project_id).model_dump() == events_before
        )
        assert tracker.rev == before["TaskTracker"][0]["rev"]
        applied = update(False)
        assert applied.valid is legal
        if not legal:
            assert preview.errors == applied.errors
            assert preview.results[0].error_data == applied.results[0].error_data
            assert preview.results[0].error_data["current"] == current
        else:
            ticket = repo.brief(project_id=project_id, ticket_key="target").ticket
            assert ticket.status.value == requested
            assert ticket.dependency_keys == ["dep"]
            events = (
                ContextRepository(session)
                .timeline(project_id=project_id, event_type=TRACKER_TICKET_STATUS_CHANGED)
                .items
            )
            assert len(events) == (current != requested)
            assert tracker.rev == before["TaskTracker"][0]["rev"] + 1
    if not legal:
        session.commit()  # A caller's later commit must not persist rejected work.
        assert tracker.rev == before["TaskTracker"][0]["rev"]
        assert _ticket_create_state(session.get_bind(), project_id) == before
        assert (
            ContextRepository(session).timeline(project_id=project_id).model_dump() == events_before
        )


@pytest.mark.parametrize("as_list", [False, True])
@pytest.mark.parametrize("patch", [{"title": "Hijacked"}, {"status": "complete"}])
def test_ticket_preview_apply_mirror_guard_parity(session, project_id, as_list, patch):
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "parity-plan",
                "title": "Parity plan",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
        )
        .data
    )
    key = workflow_step_ticket_key(plan.id, "prepare")
    repo = TrackerRepository(session)
    before = _ticket_create_state(session.get_bind(), project_id)
    errors = []
    for dry_run in (True, False):
        if as_list:
            result = repo.update_ticket_list(
                project_id=project_id,
                updates_json=[{"ticket_key": key, "patch_json": patch}],
                dry_run=dry_run,
            ).data
            assert result.valid is False
            errors.append(result.results[0].error_data)
        else:
            with pytest.raises(ValidationError) as exc:
                repo.update_ticket(
                    project_id=project_id, ticket_key=key, patch_json=patch, dry_run=dry_run
                )
            errors.append(exc.value.data)
    assert errors[0] == errors[1]
    assert errors[0]["run_plan_id"] == plan.id
    session.commit()
    assert _ticket_create_state(session.get_bind(), project_id) == before


def test_ticket_list_preview_tracks_prior_status_and_dependency_updates(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="sequential", title="Sequential")
    for key in ("a", "b"):
        repo.create_ticket(project_id=project_id, task_key="sequential", key=key, title=key)
    updates = [
        {"ticket_key": "a", "patch_json": {"status": "skipped", "dependency_keys": ["b"]}},
        {"ticket_key": "a", "patch_json": {"status": "aborted"}},
        {"ticket_key": "b", "patch_json": {"dependency_keys": ["a"]}},
        {"ticket_key": "a", "patch_json": {"status": "in-progress"}},
    ]
    before = _ticket_create_state(session.get_bind(), project_id)
    preview = repo.update_ticket_list(
        project_id=project_id, updates_json=updates, dry_run=True
    ).data
    assert [item.action for item in preview.results] == ["validated", "error", "error", "validated"]
    assert _ticket_create_state(session.get_bind(), project_id) == before
    applied = repo.update_ticket_list(project_id=project_id, updates_json=updates).data
    assert [item.action for item in applied.results] == ["updated", "error", "error", "updated"]
    assert preview.errors == applied.errors
    assert (
        repo.brief(project_id=project_id, ticket_key="a").ticket.status
        == TrackerItemStatus.IN_PROGRESS
    )
    assert repo.brief(project_id=project_id, ticket_key="b").ticket.dependency_keys == []
    assert applied.rev == before["TaskTracker"][0]["rev"] + 2
    events = (
        ContextRepository(session)
        .timeline(project_id=project_id, event_type=TRACKER_TICKET_STATUS_CHANGED)
        .items
    )
    assert [
        (item.metadata_json["ticket_key"], item.metadata_json["new_status"]) for item in events
    ] == [("a", "skipped"), ("a", "in-progress")]


def test_invalid_ticket_list_preserves_all_rows_events_and_revision(session, project_id):
    _seed_ticket_create_state(session, project_id)
    repo = TrackerRepository(session)
    repo.create_ticket(
        project_id=project_id, task_key="atomic", key="skipped", title="Skipped", status="skipped"
    )
    tracker = session.exec(select(TaskTracker).where(TaskTracker.project_id == project_id)).one()
    before = _ticket_create_state(session.get_bind(), project_id)
    events_before = ContextRepository(session).timeline(project_id=project_id).model_dump()
    updates = [
        {
            "ticket_key": "existing",
            "patch_json": {
                "status": "complete",
                "add_dependency_keys": ["skipped"],
                "references_json": [{"ref": "valid"}, {"path": "invalid"}],
            },
        },
        {"ticket_key": "skipped", "patch_json": {"status": "aborted", "title": "Invalid"}},
    ]
    results = []
    for dry_run in (True, False):
        results.append(
            repo.update_ticket_list(
                project_id=project_id, updates_json=updates, dry_run=dry_run
            ).data
        )
        assert tracker.rev == before["TaskTracker"][0]["rev"]
        session.commit()
        assert _ticket_create_state(session.get_bind(), project_id) == before
        assert (
            ContextRepository(session).timeline(project_id=project_id).model_dump() == events_before
        )
    assert results[0].errors == results[1].errors
    assert [item.action for item in results[1].results] == ["error", "error"]


@pytest.mark.parametrize(
    ("patch", "legal", "dependencies"),
    [
        ({"dependency_keys": ["target"]}, False, []),
        ({"add_dependency_keys": ["target"], "remove_dependency_keys": ["target"]}, False, []),
        ({"remove_dependency_keys": ["dep"]}, False, []),
        ({"add_dependency_keys": ["dep"], "remove_dependency_keys": ["dep"]}, True, []),
        ({"dependency_keys": ["dep"], "add_dependency_keys": []}, False, []),
        ({"references_json": [{"ref": "valid"}, {"path": "invalid"}]}, False, []),
        ({"kind": "invalid"}, False, []),
        ({"status": "invalid"}, False, []),
        ({"parent_ticket_key": "missing"}, False, []),
    ],
)
def test_ticket_preview_apply_invariant_parity(session, project_id, patch, legal, dependencies):
    _seed_ticket_create_state(session, project_id)
    repo = TrackerRepository(session)
    repo.create_ticket(project_id=project_id, task_key="atomic", key="dep", title="Dep")
    repo.create_ticket(project_id=project_id, task_key="atomic", key="target", title="Target")
    # Both modes must validate before changing status, metadata, references or edges.
    patch = {"status": "in-progress", "title": "Changed", **patch}
    before = _ticket_create_state(session.get_bind(), project_id)
    errors = []
    for dry_run in (True, False):
        try:
            repo.update_ticket(
                project_id=project_id, ticket_key="target", patch_json=patch, dry_run=dry_run
            )
        except (ConflictError, NotFoundError, ValidationError, ValueError) as exc:
            assert not legal
            errors.append((str(exc), getattr(exc, "data", None)))
        else:
            assert legal
        if dry_run or not legal:
            session.commit()
            assert _ticket_create_state(session.get_bind(), project_id) == before
    if legal:
        assert (
            repo.brief(project_id=project_id, ticket_key="target").ticket.dependency_keys
            == dependencies
        )
    else:
        assert len(errors) == 2
        assert errors[0] == errors[1]


@pytest.mark.parametrize("single", [False, True])
def test_ticket_create_failure_preserves_outer_work_after_caller_commit(
    session: Session, project_id: int, single: bool
) -> None:
    _seed_ticket_create_state(session, project_id)
    baseline = _ticket_create_state(session.get_bind(), project_id)
    # Unflushed caller-owned work must survive the nested rollback.
    unrelated = session.exec(select(TrackerTask).where(TrackerTask.key == "atomic")).one()
    unrelated.title = "Caller-owned work"
    session.add(unrelated)
    repo = TrackerRepository(session)
    with pytest.raises(ValidationError if single else ConflictError):
        if single:
            repo.create_ticket(
                project_id=project_id,
                task_key="atomic",
                key="new-single",
                title="Single",
                parent_ticket_key="existing",
                dependency_keys=["existing"],
                references_json=[{"ref": "valid-first"}, {"path": "missing-ref"}],
                commit=False,
            )
        else:
            repo.create_ticket_list(
                project_id=project_id,
                ticket_list_json={
                    "task_key": "atomic",
                    "tickets": [
                        {
                            "key": "cycle-a",
                            "dependency_keys": ["cycle-b"],
                            "references_json": [{"ref": "valid"}],
                        },
                        {"key": "cycle-b", "dependency_keys": ["cycle-a"]},
                    ],
                },
            )
    session.commit()
    actual = _ticket_create_state(session.get_bind(), project_id)
    baseline["TrackerTask"][0]["title"] = "Caller-owned work"
    assert actual == baseline


@pytest.mark.parametrize("outer_write", [False, True])
def test_ticket_create_commit_false_leaves_success_under_caller_control(
    session: Session, project_id: int, outer_write: bool
) -> None:
    _seed_ticket_create_state(session, project_id)
    baseline = _ticket_create_state(session.get_bind(), project_id)
    if outer_write:
        task = session.exec(select(TrackerTask).where(TrackerTask.key == "atomic")).one()
        task.title = "Pending caller change"
        session.add(task)
    result = TrackerRepository(session).create_ticket(
        project_id=project_id,
        task_key="atomic",
        key="pending",
        title="Pending",
        references_json=[{"ref": 123, "type": "source", "metadata_json": {"keep": True}}],
        commit=False,
    )
    assert result.data.ticket.key == "pending"
    session.rollback()
    assert _ticket_create_state(session.get_bind(), project_id) == baseline


def test_ticket_create_rollback_restores_retained_revision_before_caller_commit(
    session: Session, project_id: int
) -> None:
    _seed_ticket_create_state(session, project_id)
    repo = TrackerRepository(session)
    tracker = session.exec(select(TaskTracker).where(TaskTracker.project_id == project_id)).one()
    baseline = _ticket_create_state(session.get_bind(), project_id)
    baseline_rev, baseline_updated_at = tracker.rev, tracker.updated_at
    # Retain the identity-map objects and pending caller-owned edits across rejection.
    tracker.description = "Caller-owned tracker description"
    task = session.exec(select(TrackerTask).where(TrackerTask.key == "atomic")).one()
    task.title = "Caller-owned task title"
    session.add_all([tracker, task])
    with pytest.raises(ConflictError, match="cycle"):
        repo.create_ticket_list(
            project_id=project_id,
            ticket_list_json={
                "task_key": "atomic",
                "tickets": [
                    {"key": "cycle-a", "dependency_keys": ["cycle-b"]},
                    {"key": "cycle-b", "dependency_keys": ["cycle-a"]},
                ],
            },
        )
    # Inspect the same session before commit/rollback can hide stale cached values.
    preview = repo.validate_ticket_list(
        project_id=project_id,
        ticket_list_json={
            "task_key": "atomic",
            "tickets": [{"key": "invalid", "references_json": [{"path": "missing-ref"}]}],
        },
    )
    database_rev, database_updated_at = session.exec(
        select(TaskTracker.rev, TaskTracker.updated_at).where(TaskTracker.id == tracker.id)
    ).one()
    assert preview.valid is False
    assert tracker.rev == preview.rev == preview.tracker.rev == database_rev == baseline_rev
    assert (
        tracker.updated_at
        == preview.tracker.updated_at
        == database_updated_at
        == baseline_updated_at
    )
    assert tracker.description == "Caller-owned tracker description"
    assert task.title == "Caller-owned task title"
    session.commit()
    baseline["TaskTracker"][0]["description"] = "Caller-owned tracker description"
    baseline["TrackerTask"][0]["title"] = "Caller-owned task title"
    assert _ticket_create_state(session.get_bind(), project_id) == baseline


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


def test_tracker_task_index_is_complete_and_snapshot_queries_are_bounded(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="indexed-work",
        title="Indexed work",
        created_by="codex",
    )
    repo.create_task(
        project_id=project_id,
        key="closed-work",
        title="Closed work",
        created_by="codex",
    )
    repo.update_task(
        project_id=project_id,
        task_key="closed-work",
        patch_json={"status": "complete"},
        actor="codex",
    )
    for index in range(20):
        key = f"indexed-ticket-{index}"
        repo.create_ticket(
            project_id=project_id,
            task_key="indexed-work",
            key=key,
            title=f"Indexed ticket {index}",
            assignee="codex" if index == 0 else None,
            blocker_reason="Waiting for review." if index == 19 else None,
            created_by="codex",
        )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="indexed-ticket-0",
        patch_json={"status": "complete"},
        actor="codex",
    )
    session.expire_all()

    statements: list[str] = []
    engine = session.get_bind()

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        task_index = repo.get(
            project_id=project_id,
            task_index_only=True,
            include_graph=False,
        )
        index_query_count = len(statements)
        statements.clear()
        open_task_index = repo.get(
            project_id=project_id,
            task_index_only=True,
            task_statuses=[TrackerItemStatus.NOT_STARTED, TrackerItemStatus.IN_PROGRESS],
            include_graph=False,
        )
        filtered_task_index = repo.get(
            project_id=project_id,
            task_index_only=True,
            task_statuses=[TrackerItemStatus.NOT_STARTED, TrackerItemStatus.IN_PROGRESS],
            task_key="closed-work",
            include_graph=False,
        )
        statements.clear()
        snapshot = repo.get(project_id=project_id, include_graph=False)
        snapshot_query_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert {task.key for task in task_index.tasks} == {"indexed-work", "closed-work"}
    assert [task.key for task in open_task_index.tasks] == ["indexed-work"]
    assert filtered_task_index.tasks == []
    assert task_index.tickets == []
    assert task_index.dependencies == []
    assert task_index.links == []
    assert task_index.graph is None
    summary = next(task for task in task_index.tasks if task.key == "indexed-work").ticket_summary
    assert summary is not None
    assert summary.total_count == 20
    assert summary.terminal_count == 1
    assert summary.blocked_count == 1
    assert summary.assignees == ["codex"]
    assert "indexed-ticket-19" in summary.search_text
    assert len(snapshot.tickets) == 20
    assert all(task.ticket_summary is None for task in snapshot.tasks)
    assert index_query_count <= 8
    assert snapshot_query_count <= 16
    with pytest.raises(ValidationError, match="task_index_only requires include_graph=false"):
        repo.get(project_id=project_id, task_index_only=True)


def test_task_index_exposes_actual_prerequisite_titles_only_when_work_is_waiting(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    for key in ("waiting-task", "upstream-task"):
        repo.create_task(project_id=project_id, key=key, title=key, created_by="codex")
    repo.create_ticket(
        project_id=project_id,
        task_key="upstream-task",
        key="upstream",
        title="Actual prerequisite title",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="waiting-task",
        key="waiting",
        title="Waiting work",
        dependency_keys=["upstream"],
        created_by="codex",
    )

    def titles() -> list[str]:
        index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
        summary = next(task for task in index.tasks if task.key == "waiting-task").ticket_summary
        assert summary is not None
        return summary.blocked_by_titles

    assert titles() == ["Actual prerequisite title"]
    focused = repo.get(project_id=project_id, task_key="waiting-task", include_graph=False)
    assert [ticket.key for ticket in focused.tickets] == ["waiting"]
    assert focused.tickets[0].blocked_by_titles == ["Actual prerequisite title"]
    assert repo.brief(project_id=project_id, ticket_key="waiting").ticket.blocked_by_titles == [
        "Actual prerequisite title"
    ]
    repo.create_ticket(
        project_id=project_id,
        task_key="waiting-task",
        key="ready",
        title="Independent ready work",
        created_by="codex",
    )
    assert titles() == []
    repo.update_ticket(
        project_id=project_id, ticket_key="ready", patch_json={"status": "complete"}, actor="codex"
    )
    assert titles() == ["Actual prerequisite title"]
    repo.update_ticket(
        project_id=project_id,
        ticket_key="upstream",
        patch_json={"status": "complete"},
        actor="codex",
    )
    assert titles() == []


def test_task_status_update_records_timeline_event(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    task = repo.create_task(
        project_id=project_id,
        key="notify-task",
        title="Notify task",
        created_by="codex",
    ).data.task
    assert task is not None

    repo.update_task(
        project_id=project_id,
        task_key="notify-task",
        patch_json={"status": "complete"},
        actor="codex",
    )

    events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TASK_STATUS_CHANGED,
        )
        .items
    )

    assert len(events) == 1
    event = events[0]
    assert event.source_type == "tracker_task"
    assert event.source_id == task.id
    assert event.title == "Task notify-task is complete"
    assert event.summary == "Task notify-task changed from not-started to complete."
    assert event.tags == ["tracker", "task", "status"]
    assert event.metadata_json["task_key"] == "notify-task"
    assert event.metadata_json["task_title"] == "Notify task"
    assert event.metadata_json["old_status"] == "not-started"
    assert event.metadata_json["new_status"] == "complete"
    assert event.metadata_json["actor"] == "codex"
    assert event.metadata_json["reason"] == "tracker.update_task"
    assert event.metadata_json["schema_version"] == 1
    assert event.metadata_json["url_path"] == f"/projects/{project_id}/tasks?task=notify-task"


def test_ticket_completion_records_ticket_and_parent_task_events(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    task = repo.create_task(
        project_id=project_id, key="notify-parent", title="Notify parent"
    ).data.task
    ticket_a = repo.create_ticket(
        project_id=project_id,
        task_key="notify-parent",
        key="notify-a",
        title="Notify A",
    ).data.ticket
    ticket_b = repo.create_ticket(
        project_id=project_id,
        task_key="notify-parent",
        key="notify-b",
        title="Notify B",
    ).data.ticket
    assert task is not None
    assert ticket_a is not None
    assert ticket_b is not None

    repo.update_ticket(
        project_id=project_id,
        ticket_key="notify-a",
        patch_json={"status": "complete"},
        actor="codex",
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="notify-b",
        patch_json={"status": "complete"},
        actor="codex",
    )

    ticket_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TICKET_STATUS_CHANGED,
        )
        .items
    )
    task_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TASK_STATUS_CHANGED,
        )
        .items
    )

    assert [event.metadata_json["ticket_key"] for event in ticket_events] == [
        "notify-a",
        "notify-b",
    ]
    assert [event.source_type for event in ticket_events] == [
        "tracker_ticket",
        "tracker_ticket",
    ]
    assert [event.source_id for event in ticket_events] == [ticket_a.id, ticket_b.id]
    assert [event.run_id for event in ticket_events] == [None, None]
    assert ticket_events[-1].tags == ["tracker", "ticket", "status"]
    assert ticket_events[-1].metadata_json["actor"] == "codex"
    assert ticket_events[-1].metadata_json["reason"] == "tracker.update_ticket"
    assert [event.metadata_json["new_status"] for event in task_events] == [
        "in-progress",
        "complete",
    ]
    assert task_events[-1].source_type == "tracker_task"
    assert task_events[-1].source_id == task.id
    assert task_events[-1].metadata_json["task_key"] == "notify-parent"


def test_non_status_patch_does_not_record_status_event(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="notify-noop", title="Notify noop")

    repo.update_task(
        project_id=project_id,
        task_key="notify-noop",
        patch_json={"title": "Renamed task"},
        actor="codex",
    )

    events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TASK_STATUS_CHANGED,
        )
        .items
    )
    assert events == []


def test_ticket_dry_run_and_same_status_do_not_record_status_event(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="notify-dry-run", title="Notify dry run")
    repo.create_ticket(
        project_id=project_id,
        task_key="notify-dry-run",
        key="dry-run-ticket",
        title="Dry run ticket",
    )

    repo.update_ticket(
        project_id=project_id,
        ticket_key="dry-run-ticket",
        patch_json={"status": "complete"},
        actor="codex",
        dry_run=True,
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="dry-run-ticket",
        patch_json={"status": "not-started"},
        actor="codex",
    )

    ticket_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TICKET_STATUS_CHANGED,
        )
        .items
    )
    task_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TASK_STATUS_CHANGED,
        )
        .items
    )

    assert ticket_events == []
    assert task_events == []


def test_bulk_ticket_update_records_status_events(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="notify-bulk", title="Notify bulk")
    ticket_a = repo.create_ticket(
        project_id=project_id,
        task_key="notify-bulk",
        key="bulk-a",
        title="Bulk A",
    ).data.ticket
    ticket_b = repo.create_ticket(
        project_id=project_id,
        task_key="notify-bulk",
        key="bulk-b",
        title="Bulk B",
    ).data.ticket
    assert ticket_a is not None
    assert ticket_b is not None

    repo.update_ticket_list(
        project_id=project_id,
        updates_json=[
            {"ticket_key": "bulk-a", "patch_json": {"status": "complete"}},
            {"ticket_key": "bulk-b", "patch_json": {"status": "complete"}},
        ],
        actor="codex",
    )

    ticket_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TICKET_STATUS_CHANGED,
        )
        .items
    )
    task_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TASK_STATUS_CHANGED,
        )
        .items
    )

    assert [event.metadata_json["ticket_key"] for event in ticket_events] == [
        "bulk-a",
        "bulk-b",
    ]
    assert [event.source_type for event in ticket_events] == [
        "tracker_ticket",
        "tracker_ticket",
    ]
    assert [event.source_id for event in ticket_events] == [ticket_a.id, ticket_b.id]
    assert [event.metadata_json["reason"] for event in ticket_events] == [
        "tracker.update_ticket_list",
        "tracker.update_ticket_list",
    ]
    assert task_events[-1].metadata_json["new_status"] == "complete"


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


def test_reopen_manual_task_without_run(session: Session, project_id: int) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="manual-reopen",
        title="Manual reopen",
        status=TrackerItemStatus.COMPLETE,
        lane_key="done",
        completion_evidence_json={"summary": "Previously closed."},
        metadata_json={"closed_by": "codex"},
        created_by="codex",
    )

    reopened = repo.reopen_task(
        project_id=project_id,
        task_key="manual-reopen",
        reason="New follow-up work was found.",
        actor="codex",
    ).data

    assert reopened.task is not None
    assert reopened.task.status == TrackerItemStatus.IN_PROGRESS
    assert reopened.task.lane_key == "implementation"
    assert reopened.task.completed_at is None
    assert reopened.task.completion_evidence_json == {"summary": "Previously closed."}
    assert reopened.run_plan_id is None
    assert reopened.run_id is None
    assert reopened.run_token is None
    assert reopened.task.metadata_json is not None
    assert reopened.task.metadata_json["last_reopen_reason"] == "New follow-up work was found."
    assert reopened.next_operations == [
        "tracker.createTicket",
        "tracker.updateTicket",
        "tracker.get",
    ]


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


def _workflow_graph_fixture(session: Session, project_id: int):
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.structural-warnings.run",
            "title": "Structural warnings",
            "steps": [
                {"id": "deliver", "title": "Deliver"},
                {"id": "handoff", "title": "Handoff", "depends_on": ["deliver"]},
            ],
        },
        created_by="codex",
    ).data
    started = run_plans.start(plan.id, project_id=project_id).data
    run_plans.claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="deliver", claimed_by="codex"
    )
    step = session.exec(
        select(RunPlanStep).where(
            RunPlanStep.run_plan_id == plan.id, RunPlanStep.step_id == "deliver"
        )
    ).one()
    tracker = TrackerRepository(session)
    parent = workflow_step_ticket_key(plan.id, "deliver")
    handoff = workflow_step_ticket_key(plan.id, "handoff")
    for key in ("impl-a", "impl-b"):
        tracker.create_ticket(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            parent_ticket_key=parent,
            run_plan_id=plan.id,
            run_plan_step_id=step.id,
            key=key,
            title=key,
            lane_key="implementation",
            dependency_keys=[parent],
        )
    return tracker, plan, step, parent, handoff


@pytest.mark.parametrize("key", ["guidance", "review-guidance", "verification-guidance"])
def test_workflow_gate_diagnostics_ignore_ordinary_ticket_prose(session, project_id, key):
    tracker, plan, step, parent, handoff = _workflow_graph_fixture(session, project_id)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=parent,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        key=key,
        title="Update agent guidance",
        lane_key="implementation",
        dependency_keys=[parent],
    )
    tracker.update_ticket(
        project_id=project_id,
        ticket_key=handoff,
        patch_json={"dependency_keys": [parent, "impl-a", "impl-b", key]},
    )

    for title, goal in (
        ("Update agent guidance", "Clarify the delivery contract."),
        ("Update review guidance before implementation", "Verify tests and release signoff."),
    ):
        tracker.update_ticket(
            project_id=project_id, ticket_key=key, patch_json={"title": title, "goal": goal}
        )
        child_checks = {
            check["key"]: check
            for check in tracker.verify(project_id=project_id, ticket_key=key).checks
        }
        assert "workflow-child-gate-contained" not in child_checks
        parent_checks = {
            check["key"]: check
            for check in tracker.verify(project_id=project_id, ticket_key=parent).checks
        }
        assert parent_checks["workflow-step-gate-children-contained"]["passed"] is True
        snapshot = tracker.get(project_id=project_id, run_plan_id=plan.id)
        assert snapshot.graph is not None
        assert snapshot.graph.warnings == []
        assert key in {
            ticket.key
            for ticket in tracker.next(
                project_id=project_id, task_key=f"workflow-{plan.id}"
            ).tickets
        }


@pytest.mark.parametrize("lane", ["verification", "review", "release", "docs", "qa"])
def test_workflow_declared_gate_bypass_and_handoff_remain_structural(session, project_id, lane):
    tracker, plan, step, parent, handoff = _workflow_graph_fixture(session, project_id)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=parent,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        key="checkpoint",
        title="Checkpoint",
        lane_key=lane,
        dependency_keys=["impl-a"],
    )
    checks = {
        check["key"]: check
        for check in tracker.verify(project_id=project_id, ticket_key="checkpoint").checks
    }
    assert checks["workflow-child-gate-contained"]["passed"] is False
    parent_checks = {
        check["key"]: check
        for check in tracker.verify(project_id=project_id, ticket_key=parent).checks
    }
    assert parent_checks["workflow-step-gate-children-contained"]["passed"] is False
    handoff_checks = {
        check["key"]: check
        for check in tracker.verify(project_id=project_id, ticket_key=handoff).checks
    }
    assert handoff_checks["workflow-next-step-terminal-children"]["passed"] is False
    snapshot = tracker.get(project_id=project_id, run_plan_id=plan.id)
    assert snapshot.graph is not None
    assert any("can bypass delivery work" in warning for warning in snapshot.graph.warnings)
    assert any("terminal child tickets" in warning for warning in snapshot.graph.warnings)

    tracker.update_ticket(
        project_id=project_id,
        ticket_key="checkpoint",
        patch_json={"dependency_keys": ["impl-a", "impl-b"]},
    )
    tracker.update_ticket(
        project_id=project_id,
        ticket_key=handoff,
        patch_json={"dependency_keys": [parent, "checkpoint"]},
    )
    repaired = tracker.verify(project_id=project_id, ticket_key="checkpoint")
    assert next(c for c in repaired.checks if c["key"] == "workflow-child-gate-contained")["passed"]
    for ticket_key, check_key in (
        (parent, "workflow-step-gate-children-contained"),
        (handoff, "workflow-next-step-terminal-children"),
    ):
        checks = tracker.verify(project_id=project_id, ticket_key=ticket_key).checks
        assert next(c for c in checks if c["key"] == check_key)["passed"] is True
    snapshot = tracker.get(project_id=project_id, run_plan_id=plan.id)
    assert snapshot.graph is not None
    assert snapshot.graph.warnings == []
    # Diagnostics do not complete prerequisites or make the gate runtime-eligible.
    assert "checkpoint" not in {
        ticket.key
        for ticket in tracker.next(project_id=project_id, task_key=f"workflow-{plan.id}").tickets
    }


def test_workflow_detached_child_warns_without_any_gate_words(session, project_id):
    tracker, plan, step, parent, _ = _workflow_graph_fixture(session, project_id)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=parent,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        key="detached",
        title="Detached",
        lane_key="planning",
    )
    for key in ("impl-a", "impl-b"):
        tracker.update_ticket(
            project_id=project_id, ticket_key=key, patch_json={"dependency_keys": []}
        )
    checks = {
        check["key"]: check
        for check in tracker.verify(project_id=project_id, ticket_key="detached").checks
    }
    assert checks["workflow-step-child-bridge"]["passed"] is False
    assert checks["workflow-child-reachable-from-step"]["passed"] is False
    assert "workflow-child-gate-contained" not in checks
    parent_checks = {
        check["key"]: check
        for check in tracker.verify(project_id=project_id, ticket_key=parent).checks
    }
    assert parent_checks["workflow-step-child-bridge"]["passed"] is False
    assert parent_checks["workflow-step-children-reachable"]["passed"] is False
    snapshot = tracker.get(project_id=project_id, run_plan_id=plan.id)
    assert snapshot.graph is not None
    assert any(
        "detached" in warning and "not reachable" in warning for warning in snapshot.graph.warnings
    )
    assert any("no activation bridge" in warning for warning in snapshot.graph.warnings)
    assert parent in checks["workflow-child-reachable-from-step"]["detail"]


def test_graph_direction_advisory_does_not_infer_gate_from_ordinary_prose(session, project_id):
    tracker = TrackerRepository(session)
    tracker.create_task(
        project_id=project_id, key="ordinary", title="Review guidance before delivery"
    )
    for key in ("first", "second"):
        tracker.create_ticket(project_id=project_id, task_key="ordinary", key=key, title=key)
    tracker.create_ticket(
        project_id=project_id,
        task_key="ordinary",
        key="review-guidance",
        title="Update review guidance before implementation",
        lane_key="implementation",
        dependency_keys=["first", "second"],
    )
    snapshot = tracker.get(project_id=project_id, task_key="ordinary")
    assert snapshot.graph is not None
    assert snapshot.graph.warnings == []


def test_workflow_parent_dependency_activates_child_while_step_is_running(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-activation.run",
            "title": "Workflow Activation",
            "steps": [{"id": "deliver", "title": "Deliver"}],
        },
        created_by="codex",
    ).data
    started = run_plans.start(plan.id, project_id=project_id).data
    run_plans.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="deliver",
        claimed_by="codex",
    )
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).one()
    parent_key = workflow_step_ticket_key(plan.id, "deliver")
    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=parent_key,
        key="workflow-activated-child",
        title="Activated child",
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        dependency_keys=[parent_key],
        created_by="codex",
    )

    child_row = session.exec(
        select(TrackerTicket).where(TrackerTicket.key == "workflow-activated-child")
    ).one()
    dependency = session.exec(
        select(TrackerTicketDependency).where(TrackerTicketDependency.ticket_id == child_row.id)
    ).one()
    next_work = tracker.next(project_id=project_id)
    snapshot = tracker.get(project_id=project_id, run_plan_id=plan.id)

    assert dependency.dependency_type == "activates"
    assert [ticket.key for ticket in next_work.tickets] == ["workflow-activated-child"]
    child = next(ticket for ticket in snapshot.tickets if ticket.key == "workflow-activated-child")
    assert child.blocked_by == []
    assert snapshot.graph is not None
    assert snapshot.graph.warnings == []


def test_tracker_terminal_semantics_reject_failure_deferral_and_warn_on_drift(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key="terminal-semantics",
        title="Terminal semantics",
        created_by="codex",
    )
    repo.create_ticket(
        project_id=project_id,
        task_key="terminal-semantics",
        key="attempted-failure",
        title="Attempted failure",
        created_by="codex",
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="attempted-failure",
        patch_json={"status": "failed", "outcome": "The attempted check failed."},
        actor="codex",
    )

    with pytest.raises(ConflictError):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="attempted-failure",
            patch_json={"status": "deferred", "blocker_reason": "Try later."},
            actor="codex",
        )

    repo.create_ticket(
        project_id=project_id,
        task_key="terminal-semantics",
        key="missing-resume-condition",
        title="Missing resume condition",
        status=TrackerItemStatus.DEFERRED,
        created_by="codex",
    )
    task = session.exec(select(TrackerTask).where(TrackerTask.key == "terminal-semantics")).one()
    task.status = TrackerItemStatus.IN_PROGRESS
    session.add(task)
    session.commit()

    snapshot = repo.get(project_id=project_id, task_key="terminal-semantics")

    assert snapshot.graph is not None
    assert any(
        "is in-progress but all child tickets are terminal" in warning
        for warning in snapshot.graph.warnings
    )
    assert any(
        "missing-resume-condition is deferred without a named resume condition" in warning
        for warning in snapshot.graph.warnings
    )


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


def test_delivery_scope_creation_and_list_preview_share_validation(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="scope", title="Scope")
    scope = {
        "disposition": "excluded",
        "parent_ticket_key": "parent",
        "reason": "Operator withdrew optional scope",
        "source_ref": "operator:scope-2",
    }
    payload = {
        "task_key": "scope",
        "tickets": [
            {"key": "parent", "title": "Parent", "kind": "group"},
            {
                "key": "optional",
                "title": "Optional",
                "status": "skipped",
                "parent_ticket_key": "parent",
                "metadata_json": {"delivery_scope": scope},
            },
        ],
    }
    assert repo.validate_ticket_list(project_id=project_id, ticket_list_json=payload).valid
    assert repo.create_ticket_list(project_id=project_id, ticket_list_json=payload).data.valid
    scope["source_ref"] = " "
    payload["tickets"] = [{**payload["tickets"][1], "key": "invalid"}]
    for result in (
        repo.validate_ticket_list(project_id=project_id, ticket_list_json=payload),
        repo.create_ticket_list(project_id=project_id, ticket_list_json=payload).data,
    ):
        assert result.valid is False
        assert result.errors[0].field == "metadata_json.delivery_scope"
    with pytest.raises(ValidationError, match="delivery_scope"):
        repo.create_ticket(
            project_id=project_id,
            task_key="scope",
            key="invalid",
            title="Invalid",
            status="skipped",
            parent_ticket_key="parent",
            metadata_json={"delivery_scope": scope},
        )
    session.commit()
    assert {t.key for t in repo.get(project_id=project_id, task_key="scope").tickets} == {
        "parent",
        "optional",
    }
    # A retained skipped marker cannot silently become a deferred exclusion
    # without the newly required future-owner context, including sequential lists.
    changes = [
        {"ticket_key": "optional", "patch_json": {"status": "not-started"}},
        {"ticket_key": "optional", "patch_json": {"status": "deferred"}},
    ]
    preview = repo.update_ticket_list(
        project_id=project_id, updates_json=changes, dry_run=True
    ).data
    applied = repo.update_ticket_list(project_id=project_id, updates_json=changes).data
    assert preview.valid and applied.valid
    assert repo.brief(project_id=project_id, ticket_key="optional").ticket.metadata_json == {}
    # Explicitly reinstating the marker on deferred work still requires resume context.
    scope["source_ref"] = "operator:scope-2"
    patch = {"metadata_json": {"delivery_scope": scope}}
    for dry_run in (True, False):
        with pytest.raises(ValidationError, match="delivery_scope"):
            repo.update_ticket(
                project_id=project_id, ticket_key="optional", patch_json=patch, dry_run=dry_run
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
    assert preview.valid is False
    assert [result.action for result in preview.results] == ["validated", "error"]
    assert preview.results[1].error == "ticket dependency edge does not exist"
    assert preview.results[0].dependency_preview is not None
    assert preview.results[0].dependency_preview.removed_dependency_keys == [
        "bulk-preview-a",
        "bulk-preview-b",
        "bulk-preview-c",
        "bulk-preview-d",
    ]
    assert preview.results[0].dependency_preview.final_dependency_keys == []
    assert preview.warnings == [
        "dependency preview removes 4 edges and adds 0; review direction before applying"
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
    assert "runPlan.claimStep" not in running_brief.workflow_handoff.next_operations
    assert "runPlan.recordStep" in running_brief.workflow_handoff.next_operations
    assert any("already running" in item.lower() for item in running_brief.suggested_next_actions)

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


def test_terminal_workflow_mirror_brief_and_verify_do_not_suggest_execution(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-terminal-guidance.run",
            "title": "Tracker Workflow Terminal Guidance",
            "steps": [{"id": "prepare", "title": "Prepare"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan.id, project_id=project_id)
    run_plans.abort(
        run_plan_id=plan.id,
        project_id=project_id,
        reason="Operator cancelled the probe.",
    )

    tracker = TrackerRepository(session)
    ticket_key = f"workflow-{plan.id}-prepare"
    brief = tracker.brief(project_id=project_id, ticket_key=ticket_key)

    assert brief.ticket.status == TrackerItemStatus.ABORTED
    assert brief.workflow_handoff is not None
    assert brief.workflow_handoff.next_operations == ["runPlan.get", "tracker.reopen"]
    assert "runPlan.claimStep" not in brief.workflow_handoff.next_operations
    assert "runPlan.recordStep" not in brief.workflow_handoff.next_operations
    assert "tracker.reopen" in brief.suggested_next_actions[0]
    assert "runPlan.checkConsistency" in brief.suggested_next_actions[0]

    verify = tracker.verify(project_id=project_id, ticket_key=ticket_key)

    assert verify.ready is False
    assert verify.checks[0]["key"] == "ticket-open-or-complete"
    assert verify.checks[0]["passed"] is False
    assert "do not mark it complete" in verify.suggested_next_actions[0].lower()


def test_terminal_plain_ticket_verify_does_not_suggest_workflow_recovery(
    session: Session,
    project_id: int,
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="plain-terminal", title="Plain terminal")
    repo.create_ticket(
        project_id=project_id,
        task_key="plain-terminal",
        key="plain-terminal-ticket",
        title="Plain terminal ticket",
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="plain-terminal-ticket",
        patch_json={"status": "aborted"},
        actor="codex",
    )

    brief = repo.brief(project_id=project_id, ticket_key="plain-terminal-ticket")
    verify = repo.verify(project_id=project_id, ticket_key="plain-terminal-ticket")

    assert brief.suggested_next_actions == [
        "Ticket is aborted; reopen the ticket if work should continue."
    ]
    assert verify.ready is False
    assert "workflow" not in verify.checks[0]["detail"]
    assert "workflow" not in verify.suggested_next_actions[0]


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


def test_workflow_step_mirror_owned_fields_cannot_bypass_run_plan_lifecycle(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "tracker.workflow-mirror-field-guard.run",
                "title": "Tracker Workflow Mirror Field Guard",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
            created_by="codex",
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data
    RunPlanRepository(session).claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        claimed_by="codex",
        project_id=project_id,
    )

    with pytest.raises(ValidationError) as exc_info:
        TrackerRepository(session).update_ticket(
            project_id=project_id,
            ticket_key=f"workflow-{plan.id}-prepare",
            patch_json={"assignee": "other-agent", "lane_key": "review"},
            actor="codex",
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["step_id"] == "prepare"
    assert exc_info.value.data["fields"] == ["assignee", "lane_key"]
    assert "runPlan.claimStep" in exc_info.value.data["next_operations"]


def test_workflow_child_ticket_creation_can_start_as_normal_tracker_work(
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

    created = (
        TrackerRepository(session)
        .create_ticket(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            key="workflow-child-active",
            title="Workflow child active",
            status=TrackerItemStatus.IN_PROGRESS,
            run_plan_id=plan.id,
            run_plan_step_id=step.id,
            created_by="codex",
        )
        .data
    )

    assert created.ticket is not None
    assert created.ticket.status == TrackerItemStatus.IN_PROGRESS


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
    consistency = run_plans.check_consistency(plan.id, project_id=project_id)
    assert not any(
        issue.code == "tracker-complete-step-not-terminal"
        and issue.ticket_key == "workflow-child-active-delivery"
        for issue in consistency.issues
    )


def test_completed_workflow_reports_historical_topology_warnings(
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
    assert len(snapshot.graph.warnings) == 1
    assert "should have exactly one root workflow step ticket" in snapshot.graph.warnings[0]
    assert "workflow-child-historical-delivery" in snapshot.graph.warnings[0]

    step_verify = tracker.verify(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-prepare",
    )
    check_keys = {check["key"] for check in step_verify.checks}
    assert "workflow-step-child-bridge" not in check_keys
    assert "workflow-step-children-reachable" not in check_keys


def test_pending_workflow_step_with_bridged_open_child_does_not_warn_closeout(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-pending-child.run",
            "title": "Tracker Workflow Pending Child",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan.id, project_id=project_id)
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None

    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=f"workflow-{plan.id}-plan",
        key="pending-child",
        title="Pending child",
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        dependency_keys=[f"workflow-{plan.id}-plan"],
        created_by="codex",
    )

    snapshot = tracker.get(project_id=project_id, task_key=f"workflow-{plan.id}")

    assert snapshot.graph is not None
    assert snapshot.graph.warnings == []


def test_workflow_backed_task_rejects_child_ticket_without_step_attachment(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-child-attachment-guard.run",
            "title": "Tracker Workflow Child Attachment Guard",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan.id, project_id=project_id)

    tracker = TrackerRepository(session)
    with pytest.raises(ValidationError) as exc_info:
        tracker.create_ticket(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            key="loose-child",
            title="Loose child",
            created_by="codex",
        )

    assert "workflow-backed tracker tickets must be created under a run-plan step" in str(
        exc_info.value
    )
    assert exc_info.value.data["required_fields"] == ["run_plan_id", "step_id"]


def test_workflow_backed_task_rejects_step_from_different_run_plan(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan_a = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-step-binding-a.run",
            "title": "Tracker Workflow Step Binding A",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
        created_by="codex",
    ).data
    plan_b = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-step-binding-b.run",
            "title": "Tracker Workflow Step Binding B",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan_a.id, project_id=project_id)
    run_plans.start(plan_b.id, project_id=project_id)
    step_b = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan_b.id)).first()
    assert step_b is not None
    assert step_b.id is not None

    with pytest.raises(ValidationError) as exc_info:
        TrackerRepository(session).create_ticket(
            project_id=project_id,
            task_key=f"workflow-{plan_a.id}",
            key="wrong-plan-step",
            title="Wrong plan step",
            run_plan_id=plan_a.id,
            run_plan_step_id=step_b.id,
            created_by="codex",
        )

    assert "belongs to a different run plan" in str(exc_info.value)
    assert exc_info.value.data["step_run_plan_id"] == plan_b.id


def test_workflow_backed_task_warns_when_legacy_data_has_extra_root(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-extra-root-warning.run",
            "title": "Tracker Workflow Extra Root Warning",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan.id, project_id=project_id)

    task = session.exec(select(TrackerTask).where(TrackerTask.key == f"workflow-{plan.id}")).first()
    assert task is not None
    assert task.id is not None
    legacy = TrackerTicket(
        tracker_id=task.tracker_id,
        project_id=project_id,
        task_id=task.id,
        key="legacy-detached-root",
        title="Legacy detached root",
        status=TrackerItemStatus.NOT_STARTED,
        kind=TrackerTicketKind.TICKET,
        priority_key="p1",
        lane_key="planning",
        source_kind=TrackerSourceKind.MANUAL,
    )
    session.add(legacy)
    session.commit()

    tracker = TrackerRepository(session)
    snapshot = tracker.get(project_id=project_id, task_key=f"workflow-{plan.id}")

    assert snapshot.graph is not None
    assert any(
        f"Workflow-backed task workflow-{plan.id} should have exactly one root" in warning
        and "legacy-detached-root" in warning
        for warning in snapshot.graph.warnings
    )


def test_workflow_backed_task_warns_when_only_root_is_not_step_mirror(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-corrupt-root-warning.run",
            "title": "Tracker Workflow Corrupt Root Warning",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan.id, project_id=project_id)

    task = session.exec(select(TrackerTask).where(TrackerTask.key == f"workflow-{plan.id}")).first()
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    mirror = session.exec(
        select(TrackerTicket).where(TrackerTicket.key == f"workflow-{plan.id}-plan")
    ).first()
    assert task is not None
    assert step is not None
    assert mirror is not None
    assert task.id is not None
    assert mirror.id is not None
    assert step.id is not None
    corrupt_root = TrackerTicket(
        tracker_id=task.tracker_id,
        project_id=project_id,
        task_id=task.id,
        key="legacy-parentless-child",
        title="Legacy parentless child",
        status=TrackerItemStatus.NOT_STARTED,
        kind=TrackerTicketKind.TICKET,
        priority_key="p1",
        lane_key="planning",
        source_kind=TrackerSourceKind.MANUAL,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
    )
    session.add(corrupt_root)
    session.flush()
    assert corrupt_root.id is not None
    session.add(
        TrackerTicketDependency(
            tracker_id=task.tracker_id,
            project_id=project_id,
            ticket_id=mirror.id,
            depends_on_ticket_id=corrupt_root.id,
        )
    )
    session.commit()

    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
    )

    assert snapshot.graph is not None
    assert any(
        f"Workflow-backed task workflow-{plan.id} should have exactly one root" in warning
        and "legacy-parentless-child" in warning
        for warning in snapshot.graph.warnings
    )


def test_pending_workflow_step_with_docs_child_does_not_warn_bypass(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-pending-docs.run",
            "title": "Tracker Workflow Pending Docs",
            "steps": [{"id": "deliver", "title": "Deliver"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan.id, project_id=project_id)
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None

    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=f"workflow-{plan.id}-deliver",
        key="delivery-runtime",
        title="Delivery runtime",
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        dependency_keys=[f"workflow-{plan.id}-deliver"],
        created_by="codex",
    )
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=f"workflow-{plan.id}-deliver",
        key="delivery-docs",
        title="Delivery docs",
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        dependency_keys=[f"workflow-{plan.id}-deliver"],
        created_by="codex",
    )

    snapshot = tracker.get(project_id=project_id, task_key=f"workflow-{plan.id}")

    assert snapshot.graph is not None
    assert snapshot.graph.warnings == []


def test_completed_workflow_step_with_open_child_warns_closeout(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-open-child.run",
            "title": "Tracker Workflow Open Child",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
        created_by="codex",
    ).data
    run_plans.start(plan.id, project_id=project_id)
    step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).first()
    assert step is not None
    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=f"workflow-{plan.id}-plan",
        key="open-child",
        title="Open child",
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        dependency_keys=[f"workflow-{plan.id}-plan"],
        created_by="codex",
    )

    mirror = tracker._ticket_by_key(
        tracker.ensure_tracker(project_id=project_id).id,
        f"workflow-{plan.id}-plan",
    )
    mirror.status = TrackerItemStatus.COMPLETE
    session.add(mirror)
    session.commit()

    snapshot = tracker.get(project_id=project_id, task_key=f"workflow-{plan.id}")

    assert snapshot.graph is not None
    assert snapshot.graph.warnings == [
        f"Workflow step workflow-{plan.id}-plan is complete while attached child tickets "
        "remain open (open-child)."
    ]


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


def test_workflow_task_reject_requires_run_plan_lifecycle(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "tracker.workflow-reject-guard.run",
                "title": "Tracker Workflow Reject Guard",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
            created_by="codex",
        )
        .data
    )

    with pytest.raises(ValidationError) as exc_info:
        TrackerRepository(session).reject_task(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            reason="Do not split workflow lifecycle.",
            actor="codex",
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert "runPlan.abort" in exc_info.value.data["next_operations"]


def test_completed_workflow_task_reject_cannot_override_plan_lifecycle(
    session: Session,
    project_id: int,
) -> None:
    run_plans = RunPlanRepository(session)
    plan = run_plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "tracker.workflow-terminal-reject-guard.run",
            "title": "Tracker Workflow Terminal Reject Guard",
            "steps": [{"id": "prepare", "title": "Prepare"}],
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
        result_json={"summary": "done"},
        project_id=project_id,
    )

    with pytest.raises(ConflictError) as exc_info:
        TrackerRepository(session).reject_task(
            project_id=project_id,
            run_plan_id=plan.id,
            reason="Cannot reject completed canonical workflow.",
            actor="codex",
            allow_workflow_reject=True,
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["run_plan_status"] == "completed"
    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    assert snapshot.tasks[0].status == TrackerItemStatus.COMPLETE
    assert {ticket.status for ticket in snapshot.tickets} == {TrackerItemStatus.COMPLETE}


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
    assert preview.valid is True
    assert preview.results[0].action == "validated"
