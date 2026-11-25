"""Real exchange observations reach the same Work read and shared feed projections.

Supabase HTTP is substituted by the established connected fixture; no native
agent or production connection is launched by this projection test.
"""

from sqlmodel import Session

from stackos.host_mcp import service
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.agent_stats import usage_refresh
from tests.integration.test_routes.test_local_runtime_exchange import offer

pytest_plugins = ["tests.integration.test_routes.test_local_runtime_exchange"]


def test_exchange_to_work_and_feed_without_tokens_or_lifecycle_mutation(connected, monkeypatch):
    client, _, identity = connected
    exchange, connection, invocation, _, handoff = offer(connected)
    project_id = identity["project_id"]
    monkeypatch.setitem(service._active_invocations, invocation.invocation_id, invocation)
    clock = [100.0]
    monkeypatch.setattr("stackos.local_runtime.exchange.time.monotonic", lambda: clock[0])
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        repo.create_task(project_id=project_id, key="task", title="Synthetic Work")
        repo.create_ticket(project_id=project_id, task_key="task", key="ticket", title="Build")
        before = repo.get(project_id=project_id, task_key="task")
        exchange.poll(connection, invocation.invocation_id)
        previous = None
        for state in ("connected", "disconnected", "needs_attention"):
            if state == "disconnected":
                clock[0] += 31
            elif state == "needs_attention":
                exchange.poll(connection, None)
            executions = service.usage_executions(project_id)
            assert executions != previous  # Existing stream change detector sees it.
            previous = executions
            feed = usage_refresh(session, project_id, executions)
            snapshot = repo.get(project_id=project_id, task_key="task")
            for collection in ("tasks", "tickets"):
                active = getattr(snapshot, collection)[0].active_execution
                assert active.connection_state == state
                assert active.live_usage is None
                assert not active.stop_requested
                assert feed[collection][0]["active_execution"]["connection_state"] == state
                assert (
                    getattr(snapshot, collection)[0].status == getattr(before, collection)[0].status
                )
            assert not invocation.cancelled.is_set() and not handoff.ready.is_set()
        invocation.cancelled.set()
        stopped = usage_refresh(session, project_id, executions)["tasks"][0]["active_execution"]
        assert stopped["stop_requested"]
        invocation.finished = True
        assert repo.get(project_id=project_id, task_key="task").tasks[0].active_execution is None
