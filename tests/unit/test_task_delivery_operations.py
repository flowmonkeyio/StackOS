from __future__ import annotations

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
    assert "delivery_agent" in schema["required"] and "review_agent" in schema["required"]
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


def test_launch_response_preserves_inspection_fields_and_rejects_ack():
    spec = build_operation_registry().get("tracker.startDelivery")
    data = {
        "task_key": "one",
        "status": "queued",
        "ticket_key": None,
        "job_ref": "engineering-delivery:1:1",
        "recommended_tool": "tracker.get",
        "message": "Inspect task_key with response_mode=raw.",
    }
    payload = {"data": data, "project_id": 1, "run_id": None}
    assert shape_operation_response(spec, payload, response_mode="compact")["data"] == data
    assert shape_operation_response(spec, payload, response_mode="raw") == payload
    with pytest.raises(Exception, match="response_mode"):
        resolve_response_mode(spec, {"response_mode": "ack"}, surface="mcp")
