"""Optional shared fixtures for integration tests."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from stackos import logging as stackos_logging
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


@pytest.fixture
def emitted_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Use the daemon's actual processor chain and file sink, with isolated output."""
    root = logging.getLogger()
    previous_handlers, previous_level = list(root.handlers), root.level
    previous_config = structlog.get_config()
    monkeypatch.setattr(stackos_logging, "_configured", False)
    path = tmp_path / "diagnostics" / "daemon.log"
    stackos_logging.configure_logging(log_path=path)
    configured_handlers = list(root.handlers)
    try:
        yield path
    finally:
        for handler in configured_handlers:
            root.removeHandler(handler)
            handler.close()
        root.handlers = previous_handlers
        root.setLevel(previous_level)
        structlog.configure(**previous_config)
