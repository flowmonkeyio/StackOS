"""Recorded incident packets for contract and isolated repository tests.

These are test data, not a collector, runtime controller, or live repair proof.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "plugins/support/workflows/internal-diagnostics.yaml"
CASE = ROOT / "examples/diagnostics/launch-handshake-2026-10-23.json"
STAGES = (
    ("resume-provenance", "incident_resume"),
    ("collect-classify", "diagnostic_findings"),
    ("propose-remediation", "remediation_receipt"),
    ("verify-escalate", "incident_receipt"),
)


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def incident() -> dict[str, Any]:
    return json.loads(CASE.read_text(encoding="utf-8"))


def receipt(stage: str, project_id: int = 1, plan_id: int | None = None) -> dict[str, Any]:
    case = incident()
    observations = case["observations"]
    refs = [entry["ref"] for entry in observations]
    attempts = []
    for key, layer, operation, target, outcome, effect, count, failures, evidence in (
        (
            "external-create",
            "external_orchestration",
            "cloud_threads.create",
            "selected-connected-desktop",
            "unknown",
            "unknown",
            3,
            3,
            observations[:3],
        ),
        (
            "bridge-initialize",
            "mcp_bridge",
            "session.initialize",
            "reported-project-virtualenv",
            "unknown",
            "unknown",
            1,
            1,
            observations[3:4],
        ),
        (
            "process-inspection",
            "unknown",
            "process-inspection",
            "prior-bridge-child",
            "denied",
            "none",
            1,
            0,
            observations[4:5],
        ),
    ):
        attempts.append(
            {
                "attempt_key": key,
                "layer": layer,
                "operation_ref": operation,
                "target_ref": target,
                "outcome": outcome,
                "effect": effect,
                "observed_at": None,
                "attempt_count": count,
                "attempt_limit": count,
                "failure_count": failures,
                "failure_limit": failures,
                "retry_disposition": "no_retry" if outcome == "denied" else "reconcile_first",
                "request_ref": evidence[-1]["request_id"],
                "evidence_refs": [entry["ref"] for entry in evidence],
            }
        )
    result: dict[str, Any] = {
        "schema_version": "stackos.internal-diagnostics-receipt.v1",
        "incident_key": case["case_id"],
        "mode": "recorded",
        "stage": stage,
        "source_revision": case["source_revision"],
        "scope": {
            "project_id": project_id,
            "target_refs": ["external-task-launch", "stackos-mcp-initialize"],
            "authority_refs": ["examples/diagnostics/launch-handshake-2026-10-23.json#authority"],
        },
        "runtime": {
            "installed_package": "unverified",
            "package_ref": None,
            "executable_ref": None,
            "version": None,
            "package_source_revision": None,
            "fingerprint_sha256": None,
            "release_evidence_ref": None,
        },
        "attempts": attempts,
        "evidence_refs": refs + [entry["ref"] for entry in case["validated_source_findings"]],
        "boundaries": {
            "packaged_only": True,
            "blind_replay_allowed": False,
            "live_repair_authorized": False,
            "authority_ref": None,
            "raw_data_retained": False,
        },
        "next_action": {
            "disposition": "escalate" if stage == "verify-escalate" else "await_evidence",
            "operation_ref": "read-existing-sanitized-evidence",
            "target_ref": "recorded-launch-and-handshake-errors",
            "condition_code": "published_runtime_and_handshake_leaf_missing",
        },
        "cursor": {
            "run_plan_id": plan_id,
            "step_id": stage,
            "ticket_ref": f"workflow-{plan_id}-{stage}" if plan_id else None,
            "tracker_revision": None,
            "previous_receipt_ref": None,
            "captured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "status": "unresolved",
    }
    if stage == "collect-classify":
        result["findings"] = [
            {
                "finding_code": "external_create_unknown",
                "layer": "external_orchestration",
                "classification": "observed",
                "observed_at": None,
                "evidence_refs": refs[:3],
            },
            {
                "finding_code": "handshake_leaf_missing",
                "layer": "mcp_bridge",
                "classification": "unknown",
                "observed_at": None,
                "evidence_refs": refs[3:4],
            },
            {
                "finding_code": "workspace_setup_not_established_as_initialize_cause",
                "layer": "workspace_binding",
                "classification": "inferred",
                "observed_at": None,
                "evidence_refs": [case["validated_source_findings"][0]["ref"]],
            },
        ]
    if stage == "propose-remediation":
        result["remediation"] = {
            "disposition": "not_attempted",
            "operation_ref": None,
            "target_ref": None,
            "attempt_ref": None,
            "effect": "none",
            "evidence_refs": refs,
        }
    if stage == "verify-escalate":
        result["verification"] = {
            "symptom_check": "not_run",
            "cause_status": "bounded_uncertainty",
            "current_effect_status": "unresolved",
            "observed_at": None,
            "runtime_fingerprint_sha256": None,
            "evidence_refs": refs,
            "gap_codes": [
                "published_package_unverified",
                "handshake_leaf_missing",
                "upstream_trace_missing",
            ],
        }
    return result


def inputs(project_id: int) -> dict[str, Any]:
    initial = receipt("resume-provenance", project_id)
    return {
        "incident_key": initial["incident_key"],
        "mode": initial["mode"],
        "source_revision": initial["source_revision"],
        "incident_scope": initial["scope"],
        "runtime_fingerprint": initial["runtime"],
        "prior_attempts": initial["attempts"],
        "evidence_refs": initial["evidence_refs"],
    }
