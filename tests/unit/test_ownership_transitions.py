"""Accepted supervision transition contract; observations never imply lifecycle."""

import pytest

from stackos.db.model_enums import (
    RUN_ATTEMPT_DISPOSITION_STATUS,
    RUN_OBSERVATION_TRANSITIONS,
    RunAttemptDisposition,
    RunObservationState,
    RunStatus,
)
from stackos.repositories.base import ConflictError, validate_transition

# Independent contract cases from supervision spec0.7's observation table.
EXPECTED = {
    "running": {
        "waiting",
        "suspected_stall",
        "confirmed_hang",
        "host_suspended",
        "blocked",
        "scope_drift",
        "review_ready",
    },
    "waiting": {"running", "blocked", "host_suspended"},
    "suspected_stall": {
        "running",
        "confirmed_hang",
        "waiting",
        "blocked",
        "host_suspended",
        "review_ready",
    },
    "confirmed_hang": {"blocked"},
    "host_suspended": {"running", "waiting", "suspected_stall", "blocked", "confirmed_hang"},
    "blocked": {"running", "waiting"},
    "scope_drift": {"running", "waiting"},
    "review_ready": {"accepted_completion", "blocked", "scope_drift"},
    "accepted_completion": set(),
}


@pytest.mark.parametrize("previous", list(RunObservationState))
@pytest.mark.parametrize("next_state", list(RunObservationState))
def test_observation_contract_pairs(previous, next_state) -> None:
    if next_state.value in EXPECTED[previous.value]:
        validate_transition(previous, next_state, RUN_OBSERVATION_TRANSITIONS, label="observation")
    else:
        with pytest.raises(ConflictError):
            validate_transition(
                previous, next_state, RUN_OBSERVATION_TRANSITIONS, label="observation"
            )


def test_terminal_reasons_use_existing_run_status_without_acceptance_aliases() -> None:
    assert set(RUN_ATTEMPT_DISPOSITION_STATUS) == set(RunAttemptDisposition)
    assert RUN_ATTEMPT_DISPOSITION_STATUS[RunAttemptDisposition.SUCCEEDED] == RunStatus.SUCCESS
    assert RUN_ATTEMPT_DISPOSITION_STATUS[RunAttemptDisposition.FAILED] == RunStatus.FAILED
    assert all(
        RUN_ATTEMPT_DISPOSITION_STATUS[value] == RunStatus.ABORTED
        for value in (
            RunAttemptDisposition.CANCELLED,
            RunAttemptDisposition.ABANDONED,
            RunAttemptDisposition.SUPERSEDED,
        )
    )
