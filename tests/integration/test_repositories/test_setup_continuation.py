"""Setup questions resume the same tracked work and frozen native session."""

# ruff: noqa: F811
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, select

from stackos.db.models import AgentRequest
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.project_setup import ProjectSetupRepository
from stackos.repositories.tracker import TrackerRepository
from tests.helpers.native_task_routes import native_route_defaults  # noqa: F401
from tests.integration.test_repositories.test_managed_setup import (
    install_native,
    launch,
    managed,  # noqa: F401
    native_lifetime,  # noqa: F401
    task_receipts,
)


def question_runner(monkeypatch, *, repeat=False, fail_resume=False):
    calls = []

    def runner(root, instructions, prompt, schema, *, on_session, resume_thread_id=None, **kwargs):
        context = json.loads(prompt)
        calls.append({**context, "native": kwargs, "resume_thread_id": resume_thread_id})
        on_session("continuation-native")
        if len(calls) > 1:
            assert resume_thread_id == "continuation-native"
            assert context["project_setup"]["answers"][0]["answer"] == "Maintain this project"
            if fail_resume:
                return AgentRunResult(1, thread_id="continuation-native", error="Resume failed")
        report = {
            "status": "needs_input",
            "question": {
                "question_id": "goal" if len(calls) == 1 or repeat else "details",
                "revision": 1,
                "prompt": "What is the goal?",
                "workflow_keys": [],
                "response_channel": "managed_ui",
            },
            "progress": {"message": "Checked fixture", "evidence_ref": "fixture:checked"},
        }
        return AgentRunResult(
            0,
            thread_id="continuation-native",
            result={
                "status": "blocked",
                "summary": "A concrete setup answer is needed.",
                "outputs": json.dumps({"setup_report": report}),
                "followups": [],
            },
        )

    install_native(monkeypatch, runner)
    return calls


def answer(managed, attempt, **overrides):
    execution, project, scheduler, factory = managed
    arguments = dict(
        expected_revision=attempt.revision,
        question_id="goal",
        question_revision=1,
        answer="Maintain this project",
        session_factory=factory,
        scheduler=scheduler,
    )
    arguments.update(overrides)
    return execution.continue_managed(project, attempt.request_id, **arguments)


def pending_question(managed, session, monkeypatch, **options):
    calls = question_runner(monkeypatch, **options)
    first = launch(managed, host_key="codex", model="gpt-6.1-sol")
    managed[2].run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(managed[1])
    assert state.current_attempt.status == "needs_input", state.work
    assert state.work.can_answer
    return first, state.current_attempt, calls


def test_answer_once_preserves_native_receipts_and_frozen_selection(managed, session, monkeypatch):
    first, pending, calls = pending_question(managed, session, monkeypatch)
    TrackerRepository(session).configure_delivery(
        project_id=managed[1],
        delivery={"main_agent": {"host_key": "codex", "model": "gpt-6-luna"}},
    )
    continued = answer(managed, pending)
    assert continued.attempt.request_id == pending.request_id
    assert continued.run_plan_id == first.run_plan_id and continued.task_key == first.task_key
    with pytest.raises(ConflictError):
        answer(managed, pending)
    managed[2].run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(managed[1])
    assert current.current_attempt.question.question_id == "details", current.work
    assert len(current.current_attempt.answers) == 1
    assert len(calls) == 2
    assert calls[1]["resume_thread_id"] == "continuation-native"
    assert all(call["native"]["model"] == "gpt-6.1-sol" for call in calls)
    assert len(session.exec(select(AgentRequest)).all()) == 1
    _, receipts = task_receipts(session, managed[1], first.task_key)
    evidence = receipts[f"workflow-{first.run_plan_id}-prepare"]
    assert evidence["sessions"]["delivery"]["thread_id"] == "continuation-native"
    assert len(evidence["attempts"]) == 1
    assert evidence["attempts"][0]["delivery"]["validated_outcome"] == "blocked"


@pytest.mark.parametrize("case", ["stale", "question", "revision", "blank", "foreign"])
def test_invalid_answer_never_consumes_or_launches(managed, session, monkeypatch, case):
    _, pending, calls = pending_question(managed, session, monkeypatch)
    overrides = {
        "stale": {"expected_revision": pending.revision - 1},
        "question": {"question_id": "foreign"},
        "revision": {"question_revision": 2},
        "blank": {"answer": " "},
        "foreign": {},
    }[case]
    target = managed if case != "foreign" else (managed[0], managed[1] + 1000, *managed[2:])
    with pytest.raises((ConflictError, NotFoundError)):
        answer(target, pending, **overrides)
    session.expire_all()
    current = ProjectSetupRepository(session).get(managed[1]).current_attempt
    assert current.question.answer_state == "pending" and not current.answers
    assert not managed[2].jobs and len(calls) == 1


@pytest.fixture
def threaded_managed(managed, session, tmp_path):
    from stackos.db.connection import make_engine
    from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot

    engine = make_engine(tmp_path / "continuation.sqlite")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    try:
        with Session(engine) as own:
            yield (*managed[:3], lambda: Session(engine)), own
    finally:
        engine.dispose()


def test_concurrent_answers_admit_one_retained_handoff(threaded_managed, monkeypatch):
    managed, session = threaded_managed
    _, pending, _ = pending_question(managed, session, monkeypatch)

    def attempt_answer(_):
        try:
            return answer(managed, pending).attempt.request_id
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt_answer, range(2)))
    assert sum(value is not None for value in results) == 1
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert len(managed[2].jobs) == 1


@pytest.mark.parametrize("failure", ["resume", "reask"])
def test_resume_failure_or_reasked_question_stays_stopped(managed, session, monkeypatch, failure):
    _, pending, calls = pending_question(
        managed,
        session,
        monkeypatch,
        fail_resume=failure == "resume",
        repeat=failure == "reask",
    )
    answer(managed, pending)
    managed[2].run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(managed[1])
    assert current.work.delivery_status == "stopped"
    assert current.work.reason
    assert len(calls) == 2 and not managed[2].jobs
    assert current.current_attempt.answers[0].answer == "Maintain this project"
    assert current.current_attempt.question.answer_state == "answered"


def test_cancelled_pending_question_requires_answer_and_reuses_work(managed, session, monkeypatch):
    first, pending, _ = pending_question(managed, session, monkeypatch)
    cancelled = managed[0].cancel_setup(
        managed[1],
        pending.request_id,
        expected_revision=pending.revision,
        external_work_stopped=False,
        session_factory=managed[3],
        scheduler=managed[2],
    )
    with pytest.raises(ConflictError, match="Answer"):
        managed[0].continue_managed(
            managed[1],
            pending.request_id,
            expected_revision=cancelled.revision,
            session_factory=managed[3],
            scheduler=managed[2],
        )
    resumed = answer(managed, cancelled)
    assert resumed.task_key == first.task_key
    managed[2].run()
    session.expire_all()
    assert (
        ProjectSetupRepository(session).get(managed[1]).current_attempt.question.question_id
        == "details"
    )
