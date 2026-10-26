"""Keep process-owned host rate budgets local to each isolated test database."""

from collections.abc import Iterator
from contextlib import contextmanager

from stackos.integrations._rate_limit import reset_buckets


@contextmanager
def isolated_host_rate_limits() -> Iterator[None]:
    """Reset between test lifetimes; real clocks and within-test pacing remain."""
    reset_buckets()
    try:
        yield
    finally:
        reset_buckets()
