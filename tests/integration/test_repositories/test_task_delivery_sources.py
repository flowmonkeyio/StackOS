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
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="fresh", title="Fresh")
    repo.create_ticket(project_id=project_id, task_key="fresh", key="fresh-child", title="Fresh")
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="adoption_required"):
        start(
            session,
            project_id,
            tmp_path,
            scheduler,
            task_key="fresh",
            delivery_agent={"name": "a", "instructions": "old"},
            review_agent={"name": "b", "instructions": "old"},
        )
    assert not scheduler.jobs


@pytest.mark.parametrize("argument_form", ["legacy", "map"])
@pytest.mark.parametrize("partially_mapped", [False, True])
@pytest.mark.parametrize("task_choice", ["inherit", "set", "clear"])
def test_selected_source_preserves_role_fallbacks_across_argument_forms(
    session, project_id, tmp_path, argument_form, partially_mapped, task_choice
):
    ref = source(session, project_id, tmp_path, "builder")
    repo = TrackerRepository(session)
    review = {
        "host_key": "claude-code",
        "name": "independent",
        "instructions": "Review",
        "model": "claude-sonnet-5-5",
    }
    saved = {
        "delivery_agent": {
            "name": "builder",
            "instructions": "Legacy instructions",
            "model": "gpt-6-astra",
            "provider": "custom-provider",
            "model_reasoning_effort": "high",
        },
        "review_agent": review,
    }
    if partially_mapped:
        saved["agents"] = {"delivery_agent": {"source_ref": ref, "model": "gpt-6.1-sol"}}
    repo.configure_delivery(project_id=project_id, delivery=saved)
    selection = {} if partially_mapped else {"source_ref": ref}
    expected = ("gpt-6.1-sol" if partially_mapped else "gpt-6-astra", "custom-provider", "high")
    expected_origin = "project"
    if task_choice == "set":
        selection.update(model="gpt-6.1-sol", model_reasoning_effort="medium")
        expected = ("gpt-6.1-sol", "custom-provider", "medium")
        expected_origin = "task"
    elif task_choice == "clear":
        selection.update(model=None, provider=None, model_reasoning_effort=None)
        expected = (None, None, None)
        expected_origin = "host"
    arguments = (
        {"delivery_agent": selection}
        if argument_form == "legacy"
        else {"agents": {"delivery_agent": selection}}
    )
    resolved = task_delivery.resolve_delivery_settings(
        session,
        project_id=project_id,
        project_dir=str(tmp_path),
        role_keys={"delivery_agent", "review_agent"},
        **arguments,
    )["agents"]
    actual = resolved["delivery_agent"]
    assert (
        actual.get("model"),
        actual.get("provider"),
        actual["native_settings"].get("model_reasoning_effort"),
    ) == expected
    assert actual["instructions"] == "builder"
    assert actual["setting_sources"] == {
        "model": expected_origin,
        "model_reasoning_effort": expected_origin,
    }
    assert resolved["review_agent"] == review
    assert repo.delivery_defaults(project_id=project_id) == saved


