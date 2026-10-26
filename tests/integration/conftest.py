"""Optional shared fixtures for integration tests."""

from collections.abc import Iterator

import pytest

from tests.helpers.integration_clock import IntegrationClock, isolated_integration_clock
from tests.helpers.rate_limit_isolation import isolated_host_rate_limits


@pytest.fixture(autouse=True)
def _isolated_host_rate_limits() -> Iterator[None]:
    """Fresh databases reuse project IDs, so their process-level budgets must be fresh too."""
    with isolated_host_rate_limits():
        yield


@pytest.fixture
def integration_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[IntegrationClock]:
    """Use virtual provider waits only in tests that explicitly request this fixture."""
    with isolated_integration_clock(monkeypatch) as clock:
        yield clock
