"""Unit tests for ``stackos.repositories.base``."""

from __future__ import annotations

import pytest

from stackos.db.models import (
    RUN_STATUS_TRANSITIONS,
    RunStatus,
)
from stackos.repositories.base import (
    ConflictError,
    Envelope,
    Page,
    ValidationError,
    _normalise_limit,
    validate_transition,
)


def test_normalise_limit_default() -> None:
    assert _normalise_limit(None) == 50


def test_normalise_limit_clamps_max() -> None:
    with pytest.raises(ValidationError):
        _normalise_limit(201)


def test_normalise_limit_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        _normalise_limit(0)


def test_validate_transition_legal() -> None:
    validate_transition(
        RunStatus.RUNNING,
        RunStatus.SUCCESS,
        RUN_STATUS_TRANSITIONS,
        label="run.status",
    )


def test_validate_transition_illegal_raises() -> None:
    with pytest.raises(ConflictError) as exc_info:
        validate_transition(
            RunStatus.SUCCESS,
            RunStatus.RUNNING,
            RUN_STATUS_TRANSITIONS,
            label="run.status",
        )
    assert exc_info.value.code == -32008
    assert exc_info.value.data["current"] == "success"
    assert exc_info.value.data["attempted"] == "running"
    assert exc_info.value.data["allowed"] == []


def test_envelope_roundtrip() -> None:
    """Envelope is generic and serialises predictably."""
    env = Envelope[int](data=42, project_id=1, run_id=99)
    dump = env.model_dump()
    assert dump == {"data": 42, "run_id": 99, "project_id": 1}


def test_page_default() -> None:
    p = Page[str](items=["a", "b"], next_cursor=10, total_estimate=100)
    assert p.next_cursor == 10
    assert p.total_estimate == 100
