"""Existing contract checks for the engineering tracked-delivery template."""

from __future__ import annotations

from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from stackos.workflows.template_schema import validate_workflow_template_obj

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / "plugins/engineering/workflows/tracked-delivery.yaml"
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _schema(workflow: dict, key: str) -> dict:
    return next(item["schema"] for item in workflow["outputs"] if item["key"] == key)


def test_tracked_delivery_v07_has_four_role_sessions_and_observed_handoff() -> None:
    workflow = _workflow()
    validation = validate_workflow_template_obj(workflow)
    assert validation.valid, validation.errors
    assert workflow["key"] == "engineering.tracked-delivery"
    assert workflow["version"] == "0.7.0"
    assert [step["id"] for step in workflow["steps"]] == [
        "plan-task",
        "implement-task",
        "review-task",
        "write-tests",
        "handoff",
    ]
    assert len(workflow["policies"]) == 6
    roles = [item["role"] for item in workflow["agent_requirements"]]
    assert roles == ["planner", "executor", "reviewer", "test-writer"]
    assert len(set(roles)) == 4
    assert workflow["steps"][3]["depends_on"] == ["review-task"]
    assert "observed" in workflow["steps"][-1]["title"].lower()


def test_tracked_delivery_keeps_intent_and_actual_behavior_explicit() -> None:
    workflow = _workflow()
    steps = {step["id"]: " ".join(step["instructions"]) for step in workflow["steps"]}
    assert "original task" in steps["plan-task"].lower()
    assert "Do not write tests" in steps["implement-task"]
    assert "actual changed code" in steps["review-task"]
    assert "rejection blocks test writing" in steps["review-task"]
    assert "after the review" in steps["write-tests"]
    assert "actual integrated behavior" in steps["handoff"]


def test_role_receipts_require_complete_execution_and_captured_code() -> None:
    workflow = _workflow()
    plan = Draft202012Validator(_schema(workflow, "plan_receipt"))
    implementation = Draft202012Validator(_schema(workflow, "implementation_receipt"))
    complete_plan = {
        "thread_id": "thread-plan",
        "model": "gpt-6-astra",
        "status": "complete",
        "prompt": "Plan the request",
        "started_at": "start",
        "ended_at": "end",
        "duration_seconds": 1,
        "answer": {"status": "complete", "plan": "Implement the change"},
    }
    assert not list(plan.iter_errors(complete_plan))
    assert list(plan.iter_errors({**complete_plan, "status": "held"}))
    complete_implementation = {
        **complete_plan,
        "code_sha256": "a" * 64,
        "changed_paths": ["src/feature.py"],
    }
    assert not list(implementation.iter_errors(complete_implementation))
    assert list(implementation.iter_errors({**complete_implementation, "changed_paths": []}))


def test_reviewer_rejection_and_missing_observation_evidence_cannot_complete() -> None:
    workflow = _workflow()
    review = Draft202012Validator(_schema(workflow, "review_receipt"))
    handoff = Draft202012Validator(_schema(workflow, "outcome_receipt"))
    approved = {
        "thread_id": "thread-review",
        "model": "gpt-6-astra",
        "status": "complete",
        "prompt": "Review the actual code",
        "started_at": "start",
        "ended_at": "end",
        "duration_seconds": 1,
        "answer": {
            "verdict": "approve",
            "findings": [],
            "missing_requirements": [],
            "irrelevant_changes": [],
        },
        "verdict": "approve",
        "code_sha256": "a" * 64,
    }
    assert not list(review.iter_errors(approved))
    assert list(review.iter_errors({**approved, "verdict": "reject"}))
    demonstrated = {
        "original_requirements": "The feature works",
        "observed_behavior": "The integrated check passed",
        "evidence_refs": ["run:1:handoff:observation:0"],
        "gaps": [],
        "status": "demonstrated",
    }
    assert not list(handoff.iter_errors(demonstrated))
    assert list(handoff.iter_errors({**demonstrated, "evidence_refs": []}))
    assert list(handoff.iter_errors({**demonstrated, "status": "held"}))


def test_observed_handoff_requires_evidence_and_empty_gaps() -> None:
    workflow = _workflow()
    handoff = Draft202012Validator(_schema(workflow, "outcome_receipt"))
    result = {
        "original_requirements": "Change works in the integrated consumer",
        "observed_behavior": "The controller executed the declared check",
        "evidence_refs": ["run:1:handoff:observation:0"],
        "gaps": [],
        "status": "demonstrated",
    }
    assert not list(handoff.iter_errors(result))
    assert list(handoff.iter_errors({**result, "gaps": ["Not observed"]}))
    assert list(handoff.iter_errors({k: v for k, v in result.items() if k != "evidence_refs"}))
