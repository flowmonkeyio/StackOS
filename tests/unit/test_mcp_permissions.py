"""Unit tests for the per-skill tool-grant matrix."""

from __future__ import annotations

import pytest

from content_stack.mcp.errors import ToolNotGrantedError
from content_stack.mcp.permissions import (
    SKILL_TOOL_GRANTS,
    SYSTEM_SKILL,
    TEST_SKILL,
    check_grant,
    is_full_grant,
    resolve_run_token,
)

# ---------------------------------------------------------------------------
# is_full_grant + sentinels.
# ---------------------------------------------------------------------------


def test_system_skill_is_not_full_grant() -> None:
    """``__system__`` is explicit, not an unrestricted escape hatch."""
    assert not is_full_grant(SYSTEM_SKILL)


def test_test_skill_is_full_grant() -> None:
    """``__test__`` is a full-grant sentinel."""
    assert is_full_grant(TEST_SKILL)


def test_real_skill_is_not_full_grant() -> None:
    """Real skill names are not full-grant."""
    assert not is_full_grant("_test_editor")
    assert not is_full_grant("outline")


# ---------------------------------------------------------------------------
# check_grant.
# ---------------------------------------------------------------------------


def test_check_grant_for_system_skill_covers_agent_owned_operations() -> None:
    """System calls can operate product state because the browser UI is observer-only."""
    check_grant("run.start", SYSTEM_SKILL)
    check_grant("project.create", SYSTEM_SKILL)
    check_grant("article.markPublished", SYSTEM_SKILL)
    check_grant("auth.status", SYSTEM_SKILL)
    check_grant("auth.test", SYSTEM_SKILL)
    check_grant("context.query", SYSTEM_SKILL)
    check_grant("learning.query", SYSTEM_SKILL)
    check_grant("experiment.query", SYSTEM_SKILL)
    check_grant("decision.query", SYSTEM_SKILL)
    check_grant("workflowTemplate.list", SYSTEM_SKILL)
    check_grant("workflowTemplate.describe", SYSTEM_SKILL)
    check_grant("workflowTemplate.validate", SYSTEM_SKILL)
    check_grant("runPlan.create", SYSTEM_SKILL)
    check_grant("runPlan.validate", SYSTEM_SKILL)
    check_grant("runPlan.start", SYSTEM_SKILL)
    check_grant("runPlan.get", SYSTEM_SKILL)
    check_grant("runPlan.list", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("learning.create", SYSTEM_SKILL)
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
        check_grant("experiment.recordDecision", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("decision.record", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("gscOauth.start", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("integration.set", SYSTEM_SKILL)
    with pytest.raises(ToolNotGrantedError):
        check_grant("dataforseo.serp", SYSTEM_SKILL)


def test_check_grant_passes_for_test_skill() -> None:
    """Test sentinel can call any tool."""
    check_grant("article.markPublished", TEST_SKILL)


def test_check_grant_passes_for_allowed_tool() -> None:
    """A skill can call a tool in its allow-list."""
    # _test_editor's allow-list includes article.setEdited.
    check_grant("article.setEdited", "_test_editor")
    check_grant("runPlan.claimStep", "stackos/run-plan-controller")
    check_grant("runPlan.recordStep", "stackos/run-plan-controller")


def test_check_grant_raises_for_forbidden_tool() -> None:
    """A skill cannot call a tool outside its allow-list."""
    with pytest.raises(ToolNotGrantedError) as exc_info:
        check_grant("article.markPublished", "_test_editor")
    assert exc_info.value.code == -32007
    assert exc_info.value.data["tool"] == "article.markPublished"
    assert exc_info.value.data["skill"] == "_test_editor"


def test_check_grant_raises_for_unknown_skill() -> None:
    """Unknown (non-sentinel) skills have empty grants → forbidden."""
    with pytest.raises(ToolNotGrantedError):
        check_grant("article.get", "totally-unknown-skill")


# ---------------------------------------------------------------------------
# resolve_run_token (in-memory; full integration in test_mcp_runs.py).
# ---------------------------------------------------------------------------


def test_resolve_run_token_returns_system_for_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token → (None, '__system__')."""
    # We don't need a real session; resolve_run_token short-circuits on None.
    run, skill = resolve_run_token(None, session=None)  # type: ignore[arg-type]
    assert run is None
    assert skill == SYSTEM_SKILL


def test_resolve_run_token_returns_system_for_empty() -> None:
    """Empty-string token → (None, '__system__')."""
    run, skill = resolve_run_token("", session=None)  # type: ignore[arg-type]
    assert run is None
    assert skill == SYSTEM_SKILL


# ---------------------------------------------------------------------------
# Matrix shape.
# ---------------------------------------------------------------------------


def test_matrix_has_test_skills_for_grant_tests() -> None:
    """The four test skills the integration tests reference are in the matrix."""
    for k in ("_test_keyword_discovery", "_test_editor", "_test_eeat_gate", "_test_publisher"):
        assert k in SKILL_TOOL_GRANTS
        assert isinstance(SKILL_TOOL_GRANTS[k], frozenset)


def test_matrix_test_skills_are_narrow() -> None:
    """Test skills allow ≤ 10 tools; reflects M3 constraint."""
    for k in ("_test_keyword_discovery", "_test_editor", "_test_eeat_gate", "_test_publisher"):
        assert 1 <= len(SKILL_TOOL_GRANTS[k]) <= 10
