"""Synthetic host handoff proofs; these fixtures are not executed model analysis."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlmodel import Session

from plugins.finance.scripts import finance_management as management
from stackos.db.models import AgentRequestStatus, RunPlanStepStatus
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.communications import (
    LocalAgentChatCreateMessageInput,
    local_agent_chat_create_message,
)
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from tests.helpers.finance_workspace import record

QUERY = {
    "start": "2026-09-01",
    "end": "2026-09-30",
    "recognition_basis": "cash",
    "date_basis": "transaction",
    "timezone": "UTC",
}


def inputs(request_id: int, snapshot: dict) -> dict:
    return {
        "workspace_ref": "finance-workspace:synthetic",
        "request_id": request_id,
        "source_snapshot": {"revision": snapshot["revision"], "sha256": snapshot["sha256"]},
        "query": QUERY,
        "analysis_kind": "opportunities",
    }


def source(tmp_path: Path) -> tuple[Path, dict]:
    path = management.workspace.initialize(tmp_path / "synthetic-finance")
    document = management.workspace.read_document(path)
    document["sources"].append(
        record(
            "source:synthetic",
            "verified",
            source_kind="bank-export",
            source_identity="synthetic:september",
            attachment_refs=[],
            coverage_state="missing",
        )
    )
    document["revision"] += 1
    management.workspace.write_document(path, document, management.workspace.digest(path))
    return path, management.read_snapshot(path)


def reviewed_result(
    request_id: int, snapshot: dict, run_ref: str, state: str
) -> tuple[dict, bytes]:
    report = (
        b"# Synthetic analysis fixture\nCoverage is incomplete; no savings estimate is supported.\n"
    )
    result = {
        "format": "finance-management-result-v1",
        "request_id": str(request_id),
        "source_snapshot": {"revision": snapshot["revision"], "sha256": snapshot["sha256"]},
        "query": QUERY,
        "producer_ref": "agent:synthetic-preparer",
        "run_ref": run_ref,
        "created_at": "2026-10-13T12:00:00Z",
        "state": state,
        "opportunities": [],
        "evidence_refs": ["source:synthetic"],
        "gaps": ["Missing bank coverage; revenue and profit are unknown."],
        "report_sha256": hashlib.sha256(report).hexdigest(),
    }
    if state == "supported-opportunities":
        result["opportunities"] = [
            {
                "opportunity_ref": "finding:synthetic-coverage",
                "kind": "source-quality",
                "rationale": "Request missing bank coverage before relying on a savings estimate.",
                "evidence_refs": ["source:synthetic"],
                "uncertainty": "The bank export window is incomplete.",
                "assumptions": [],
            }
        ]
    result["review"] = {
        "reviewer_ref": "agent:synthetic-control-reviewer",
        "run_ref": run_ref,
        "verdict": "approved",
        "reviewed_at": "2026-10-13T12:10:00Z",
        "target_digest_sha256": management.result_digest(result),
    }
    return result, report


def test_management_method_requires_independent_review_without_provider_grants(
    session: Session, project_id: int, tmp_path: Path
) -> None:
    _, snapshot = source(tmp_path)
    repo = RunPlanRepository(session)
    checked = repo.validate_plan(
        project_id=project_id,
        plugin_slug="finance",
        template_key="finance.management-review",
        inputs_json=inputs(1, snapshot),
        enforce_required_inputs=True,
    )
    assert checked.valid, checked.errors
    assert checked.warnings == []
    assert checked.plan is not None
    assert checked.plan.grant_snapshot_json["mcp_tool_grants"] == []
    spec = (
        WorkflowTemplateLoader(session)
        .describe_template(
            project_id=project_id, plugin_slug="finance", key="finance.management-review"
        )
        .spec
    )
    assert {a.agent_preset_ref for a in spec.agent_requirements if a.requirement == "required"} == {
        "stackos.finance.bookkeeping-preparer",
        "stackos.finance.control-reviewer",
    }
    schema = spec.outputs[0].schema_data
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    published = {
        "status": "published",
        "request_id": 1,
        "source_snapshot": inputs(1, snapshot)["source_snapshot"],
        "exception_refs": [],
    }
    assert list(Draft202012Validator(schema).iter_errors(published))


def test_method_accepts_canonical_clamped_comparison(
    session: Session, project_id: int, tmp_path: Path
) -> None:
    _, snapshot = source(tmp_path)
    selected = inputs(1, snapshot)
    selected["query"] = {
        **QUERY,
        "start": "2024-02-29",
        "end": "2024-03-01",
        "comparison": management.comparison_window("2024-02-29", "2024-03-01", "previous-year"),
    }
    described = (
        WorkflowTemplateLoader(session)
        .describe_template(
            project_id=project_id, plugin_slug="finance", key="finance.management-review"
        )
        .spec
    )
    query_schema = next(item.schema_data for item in described.inputs if item.key == "query")
    assert list(Draft202012Validator(query_schema).iter_errors(selected["query"])) == []
    checked = RunPlanRepository(session).validate_plan(
        project_id=project_id,
        plugin_slug="finance",
        template_key="finance.management-review",
        inputs_json=selected,
        enforce_required_inputs=True,
    )
    assert checked.valid, checked.errors


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["supported-opportunities", "no-supported-opportunity", "incomplete-data"]
)
async def test_synthetic_local_chat_host_plan_review_publish_complete(
    session: Session, project_id: int, tmp_path: Path, state: str
) -> None:
    path, snapshot = source(tmp_path)
    master_before = path.read_bytes()
    context = MCPContext(
        session=session, request_id="synthetic-finance-ingress", project_id=project_id
    )
    queued = await local_agent_chat_create_message(
        LocalAgentChatCreateMessageInput(
            project_id=project_id,
            thread_key="finance-management",
            message_key="synthetic-analysis",
            sender_ref="local-user:synthetic",
            text="Analyze the selected finance snapshot.",
            metadata_json={
                "workflow_key": "finance.management-review",
                "workspace_ref": "finance-workspace:synthetic",
                "source_snapshot": inputs(1, snapshot)["source_snapshot"],
                "query": QUERY,
                "analysis_kind": "opportunities",
            },
        ),
        context,
        ProgressEmitter(None, None),
    )
    request = queued.data.agent_request
    assert request is not None
    requests = AgentRequestRepository(session)
    assert (
        requests.get(project_id=project_id, request_id=request.id).status == AgentRequestStatus.NEW
    )
    # A host explicitly consumes and claims the persisted request. The daemon never runs a model.
    prepared = requests.prepare_run_plan(
        project_id=project_id,
        request_id=request.id,
        claimed_by="host:synthetic",
        template_key="finance.management-review",
        plugin_slug="finance",
        inputs_json=inputs(request.id, snapshot),
    ).data
    plans = RunPlanRepository(session)
    started = plans.start(prepared.run_plan.id, project_id=project_id).data
    run_ref = f"run-plan:{prepared.run_plan.id}"
    result, report = reviewed_result(request.id, snapshot, run_ref, state)
    safe_summary = {
        "status": "scoped",
        "request_id": request.id,
        "source_snapshot": inputs(request.id, snapshot)["source_snapshot"],
        "exception_refs": [],
    }
    publication = None
    for step_id, status in (
        ("preflight", "scoped"),
        ("analyze", "drafted"),
        ("review", "reviewed"),
        ("publish", "published"),
    ):
        plans.claim_step(
            run_plan_id=prepared.run_plan.id,
            run_id=started.run_id,
            step_id=step_id,
            claimed_by="host:synthetic",
        )
        safe_summary = {**safe_summary, "status": status}
        if status in {"drafted", "reviewed", "published"}:
            safe_summary["result_digest_sha256"] = management.result_digest(result)
            safe_summary["report_sha256"] = result["report_sha256"]
        if status in {"reviewed", "published"}:
            safe_summary["control_review_ref"] = "external-control-review:synthetic"
        if status == "published":
            publication = management.publish_result(path, result, report)
            external = management.read_result(
                path,
                publication["external_result_ref"],
                expected_digest=publication["result_sha256"],
                expected_query=QUERY,
            )
            assert external["state"] == "current"
            assert external["report"] == report.decode()
            safe_summary.update(
                external_result_ref=publication["external_result_ref"],
                result_sha256=publication["result_sha256"],
                outcome=state,
            )
        plans.record_step(
            run_plan_id=prepared.run_plan.id,
            run_id=started.run_id,
            step_id=step_id,
            status=RunPlanStepStatus.SUCCESS,
            result_json={"management_summary": safe_summary},
        )
    assert publication is not None
    completed = requests.complete(
        project_id=project_id,
        request_id=request.id,
        claim_token=prepared.claim_token,
        metadata_json={
            "external_result_ref": publication["external_result_ref"],
            "external_result_sha256": publication["result_sha256"],
            "source_snapshot": publication["source_snapshot"],
            "state": "published",
        },
    ).data
    assert completed.status == AgentRequestStatus.RESOLVED
    assert path.read_bytes() == master_before
    control = json.dumps(completed.metadata_json) + json.dumps(safe_summary)
    assert report.decode() not in control
    assert "estimated_impact" not in control
    assert "opportunities" not in safe_summary
    assert completed.metadata_json["external_result_sha256"] != management.result_digest(result)
    # A later source revision makes the completed request's retained result stale.
    document = management.workspace.read_document(path)
    document["sources"].append(
        record(
            "source:later",
            source_kind="synthetic",
            source_identity="synthetic:later",
            attachment_refs=[],
            coverage_state="missing",
        )
    )
    document["revision"] += 1
    management.workspace.write_document(path, document, management.workspace.digest(path))
    assert (
        management.read_result(
            path,
            publication["external_result_ref"],
            expected_digest=publication["result_sha256"],
            expected_query=QUERY,
        )["state"]
        == "stale"
    )


@pytest.mark.parametrize("problem", ["self-review", "unreviewed", "stale", "report-changed"])
def test_publication_rejects_invalid_synthetic_host_output(tmp_path: Path, problem: str) -> None:
    path, snapshot = source(tmp_path)
    result, report = reviewed_result(1, snapshot, "run-plan:synthetic", "incomplete-data")
    result = copy.deepcopy(result)
    if problem == "self-review":
        result["review"]["reviewer_ref"] = result["producer_ref"]
    elif problem == "unreviewed":
        result.pop("review")
    elif problem == "report-changed":
        report += b"Unreviewed claim."
    else:
        document = management.workspace.read_document(path)
        document["sources"].append(
            record(
                "source:new",
                source_kind="synthetic",
                source_identity="synthetic:new",
                attachment_refs=[],
                coverage_state="missing",
            )
        )
        document["revision"] += 1
        management.workspace.write_document(path, document, management.workspace.digest(path))
    with pytest.raises(ValueError):
        management.publish_result(path, result, report)
    assert not (path.parent / "exports" / "analysis" / "1" / "manifest.json").exists()
