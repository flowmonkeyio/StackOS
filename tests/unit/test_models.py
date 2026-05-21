"""Unit tests for StackOS model enum and state-machine surface."""

from __future__ import annotations

from content_stack.db.models import (
    ACTION_CALL_STATUS_TRANSITIONS,
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    ActionCallStatus,
    ApprovalRequestStatus,
    PluginSource,
    RunKind,
    RunPlanStatus,
    RunPlanStepStatus,
    RunStatus,
    RunStepStatus,
)


def _values(enum_cls: type) -> set[str]:
    return {m.value for m in enum_cls}


def test_run_kind_values() -> None:
    assert _values(RunKind) == {
        "run-plan",
        "skill-run",
        "action",
        "scheduled-job",
        "maintenance",
    }


def test_run_status_values() -> None:
    assert _values(RunStatus) == {"running", "success", "failed", "aborted"}


def test_run_step_status_values() -> None:
    assert _values(RunStepStatus) == {"pending", "running", "success", "failed", "skipped"}


def test_run_plan_status_values() -> None:
    assert _values(RunPlanStatus) == {"draft", "started", "completed", "failed", "aborted"}


def test_run_plan_step_status_values() -> None:
    assert _values(RunPlanStepStatus) == {
        "pending",
        "running",
        "success",
        "failed",
        "skipped",
        "blocked",
    }


def test_approval_request_status_values() -> None:
    assert _values(ApprovalRequestStatus) == {"pending", "approved", "rejected", "cancelled"}


def test_action_call_status_values() -> None:
    assert _values(ActionCallStatus) == {"dry-run", "success", "failed"}


def test_plugin_source_values() -> None:
    assert _values(PluginSource) == {"builtin", "repo", "project", "user"}


def test_status_machine_legal_transitions_only_reference_defined_statuses() -> None:
    for src, dests in RUN_STATUS_TRANSITIONS.items():
        assert isinstance(src, RunStatus)
        for dest in dests:
            assert isinstance(dest, RunStatus)
    for src, dests in RUN_PLAN_STATUS_TRANSITIONS.items():
        assert isinstance(src, RunPlanStatus)
        for dest in dests:
            assert isinstance(dest, RunPlanStatus)
    for src, dests in RUN_PLAN_STEP_STATUS_TRANSITIONS.items():
        assert isinstance(src, RunPlanStepStatus)
        for dest in dests:
            assert isinstance(dest, RunPlanStepStatus)
    for src, dests in APPROVAL_REQUEST_STATUS_TRANSITIONS.items():
        assert isinstance(src, ApprovalRequestStatus)
        for dest in dests:
            assert isinstance(dest, ApprovalRequestStatus)
    for src, dests in ACTION_CALL_STATUS_TRANSITIONS.items():
        assert isinstance(src, ActionCallStatus)
        for dest in dests:
            assert isinstance(dest, ActionCallStatus)


def test_terminal_states_are_terminal() -> None:
    assert RUN_STATUS_TRANSITIONS[RunStatus.SUCCESS] == frozenset()
    assert RUN_PLAN_STATUS_TRANSITIONS[RunPlanStatus.COMPLETED] == frozenset()
    assert RUN_PLAN_STATUS_TRANSITIONS[RunPlanStatus.FAILED] == frozenset()
    assert ACTION_CALL_STATUS_TRANSITIONS[ActionCallStatus.SUCCESS] == frozenset()
