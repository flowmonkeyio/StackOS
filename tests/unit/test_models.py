"""Unit tests for StackOS model enum and state-machine surface."""

from __future__ import annotations

from stackos.db.models import (
    ACTION_CALL_STATUS_TRANSITIONS,
    AGENT_REQUEST_STATUS_TRANSITIONS,
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    ActionCallStatus,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    ApprovalRequestStatus,
    PluginSource,
    RunKind,
    RunPlanStatus,
    RunPlanStepStatus,
    RunStatus,
    RunStepStatus,
    TrackerItemStatus,
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


def test_tracker_item_status_values_match_tasks_and_tickets() -> None:
    assert _values(TrackerItemStatus) == {
        "not-started",
        "in-progress",
        "complete",
        "deferred",
        "aborted",
        "failed",
        "skipped",
    }


def test_approval_request_status_values() -> None:
    assert _values(ApprovalRequestStatus) == {"pending", "approved", "rejected", "cancelled"}


def test_action_call_status_values() -> None:
    assert _values(ActionCallStatus) == {"dry-run", "running", "success", "failed"}


def test_agent_request_status_values() -> None:
    assert _values(AgentRequestStatus) == {
        "new",
        "claimed",
        "run-created",
        "run-started",
        "responded",
        "resolved",
        "ignored",
        "failed",
    }


def test_agent_request_attention_status_values() -> None:
    assert _values(AgentRequestAttentionStatus) == {"unread", "read", "archived"}


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
    for src, dests in AGENT_REQUEST_STATUS_TRANSITIONS.items():
        assert isinstance(src, AgentRequestStatus)
        for dest in dests:
            assert isinstance(dest, AgentRequestStatus)


def test_terminal_states_are_terminal() -> None:
    assert RUN_STATUS_TRANSITIONS[RunStatus.SUCCESS] == frozenset()
    assert RUN_PLAN_STATUS_TRANSITIONS[RunPlanStatus.COMPLETED] == frozenset()
    assert RUN_PLAN_STATUS_TRANSITIONS[RunPlanStatus.FAILED] == frozenset()
    assert ACTION_CALL_STATUS_TRANSITIONS[ActionCallStatus.RUNNING] == frozenset(
        {ActionCallStatus.SUCCESS, ActionCallStatus.FAILED}
    )
    assert ACTION_CALL_STATUS_TRANSITIONS[ActionCallStatus.SUCCESS] == frozenset()
    assert AGENT_REQUEST_STATUS_TRANSITIONS[AgentRequestStatus.RESOLVED] == frozenset()
    assert AGENT_REQUEST_STATUS_TRANSITIONS[AgentRequestStatus.IGNORED] == frozenset()
    assert AGENT_REQUEST_STATUS_TRANSITIONS[AgentRequestStatus.FAILED] == frozenset()
