"""Continuation uses a linked request, never a second execution on the old one."""

# Imported pytest fixtures are intentionally shadowed by fixture arguments.
# ruff: noqa: F811

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlmodel import select

from stackos.db.models import AgentRequest
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.project_setup import ProjectSetupRepository
from tests.integration.test_repositories.test_managed_setup import (
    launch,
    managed,  # noqa: F401
    native_lifetime,  # noqa: F401
)
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_task_delivery import native_source_fixtures  # noqa: F401


def question_runner(monkeypatch, *, second_status="partial"):
    definition = service._definition_by_key("codex")
    calls = []

    def runner(root, instructions, prompt, schema, *, on_session, resume_thread_id=None, **kw):
        context = json.loads(prompt)
        calls.append(context)
        on_session("continuation-native")
        if len(calls) == 1:
            assert resume_thread_id is None
            report = {
                "status": "needs_input",
                "question": {
                    "question_id": "goal",
                    "revision": 1,
                    "prompt": "What is the goal?",
                    "workflow_keys": [],
                    "response_channel": "managed_ui",
                },
            }
        else:
            assert resume_thread_id == "continuation-native"
            assert context["answers"][0]["answer"] == "Maintain this project"
            report = {"status": second_status}
            if second_status == "needs_input":
                report["question"] = {
                    "question_id": "goal",
                    "revision": 1,
                    "prompt": "What is the goal?",
                    "workflow_keys": [],
                    "response_channel": "managed_ui",
                }
        report["progress"] = {"message": "Checked fixture", "evidence_ref": "fixture:checked"}
        return AgentRunResult(
            0, thread_id="continuation-native", result={"report_json": json.dumps(report)}
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=runner)
    )
    return calls


def answer(managed, attempt, **overrides):
    execution, project, scheduler, factory = managed
    return execution.continue_managed(
        project,
        attempt.request_id,
        expected_revision=attempt.revision,
        question_id="goal",
        question_revision=1,
        answer="Maintain this project",
        session_factory=factory,
        scheduler=scheduler,
        **overrides,
    )


def test_answer_once_preserves_native_receipt_and_exact_settings(managed, session, monkeypatch):
    calls = question_runner(monkeypatch)
    first = launch(managed, host_key="codex", model="gpt-6.1-sol")
    managed[2].run()
    session.expire_all()
    original = session.get(AgentRequest, first.attempt.request_id)
    native = copy.deepcopy(original.metadata_json["native_execution"])
    pending = ProjectSetupRepository(session).get(managed[1]).current_attempt
    second = answer(managed, pending)
    assert second.attempt.root_request_id == first.attempt.request_id
    assert second.attempt.predecessor_request_id == first.attempt.request_id
    assert second.attempt.attempt_ref == first.attempt.attempt_ref
    assert second.attempt.managed.settings == pending.managed.settings
    assert second.claim_token is None
    with pytest.raises(ConflictError):
        answer(managed, pending)
    managed[2].run()
    session.expire_all()
    assert session.get(AgentRequest, original.id).metadata_json["native_execution"] == native
    latest = ProjectSetupRepository(session).get(managed[1]).current_attempt
    assert latest.status == "partial" and len(calls) == 2
    assert len(session.exec(select(AgentRequest)).all()) == 2
    assert latest.answers[0].question_id == "goal"


