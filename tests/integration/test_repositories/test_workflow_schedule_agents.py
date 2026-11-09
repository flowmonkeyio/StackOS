"""Scheduled admissions consume current project sources through the handoff owner."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from stackos import task_delivery
from stackos.db.models import AgentRequest, RunPlan, ScheduledJob, TrackerTask
from stackos.host_mcp.result import AgentRunResult
from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ScheduledJobRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec
from tests.integration.test_repositories.test_task_delivery import QueuedScheduler, generic_result
from tests.integration.test_repositories.test_task_delivery_sources import source


class AdmissionScheduler(QueuedScheduler):
    def add_listener(self, *_args):
        """Timing listeners are exercised by the real scheduler tests."""


def configuration(session, project_id, root, *, count=1, ambiguous=False):
    roles = [f"role{i}" for i in range(count)]
    WorkflowTemplateLoader(session).save_project_template(
        project_id=project_id,
        spec=WorkflowTemplateSpec.model_validate(
            {
                "schema_version": "stackos.workflow-template.v1",
                "key": "fixture.scheduled-agents",
                "name": "Scheduled agents",
                "version": "0.1.0",
                "agent_requirements": [
                    {
                        "role": role,
                        "requirement": "required",
                        "agent_preset_ref": "stackos.sdlc.delivery",
                        "applies_to_steps": ["step0" if ambiguous else f"step{i}"],
                    }
                    for i, role in enumerate(roles)
                ],
                "steps": [
                    {"id": f"step{i}", "title": "Work", "depends_on": [f"step{i - 1}"] if i else []}
                    for i in range(1 if ambiguous else count)
                ],
            }
        ),
    )
    return {
        "kind": "agent-proof",
        "name": "Agent proof",
        "cron_expr": "* * * * *",
        "timezone": "UTC",
        "template_key": "fixture.scheduled-agents",
        "inputs_json": {},
        "execution_json": {
            "project_dir": str(root),
            "agents": {
                role: {
                    "source_ref": source(
                        session,
                        project_id,
                        root,
                        role,
                        settings='model="gpt-6.1-sol"\nmodel_reasoning_effort="high"\n',
                    )
                }
                for role in roles
            },
        },
    }


def assert_no_admission(session):
    for model in (AgentRequest, RunPlan, TrackerTask):
        assert session.exec(select(model)).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3])
async def test_schedule_and_manual_resolver_share_sources_and_frozen_occurrences(
    session, project_id, tmp_path, monkeypatch, count
):
    config = configuration(session, project_id, tmp_path, count=count)
    repo = ScheduledJobRepository(session)
    saved = repo.set(project_id=project_id, enabled=True, **config).data
    assert_no_admission(session)
    row = session.get(ScheduledJob, saved.id)
    assert row.execution_json == config["execution_json"]
    direct = task_delivery.resolve_delivery_settings(
        session, project_id=project_id, **config["execution_json"]
    )
    scheduler = AdmissionScheduler()
    runtime = WorkflowScheduleRuntime(scheduler, lambda: Session(session.get_bind()))
    due = datetime(2030, 1, 1, tzinfo=UTC)
    await runtime._launch(row, due, lambda: True)
    session.expire_all()
    first = session.exec(select(TrackerTask)).one()
    frozen = first.metadata_json["delivery"]
    assert frozen["agents"] == direct["agents"]
    assert frozen["agent_assignments"] == {
        f"workflow-{frozen['run_plan_id']}-step{i}": f"role{i}" for i in range(count)
    }
    assert set(frozen["agents"]) == {f"role{i}" for i in range(count)}
    source_file = (
        tmp_path / config["execution_json"]["agents"]["role0"]["source_ref"]["source_path"]
    )
    source_file.write_text(source_file.read_text().replace('"role0"\nmodel', '"Changed"\nmodel'))
    # A queued occurrence consumes its accepted bytes even after an external edit.
    calls = []

    def launch(_host, _dir, instructions, _prompt, _schema, **kwargs):
        calls.append(instructions.split("\n\n", 1)[0])
        return AgentRunResult(0, thread_id=f"fixture-{len(calls)}", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler.run()
    assert calls == [f"role{i}" for i in range(count)]
    session.expire_all()
    assert (
        session.get(TrackerTask, first.id).metadata_json["delivery"]["agents"] == frozen["agents"]
    )
    await runtime._launch(
        session.get(ScheduledJob, saved.id), due + timedelta(minutes=1), lambda: True
    )
    session.expire_all()
    latest = session.exec(select(TrackerTask).order_by(TrackerTask.id.desc())).first()
    fresh = latest.metadata_json["delivery"]["agents"]["role0"]
    assert fresh["instructions"] == "Changed"
    assert fresh["source_digest"] != frozen["agents"]["role0"]["source_digest"]
    assert session.get(ScheduledJob, saved.id).execution_json == config["execution_json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["delete", "rename", "malformed"])
async def test_changed_source_blocks_tick_and_enable_before_creating_work(
    session, project_id, tmp_path, mutation
):
    config = configuration(session, project_id, tmp_path)
    repo = ScheduledJobRepository(session)
    saved = repo.set(project_id=project_id, enabled=True, **config).data
    path = tmp_path / config["execution_json"]["agents"]["role0"]["source_ref"]["source_path"]
    if mutation == "delete":
        path.unlink()
    elif mutation == "rename":
        path.rename(path.with_name("renamed.toml"))
    else:
        path.write_text("invalid TOML")
    scheduler = AdmissionScheduler()
    runtime = WorkflowScheduleRuntime(scheduler, lambda: Session(session.get_bind()))
    await runtime._launch(session.get(ScheduledJob, saved.id), datetime.now(UTC), lambda: True)
    session.expire_all()
    assert session.get(ScheduledJob, saved.id).last_run_status == "unavailable"
    assert (
        config["execution_json"]["agents"]["role0"]["source_ref"]["source_path"]
        in repo.list(project_id)[0].setup_issue
    )
    assert_no_admission(session)
    assert scheduler.jobs == []
    repo.toggle(saved.id, project_id=project_id, enabled=False)
    with pytest.raises(ValidationError):
        repo.toggle(saved.id, project_id=project_id, enabled=True)
    assert not session.get(ScheduledJob, saved.id).enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["delete", "malformed", "edit"])
async def test_preflight_snapshot_survives_source_change_until_handoff(
    session, project_id, tmp_path, monkeypatch, mutation
):
    config = configuration(session, project_id, tmp_path)
    repo = ScheduledJobRepository(session)
    saved = repo.set(project_id=project_id, enabled=True, **config).data
    accepted = task_delivery.resolve_delivery_settings(
        session, project_id=project_id, **config["execution_json"]
    )["agents"]["role0"]
    path = tmp_path / accepted["source_ref"]["source_path"]
    original = path.read_text()
    scheduler = AdmissionScheduler()
    runtime = WorkflowScheduleRuntime(scheduler, lambda: Session(session.get_bind()))
    due = datetime(2030, 1, 1, tzinfo=UTC)

    def final_guard():
        if mutation == "delete":
            path.unlink()
        elif mutation == "malformed":
            path.write_text("invalid TOML")
        else:
            path.write_text(original.replace('"role0"\nmodel', '"Changed"\nmodel'))
        return True

    await runtime._launch(session.get(ScheduledJob, saved.id), due, final_guard)
    session.expire_all()
    assert session.get(ScheduledJob, saved.id).last_run_status == "started"
    first = session.exec(select(TrackerTask)).one()
    assert first.metadata_json["delivery"]["agents"] == {"role0": accepted}
    assert len(scheduler.jobs) == 1
    # Existing overlap still protects runnable work; it is not an orphaned admission.
    await runtime._launch(
        session.get(ScheduledJob, saved.id), due + timedelta(minutes=1), lambda: True
    )
    session.expire_all()
    assert session.get(ScheduledJob, saved.id).last_run_status == "overlap"
    assert len(session.exec(select(AgentRequest)).all()) == 1
    calls = []

    def launch(_host, _dir, instructions, _prompt, _schema, **kwargs):
        calls.append(instructions.split("\n\n", 1)[0])
        return AgentRunResult(0, thread_id="frozen-preflight", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler.run()
    assert calls == ["role0"]
    session.expire_all()
    assert session.exec(select(RunPlan)).one().status == "completed"
    assert session.get(TrackerTask, first.id).status == "complete"
    if mutation != "edit":
        await runtime._launch(
            session.get(ScheduledJob, saved.id), due + timedelta(minutes=2), lambda: True
        )
        session.expire_all()
        assert session.get(ScheduledJob, saved.id).last_run_status == "unavailable"
        assert len(session.exec(select(AgentRequest)).all()) == 1
    path.write_text(original.replace('"role0"\nmodel', '"Changed"\nmodel'))
    await runtime._launch(
        session.get(ScheduledJob, saved.id), due + timedelta(minutes=3), lambda: True
    )
    session.expire_all()
    assert session.get(ScheduledJob, saved.id).last_run_status == "started"
    latest = session.exec(select(TrackerTask).order_by(TrackerTask.id.desc())).first()
    fresh = latest.metadata_json["delivery"]["agents"]["role0"]
    assert fresh["instructions"] == "Changed"
    assert fresh["source_digest"] != accepted["source_digest"]
    assert session.get(TrackerTask, first.id).metadata_json["delivery"]["agents"] == {
        "role0": accepted
    }


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("selection", ["missing_source", "copied_codex"])
def test_save_validates_explicit_unassigned_roles(
    session, project_id, tmp_path, enabled, selection
):
    config = configuration(session, project_id, tmp_path, count=2, ambiguous=True)
    config["execution_json"]["step_agent_assignments"] = {"step0": "role0"}
    ref = config["execution_json"]["agents"]["role1"]["source_ref"]
    (tmp_path / ref["source_path"]).unlink()
    if selection == "copied_codex":
        config["execution_json"]["agents"]["role1"] = {
            "name": "copied",
            "instructions": "Duplicate Codex prompt",
        }
    with pytest.raises(ValidationError):
        ScheduledJobRepository(session).set(project_id=project_id, enabled=enabled, **config)
    assert session.exec(select(ScheduledJob)).all() == []
    assert_no_admission(session)


@pytest.mark.asyncio
async def test_supplied_non_codex_role_and_unrelated_project_defaults(
    session, project_id, tmp_path
):
    config = configuration(session, project_id, tmp_path, count=2, ambiguous=True)
    config["execution_json"]["step_agent_assignments"] = {"step0": "role0"}
    config["execution_json"]["agents"]["role1"] = {
        "host_key": "claude-code",
        "name": "research",
        "instructions": "Prepared research",
    }
    TrackerRepository(session).configure_delivery(
        project_id=project_id,
        delivery={
            "agents": {
                "unrelated": {
                    "source_ref": {
                        "host_key": "codex",
                        "native_name": "missing",
                        "source_path": ".codex/agents/missing.toml",
                    }
                }
            }
        },
    )
    saved = ScheduledJobRepository(session).set(project_id=project_id, enabled=True, **config).data
    assert session.get(ScheduledJob, saved.id).execution_json == config["execution_json"]
    runtime = WorkflowScheduleRuntime(AdmissionScheduler(), lambda: Session(session.get_bind()))
    await runtime._launch(session.get(ScheduledJob, saved.id), datetime.now(UTC), lambda: True)
    session.expire_all()
    assert set(session.exec(select(TrackerTask)).one().metadata_json["delivery"]["agents"]) == {
        "role0"
    }


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "bad_selection", ["ambiguous", "step", "role", "undeclared", "missing", "copied"]
)
def test_schedule_selection_rejects_invalid_configuration_before_save(
    session, project_id, tmp_path, bad_selection, enabled
):
    config = configuration(session, project_id, tmp_path, count=2, ambiguous=True)
    execution = config["execution_json"]
    if bad_selection == "step":
        execution["step_agent_assignments"] = {"missing": "role0"}
    elif bad_selection == "role":
        execution["step_agent_assignments"] = {"step0": "missing"}
    elif bad_selection == "undeclared":
        execution["agents"]["extra"] = execution["agents"]["role0"]
    elif bad_selection == "missing":
        execution["agents"].pop("role1")
    elif bad_selection == "copied":
        execution["step_agent_assignments"] = {"step0": "role0"}
        execution["agents"]["role0"] = {"name": "copied", "instructions": "Duplicate prompt"}
    with pytest.raises(ValidationError):
        ScheduledJobRepository(session).set(project_id=project_id, enabled=enabled, **config)
    assert session.exec(select(ScheduledJob)).all() == []
    assert_no_admission(session)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_explicit_schedule_step_assignment_translates_to_occurrence_ticket(
    session, project_id, tmp_path, explicit
):
    config = configuration(session, project_id, tmp_path, count=2, ambiguous=True)
    templates = WorkflowTemplateLoader(session)
    spec = templates.describe_template(key=config["template_key"], project_id=project_id).spec
    spec.version = "0.1.1"
    spec.steps[0].extensions_json = {"agent_assignment": "role0"}
    templates.save_project_template(project_id=project_id, spec=spec)
    if explicit:
        config["execution_json"]["step_agent_assignments"] = {"step0": "role1"}
    saved = ScheduledJobRepository(session).set(project_id=project_id, enabled=True, **config).data
    runtime = WorkflowScheduleRuntime(AdmissionScheduler(), lambda: Session(session.get_bind()))
    await runtime._launch(session.get(ScheduledJob, saved.id), datetime.now(UTC), lambda: True)
    session.expire_all()
    task = session.exec(select(TrackerTask)).one()
    state = task.metadata_json["delivery"]
    assert state["agent_assignments"] == {
        f"workflow-{state['run_plan_id']}-step0": "role1" if explicit else "role0"
    }


@pytest.mark.parametrize("selection", ["missing_source", "copied_legacy"])
def test_disabled_save_revalidates_supplied_source_without_starting_work(
    session, project_id, tmp_path, selection
):
    config = configuration(session, project_id, tmp_path)
    ref = config["execution_json"]["agents"]["role0"]["source_ref"]
    (tmp_path / ref["source_path"]).unlink()
    if selection == "copied_legacy":
        config["execution_json"] = {
            "project_dir": str(tmp_path),
            "delivery_agent": {"name": "copied", "instructions": "Duplicate Codex prompt"},
        }
    with pytest.raises(ValidationError):
        ScheduledJobRepository(session).set(project_id=project_id, **config)
    assert session.exec(select(ScheduledJob)).all() == []
    assert_no_admission(session)


@pytest.mark.asyncio
async def test_non_codex_schedule_keeps_inline_execution(session, project_id, tmp_path):
    config = configuration(session, project_id, tmp_path)
    inline = {"name": "Research", "instructions": "Prepared research", "host_key": "claude-code"}
    config["execution_json"]["agents"] = {"role0": inline}
    TrackerRepository(session).configure_delivery(project_id=project_id, delivery={})
    saved = ScheduledJobRepository(session).set(project_id=project_id, enabled=True, **config).data
    runtime = WorkflowScheduleRuntime(AdmissionScheduler(), lambda: Session(session.get_bind()))
    await runtime._launch(session.get(ScheduledJob, saved.id), datetime.now(UTC), lambda: True)
    session.expire_all()
    assert session.exec(select(TrackerTask)).one().metadata_json["delivery"]["agents"] == {
        "role0": inline
    }