def test_ordinary_assignments_select_each_delivery_role_and_reviewer_only(
    session, project_id, tmp_path, monkeypatch
):
    selected = {
        role: {"source_ref": source(session, project_id, tmp_path, role)}
        for role in ("writer", "tester", "review_agent")
    }
    repo = TrackerRepository(session)
    repo.configure_delivery(
        project_id=project_id,
        delivery={
            "agents": {
                **selected,
                "unrelated": {
                    "source_ref": {
                        "host_key": "codex",
                        "native_name": "missing",
                        "source_path": ".codex/agents/missing.toml",
                    }
                },
            },
        },
    )
    repo.create_task(project_id=project_id, key="assigned", title="Assigned")
    repo.create_ticket(project_id=project_id, task_key="assigned", key="first", title="First")
    repo.create_ticket(
        project_id=project_id,
        task_key="assigned",
        key="second",
        title="Second",
        dependency_keys=["first"],
    )
    calls = []

    def launch(_host, _dir, instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, instructions.split("\n\n", 1)[0]))
        return AgentRunResult(0, thread_id=f"{key}-{phase}", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    start(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key="assigned",
        agent_assignments={"first": "writer", "second": "tester"},
    )
    admitted = (
        repo.get(project_id=project_id, task_key="assigned").tasks[0].metadata_json["delivery"]
    )
    assert set(admitted["agents"]) == set(selected)
    scheduler.run()
    assert calls == [
        ("first", "writer"),
        ("first", "review_agent"),
        ("second", "tester"),
        ("second", "review_agent"),
    ]
    session.expire_all()
    assert (
        repo.get(project_id=project_id, task_key="assigned")
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "complete"
    )


def test_source_values_still_win_over_explicit_null_fallbacks(session, project_id, tmp_path):
    ref = source(
        session,
        project_id,
        tmp_path,
        "builder",
        settings='model="gpt-6.1-sol"\nmodel_reasoning_effort="high"\n',
    )
    repo = TrackerRepository(session)
    repo.configure_delivery(
        project_id=project_id,
        delivery={
            "delivery_agent": {"model": "gpt-6-astra", "model_reasoning_effort": "low"},
        },
    )
    actual = task_delivery.resolve_delivery_settings(
        session,
        project_id=project_id,
        project_dir=str(tmp_path),
        agents={
            "delivery_agent": {"source_ref": ref, "model": None, "model_reasoning_effort": None}
        },
        role_keys={"delivery_agent"},
    )["agents"]["delivery_agent"]
    assert actual["model"] == "gpt-6.1-sol"
    assert actual["native_settings"]["model_reasoning_effort"] == "high"
    assert actual["setting_sources"] == {"model": "source", "model_reasoning_effort": "source"}


def test_new_map_handoff_retains_saved_mixed_host_reviewer(session, project_id, tmp_path):
    ref = source(session, project_id, tmp_path, "builder")
    repo = TrackerRepository(session)
    review = {"name": "reviewer", "host_key": "claude-code", "instructions": "Review"}
    repo.configure_delivery(
        project_id=project_id,
        delivery={
            "delivery_agent": {
                "instructions": "Old",
                "model": "gpt-6-astra",
                "provider": "custom-provider",
                "model_reasoning_effort": "high",
            },
            "review_agent": review,
        },
    )
    repo.create_task(project_id=project_id, key="mixed", title="Mixed")
    repo.create_ticket(project_id=project_id, task_key="mixed", key="work", title="Work")
    scheduler = QueuedScheduler()
    start(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key="mixed",
        agents={"delivery_agent": {"source_ref": ref}},
    )
    agents = (
        repo.get(project_id=project_id, task_key="mixed")
        .tasks[0]
        .metadata_json["delivery"]["agents"]
    )
    assert agents["review_agent"] == review
    assert agents["delivery_agent"]["model"] == "gpt-6-astra"
    assert agents["delivery_agent"]["provider"] == "custom-provider"
    assert agents["delivery_agent"]["native_settings"] == {"model_reasoning_effort": "high"}
    assert len(scheduler.jobs) == 1


def test_partial_adoption_preserves_unrelated_unavailable_sources(session, project_id, tmp_path):
    ref = source(session, project_id, tmp_path, "builder")
    selected = {
        "delivery_agent": {
            "source_ref": ref,
            "expected_digest": ProjectAgentService(tmp_path).get(ref).digest,
        }
    }
    missing = {
        "host_key": "codex",
        "native_name": "elsewhere",
        "source_path": ".codex/agents/elsewhere.toml",
    }
    unrelated = {"source_ref": missing, "provider": None}
    original = {
        "agents": {"unrelated": unrelated},
        "review_agent": None,
        "delivery_agent": {"name": "builder", "instructions": "Old", "model": None},
    }
    repo = TrackerRepository(session)
    repo.configure_delivery(project_id=project_id, delivery=original)
    preview = repo.adopt_delivery_sources(
        project_id=project_id, project_dir=str(tmp_path), sources=selected, preview=True
    )
    assert set(preview["adoption"]["agents"]) == {"delivery_agent"}
    assert repo.delivery_defaults(project_id=project_id) == original
    result = repo.adopt_delivery_sources(
        project_id=project_id, project_dir=str(tmp_path), sources=selected, preview=False
    )
    saved = repo.delivery_defaults(project_id=project_id)
    assert saved == preview["adoption"]["delivery"] == result["config_json"]["delivery"]
    assert saved["agents"]["unrelated"] == unrelated
    assert saved["review_agent"] is None


@pytest.mark.parametrize("problem", ["missing", "ambiguous", "stale"])
def test_selected_adoption_source_failure_preserves_all_defaults(
    session, project_id, tmp_path, problem
):
    from stackos.repositories.base import ConflictError

    ref = source(session, project_id, tmp_path, "builder")
    digest = ProjectAgentService(tmp_path).get(ref).digest
    path = tmp_path / ref["source_path"]
    if problem == "missing":
        path.unlink()
    elif problem == "ambiguous":
        (path.parent / "duplicate.toml").write_bytes(path.read_bytes())
    else:
        path.write_text(path.read_text() + "\n# changed\n")
    original = {"delivery_agent": None, "review_agent": None}
    repo = TrackerRepository(session)
    repo.configure_delivery(project_id=project_id, delivery=original)
    with pytest.raises((ValidationError, ConflictError)):
        repo.adopt_delivery_sources(
            project_id=project_id,
            project_dir=str(tmp_path),
            preview=False,
            sources={"delivery_agent": {"source_ref": ref, "expected_digest": digest}},
        )
    assert repo.delivery_defaults(project_id=project_id) == original


@pytest.mark.parametrize("role_value", [42, "invalid"])
def test_invalid_saved_role_adoption_is_a_structured_error(
    session, project_id, tmp_path, role_value
):
    ref = source(session, project_id, tmp_path, "builder")
    repo = TrackerRepository(session)
    original = {"delivery_agent": role_value}
    repo.configure_delivery(project_id=project_id, delivery=original)
    with pytest.raises(ValidationError, match="must be an object"):
        repo.adopt_delivery_sources(
            project_id=project_id,
            project_dir=str(tmp_path),
            preview=True,
            sources={
                "delivery_agent": {
                    "source_ref": ref,
                    "expected_digest": ProjectAgentService(tmp_path).get(ref).digest,
                }
            },
        )
    assert repo.delivery_defaults(project_id=project_id) == original


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
    # Resolve the fixture blocker before requeueing its saved session.
    for status in ("in-progress", "not-started"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="work",
            patch_json={"status": status, "blocker_reason": None},
        )
    start(session, project_id, tmp_path, scheduler, task_key="resume", agents=roles)
    current = (
        repo.get(project_id=project_id, task_key="resume")
        .tasks[0]
        .metadata_json["delivery"]["agents"]["delivery_agent"]
    )
    assert current["instructions"] == "Future"
    assert current["native_settings"] == {"model_reasoning_effort": "low"}
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


def legacy_admission(session, project_id, root):
    """Persist the pre-source-adoption handoff and native identity shape."""
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="legacy", title="Admitted before upgrade")
    repo.create_ticket(project_id=project_id, task_key="legacy", key="existing", title="Existing")
    repo.create_ticket(
        project_id=project_id,
        task_key="legacy",
        key="pending",
        title="Pending",
        dependency_keys=["existing"],
    )
    task = repo.get(project_id=project_id, task_key="legacy").tasks[0]
    roles = {
        "delivery_agent": {
            "name": "writer",
            "instructions": "Original writer",
            "host_key": "codex",
            "model": "gpt-6.1-sol",
        },
        "review_agent": {
            "name": "reviewer",
            "instructions": "Original reviewer",
            "host_key": "codex",
            "model": "gpt-6-astra",
        },
    }
    state = {
        "status": "stopped",
        "handoff_id": "prior-admission",
        "job_ref": f"task-delivery:{project_id}:{task.id}:prior-admission",
        "host_key": "codex",
        "project_dir": str(root),
        "agent_assignments": {},
        **roles,
    }
    sessions = {
        phase: {
            "agent": agent["name"],
            "host_key": "codex",
            "project_dir": str(root),
            "model": agent["model"],
            "thread_id": f"original-{phase}",
        }
        for phase, agent in (
            ("delivery", roles["delivery_agent"]),
            ("review", roles["review_agent"]),
        )
    }
    repo.update_task(
        project_id=project_id,
        task_key="legacy",
        patch_json={"metadata_json": {"keep": "yes", "delivery": state}},
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="existing",
        patch_json={
            "completion_evidence_json": {
                "automatic_delivery": {"sessions": sessions, "repair_count": 2}
            },
        },
    )
    return repo, state, sessions


