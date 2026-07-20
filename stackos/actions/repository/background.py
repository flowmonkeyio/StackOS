"""Process-local execution state for background actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from threading import Lock
from typing import Any

from stackos.actions.connectors import ActionProgressCallback
from stackos.logging import get_logger

BackgroundWorker = Callable[[ActionProgressCallback], Coroutine[Any, Any, None]]


class BackgroundActionTasks:
    """Keep background action tasks alive and expose their live progress."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._progress: dict[int, dict[str, Any]] = {}

    def start(self, action_call_id: int, worker: BackgroundWorker) -> None:
        with self._lock:
            self._progress[action_call_id] = {"phase": "accepted"}

        def report(progress: dict[str, Any]) -> None:
            with self._lock:
                self._progress[action_call_id] = dict(progress)

        task: asyncio.Task[None] = asyncio.create_task(
            worker(report),
            name=f"stackos-action-{action_call_id}",
        )
        with self._lock:
            self._tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            with self._lock:
                self._tasks.discard(done)
                self._progress.pop(action_call_id, None)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                get_logger("stackos.actions.background").exception(
                    "action.background.failed",
                    action_call_id=action_call_id,
                )

        task.add_done_callback(completed)

    def progress(self, action_call_id: int) -> dict[str, Any] | None:
        with self._lock:
            progress = self._progress.get(action_call_id)
            return dict(progress) if progress is not None else None


BACKGROUND_ACTION_TASKS = BackgroundActionTasks()


__all__ = ["BACKGROUND_ACTION_TASKS", "BackgroundActionTasks"]
