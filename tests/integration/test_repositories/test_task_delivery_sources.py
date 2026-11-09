"""Source-backed admission uses disposable files and the existing delivery owner."""

import json

import pytest
from sqlmodel import Session

from stackos import task_delivery
from stackos.host_mcp.project_agents import ProjectAgentService
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.base import ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.workspaces import WorkspaceRepository
from tests.integration.test_repositories.test_task_delivery import QueuedScheduler, generic_result


def source(session, project_id, root, name, instructions=None, settings=""):
    root.mkdir(exist_ok=True)
    WorkspaceRepository(session).connect(
        project_id=project_id, last_known_root=str(root), repo_fingerprint=f"fixture:{root}"
    )
    folder = root / ".codex" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.toml").write_text(
        f'name = "{name}"\ndescription = "Fixture"\n'
        f"developer_instructions = {json.dumps(instructions or name)}\n{settings}"
    )
    ref = {"host_key": "codex", "native_name": name, "source_path": f".codex/agents/{name}.toml"}
    return ProjectAgentService(root).get(ref).source_ref.model_dump()


def start(session, project_id, root, scheduler, **kwargs):
    return task_delivery.start_delivery(
        session=session,
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
        project_id=project_id,
        project_dir=str(root),
        **kwargs,
    )


def test_fresh_codex_inline_requires_explicit_adoption(session, project_id, tmp_path):
    with pytest.raises(ValidationError, match="adoption_required"):
        task_delivery.resolve_delivery_settings(
            session,
            project_id=project_id,
            project_dir=str(tmp_path),
            delivery_agent={"name": "a", "instructions": "old"},
            review_agent={"name": "b", "instructions": "old"},
        )


def test_source_settings_win_and_omissions_inherit(session, project_id, tmp_path):
    ref = source(
        session,
        project_id,
        tmp_path,
        "writer",
        settings=(
            'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "high"\n[features]\napps = false\n'
        ),
    )
    repo = TrackerRepository(session)
    repo.configure_delivery(
        project_id=project_id,
        delivery={
            "agents": {
                "writer": {
                    "source_ref": ref,
                    "model": "gpt-6-astra",
                    "model_reasoning_effort": "low",
                }
            }
        },
    )
    resolved = task_delivery.resolve_delivery_settings(
        session,
        project_id=project_id,
        project_dir=str(tmp_path),
        agents={"writer": {"model_reasoning_effort": "medium"}},
    )["agents"]["writer"]
    assert resolved["model"] == "gpt-6.1-sol"
    assert resolved["native_settings"] == {
        "model_reasoning_effort": "high",
        "features": {"apps": False},
    }
    assert resolved["source_ref"] == ref and len(resolved["source_digest"]) == 64
    assert resolved["instructions"] == "writer"
    (tmp_path / ref["source_path"]).write_text(
        'name="writer"\ndescription="Fixture"\ndeveloper_instructions="new"\n'
    )
    changed = task_delivery.resolve_delivery_settings(
        session,
        project_id=project_id,
        project_dir=str(tmp_path),
        agents={"writer": {"model_reasoning_effort": "medium"}},
    )["agents"]["writer"]
    assert changed["model"] == "gpt-6-astra"
    assert changed["native_settings"] == {"model_reasoning_effort": "medium"}
    assert changed["source_digest"] != resolved["source_digest"]


@pytest.mark.parametrize("count", [1, 3])
def test_declared_roles_execute_without_engineering_pair(
    session, project_id, tmp_path, monkeypatch, count
):
    refs = {
        f"role{i}": {"source_ref": source(session, project_id, tmp_path, f"agent{i}")}
        for i in range(count)
    }
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "roles",
            "title": "Roles",
            "steps": [
                {"id": f"step{i}", "title": "Step", "depends_on": [f"step{i - 1}"] if i else []}
                for i in range(count)
            ],
        },
    ).data
    # Use the frozen template's declared method; no phase-name inference.
    from stackos.db.models import RunPlan

    row = session.get(RunPlan, plan.id)
    row.template_snapshot_json = {
        "agent_requirements": [
            {"role": f"role{i}", "requirement": "required", "applies_to_steps": [f"step{i}"]}
            for i in range(count)
        ]
    }
    session.add(row)
    session.commit()
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="step0")
    calls = []

    def launch(_host, _dir, instructions, prompt, _schema, **kwargs):
        calls.append(instructions.split("\n\n", 1)[0])
        return AgentRunResult(0, thread_id=f"thread-{len(calls)}", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
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
            },
        },
    )
    start(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="step0",
        agents=refs,
    )
    # Editing after admission cannot change the queued invocation.
    (tmp_path / refs["role0"]["source_ref"]["source_path"]).unlink()
    scheduler.run()
    assert calls == [f"agent{i}" for i in range(count)]
    session.expire_all()
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key=f"workflow-{plan.id}")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["status"] == "complete", state
    from stackos.repositories.tracker.agent_stats import query_agent_stats

    stats = query_agent_stats(session, project_id=project_id)
    assert {row.selected_role for row in stats.rows} == {f"role{i}" for i in range(count)}


