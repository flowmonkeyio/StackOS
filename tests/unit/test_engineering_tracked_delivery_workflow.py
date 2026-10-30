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


def test_tracked_delivery_v05_has_five_checkpoints() -> None:
    workflow = _workflow()
    validation = validate_workflow_template_obj(workflow)
    assert validation.valid, validation.errors
    assert workflow["key"] == "engineering.tracked-delivery"
    assert workflow["version"] == "0.5.0"
    assert [step["id"] for step in workflow["steps"]] == [
        "scope-work",
        "deliver-tickets",
        "verify-delivery",
        "review-delivery",
        "release-closeout",
    ]
    assert len(workflow["policies"]) == 6
    assert [item["role"] for item in workflow["agent_requirements"]] == [
        "delivery",
        "delivery-reviewer",
    ]


def test_tracked_delivery_keeps_intent_and_actual_behavior_explicit() -> None:
    workflow = _workflow()
    steps = {step["id"]: " ".join(step["instructions"]) for step in workflow["steps"]}
    assert "ask the operator" in steps["scope-work"]
    assert "Implement and document" in steps["deliver-tickets"]
    assert "loaded app" in steps["verify-delivery"]
    assert "self-written summaries" in steps["review-delivery"]
    assert "say unfinished" in steps["release-closeout"]


def test_blocked_intent_and_incomplete_implementation_cannot_record_success() -> None:
    workflow = _workflow()
    intent = Draft202012Validator(_schema(workflow, "intent_summary"))
    implementation = Draft202012Validator(_schema(workflow, "implementation_summary"))
    clear = {
        "original_request": "Change the app flow",
        "intended_behavior": "New flow works",
        "constraints": [],
        "open_questions": [],
        "status": "clear",
    }
    assert not list(intent.iter_errors(clear))
    assert list(
        intent.iter_errors({**clear, "status": "blocked", "open_questions": ["Which flow?"]})
    )
    complete = {
        "candidate_ref": "commit:abc",
        "changes": ["flow"],
        "docs": ["guide"],
        "remaining_work": [],
    }
    assert not list(implementation.iter_errors(complete))
    assert list(implementation.iter_errors({**complete, "remaining_work": ["finish flow"]}))


def test_unproved_behavior_or_unapproved_review_cannot_record_success() -> None:
    workflow = _workflow()
    behavior = Draft202012Validator(_schema(workflow, "behavior_summary"))
    review = Draft202012Validator(_schema(workflow, "review_summary"))
    observed = {
        "candidate_ref": "commit:abc",
        "observed_behavior": "Loaded flow worked",
        "evidence_refs": ["run:1"],
        "gaps": [],
        "status": "demonstrated",
    }
    assert not list(behavior.iter_errors(observed))
    assert list(behavior.iter_errors({**observed, "status": "unproved"}))
    assert list(behavior.iter_errors({**observed, "evidence_refs": []}))
    approved = {
        "candidate_ref": "commit:abc",
        "reviewer": "independent agent",
        "verdict": "approve",
        "findings": [],
        "evidence_refs": ["review:1"],
    }
    assert not list(review.iter_errors(approved))
    assert list(review.iter_errors({**approved, "verdict": "repair"}))


def test_release_handoff_requires_demonstrated_behavior_and_authority() -> None:
    workflow = _workflow()
    release = Draft202012Validator(_schema(workflow, "release_summary"))
    result = {
        "original_request": "Change the app flow",
        "changes": ["flow"],
        "observed_behavior": "Loaded flow worked",
        "evidence_refs": ["run:1"],
        "gaps": [],
        "status": "demonstrated",
        "review_ref": "review:1",
        "release_state": "held",
        "authority": "no publication approval",
    }
    assert not list(release.iter_errors(result))
    assert list(release.iter_errors({**result, "status": "unproved"}))
    assert list(release.iter_errors({k: v for k, v in result.items() if k != "authority"}))
