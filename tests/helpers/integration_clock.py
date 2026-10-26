"""Deterministic elapsed time for tests with mocked provider transports."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


class IntegrationClock:
    """Advance connector time when it waits, while still yielding to asyncio."""

    def __init__(self, real_sleep: Callable[[float], Awaitable[None]]) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self._real_sleep = real_sleep

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        advanced = self.now + max(seconds, 0.0)
        # A fractional token remainder can request less than one float ULP.
        # Positive waits must still advance time or the real limiter can spin.
        self.now = (
            math.nextafter(self.now, math.inf) if seconds > 0 and advanced == self.now else advanced
        )
        await self._real_sleep(0)


@contextmanager
def isolated_integration_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[IntegrationClock]:
    """Keep retry and token refill on one clock without leaking bucket state."""
    from stackos_connectors.shared import rate_limit as connector_rate_limit

    from stackos.integrations import _rate_limit as host_rate_limit

    modules = (connector_rate_limit, host_rate_limit)
    # Native buckets are created per integration instance; only the host owns
    # a process-level registry that can retain timestamps between tests.
    host_rate_limit.reset_buckets()
    clock = IntegrationClock(asyncio.sleep)
    with monkeypatch.context() as patch:
        for module in modules:
            patch.setattr(module, "time", SimpleNamespace(monotonic=clock.monotonic))
        # Patch the wait, not the event loop's clock. Real scheduling still
        # occurs through sleep(0); token refill sees the elapsed virtual wait.
        patch.setattr(asyncio, "sleep", clock.sleep)
        try:
            yield clock
        finally:
            host_rate_limit.reset_buckets()
