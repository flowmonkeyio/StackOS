"""Token bucket math + process-level registry."""

from __future__ import annotations

import asyncio

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


def test_async_acquire_paces_when_drained() -> None:
    """When the bucket is drained, ``acquire`` waits for refill."""
    reset_buckets()
    bucket = TokenBucket.for_qps(20)  # 20 qps so 50ms between tokens

    async def drain_and_wait() -> None:
        # Drain instantly.
        for _ in range(20):
            await bucket.acquire(1)
        # The 21st call must wait for ~50ms (1 token / 20 qps).
        loop = asyncio.get_running_loop()
        before = loop.time()
        await bucket.acquire(1)
        after = loop.time()
        # Allow some slack for scheduler jitter.
        assert (after - before) >= 0.04

    asyncio.run(drain_and_wait())
