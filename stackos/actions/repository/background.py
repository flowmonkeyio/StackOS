"""Process-local execution state for background actions."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any

from stackos.actions.connectors import ActionProgressCallback
from stackos.logging import get_logger

BackgroundWorker = Callable[[ActionProgressCallback], Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class CommandBinding:
    project_id: int
    action_call_id: int
    attempt_run_id: int
    controller_run_id: int
    run_plan_id: int
    run_plan_step_id: int
    canonical_item_id: str
    owner_ref: str
    epoch: int
    revision: int
    scope_digest: str
    source_revision: str


@dataclass
class CommandSlot:
    """Private original objects, independent of requester/task completion."""

    binding: CommandBinding
    started: float
    deadline: float
    work_deadline: float
    resources: dict[str, Any] = field(default_factory=dict, repr=False)
    tasks: list[asyncio.Task[Any]] = field(default_factory=list, repr=False)
    result: dict[str, Any] = field(default_factory=lambda: {"status": "unknown"})
    usage: dict[str, Any] = field(default_factory=lambda: {"total_tokens": None})
    effects: str = "unknown"
    cli_exit: int | None = None
    reaped: bool = False
    fds_closed: bool = False
    descendants: str = "unknown"
    remote: str = "unknown"
    cancellation_at: float | None = None
    failure: str | None = None
    last_sequence: int = -1
    acquired: bool = False
    admitted: bool = False
    birth_unknown: bool = False

    def adopt(self, name: str, resource: Any, *, custody: bool = True) -> None:
        """Called at birth, before another acquisition/await; never replace identity."""
        if name in self.resources:
            raise ValueError("original resource already retained")
        self.resources[name] = resource
        self.acquired = self.acquired or custody

    def observe(self, sequence: int, event: dict[str, Any]) -> None:
        if sequence <= self.last_sequence:
            return
        self.last_sequence = sequence
        if event.get("type") == "permission.required":
            self.failure = "unsupported-permission"
            raise RuntimeError("correlated permission transport is not supported")
        # A late observation cannot erase completed useful output or earlier usage.
        if (
            event.get("result", {}).get("status") == "completed"
            and self.result.get("status") != "completed"
        ):
            self.result = dict(event["result"])
        if "usage" in event:
            for key, value in event["usage"].items():
                if type(value) is int and value >= 0:
                    self.usage[key] = max(self.usage.get(key) or 0, value)


CommandWork = Callable[[CommandSlot], Coroutine[Any, Any, None]]
CommandCleanup = Callable[[CommandSlot, float], Coroutine[Any, Any, None]]


class BackgroundActionTasks:
    """Keep background action tasks alive and expose their live progress."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._progress: dict[int, dict[str, Any]] = {}
        self._commands: dict[tuple[int, int], CommandSlot] = {}
        self._incarnation = uuid.uuid4().hex
        self._accepting = True

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

    def command_slot(self, binding: CommandBinding, *, clock=time.monotonic) -> CommandSlot:
        if not self._accepting:
            raise RuntimeError("daemon command owner is stopping")
        key = (binding.action_call_id, binding.attempt_run_id)
        existing = self._commands.get(key)
        if existing is not None:
            if existing.binding != binding:
                raise ValueError("original command binding changed")
            return existing
        if any(
            slot.binding.project_id == binding.project_id
            and slot.binding.canonical_item_id == binding.canonical_item_id
            for slot in self._commands.values()
        ):
            raise RuntimeError("original attempt holds capacity; explicit clearance required")
        start = clock()
        slot = CommandSlot(binding, start, start + 90.0, start + 78.0)
        self._commands[key] = slot
        return slot

    def activate(self) -> None:
        if self._commands:
            raise RuntimeError("retained commands require explicit accounting; no reset")
        self._accepting = True
        self._incarnation = uuid.uuid4().hex

    def receipt(self, slot: CommandSlot, *, clock=time.monotonic) -> dict[str, Any]:
        held = (
            self._commands.get((slot.binding.action_call_id, slot.binding.attempt_run_id)) is slot
        )
        ack = held and self._accepting and slot.acquired
        local_clean = slot.reaped and slot.fds_closed
        return {
            "binding": asdict(slot.binding),
            "result": dict(slot.result),
            "effects": {"status": slot.effects},
            "cancellation": {
                "requested": slot.cancellation_at is not None,
                "requested_at": slot.cancellation_at,
            },
            "cli": {"exit_code": slot.cli_exit, "reaped": slot.reaped},
            "cleanup": {
                "status": "confirmed" if local_clean and not slot.birth_unknown else "unknown",
                "birth_unknown": slot.birth_unknown,
            },
            "custody": {
                "acknowledged": ack,
                "daemon_incarnation": self._incarnation if ack else None,
                "retained_resource_names": sorted(slot.resources),
                "survives_daemon_loss": False,
            },
            "termination": {"descendants": slot.descendants, "remote": slot.remote},
            "clock": {
                "started": slot.started,
                "deadline": slot.deadline,
                "work_deadline": slot.work_deadline,
                "elapsed": clock() - slot.started,
                "cleanup_reserve_seconds": 12,
            },
            "usage": {**slot.usage, "allowance_tokens": 16000, "hard_cap": False},
            "attempts_consumed": 1 if slot.admitted else 0,
            "failure": slot.failure,
            "next_action": "needs-operator" if slot.failure == "unsupported-permission" else None,
            "retry_safe": False,
            "capacity_reusable": False,
            "quiescent": False,
        }

    async def command(
        self,
        slot: CommandSlot,
        *,
        prepare: CommandWork,
        authorize: Callable[[], None],
        acquire: Callable[[CommandSlot], None],
        work: CommandWork,
        cleanup: CommandCleanup,
        clock=time.monotonic,
    ) -> dict[str, Any]:
        if slot.tasks:
            await asyncio.shield(slot.tasks[0])
            return self.receipt(slot, clock=clock)

        async def bounded(awaitable: Coroutine[Any, Any, None], until: float) -> bool:
            task = asyncio.create_task(awaitable)
            slot.tasks.append(task)
            done, _ = await asyncio.wait({task}, timeout=max(0.0, until - clock()))
            if task not in done:
                return False
            task.result()
            return True

        async def run() -> dict[str, Any]:
            try:
                if not await bounded(prepare(slot), slot.work_deadline):
                    raise TimeoutError("preparation exceeded original work allowance")
                if not self._accepting or clock() >= slot.work_deadline:
                    raise TimeoutError("original work allowance expired")
                authorize()
                slot.admitted = True
                if clock() >= slot.work_deadline:
                    raise TimeoutError("canonical admission consumed original work allowance")
                # No await between fresh canonical admission and retained acquisition.
                acquire(slot)
                if not await bounded(work(slot), slot.work_deadline):
                    slot.cancellation_at = clock()
                    slot.failure = "work-deadline"
            except BaseException as exc:
                slot.failure = slot.failure or type(exc).__name__
            finally:
                # Cancelling a coroutine is not child/FD termination evidence.
                for task in slot.tasks[1:]:
                    if not task.done():
                        slot.cancellation_at = slot.cancellation_at or clock()
                        task.cancel()
                if slot.acquired:
                    try:
                        if not await bounded(cleanup(slot, slot.deadline), slot.deadline):
                            slot.failure = "cleanup-unknown"
                    except BaseException:
                        slot.failure = "cleanup-unknown"
            return self.receipt(slot, clock=clock)

        task = asyncio.create_task(
            run(), name=f"stackos-owned-command-{slot.binding.action_call_id}"
        )
        slot.tasks.append(task)
        # Requester cancellation never cancels this owner task or discards its slot.
        await asyncio.shield(task)
        return self.receipt(slot, clock=clock)

    async def shutdown(self, *, clock=time.monotonic) -> dict[str, Any]:
        self._accepting = False
        for slot in self._commands.values():
            if slot.tasks and not slot.tasks[0].done():
                slot.cancellation_at = clock()
                slot.tasks[0].cancel()
        tasks = {slot.tasks[0] for slot in self._commands.values() if slot.tasks}
        if tasks:
            until = max(slot.deadline for slot in self._commands.values())
            await asyncio.wait(tasks, timeout=max(0.0, until - clock()))
        receipts = [self.receipt(slot, clock=clock) for slot in self._commands.values()]
        clean = all(
            receipt["cleanup"]["status"] == "confirmed"
            and receipt["termination"] == {"descendants": "confirmed", "remote": "confirmed"}
            for receipt in receipts
        )
        return {"clean": clean, "commands": receipts}


BACKGROUND_ACTION_TASKS = BackgroundActionTasks()


__all__ = ["BACKGROUND_ACTION_TASKS", "BackgroundActionTasks"]
