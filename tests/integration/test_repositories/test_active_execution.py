"""Real host-service lifetimes projected through tracker reads and its existing SSE feed."""

from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos.context import ContextRepository
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.events import record_execution_change


@pytest.mark.parametrize("exit_kind", ["success", "error", "exception", "cancel"])
def test_actual_call_identity_transitions_and_cleanup(
    session, project_id, monkeypatch, tmp_path, exit_kind
):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="live", title="Live")
    repo.create_ticket(project_id=project_id, task_key="live", key="child", title="Child")
    repo.create_ticket(project_id=project_id, task_key="live", key="parent", title="Parent")
    repo.update_ticket(
        project_id=project_id, ticket_key="child", patch_json={"status": "in-progress"}
    )
    context = ContextRepository(session)
    cursor = context.latest_tracker_status_event_id(project_id=project_id)
    assert repo.brief(project_id=project_id, ticket_key="child").ticket.active_execution is None
    seen = []

    def runner(*args, **kwargs):
        brief = repo.brief(project_id=project_id, ticket_key="child")
        current = brief.ticket.active_execution
        assert current is not None
        assert brief.task.active_execution == current
        index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
        assert next(task for task in index.tasks if task.key == "live").active_execution == current
        full = repo.get(project_id=project_id, task_key="live", include_graph=True)
        assert (
            next(ticket for ticket in full.tickets if ticket.key == "child").active_execution
            == current
        )
        assert (
            next(ticket for ticket in full.tickets if ticket.key == "parent").active_execution
            is None
        )
        assert service.active_execution(project_id + 100, "live") is None
        assert service.active_execution(project_id, "other") is None
        assert brief.ticket.status == "in-progress"
        seen.append(current)
        if exit_kind == "exception":
            raise RuntimeError("native failed")
        if exit_kind == "cancel":
            raise KeyboardInterrupt()
        return AgentRunResult(0 if exit_kind == "success" else 1, thread_id="same-native-session")

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: SimpleNamespace(run_agent=runner)
    )
    for name, model in [
        ("Project implementer", "model-v1"),
        ("Independent reviewer", "model-v2"),
        ("Project implementer", "model-v1"),
    ]:
        kwargs = dict(
            execution_scope=service.AgentExecutionScope(project_id, "live", "child", name),
            on_execution_change=lambda call, active: record_execution_change(session, call, active),
            model=model,
            resume_thread_id="same-native-session",
        )
        if exit_kind in {"exception", "cancel"}:
            with pytest.raises(RuntimeError if exit_kind == "exception" else KeyboardInterrupt):
                service.run_agent("fixture", tmp_path, "Instructions", "Prompt", {}, **kwargs)
        else:
            result = service.run_agent("fixture", tmp_path, "Instructions", "Prompt", {}, **kwargs)
            assert result.thread_id == "same-native-session"
        assert service.active_execution(project_id, "live") is None
        assert repo.brief(project_id=project_id, ticket_key="child").ticket.active_execution is None
    assert [value.agent for value in seen] == [
        "Project implementer",
        "Independent reviewer",
        "Project implementer",
    ]
    assert [value.model for value in seen] == ["model-v1", "model-v2", "model-v1"]
    assert len({value.invocation_id for value in seen}) == 3
    assert seen[0].started_at < seen[1].started_at < seen[2].started_at
    with Session(session.get_bind()) as reader:
        events = (
            ContextRepository(reader)
            .tracker_status_timeline(project_id=project_id, task_key="live", after_id=cursor)
            .items
        )
    assert [event.event_type for event in events] == ["tracker.execution.changed"] * 6
    assert [event.metadata_json["active"] for event in events] == [True, False] * 3
    assert (
        context.tracker_status_timeline(
            project_id=project_id, task_key="other", after_id=cursor
        ).items
        == []
    )
    assert (
        context.latest_tracker_status_event_id(project_id=project_id, task_key="live")
        == events[-1].id
    )
    assert service._active_invocations == {}


def test_invalid_launch_never_registers(monkeypatch, tmp_path):
    changes = []
    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: "Unsupported selection")
    result = service.run_agent(
        "fixture",
        tmp_path,
        "Instructions",
        "Prompt",
        {},
        execution_scope=service.AgentExecutionScope(1, "task", "ticket", "worker"),
        on_execution_change=lambda *args: changes.append(args),
    )
    assert result.error == "Unsupported selection"
    assert changes == []
    assert service.active_execution(1, "task") is None