def test_missing_source_never_falls_back(session, project_id, tmp_path):
    ref = source(session, project_id, tmp_path, "writer")
    (tmp_path / ref["source_path"]).unlink()
    with pytest.raises(ValidationError, match="unavailable"):
        task_delivery.resolve_delivery_settings(
            session,
            project_id=project_id,
            project_dir=str(tmp_path),
            agents={"writer": {"source_ref": ref}},
        )


def test_explicit_adoption_preview_readback_preserves_settings_and_history(
    session, project_id, tmp_path
):
    from copy import deepcopy

    ref = source(
        session,
        project_id,
        tmp_path,
        "builder",
        instructions="Current source",
        settings='model="gpt-6.1-sol"\n',
    )
    repo = TrackerRepository(session)
    previous = {
        "host_key": "codex",
        "delivery_agent": {
            "name": "builder",
            "instructions": "Old copy",
            "model": "gpt-6-astra",
            "provider": None,
        },
        "review_agent": {
            "host_key": "claude-code",
            "name": "review",
            "instructions": "Keep",
            "model": None,
        },
    }
    repo.configure_delivery(project_id=project_id, delivery=previous)
    repo.create_task(
        project_id=project_id,
        key="live",
        title="Live",
        metadata_json={
            "delivery": {"status": "running", "delivery_agent": {"instructions": "Frozen"}}
        },
    )
    before = repo.get(project_id=project_id, task_key="live").tasks[0].model_dump()
    selected = {
        "delivery_agent": {
            "source_ref": ref,
            "expected_digest": ProjectAgentService(tmp_path).get(ref).digest,
        }
    }
    preview = repo.adopt_delivery_sources(
        project_id=project_id, project_dir=str(tmp_path), sources=selected, preview=True
    )
    assert preview["adoption"]["differences"]["delivery_agent"]["instructions"] == {
        "saved": "Old copy",
        "source": "Current source",
    }
    assert repo.delivery_defaults(project_id=project_id) == previous
    result = repo.adopt_delivery_sources(
        project_id=project_id, project_dir=str(tmp_path), sources=selected, preview=False
    )
    wanted = deepcopy(previous)
    migrated = wanted.pop("delivery_agent")
    migrated.pop("instructions")
    wanted["agents"] = {"delivery_agent": {**migrated, "source_ref": ref}}
    assert repo.delivery_defaults(project_id=project_id) == wanted
    assert result["config_json"]["delivery"] == wanted
    effective = task_delivery.resolve_delivery_settings(
        session, project_id=project_id, project_dir=str(tmp_path)
    )["agents"]
    assert effective["review_agent"] == {
        "host_key": "claude-code",
        "name": "review",
        "instructions": "Keep",
    }
    assert repo.get(project_id=project_id, task_key="live").tasks[0].model_dump() == before
    (tmp_path / ref["source_path"]).write_text(
        'name="builder"\ndescription="Changed"\ndeveloper_instructions="Future"\n'
    )
    from stackos.repositories.base import ConflictError

    with pytest.raises(ConflictError, match="stale_source"):
        repo.adopt_delivery_sources(
            project_id=project_id, project_dir=str(tmp_path), sources=selected, preview=False
        )
    assert repo.delivery_defaults(project_id=project_id) == wanted


def test_ordinary_review_requires_distinct_selected_native_identity(session, project_id, tmp_path):
    ref = source(session, project_id, tmp_path, "same")
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="ordinary", title="Ordinary")
    repo.create_ticket(project_id=project_id, task_key="ordinary", key="work", title="Work")
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="independent review_agent"):
        start(
            session,
            project_id,
            tmp_path,
            scheduler,
            task_key="ordinary",
            agents={"delivery_agent": {"source_ref": ref}},
        )
    with pytest.raises(ValidationError, match="independent"):
        start(
            session,
            project_id,
            tmp_path,
            scheduler,
            task_key="ordinary",
            agents={role: {"source_ref": ref} for role in ("delivery_agent", "review_agent")},
        )
    assert scheduler.jobs == []


