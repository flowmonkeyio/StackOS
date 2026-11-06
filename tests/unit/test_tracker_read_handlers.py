from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from stackos.operations.tracker.read_handlers import (
    tracker_agent_stats,
    tracker_agent_stats_all,
    tracker_get,
)
from stackos.operations.tracker.schemas import (
    TrackerAgentStatsAllInput,
    TrackerAgentStatsInput,
    TrackerGetInput,
)
from stackos.repositories.tracker import TrackerRepository


def test_tracker_get_handler_keeps_the_event_loop_responsive(monkeypatch) -> None:
    expected = object()

    def slow_get(_repository: TrackerRepository, **_arguments):
        time.sleep(0.05)
        return expected

    monkeypatch.setattr(TrackerRepository, "get", slow_get)

    async def exercise() -> None:
        task = asyncio.create_task(
            tracker_get(
                TrackerGetInput(project_id=1, include_graph=False),
                SimpleNamespace(session=object()),  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
            )
        )
        await asyncio.sleep(0.005)
        assert not task.done()
        assert await task is expected

    asyncio.run(exercise())


@pytest.mark.parametrize("global_scope", [False, True])
def test_agent_stats_handlers_keep_the_event_loop_responsive(monkeypatch, global_scope):
    expected = object()

    def slow_read(_repository, **arguments):
        assert arguments == ({"is_active": None} if global_scope else {"project_id": 1})
        time.sleep(0.05)
        return expected

    monkeypatch.setattr(
        TrackerRepository, "agent_stats_all" if global_scope else "agent_stats", slow_read
    )

    async def exercise():
        handler = tracker_agent_stats_all if global_scope else tracker_agent_stats
        inp = (
            TrackerAgentStatsAllInput(is_active=None)
            if global_scope
            else TrackerAgentStatsInput(project_id=1)
        )
        task = asyncio.create_task(handler(inp, SimpleNamespace(session=object()), None))
        await asyncio.sleep(0.005)
        assert not task.done()
        assert await task is expected

    asyncio.run(exercise())