@pytest.mark.parametrize("case", ["stale", "foreign", "live", "identity", "existing"])
def test_invalid_answer_never_consumes_or_launches(managed, session, monkeypatch, case):
    question_runner(monkeypatch)
    first = launch(managed, host_key="codex")
    managed[2].run()
    session.expire_all()
    attempt = ProjectSetupRepository(session).get(managed[1]).current_attempt
    row = session.get(AgentRequest, first.attempt.request_id)
    if case == "stale":
        attempt.revision -= 1
    elif case == "foreign":
        managed = (managed[0], managed[1] + 999, managed[2], managed[3])
    else:
        metadata = copy.deepcopy(row.metadata_json)
        if case == "live":
            metadata["native_execution"]["finished_at"] = None
        elif case == "identity":
            metadata["native_execution"]["execution"]["thread_id"] = None
        else:
            metadata["project_setup"]["entry_kind"] = "existing_agent"
        row.metadata_json = metadata
        session.add(row)
        session.commit()
    with pytest.raises((ConflictError, NotFoundError)):
        answer(managed, attempt)
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert (
        session.get(AgentRequest, row.id).metadata_json["project_setup"]["question"]["answer_state"]
        == "pending"
    )


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("changed_sources", [False, True, "unavailable"])
def test_recovery_preserves_success_and_partial_edits(
    managed,
    session,
    monkeypatch,
    cancelled,
    changed_sources,
):
    execution, project, scheduler, factory = managed
    definition = service._definition_by_key("codex")
    first_key, remaining_key = "engineering.tracked-delivery", "agency.project-setup"
    calls = []

    def runner(root, instructions, prompt, schema, *, on_session, **kw):
        context = json.loads(prompt)
        calls.append(context)
        on_session("partial-native")
        if len(calls) == 1:
            (root / "partial.txt").write_text("unfinished adaptation")
            proof = verified_result(ProjectSetupRepository(session), project)
            proof.attempt_ref = context["attempt_ref"]
            report = {
                "status": "interrupted",
                "workflow_keys": [first_key, remaining_key],
                "workflow_results": [
                    {
                        "workflow_key": first_key,
                        "expected_revision": 0,
                        "result": proof.model_dump(mode="json"),
                    },
                    {
                        "workflow_key": remaining_key,
                        "expected_revision": 0,
                        "result": {
                            "attempt_ref": context["attempt_ref"],
                            "result_ref": "fixture:partial",
                            "status": "unfinished",
                            "changes": ["partial.txt"],
                        },
                    },
                ],
            }
        else:
            assert context["remaining_workflow_keys"] == [remaining_key]
            assert context["revalidate_workflow_keys"] == ([first_key] if changed_sources else [])
            assert context["retained_outcomes"][first_key]["status"] == "success"
            assert context["partial_changes_possible"]
            assert (root / "partial.txt").read_text() == "unfinished adaptation"
            assert kw["resume_thread_id"] == "partial-native"
            report = {"status": "partial"}
        report["progress"] = {"message": "Checked partial state", "evidence_ref": "fixture:partial"}
        return AgentRunResult(
            0, thread_id="partial-native", result={"report_json": json.dumps(report)}
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=runner)
    )
    execution.start_managed(
        project_id=project,
        session_factory=factory,
        scheduler=scheduler,
        mode="setup",
        workflow_keys=[first_key, remaining_key],
        preferences={"host_key": "codex"},
    )
    scheduler.run()
    session.expire_all()
    pending = ProjectSetupRepository(session).get(project).current_attempt
    assert pending.status == "interrupted", pending.managed.diagnostic
    if cancelled:
        pending = execution.cancel_managed(
            project, pending.request_id, session_factory=factory, scheduler=scheduler
        )
    receipt = ProjectSetupRepository(session)._row(project, first_key).model_dump(mode="json")
    if changed_sources:
        original_sources = ProjectSetupRepository.inspect_sources

        def sources(repo, project_id, key, **kwargs):
            if key == first_key and changed_sources == "unavailable":
                raise ValidationError("fixture source is unavailable; repair during reconciliation")
            result = original_sources(repo, project_id, key, **kwargs)
            if key == first_key:
                result[0] = result[0].model_copy(update={"digest": "b" * 64})
            return result

        monkeypatch.setattr(ProjectSetupRepository, "inspect_sources", sources)
    successor = execution.continue_managed(
        project,
        pending.request_id,
        expected_revision=pending.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    scheduler.run()
    session.expire_all()
    assert (
        ProjectSetupRepository(session)._row(project, first_key).model_dump(mode="json") == receipt
    )
    assert (
        ProjectSetupRepository(session).get(project).current_attempt.request_id
        == successor.attempt.request_id
    )
    assert len(calls) == 2


@pytest.fixture
def threaded_managed(managed, session, tmp_path):
    from sqlmodel import Session

    from stackos.db.connection import make_engine
    from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot

    engine = make_engine(tmp_path / "continuation.sqlite")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    try:
        with Session(engine) as own:
            yield (*managed[:3], lambda: Session(engine)), own
    finally:
        engine.dispose()


def test_concurrent_answers_admit_one_successor(threaded_managed, monkeypatch):
    managed, session = threaded_managed
    question_runner(monkeypatch)
    launch(managed, host_key="codex")
    managed[2].run()
    session.expire_all()
    pending = ProjectSetupRepository(session).get(managed[1]).current_attempt

    def attempt_answer(_):
        try:
            return answer(managed, pending).attempt.request_id
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt_answer, range(2)))
    assert sum(value is not None for value in results) == 1
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == 2


def test_resume_failure_stops_without_fallback_or_reasking(managed, session, monkeypatch):
    question_runner(monkeypatch)
    launch(managed, host_key="codex")
    managed[2].run()
    session.expire_all()
    pending = ProjectSetupRepository(session).get(managed[1]).current_attempt
    answer(managed, pending)
    definition = service._definition_by_key("codex")

    def fail(*args, on_session, **kwargs):
        on_session("wrong-session")
        return AgentRunResult(0, thread_id="wrong-session", result={"report_json": "{}"})

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=fail)
    )
    managed[2].run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(managed[1]).current_attempt
    assert current.status == "failed"
    assert current.managed.diagnostic
    assert current.question is None and current.answers[0].answer == "Maintain this project"
    assert len(session.exec(select(AgentRequest)).all()) == 2
    with pytest.raises(ConflictError):
        managed[0].continue_managed(
            managed[1],
            current.request_id,
            expected_revision=current.revision,
            session_factory=managed[3],
            scheduler=managed[2],
        )


def test_answered_question_cannot_be_reasked(managed, session, monkeypatch):
    question_runner(monkeypatch, second_status="needs_input")
    launch(managed, host_key="codex")
    managed[2].run()
    session.expire_all()
    pending = ProjectSetupRepository(session).get(managed[1]).current_attempt
    answer(managed, pending)
    managed[2].run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(managed[1]).current_attempt
    assert current.status == "failed" and current.question is None
    assert "already answered" in current.managed.diagnostic


def test_cancelled_pending_question_still_requires_its_answer(managed, session, monkeypatch):
    question_runner(monkeypatch)
    launch(managed, host_key="codex")
    managed[2].run()
    session.expire_all()
    pending = ProjectSetupRepository(session).get(managed[1]).current_attempt
    pending = managed[0].cancel_managed(
        managed[1], pending.request_id, session_factory=managed[3], scheduler=managed[2]
    )
    with pytest.raises(ConflictError):
        managed[0].continue_managed(
            managed[1],
            pending.request_id,
            expected_revision=pending.revision,
            session_factory=managed[3],
            scheduler=managed[2],
        )
    answer(managed, pending)
    managed[2].run()
    session.expire_all()
    assert ProjectSetupRepository(session).get(managed[1]).current_attempt.status == "partial"