def test_ambiguous_source_identity_never_substitutes_a_definition(session, project_id, tmp_path):
    ref = source(session, project_id, tmp_path, "writer")
    (tmp_path / ".codex/agents/duplicate.toml").write_bytes(
        (tmp_path / ref["source_path"]).read_bytes()
    )
    with pytest.raises(ValidationError, match="ambiguous_identity"):
        task_delivery.resolve_delivery_settings(
            session,
            project_id=project_id,
            project_dir=str(tmp_path),
            agents={"writer": {"source_ref": ref}},
        )


def test_source_edit_refreshes_admission_but_resumes_frozen_session(
    session, project_id, tmp_path, monkeypatch
):
    writer = source(
        session,
        project_id,
        tmp_path,
        "writer",
        instructions="Original",
        settings='model_reasoning_effort="high"\n',
    )
    reviewer = source(session, project_id, tmp_path, "reviewer", instructions="Independent")
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="resume", title="Resume")
    repo.create_ticket(project_id=project_id, task_key="resume", key="work", title="Work")
    roles = {"delivery_agent": {"source_ref": writer}, "review_agent": {"source_ref": reviewer}}
    calls = []

    def launch(_host, _dir, instructions, prompt, _schema, **kwargs):
        phase = json.loads(prompt)["phase"]
        calls.append(
            (
                phase,
                instructions.split("\n\n", 1)[0],
                kwargs.get("resume_thread_id"),
                kwargs["native_settings"],
            )
        )
        return AgentRunResult(
            0,
            thread_id=f"thread-{phase}",
            result=generic_result("blocked" if len(calls) == 1 else "success"),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    start(session, project_id, tmp_path, scheduler, task_key="resume", agents=roles)
    scheduler.run()
    session.expire_all()
    first = repo.brief(project_id=project_id, ticket_key="work").ticket.completion_evidence_json[
        "automatic_delivery"
    ]
    source(
        session,
        project_id,
        tmp_path,
        "writer",
        instructions="Future",
        settings='model_reasoning_effort="low"\n',
    )
    for status in ("in-progress", "not-started"):
        repo.update_ticket(project_id=project_id, ticket_key="work", patch_json={"status": status})
    start(session, project_id, tmp_path, scheduler, task_key="resume", agents=roles)
    current = (
        repo.get(project_id=project_id, task_key="resume")
        .tasks[0]
        .metadata_json["delivery"]["agents"]["delivery_agent"]
    )
    assert current["instructions"] == "Future"
    scheduler.run()
    assert calls == [
        ("delivery", "Original", None, {"model_reasoning_effort": "high"}),
        ("delivery", "Original", "thread-delivery", {"model_reasoning_effort": "high"}),
        ("review", "Independent", None, {}),
    ]
    session.expire_all()
    final = repo.brief(project_id=project_id, ticket_key="work").ticket.completion_evidence_json[
        "automatic_delivery"
    ]
    assert final["sessions"]["delivery"] == first["sessions"]["delivery"]
    assert final["repair_count"] == first["repair_count"] == 0


def test_ambiguous_declared_roles_need_explicit_assignment(session, project_id, tmp_path):
    from stackos.db.models import RunPlan

    ref = source(session, project_id, tmp_path, "writer")
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "ambiguous",
            "title": "Ambiguous",
            "steps": [{"id": "work", "title": "Work"}],
        },
    ).data
    row = session.get(RunPlan, plan.id)
    row.template_snapshot_json = {
        "agent_requirements": [
            {"role": role, "requirement": "required", "applies_to_steps": ["work"]}
            for role in ("one", "two")
        ]
    }
    session.add(row)
    session.commit()
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="work")
    scheduler = QueuedScheduler()
    scope = {
        "task_key": f"workflow-{plan.id}",
        "run_plan_id": plan.id,
        "step_id": "work",
        "agents": {role: {"source_ref": ref} for role in ("one", "two")},
    }
    with pytest.raises(ValidationError, match="Ambiguous"):
        start(session, project_id, tmp_path, scheduler, **scope)
    with pytest.raises(ValidationError, match="adoption_required"):
        start(
            session,
            project_id,
            tmp_path,
            scheduler,
            **scope,
            agent_assignments={f"workflow-{plan.id}-work": "missing"},
        )
    assert not scheduler.jobs
    start(
        session,
        project_id,
        tmp_path,
        scheduler,
        **scope,
        agent_assignments={f"workflow-{plan.id}-work": "two"},
    )
    assert len(scheduler.jobs) == 1
