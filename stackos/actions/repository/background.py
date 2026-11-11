"""Process-local action lifetimes and background progress."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

from stackos.actions.connectors import ActionProgressCallback
from stackos.host_mcp.agent_execution import managed_launch_lock
from stackos.logging import get_logger

BackgroundWorker = Callable[[ActionProgressCallback], Coroutine[Any, Any, None]]


class BackgroundActionTasks:
    """Keep background action tasks alive and expose their live progress."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._progress: dict[int, dict[str, Any]] = {}
        self._foreground: dict[int, int] = {}

    @contextmanager
    def foreground(self, project_id: int, admit: Callable[[], None]) -> Iterator[None]:
        # Foreground/transient calls have no RUNNING row. Keep their actual
        # credential/provider/output lifetime visible without persisting inputs.
        with managed_launch_lock:
            admit()
            with self._lock:
                self._foreground[project_id] = self._foreground.get(project_id, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                remaining = self._foreground[project_id] - 1
                if remaining:
                    self._foreground[project_id] = remaining
                else:
                    self._foreground.pop(project_id)

    def foreground_active(self, project_id: int) -> bool:
        with self._lock:
            return self._foreground.get(project_id, 0) > 0

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
