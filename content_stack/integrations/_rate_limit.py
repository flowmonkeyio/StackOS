"""Token-bucket rate limiter for per-integration QPS caps (PLAN.md L1037-L1041).

Each integration declares a default qps (DataForSEO 5, Firecrawl 2,
OpenAI Images 10, and so on); the ``IntegrationBudgets.qps`` column overrides
per project. The bucket capacity equals the qps so a brief burst of N
calls is allowed but sustained throughput is rate-limited.

The buckets are *process-level* — the daemon runs single-process so we
don't need cross-process coordination. Keys are
``(project_id_or_None, kind)`` so a "global" credential and a
project-scoped credential maintain independent buckets even when their
``kind`` matches.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Classic token bucket: refill rate ``qps`` tokens/sec, capacity ``qps``.

    ``acquire(n=1)`` blocks asynchronously until ``n`` tokens are
    available; the wait is computed deterministically rather than
    polling, so we don't burn CPU during the wait. Tests use
    ``acquire_now`` to peek without sleeping.
    """

    qps: float
    capacity: float
    tokens: float
    last: float

    @classmethod
    def for_qps(cls, qps: float, *, capacity: float | None = None) -> TokenBucket:
        """Build a bucket with sensible defaults."""
        cap = capacity if capacity is not None else max(1.0, qps)
        return cls(qps=qps, capacity=cap, tokens=cap, last=time.monotonic())

    def _refill(self, *, now: float | None = None) -> None:
        """Add tokens earned since the last call."""
        ts = now if now is not None else time.monotonic()
        elapsed = ts - self.last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.qps)
            self.last = ts

    def try_acquire(self, n: float = 1.0, *, now: float | None = None) -> bool:
        """Take ``n`` tokens if available; return ``False`` if not.

        Used by tests + the doctor probe. Production code calls
        ``acquire`` which awaits.
        """
        self._refill(now=now)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    async def acquire(self, n: float = 1.0) -> None:
        """Block asynchronously until ``n`` tokens are available."""
        if n <= 0:
            return
        if n > self.capacity:
            # Either the caller asked for more than the bucket can ever
            # hold (programmer error) or qps was set absurdly low. We
            # defer to the underlying integration to surface the failure
            # rather than swallowing it here.
            raise ValueError(f"requested {n} tokens but capacity is {self.capacity}")
        while True:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                return
            deficit = n - self.tokens
            wait = deficit / self.qps if self.qps > 0 else 1.0
            # 50ms minimum so we don't busy-loop if qps is huge.
            await asyncio.sleep(max(wait, 0.05))


# ---------------------------------------------------------------------------
# Process-level registry.
# ---------------------------------------------------------------------------


_buckets: dict[tuple[int | None, str], TokenBucket] = {}


def get_bucket(
    *,
    project_id: int | None,
    kind: str,
    qps: float,
) -> TokenBucket:
    """Look up (or create) the token bucket for ``(project_id, kind)``.

    The first call wins on ``qps`` — a subsequent call with a different
    qps for the same key updates the rate but keeps the existing token
    balance so we don't re-burst on every cap change.
    """
    key = (project_id, kind)
    bucket = _buckets.get(key)
    if bucket is None:
        bucket = TokenBucket.for_qps(qps)
        _buckets[key] = bucket
        return bucket
    if bucket.qps != qps:
        bucket.qps = qps
        bucket.capacity = max(bucket.capacity, qps)
    return bucket


def reset_buckets() -> None:
    """Drop every bucket (test helper — do not call from production paths)."""
    _buckets.clear()


__all__ = [
    "TokenBucket",
    "get_bucket",
    "reset_buckets",
]
