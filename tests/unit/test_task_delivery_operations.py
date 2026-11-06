from __future__ import annotations

import json
from copy import deepcopy

import pytest

from stackos.operations.registry import build_operation_registry
from stackos.operations.responses import resolve_response_mode, shape_operation_response


def test_delivery_operation_has_one_contract_and_no_ack():
    spec = build_operation_registry().get("tracker.startDelivery")
    assert spec.grant_policy == "direct-tracker-write"
    assert spec.mutating is True
    assert spec.surfaces.mcp.enabled and spec.surfaces.rest.enabled and spec.surfaces.cli.enabled
    assert spec.effective_response_policy.allowed_modes == ("compact", "raw")
    schema = spec.input_model.model_json_schema()
    assert {"delivery_agent", "review_agent", "host_key"}.isdisjoint(schema["required"])
    assert {"run_plan_id", "step_id"} <= schema["properties"].keys()
    with pytest.raises(ValueError):
        spec.input_model.model_validate(
            {
                "project_id": 1,
                "task_key": "one",
                "project_dir": "/tmp",
                "delivery_agent": {"name": "x", "instructions": ""},
                "review_agent": {"name": "y", "instructions": "Review"},
            }
        )


def test_partial_role_choices_and_local_admin_defaults_contract():
    registry = build_operation_registry()
    spec = registry.get("tracker.startDelivery")
    inp = spec.input_model.model_validate(
        {
            "project_id": 1,
            "task_key": "one",
            "project_dir": "/tmp",
            "review_agent": {"model": "gemini-3.1-pro-preview"},
        }
    )
    assert inp.delivery_agent is None
    assert inp.review_agent.model_dump(exclude_unset=True) == {"model": "gemini-3.1-pro-preview"}
    enable = registry.get("plugin.enable")
    assert enable.grant_policy == "local-admin-plugin-write"


@pytest.mark.parametrize("scope", [{"run_plan_id": 1}, {"step_id": "deliver"}])
def test_delivery_requires_paired_workflow_selector(scope):
    spec = build_operation_registry().get("tracker.startDelivery")
    with pytest.raises(ValueError, match="provided together"):
        spec.input_model.model_validate(
            {
                "project_id": 1,
                "task_key": "one",
                "project_dir": "/tmp",
                "delivery_agent": {"name": "x", "instructions": "Build"},
                "review_agent": {"name": "y", "instructions": "Review"},
                **scope,
            }
        )


def test_launch_response_preserves_inspection_fields_and_rejects_ack():
    spec = build_operation_registry().get("tracker.startDelivery")
    data = {
        "task_key": "one",
        "status": "queued",
        "ticket_key": None,
        "job_ref": "engineering-delivery:1:1",
        "run_plan_id": 12,
        "step_id": "deliver-tickets",
        "recommended_tool": "tracker.get",
        "message": "Inspect task_key with response_mode=raw.",
    }
    payload = {"data": data, "project_id": 1, "run_id": None}
    assert shape_operation_response(spec, payload, response_mode="compact")["data"] == data
    assert shape_operation_response(spec, payload, response_mode="raw") == payload
    with pytest.raises(Exception, match="response_mode"):
        resolve_response_mode(spec, {"response_mode": "ack"}, surface="mcp")


@pytest.mark.parametrize("decision", ["approve", "reject", "blocked"])
@pytest.mark.parametrize("followups", [[], [{"text": "Outside this ticket's scope"}]])
def test_review_result_accepts_scoped_decisions_and_separate_followups(decision, followups):
    from stackos.host_mcp.result import AgentRunResult, validate_agent_result
    from stackos.plugins.builtin_engineering import task_delivery

    result = AgentRunResult(
        0,
        thread_id="native-review",
        result={
            "decision": decision,
            "summary": "Focused checks and scoped findings",
            "commit_sha": "a" * 40,
            "followups": followups,
        },
    )
    assert validate_agent_result(result, task_delivery().REVIEW_SCHEMA, "codex").ok


@pytest.mark.parametrize(
    "followups", [None, "fix this", [{}], [{"text": ""}], [{"text": "note", "create_ticket": True}]]
)
def test_review_result_rejects_malformed_or_actionable_followup_fields(followups):
    from stackos.host_mcp.result import AgentRunResult, validate_agent_result
    from stackos.plugins.builtin_engineering import task_delivery

    result = AgentRunResult(
        0,
        thread_id="native-review",
        result={
            "decision": "approve",
            "summary": "Focused checks",
            "commit_sha": "a" * 40,
            "followups": followups,
        },
    )
    assert not validate_agent_result(result, task_delivery().REVIEW_SCHEMA, "codex").ok


@pytest.mark.parametrize("encoding", ["json", "repr", "nested-json"])
def test_work_prompt_excludes_serialized_notes_without_changing_canonical_diagnostics(encoding):
    from stackos.plugins.builtin_engineering import task_delivery

    result = {"decision": "reject", "followups": [{"text": "Unrelated deferred note"}]}
    serialized = repr(result) if encoding == "repr" else json.dumps(result)
    if encoding == "nested-json":
        serialized = json.dumps(serialized)
    canonical = {
        "attempts": [{"review": {"error": f"Invalid result: {serialized}"}}],
        "summary": "Required check unavailable",
        "followups": [{"text": "Unrelated deferred note"}],
    }
    original = deepcopy(canonical)
    prompt = task_delivery()._prompt_copy(canonical)
    assert "Unrelated deferred note" not in json.dumps(prompt)
    assert prompt["summary"] == "Required check unavailable"
    assert canonical == original
