from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from stackos.operations.tracker.read_handlers import tracker_get
from stackos.operations.tracker.schemas import TrackerGetInput
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
