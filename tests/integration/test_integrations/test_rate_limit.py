"""Token bucket math + process-level registry."""

from __future__ import annotations

import asyncio

import pytest
from stackos_connectors.shared.rate_limit import TokenBucket as ConnectorTokenBucket

from stackos.integrations._rate_limit import (
    TokenBucket,
    get_bucket,
    reset_buckets,
)


def test_try_acquire_takes_one_token() -> None:
    bucket = TokenBucket.for_qps(5)
    # Initial capacity equals qps.
    assert bucket.try_acquire(1) is True
    assert bucket.try_acquire(1) is True


def test_try_acquire_refuses_past_capacity() -> None:
    bucket = TokenBucket.for_qps(2)
    assert bucket.try_acquire(2) is True
    # Now empty; a second call without time advancing fails.
    assert bucket.try_acquire(1, now=bucket.last) is False


def test_refill_grants_tokens_over_time() -> None:
    bucket = TokenBucket.for_qps(2)  # capacity 2
    # Drain.
    bucket.try_acquire(2, now=bucket.last)
    # Advance 0.5s — refill = 0.5 * 2 = 1 token.
    base = bucket.last
    assert bucket.try_acquire(1, now=base + 0.5) is True


def test_get_bucket_caches_per_key() -> None:
    reset_buckets()
    a = get_bucket(project_id=1, kind="firecrawl", qps=2)
    b = get_bucket(project_id=1, kind="firecrawl", qps=2)
    assert a is b


def test_get_bucket_distinct_per_project_kind() -> None:
    reset_buckets()
    a = get_bucket(project_id=1, kind="firecrawl", qps=2)
    b = get_bucket(project_id=2, kind="firecrawl", qps=2)
    c = get_bucket(project_id=1, kind="dataforseo", qps=2)
    assert a is not b
    assert a is not c
    assert b is not c


def test_async_acquire_does_not_block_when_capacity_available() -> None:
    """``acquire`` returns immediately when tokens are available."""
    reset_buckets()
    bucket = get_bucket(project_id=1, kind="firecrawl", qps=10)

    async def take_three() -> None:
        for _ in range(3):
            await bucket.acquire(1)

    asyncio.run(take_three())


@pytest.mark.parametrize("bucket_type", [TokenBucket, ConnectorTokenBucket])
@pytest.mark.parametrize("qps", [2, 20])
def test_async_acquire_paces_when_drained(bucket_type, qps, integration_clock) -> None:
    """Assert the actual requested wait and refill, without scheduler jitter."""
    reset_buckets()
    bucket = bucket_type.for_qps(qps)

    async def drain_and_wait() -> None:
        await bucket.acquire(qps)
        assert integration_clock.sleeps == []
        await bucket.acquire(1)
        assert integration_clock.sleeps == [1 / qps]
        assert bucket.tokens == pytest.approx(0)
        assert bucket.last == pytest.approx(1 / qps)

    asyncio.run(drain_and_wait())
