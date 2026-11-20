"""Test-database isolation must preserve real pacing within one test lifetime."""

import asyncio
from types import SimpleNamespace

import pytest

from stackos.integrations import _rate_limit as host_rate_limit
from tests.helpers.rate_limit_isolation import isolated_host_rate_limits


def test_rate_isolation_resets_between_contexts_but_preserves_real_waits(monkeypatch) -> None:
    real_sleep = asyncio.sleep
    real_time = host_rate_limit.time
    requested_waits: list[float] = []

    async def observed_real_sleep(delay: float) -> None:
        requested_waits.append(delay)
        await real_sleep(delay)

    monkeypatch.setattr(host_rate_limit, "asyncio", SimpleNamespace(sleep=observed_real_sleep))
    try:
        with pytest.raises(RuntimeError, match="test lifetime ended"), isolated_host_rate_limits():
            first = host_rate_limit.get_bucket(project_id=1, kind="isolated-app", qps=20)
            same = host_rate_limit.get_bucket(project_id=1, kind="isolated-app", qps=20)
            assert same is first

            async def drain_and_wait() -> None:
                assert first.try_acquire(20)
                assert same.tokens == 0
                await same.acquire()

            asyncio.run(drain_and_wait())
            assert requested_waits
            assert all(delay >= 0.05 for delay in requested_waits)
            assert asyncio.sleep is real_sleep
            assert host_rate_limit.time is real_time
            raise RuntimeError("test lifetime ended")

        with isolated_host_rate_limits():
            second = host_rate_limit.get_bucket(project_id=1, kind="isolated-app", qps=20)
            assert second is not first
            assert second.tokens == second.capacity == 20

        after = host_rate_limit.get_bucket(project_id=1, kind="isolated-app", qps=20)
        assert after is not second
        assert after.tokens == 20
    finally:
        host_rate_limit.reset_buckets()