@pytest.mark.parametrize("mapped_snapshot", [False, True])
@pytest.mark.parametrize("explicit_same_selection", [False, True])
def test_upgrade_rehandoff_retains_legacy_task_and_native_sessions(
    session, project_id, tmp_path, monkeypatch, mapped_snapshot, explicit_same_selection
):
    repo, prior, sessions = legacy_admission(session, project_id, tmp_path)
    if mapped_snapshot:
        prior["agents"] = {role: prior[role] for role in ("delivery_agent", "review_agent")}
        repo.update_task(
            project_id=project_id,
            task_key="legacy",
            patch_json={
                "metadata_json": {"keep": "yes", "delivery": prior},
            },
        )
    repo.configure_delivery(
        project_id=project_id,
        delivery={
            "host_key": "claude-code",
            "delivery_agent": {
                "name": "future",
                "instructions": "Future defaults",
                "model": "claude-sonnet-5-5",
            },
        },
    )
    calls = []

    def launch(host, directory, instructions, prompt, schema, **kwargs):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append(
            (
                key,
                phase,
                host,
                instructions.split("\n\n", 1)[0],
                kwargs.get("model"),
                kwargs.get("resume_thread_id"),
            )
        )
        assert "native_settings" not in kwargs
        return AgentRunResult(
            0, thread_id=kwargs.get("resume_thread_id") or f"{key}-{phase}", result=generic_result()
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    selection = (
        {role: prior[role] for role in ("delivery_agent", "review_agent")}
        if explicit_same_selection
        else {}
    )
    start(session, project_id, tmp_path, scheduler, task_key="legacy", **selection)
    admitted = repo.get(project_id=project_id, task_key="legacy").tasks[0].metadata_json
    assert admitted["keep"] == "yes"
    assert admitted["delivery"]["agents"] == {
        role: prior[role] for role in ("delivery_agent", "review_agent")
    }
    scheduler.run()
    assert calls == [
        ("existing", "delivery", "codex", "Original writer", "gpt-6.1-sol", "original-delivery"),
        ("existing", "review", "codex", "Original reviewer", "gpt-6-astra", "original-review"),
        ("pending", "delivery", "codex", "Original writer", "gpt-6.1-sol", None),
        ("pending", "review", "codex", "Original reviewer", "gpt-6-astra", None),
    ]
    session.expire_all()
    final = repo.brief(
        project_id=project_id, ticket_key="existing"
    ).ticket.completion_evidence_json["automatic_delivery"]
    assert final["sessions"] == sessions
    assert final["repair_count"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"host_key": "claude-code"},
        {"delivery_agent": {"model": "gpt-6-astra"}},
        {"delivery_agent": {"instructions": "Changed"}},
        {"agents": {"extra": {"name": "extra", "instructions": "Extra"}}},
        {
            "agents": {"delivery_agent": {"model": "gpt-6-astra"}},
            "delivery_agent": {"model": "gpt-6.1-sol"},
        },
        {"agent_assignments": {"existing": "review_agent"}},
    ],
)
def test_legacy_rehandoff_rejects_changed_selection(session, project_id, tmp_path, changes):
    legacy_admission(session, project_id, tmp_path)
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="retain"):
        start(session, project_id, tmp_path, scheduler, task_key="legacy", **changes)
    assert not scheduler.jobs


def test_legacy_rehandoff_still_preflights_host(session, project_id, tmp_path, monkeypatch):
    legacy_admission(session, project_id, tmp_path)
    monkeypatch.setattr(
        task_delivery, "agent_selection_error", lambda *args, **kwargs: "Host unavailable"
    )
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="Host unavailable"):
        start(session, project_id, tmp_path, scheduler, task_key="legacy")
    assert not scheduler.jobs


@pytest.mark.parametrize("change", ["directory", "mcp", "job_identity"])
def test_legacy_rehandoff_preserves_scope_guards(session, project_id, tmp_path, change):
    repo, prior, _ = legacy_admission(session, project_id, tmp_path)
    args = {}
    root = tmp_path
    if change == "directory":
        root = tmp_path / "different"
        root.mkdir()
    elif change == "mcp":
        args["mcp_config_ref"] = {"path": str(tmp_path / "different.toml"), "sha256": "a" * 64}
    else:
        prior["job_ref"] = "another-task-job"
        repo.update_task(
            project_id=project_id,
            task_key="legacy",
            patch_json={"metadata_json": {"delivery": prior}},
        )
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError):
        start(session, project_id, root, scheduler, task_key="legacy", **args)
    assert not scheduler.jobs
