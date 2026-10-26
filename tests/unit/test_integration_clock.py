"""Virtual provider time must not contaminate later tests using real time."""

import asyncio

from stackos_connectors.shared import rate_limit as connector_rate_limit

from stackos.integrations import _rate_limit as host_rate_limit
from tests.helpers.integration_clock import isolated_integration_clock


def test_integration_clock_resets_buckets_and_restores_time_and_wait(monkeypatch) -> None:
    real_sleep = asyncio.sleep
    real_host_time = host_rate_limit.time
    real_connector_time = connector_rate_limit.time
    before = host_rate_limit.get_bucket(project_id=1, kind="clock-isolation", qps=1)
    assert before.try_acquire(1)

    try:
        with isolated_integration_clock(monkeypatch) as clock:
            during = host_rate_limit.get_bucket(project_id=1, kind="clock-isolation", qps=1)
            assert during is not before
            assert during.last == 0
            assert during.tokens == 1

            async def drain_and_refill() -> None:
                await during.acquire()
                await during.acquire()

            asyncio.run(drain_and_refill())
            assert clock.sleeps == [1]
            assert during.last == 1
            # Fractional refill rounding must never turn a positive wait into
            # a busy loop at the same virtual timestamp.
            asyncio.run(clock.sleep(1e-20))
            assert clock.monotonic() > 1

        assert asyncio.sleep is real_sleep
        assert host_rate_limit.time is real_host_time
        assert connector_rate_limit.time is real_connector_time
        after = host_rate_limit.get_bucket(project_id=1, kind="clock-isolation", qps=1)
        assert after is not during
        assert after.tokens == 1
        assert after.last != clock.now
    finally:
        host_rate_limit.reset_buckets()
