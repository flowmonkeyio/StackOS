"""Virtual provider time must not contaminate later tests using real time."""

import asyncio

import httpx
from stackos_connectors.shared import base as connector_base
from stackos_connectors.shared import rate_limit as connector_rate_limit

from stackos.integrations import _rate_limit as host_rate_limit
from tests.helpers.integration_clock import isolated_integration_clock, isolated_provider_clock


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


def test_provider_clock_keeps_app_sleep_real_and_restores_module_references(monkeypatch) -> None:
    real_sleep = asyncio.sleep
    modules = (host_rate_limit, connector_rate_limit, connector_base)
    real_asyncio = [module.asyncio for module in modules]
    real_times = (host_rate_limit.time, connector_rate_limit.time)
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, json={"ok": True})

    with isolated_provider_clock(monkeypatch) as clock:
        bucket = host_rate_limit.get_bucket(project_id=1, kind="scoped-clock", qps=1)

        async def execute() -> None:
            await bucket.acquire()
            await bucket.acquire()
            assert clock.sleeps == [1]
            assert asyncio.sleep is real_sleep
            await asyncio.sleep(0)
            assert clock.monotonic() == 1

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                native = connector_base.BaseIntegration(payload=b"", http=http)
                result = await native.call(
                    op="clock-proof", url="https://test.invalid", max_retries=1
                )
            assert result.data == {"ok": True}

        asyncio.run(execute())
        assert len(requests) == 2
        # The real retry and native refill algorithms share virtual elapsed time.
        assert clock.sleeps == [1, 0.5, 0.5]
        assert clock.monotonic() == 2

    assert asyncio.sleep is real_sleep
    assert [module.asyncio for module in modules] == real_asyncio
    assert (host_rate_limit.time, connector_rate_limit.time) == real_times
    try:
        fresh = host_rate_limit.get_bucket(project_id=1, kind="scoped-clock", qps=1)
        assert fresh is not bucket
        assert fresh.tokens == 1
    finally:
        host_rate_limit.reset_buckets()
