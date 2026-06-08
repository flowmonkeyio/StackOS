"""Unit tests for the StackOS MCP tool-grant matrix."""

from __future__ import annotations

import pytest

from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.permissions import (
    RUN_PLAN_CONTROLLER_SKILL,
    SKILL_TOOL_GRANTS,
    SYSTEM_SKILL,
    TEST_SKILL,
    check_grant,
    is_full_grant,
    resolve_run_token,
)


def test_system_skill_is_not_full_grant() -> None:
    assert not is_full_grant(SYSTEM_SKILL)


def test_test_skill_is_full_grant() -> None:
    assert is_full_grant(TEST_SKILL)


def test_check_grant_for_system_skill_covers_bootstrap_setup_operations() -> None:
    check_grant("run.start", SYSTEM_SKILL)
    check_grant("project.create", SYSTEM_SKILL)
    check_grant("workspace.bootstrap", SYSTEM_SKILL)
    check_grant("workspace.connect", SYSTEM_SKILL)
    check_grant("auth.status", SYSTEM_SKILL)
    check_grant("auth.test", SYSTEM_SKILL)
    check_grant("action.run", SYSTEM_SKILL)
    check_grant("agentPreset.list", SYSTEM_SKILL)
    check_grant("agentPreset.describe", SYSTEM_SKILL)
    check_grant("agentPreset.resolveForWorkflow", SYSTEM_SKILL)
    check_grant("skillPreset.list", SYSTEM_SKILL)
    check_grant("skillPreset.describe", SYSTEM_SKILL)
    check_grant("skillPreset.resolveForWorkflow", SYSTEM_SKILL)
    check_grant("localAgentChat.createMessage", SYSTEM_SKILL)
    check_grant("communication.send", SYSTEM_SKILL)
    check_grant("communication.reply", SYSTEM_SKILL)
    check_grant("ingressEndpoint.configure", SYSTEM_SKILL)
    check_grant("ingressEndpoint.refresh", SYSTEM_SKILL)
    check_grant("ingressEndpoint.routes", SYSTEM_SKILL)
    check_grant("ingressEndpoint.sync", SYSTEM_SKILL)
    check_grant("ingressEndpoint.status", SYSTEM_SKILL)
    check_grant("agentRequest.list", SYSTEM_SKILL)
    check_grant("agentRequest.get", SYSTEM_SKILL)
    check_grant("agentRequest.claim", SYSTEM_SKILL)
    check_grant("agentRequest.release", SYSTEM_SKILL)
    check_grant("agentRequest.linkRunPlan", SYSTEM_SKILL)
    check_grant("agentRequest.prepareRunPlan", SYSTEM_SKILL)
    check_grant("agentRequest.complete", SYSTEM_SKILL)
    check_grant("agentRequest.ignore", SYSTEM_SKILL)
    check_grant("communicationProfile.list", SYSTEM_SKILL)
    check_grant("communicationProfile.get", SYSTEM_SKILL)
    check_grant("communicationProfile.upsert", SYSTEM_SKILL)
    check_grant("communicationSurface.list", SYSTEM_SKILL)
    check_grant("communicationSurface.upsert", SYSTEM_SKILL)
    check_grant("communicationContact.list", SYSTEM_SKILL)
    check_grant("communicationContact.upsert", SYSTEM_SKILL)
    check_grant("communicationMembership.list", SYSTEM_SKILL)
    check_grant("communicationMembership.upsert", SYSTEM_SKILL)
    check_grant("communicationTarget.list", SYSTEM_SKILL)
    check_grant("communicationTarget.resolve", SYSTEM_SKILL)
    check_grant("communicationTarget.upsert", SYSTEM_SKILL)
    check_grant("communicationRoute.list", SYSTEM_SKILL)
    check_grant("communicationRoute.upsert", SYSTEM_SKILL)
    check_grant("communicationContext.query", SYSTEM_SKILL)
    check_grant("communicationProfile.list", SYSTEM_SKILL)
    check_grant("communicationProfile.get", SYSTEM_SKILL)
    check_grant("communicationProfile.upsert", SYSTEM_SKILL)
    check_grant("toolProfile.resolve", SYSTEM_SKILL)
    check_grant("context.query", SYSTEM_SKILL)
    check_grant("learning.query", SYSTEM_SKILL)
    check_grant("experiment.query", SYSTEM_SKILL)
    check_grant("decision.query", SYSTEM_SKILL)
    check_grant("workflowExtension.list", SYSTEM_SKILL)
    check_grant("workflowExtension.get", SYSTEM_SKILL)
    check_grant("workflowExtension.delete", SYSTEM_SKILL)
    check_grant("workflowExtension.validate", SYSTEM_SKILL)
    check_grant("workflowExtension.upsert", SYSTEM_SKILL)
    check_grant("workflowTemplate.list", SYSTEM_SKILL)
    check_grant("workflowTemplate.describe", SYSTEM_SKILL)
    check_grant("workflowTemplate.validate", SYSTEM_SKILL)
    check_grant("runPlan.create", SYSTEM_SKILL)
    check_grant("runPlan.validate", SYSTEM_SKILL)
    check_grant("runPlan.start", SYSTEM_SKILL)
    check_grant("runPlan.abort", SYSTEM_SKILL)
    check_grant("runPlan.checkConsistency", SYSTEM_SKILL)
    check_grant("runPlan.get", SYSTEM_SKILL)
    check_grant("runPlan.list", SYSTEM_SKILL)
    check_grant("runPlan.reopen", SYSTEM_SKILL)
    check_grant("tracker.status", SYSTEM_SKILL)
    check_grant("tracker.get", SYSTEM_SKILL)
    check_grant("tracker.next", SYSTEM_SKILL)
    check_grant("tracker.brief", SYSTEM_SKILL)
    check_grant("tracker.verify", SYSTEM_SKILL)
    check_grant("tracker.createTask", SYSTEM_SKILL)
    check_grant("tracker.createTicket", SYSTEM_SKILL)
    check_grant("tracker.patch", SYSTEM_SKILL)
    check_grant("tracker.rejectTask", SYSTEM_SKILL)
    check_grant("tracker.reopen", SYSTEM_SKILL)

    with pytest.raises(ToolNotGrantedError):
        check_grant("workflowTemplate.save", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("workflowTemplate.fork", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("runPlan.claimStep", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("runPlan.recordStep", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("runPlan.update", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("resource.upsert", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("artifact.create", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("action.execute", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("agentRequest.create", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("integration.set", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("integration.test", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("learning.update", SYSTEM_SKILL)


def test_check_grant_passes_for_test_skill() -> None:
    check_grant("anything.atAll", TEST_SKILL)


def test_run_plan_controller_has_dynamic_step_tools() -> None:
    check_grant("runPlan.claimStep", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("runPlan.recordStep", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("action.execute", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("agentRequest.create", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("communication.send", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("communication.reply", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("tracker.brief", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("tracker.patch", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("tracker.rejectTask", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("tracker.reopen", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("resource.upsert", RUN_PLAN_CONTROLLER_SKILL)
    check_grant("context.query", RUN_PLAN_CONTROLLER_SKILL)


def test_check_grant_raises_for_unknown_skill() -> None:
    with pytest.raises(ToolNotGrantedError):
        check_grant("resource.query", "totally-unknown-skill")


def test_resolve_run_token_returns_system_for_none() -> None:
    run, skill = resolve_run_token(None, session=None)  # type: ignore[arg-type]
    assert run is None
    assert skill == SYSTEM_SKILL


def test_resolve_run_token_returns_system_for_empty() -> None:
    run, skill = resolve_run_token("", session=None)  # type: ignore[arg-type]
    assert run is None
    assert skill == SYSTEM_SKILL


def test_matrix_is_clean_stackos_surface() -> None:
    assert set(SKILL_TOOL_GRANTS) == {SYSTEM_SKILL, TEST_SKILL, RUN_PLAN_CONTROLLER_SKILL}
    system_tools = SKILL_TOOL_GRANTS[SYSTEM_SKILL]
    for old_tool in ("article.create", "topic.list", "gscOauth.start", "drift.snapshot"):
        assert old_tool not in system_tools
