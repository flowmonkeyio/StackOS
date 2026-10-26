"""Optional shared fixtures for integration tests."""

from collections.abc import Iterator

import pytest

from tests.helpers.integration_clock import IntegrationClock, isolated_integration_clock


@pytest.fixture
def integration_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[IntegrationClock]:
    """Use virtual provider waits only in tests that explicitly request this fixture."""
    with isolated_integration_clock(monkeypatch) as clock:
        yield clock
