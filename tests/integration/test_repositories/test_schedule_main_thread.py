"""Schedule setup delegates workflow decisions to a native project main agent."""

from types import SimpleNamespace

import pytest
from sqlmodel import select

from stackos.db.models import AgentRequest, RunPlan, TrackerTask
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository, ScheduledJobRepository
from stackos.repositories.workspaces import WorkspaceRepository


def bind_folder(session, project_id, root):
    binding = (
        WorkspaceRepository(session)
        .connect(project_id=project_id, repo_fingerprint=f"path:{root}", last_known_root=str(root))
        .data
    )
    ProjectRepository(session).update(project_id, primary_workspace_binding_id=binding.id)


@pytest.fixture
def native_ready(monkeypatch):
    monkeypatch.setattr(
        "stackos.host_mcp.service.inspect_host",
        lambda *_a, **_k: SimpleNamespace(available=True, blocking=False, message="Ready"),
    )


@pytest.mark.parametrize("workflow", [None, "core.project-memory-review"])
def test_goal_and_optional_workflow_need_no_roles_or_business_inputs(
    session, project_id, tmp_path, native_ready, workflow
):
    bind_folder(session, project_id, tmp_path)
    saved = (
        ScheduledJobRepository(session)
        .set(
            project_id=project_id,
            kind="main",
            name="Review",
            goal="Review project context",
            template_key=workflow,
            inputs_json={"context_ref": {"$secret_ref": "secret_fixture_1234567890"}},
            cron_expr="0 9 * * mon",
            timezone="UTC",
            enabled=True,
        )
        .data
    )
    assert saved.goal == "Review project context" and saved.enabled
    assert saved.execution_json is None
    assert saved.setup_issue is None
    for model in (AgentRequest, RunPlan, TrackerTask):
        assert session.exec(select(model)).all() == []


@pytest.mark.parametrize("gap", ["folder", "goal", "host", "workflow", "saved-folder"])
def test_enabled_setup_gaps_are_actionable_without_creating_work(
    session, project_id, tmp_path, native_ready, monkeypatch, gap
):
    if gap != "folder":
        bind_folder(session, project_id, tmp_path)
    config = dict(
        project_id=project_id,
        kind="gap",
        name="Gap",
        goal="Benign goal",
        cron_expr="0 9 * * *",
        timezone="UTC",
        enabled=True,
    )
    if gap == "goal":
        config["goal"] = None
    elif gap == "workflow":
        config["template_key"] = "missing.workflow"
    elif gap == "saved-folder":
        config["execution_json"] = {"project_dir": str(tmp_path / "old")}
    elif gap == "host":
        monkeypatch.setattr(
            "stackos.host_mcp.service.inspect_host",
            lambda *_a, **_k: SimpleNamespace(available=False, message="Install native host"),
        )
    with pytest.raises((ValidationError, NotFoundError)):
        ScheduledJobRepository(session).set(**config)
    for model in (AgentRequest, RunPlan, TrackerTask):
        assert session.exec(select(model)).all() == []
