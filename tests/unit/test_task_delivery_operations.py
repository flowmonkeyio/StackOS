from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from stackos.operations.registry import build_operation_registry
from stackos.operations.responses import resolve_response_mode, shape_operation_response


@pytest.mark.parametrize(
    "extras", [{}, {"surface": "mcp", "trusted_local_admin": True}, {"surface": "rest"}]
)
def test_defaults_handler_requires_local_admin_rest(extras):
    from stackos.operations.tracker.schemas import TrackerConfigureDeliveryInput
    from stackos.operations.tracker.write_handlers import tracker_configure_delivery
    from stackos.repositories.base import ValidationError

    with pytest.raises(ValidationError, match="local-admin"):
        asyncio.run(
            tracker_configure_delivery(
                TrackerConfigureDeliveryInput(project_id=1, delivery={}),
                SimpleNamespace(extras=extras),
                None,
            )
        )


def test_delivery_operation_has_one_contract_and_no_ack():
    spec = build_operation_registry().get("tracker.startDelivery")
    assert spec.grant_policy == "direct-tracker-write"
    assert spec.mutating is True
    assert spec.surfaces.mcp.enabled and spec.surfaces.rest.enabled and spec.surfaces.cli.enabled
    assert spec.effective_response_policy.allowed_modes == ("compact", "raw")
    schema = spec.input_model.model_json_schema()
    assert {"delivery_agent", "review_agent", "host_key"}.isdisjoint(schema["required"])
    assert {"run_plan_id", "step_id"} <= schema["properties"].keys()
    assert "agent_assignments" in schema["properties"]
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


def test_assignment_map_accepts_arbitrary_safe_role_keys():
    spec = build_operation_registry().get("tracker.startDelivery")
    arguments = {
        "project_id": 1,
        "task_key": "prepared",
        "project_dir": "/tmp",
        "agent_assignments": {"workflow-42-check": "review_agent"},
    }
    assert (
        spec.input_model.model_validate(arguments).agent_assignments
        == arguments["agent_assignments"]
    )
    assert spec.input_model.model_validate(
        {**arguments, "agent_assignments": {"workflow-42-check": "new_phase_role"}}
    ).agent_assignments == {"workflow-42-check": "new_phase_role"}
    with pytest.raises(ValueError):
        spec.input_model.model_validate(
            {**arguments, "agent_assignments": {"workflow-42-check": "../../unsafe"}}
        )


def test_native_route_is_a_bounded_task_only_reference():
    registry = build_operation_registry()
    start = registry.get("tracker.startDelivery").input_model
    arguments = {"project_id": 1, "task_key": "task", "project_dir": "/tmp"}
    ref = {"path": "/tmp/routes.toml", "sha256": "a" * 64}
    assert (
        start.model_validate({**arguments, "mcp_config_ref": ref}).mcp_config_ref.model_dump()
        == ref
    )
    for invalid in [
        {**ref, "settings": {"approval_policy": "never"}},
        {**ref, "sha256": "wrong"},
        {**ref, "path": 1},
    ]:
        with pytest.raises(ValueError):
            start.model_validate({**arguments, "mcp_config_ref": invalid})
    with pytest.raises(ValueError):
        registry.get("tracker.configureDelivery").input_model.model_validate(
            {"project_id": 1, "delivery": {"mcp_config_ref": ref}}
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
    configure = registry.get("tracker.configureDelivery")
    assert configure.grant_policy == "local-admin-console-write"
    assert configure.surfaces.rest.enabled and configure.surfaces.rest.browser_safe
    assert not configure.surfaces.mcp.enabled and not configure.surfaces.cli.enabled
    assert configure.effective_response_policy.allowed_modes == ("raw",)
    assert not configure.effective_response_policy.ack_safe
    with pytest.raises(ValueError):
        configure.input_model.model_validate(
            {"project_id": 1, "delivery": {"unrelated_tracker_setting": True}}
        )


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
        "job_ref": "task-delivery:1:1",
        "run_plan_id": 12,
        "step_id": "verify-delivery",
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
    from stackos import task_delivery
    from stackos.host_mcp.result import AgentRunResult, validate_agent_result

    result = AgentRunResult(
        0,
        thread_id="native-review",
        result={
            "status": {"approve": "success", "reject": "repair", "blocked": "blocked"}[decision],
            "summary": "Focused checks and scoped findings",
            "outputs": json.dumps({"commit_sha": "a" * 40}),
            "followups": followups,
        },
    )
    assert validate_agent_result(result, task_delivery.RESULT_SCHEMA, "codex").ok


@pytest.mark.parametrize(
    "followups", [None, "fix this", [{}], [{"text": ""}], [{"text": "note", "create_ticket": True}]]
)
def test_review_result_rejects_malformed_or_actionable_followup_fields(followups):
    from stackos import task_delivery
    from stackos.host_mcp.result import AgentRunResult, validate_agent_result

    result = AgentRunResult(
        0,
        thread_id="native-review",
        result={
            "status": "success",
            "summary": "Focused checks",
            "outputs": json.dumps({"commit_sha": "a" * 40}),
            "followups": followups,
        },
    )
    assert not validate_agent_result(result, task_delivery.RESULT_SCHEMA, "codex").ok


@pytest.mark.parametrize("encoding", ["json", "repr", "nested-json"])
def test_work_prompt_excludes_serialized_notes_without_changing_canonical_diagnostics(encoding):
    from stackos import task_delivery

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
    prompt = task_delivery._prompt_copy(canonical)
    assert "Unrelated deferred note" not in json.dumps(prompt)
    assert prompt["summary"] == "Required check unavailable"
    assert canonical == original
