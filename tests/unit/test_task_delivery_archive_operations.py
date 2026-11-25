"""The existing delivery operation exposes bounded, pinned candidate recovery."""

from copy import deepcopy

import pytest

from stackos.operations.registry import build_operation_registry
from stackos.operations.responses import shape_operation_response


def recovery_request():
    part = {"path": "proof/source.tar.gz", "sha256": "a" * 64, "root": "page", "exclude": []}
    return {
        "ticket_key": "page",
        "expected_attempt": 2,
        "expected_repair_count": 1,
        "expected_delivery_thread_id": "original-delivery",
        "expected_review_thread_id": "original-review",
        "expected_backend_commit_sha": "b" * 40,
        "expected_delivery_receipt_digest": "c" * 64,
        "candidate": {"kind": "archives", "source": part, "build": deepcopy(part)},
        "reason": "Correct mistaken Git enrollment for the standalone page",
    }


def test_candidate_recovery_is_shared_bounded_operation_contract():
    spec = build_operation_registry().get("tracker.startDelivery")
    arguments = {
        "project_id": 1,
        "task_key": "prepared",
        "project_dir": "/tmp/page",
        "reconcile_candidate": recovery_request(),
    }
    assert spec.surfaces.mcp.enabled and spec.surfaces.rest.enabled and spec.surfaces.cli.enabled
    assert spec.effective_response_policy.allowed_modes == ("compact", "raw")
    value = spec.input_model.model_validate(arguments)
    assert value.reconcile_candidate.model_dump() == arguments["reconcile_candidate"]
    for key, invalid in (
        ("expected_attempt", True),
        ("expected_repair_count", 0.5),
        ("expected_repair_count", 4),
        ("expected_delivery_receipt_digest", "stale"),
        ("unknown_override", True),
    ):
        modified = deepcopy(arguments)
        modified["reconcile_candidate"][key] = invalid
        with pytest.raises(ValueError):
            spec.input_model.model_validate(modified)
    for path in ("source", "build"):
        modified = deepcopy(arguments)
        modified["reconcile_candidate"]["candidate"][path]["skip_validation"] = True
        with pytest.raises(ValueError, match="Extra inputs"):
            spec.input_model.model_validate(modified)


def test_candidate_recovery_cannot_replace_reviewer_or_change_native_settings():
    spec = build_operation_registry().get("tracker.startDelivery")
    arguments = {
        "project_id": 1,
        "task_key": "prepared",
        "project_dir": "/tmp/page",
        "reconcile_candidate": recovery_request(),
    }
    replacement = {
        "ticket_key": "page",
        "expected_attempt": 2,
        "expected_review_thread_id": "original-review",
        "expected_candidate_commit_sha": "b" * 40,
        "expected_reviewer_snapshot_digest": "d" * 64,
        "reason": "Replace reviewer",
    }
    with pytest.raises(ValueError, match="mutually exclusive"):
        spec.input_model.model_validate({**arguments, "replace_review": replacement})
    for override in (
        {"host_key": "codex"},
        {"review_agent": {"name": "new-reviewer"}},
        {"delivery_agent": {"instructions": "New instructions"}},
        {"agents": {}},
        {"agent_assignments": {}},
    ):
        with pytest.raises(ValueError):
            spec.input_model.model_validate({**arguments, **override})


def test_candidate_recovery_compact_response_preserves_recovery_identity():
    spec = build_operation_registry().get("tracker.startDelivery")
    recovery = {
        "ticket_key": "page",
        "attempt": 2,
        "repair_count": 1,
        "candidate": recovery_request()["candidate"],
        "retained_review_thread_id": "original-review",
        "delivery_receipt_digest": "c" * 64,
    }
    payload = {
        "ok": True,
        "operation": "tracker.startDelivery",
        "status": "success",
        "project_id": 1,
        "run_id": None,
        "data": {"task_key": "prepared", "candidate_reconciliation": recovery},
    }
    shaped = shape_operation_response(spec, payload, response_mode="compact")
    assert shaped["data"]["candidate_reconciliation"] == recovery
    assert shape_operation_response(spec, payload, response_mode="raw") == payload
