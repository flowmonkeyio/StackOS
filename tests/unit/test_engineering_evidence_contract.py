"""Contract tests for versioned engineering evidence."""

from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator

from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS


def _schema() -> dict:
    engineering = next(
        manifest for manifest in BUILTIN_PLUGIN_MANIFESTS if manifest.slug == "engineering"
    )
    evidence = next(
        resource for resource in engineering.resources if resource.key == "engineering-evidence"
    )
    return evidence.schema_data


def _executed_evidence() -> dict:
    return {
        "schema_version": "stackos.engineering-evidence.v2",
        "title": "Tracked-delivery contract tests",
        "evidence_type": "automated-test",
        "evidence_mode": "executed",
        "repository_bound": True,
        "summary": "The versioned workflow contract passed.",
        "status": "passed",
        "lifecycle_state": "current",
        "repository_state": {
            "head_commit": "a" * 40,
            "staged_diff_checksum": f"sha256:{'b' * 64}",
            "unstaged_diff_checksum": f"sha256:{'c' * 64}",
            "untracked_manifest_checksum": f"sha256:{'d' * 64}",
            "migration_head": None,
            "generated_contract_revision": None,
            "captured_at": "2026-07-16T21:00:00Z",
        },
        "execution": {
            "status": "executed",
            "command_or_run_ref": (
                "uv run pytest tests/unit/test_engineering_tracked_delivery_workflow.py -q"
            ),
            "pass_count": 4,
            "fail_count": 0,
            "skipped_count": 0,
            "output_checksum": f"sha256:{'e' * 64}",
            "executed_at": "2026-07-16T21:05:00Z",
        },
        "scope": {
            "covered_paths": ["plugins/engineering/workflows/tracked-delivery.yaml"],
            "flow_refs": ["contract-to-release"],
            "ticket_keys": ["sdlc-contract-v030"],
            "run_plan_id": 211,
        },
        "author": "codex",
        "checked_at": "2026-07-16T21:05:00Z",
    }


def _authored_evidence() -> dict:
    return {
        "schema_version": "stackos.engineering-evidence.v2",
        "title": "Design review disposition",
        "evidence_type": "design-review",
        "evidence_mode": "authored",
        "summary": "The project-native design was approved with no blockers.",
        "status": "informational",
        "lifecycle_state": "current",
        "scope": {
            "covered_paths": ["plugins/engineering/workflows/tracked-delivery.yaml"],
            "flow_refs": [],
            "ticket_keys": ["sdlc-contract-v030"],
            "run_plan_id": 211,
        },
        "checked_at": "2026-07-16T21:05:00Z",
    }


def test_repository_bound_execution_requires_repository_and_execution_receipts() -> None:
    schema = _schema()
    validator = Draft202012Validator(schema)
    valid = _executed_evidence()

    assert list(validator.iter_errors(valid)) == []

    missing_repository = deepcopy(valid)
    missing_repository.pop("repository_state")
    assert list(validator.iter_errors(missing_repository))

    false_pass = deepcopy(valid)
    false_pass["execution"]["fail_count"] = 1
    assert list(validator.iter_errors(false_pass))


def test_non_repository_execution_requires_receipt_without_git_fingerprint() -> None:
    schema = _schema()
    validator = Draft202012Validator(schema)
    runtime = _executed_evidence()
    runtime["evidence_type"] = "browser-review"
    runtime["repository_bound"] = False
    runtime.pop("repository_state")
    runtime["execution"]["command_or_run_ref"] = "browser-run:42"

    assert list(validator.iter_errors(runtime)) == []

    missing_receipt = deepcopy(runtime)
    missing_receipt.pop("execution")
    assert list(validator.iter_errors(missing_receipt))


def test_authored_evidence_does_not_fabricate_execution_fields() -> None:
    schema = _schema()
    validator = Draft202012Validator(schema)
    authored = _authored_evidence()

    assert "repository_state" not in authored
    assert "execution" not in authored
    assert list(validator.iter_errors(authored)) == []

    false_executed_pass = deepcopy(authored)
    false_executed_pass["status"] = "passed"
    assert list(validator.iter_errors(false_executed_pass))


def test_engineering_evidence_lifecycle_requires_repair_context() -> None:
    schema = _schema()
    validator = Draft202012Validator(schema)

    superseded = _executed_evidence()
    superseded["lifecycle_state"] = "superseded"
    superseded["superseded_by"] = "evidence-v3"
    assert list(validator.iter_errors(superseded)) == []

    missing_replacement = deepcopy(superseded)
    missing_replacement.pop("superseded_by")
    assert list(validator.iter_errors(missing_replacement))

    invalidated = _executed_evidence()
    invalidated["lifecycle_state"] = "invalidated"
    invalidated["invalidated_reason"] = "A covered file changed after verification."
    assert list(validator.iter_errors(invalidated)) == []

    missing_reason = deepcopy(invalidated)
    missing_reason.pop("invalidated_reason")
    assert list(validator.iter_errors(missing_reason))
