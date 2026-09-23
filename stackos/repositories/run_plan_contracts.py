"""Read-time comparison of frozen workflow contracts with installed guidance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session

from stackos.db.models import RunPlan
from stackos.repositories.base import RepositoryError
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec


class RunPlanWorkflowContractOut(BaseModel):
    status: Literal["current", "mismatch", "unavailable"]
    workflow_key: str
    frozen_version: str | None = None
    installed_version: str | None = None
    frozen_digest: str | None = None
    installed_digest: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    changed_path_count: int = 0
    message: str
    next_action: str | None = None


def _digest(contract: dict[str, Any]) -> str:
    serialized = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _changed_paths(before: Any, after: Any, path: str = "$") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        paths: list[str] = []
        for key in sorted(before.keys() | after.keys()):
            child = f"{path}.{key}"
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(_changed_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            changed
            for index, (old, new) in enumerate(zip(before, after, strict=True))
            for changed in _changed_paths(old, new, f"{path}[{index}]")
        ]
    return [] if before == after else [path]


def inspect_workflow_contract(session: Session, plan: RunPlan) -> RunPlanWorkflowContractOut | None:
    """Never replace the run's snapshot, instructions, grants, approvals, or results."""
    if not plan.template_key:
        return None
    report = RunPlanWorkflowContractOut(
        status="unavailable",
        workflow_key=plan.template_key,
        frozen_version=plan.template_version,
        message="The frozen workflow contract cannot be compared with installed guidance.",
        next_action=(
            "Inspect runPlan.get with response_mode=raw and workflowTemplate.describe for this "
            "workflow and project before continuing. Preserve this run's snapshot and history; "
            "reconcile existing external effects and evidence before any further side effect. "
            "Do not replay completed steps or create replacement external objects."
        ),
    )
    if plan.template_snapshot_json is None:
        report.message = "This run has no frozen workflow snapshot; compatibility is unknown."
        return report
    try:
        # Normalize model defaults on both sides without modifying the stored legacy snapshot.
        frozen = WorkflowTemplateSpec.model_validate(plan.template_snapshot_json).model_dump(
            mode="json"
        )
        report.frozen_digest = _digest(frozen)
        repo_root = None
        if plan.template_source == "repo":
            if not plan.template_origin_path:
                report.message = "This run has no saved repository workflow origin."
                return report
            origin = Path(plan.template_origin_path)
            repo_root = next((str(p.parent) for p in origin.parents if p.name == ".stackos"), None)
            if repo_root is None:
                report.message = "The saved repository workflow origin cannot be resolved."
                return report
        installed = WorkflowTemplateLoader(session, sync_plugins=False).describe_template(
            key=plan.template_key,
            project_id=plan.project_id,
            repo_root=repo_root,
        )
        contract = installed.spec.model_dump(mode="json")
    except (RepositoryError, ValueError, OSError):
        # A missing or invalid installed contract must not make historical runs unreadable.
        return report
    report.installed_version = installed.spec.version
    report.installed_digest = _digest(contract)
    paths = _changed_paths(frozen, contract)
    report.changed_paths = paths[:40]
    report.changed_path_count = len(paths)
    if paths:
        report.status = "mismatch"
        same_version = report.frozen_version == report.installed_version
        report.message = (
            "Frozen workflow instructions or contract differ from the installed effective "
            "workflow" + (", despite the same version label." if same_version else ".")
        )
    else:
        report.status = "current"
        report.message = "The frozen workflow contract matches the installed effective workflow."
        report.next_action = None
    return report
