"""Scripted agent handoffs through source-native MCP, without live providers.

Fixtures stand in for agent judgment and review. These tests prove execution,
grants and durable readback; independent agent rehearsals cover the decisions.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from stackos.config import Settings
from tests.integration.test_mcp.conftest import MCPClient
from tests.unit.test_seo_workflow_contracts import opportunity, refresh, snapshot


def call(client: MCPClient, tool_name: str, **arguments: object) -> dict:
    return client.call_tool_structured(tool_name, {"response_mode": "raw", **arguments})


def start(client: MCPClient, project_id: int, workflow: str, inputs: dict) -> tuple[dict, str]:
    validation = call(
        client,
        "runPlan.validate",
        project_id=project_id,
        workflow_key=workflow,
        inputs_json=inputs,
        enforce_required_inputs=True,
    )
    assert validation["valid"] is True
    created = call(
        client, "runPlan.create", project_id=project_id, workflow_key=workflow, inputs_json=inputs
    )["data"]
    plan = call(client, "runPlan.get", run_plan_id=created["id"])
    assert plan["approval_requests"] == []
    started = call(client, "runPlan.start", project_id=project_id, run_plan_id=plan["id"])
    return plan, started["data"]["run_token"]


def artifact(
    client: MCPClient, settings: Settings, pid: int, token: str, name: str, body: object
) -> str:
    # The test host supplies an explicitly available artifact content-write route.
    # artifact.create registers the retained file; it does not write its contents.
    raw = json.dumps(body, sort_keys=True).encode()
    path = settings.generated_assets_dir / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    created = call(
        client,
        "artifact.create",
        project_id=pid,
        plugin_slug="seo",
        kind="seo-evidence",
        name=name,
        uri=f"/generated-assets/{path.name}",
        mime_type="application/json",
        metadata_json={"sha256": hashlib.sha256(raw).hexdigest()},
        run_token=token,
    )["data"]
    readback = call(client, "artifact.read", project_id=pid, artifact_id=created["id"])
    assert readback["content_available"] is True
    assert json.loads(readback["content"]) == body
    return f"artifact:{created['id']}"


def record(client: MCPClient, pid: int, token: str, key: str, external_id: str, body: dict) -> str:
    written = call(
        client,
        "resource.upsert",
        project_id=pid,
        plugin_slug="seo",
        resource_key=key,
        external_id=external_id,
        data_json=body,
        run_token=token,
    )["data"]
    fetched = call(client, "resource.get", project_id=pid, record_id=written["id"])
    assert fetched["record"]["data_json"] == body
    return f"resource:{written['id']}"


def collect(client: MCPClient, settings: Settings, pid: int, token: str, prefix: str) -> dict:
    export = artifact(
        client, settings, pid, token, f"{prefix}-export", {"signups": 0, "sessions": 400}
    )
    inventory = artifact(
        client, settings, pid, token, f"{prefix}-inventory", ["https://example.com/welcome"]
    )
    index = [
        {
            "evidence_ref": "ev:page",
            "kind": "artifact",
            "source": "operator-export",
            "captured_at": "2026-09-24T12:00:00Z",
            "lifecycle_state": "current",
            "scope": {"url": "https://example.com/welcome"},
            "receipt_ref": export,
            "artifact_ref": export,
            "limitations": ["Supplied fixture, no live provider"],
        }
    ]
    ledger = [
        {
            "source": "supplied-export",
            "evaluated_at": "2026-09-24T12:00:00Z",
            "status": "used",
            "evidence_class": "operator-supplied",
            "coverage": "One page",
            "limitations": ["No connected providers"],
            "evidence_refs": ["ev:page"],
        }
    ]
    retained_index = artifact(
        client,
        settings,
        pid,
        token,
        f"{prefix}-index",
        {
            "source_ledger": ledger,
            "evidence_index": index,
            "limitations": [],
        },
    )
    measurement = snapshot()
    measurement.update(source_ref=export, evidence_index_ref=retained_index)
    measurement["url_mapping"]["mapping_ref"] = inventory
    snapshot_ref = record(client, pid, token, "search-performance-snapshot", prefix, measurement)
    return {
        "contract_version": "stackos.seo.evidence-packet.v1",
        "source_ledger": ledger,
        "evidence_index": index,
        "evidence_index_ref": retained_index,
        "snapshot_refs": [snapshot_ref],
        "inventory_ref": inventory,
        "limitations": [],
    }


def claim(client: MCPClient, plan: dict, token: str, step: str) -> dict:
    return call(client, "runPlan.claimStep", run_plan_id=plan["id"], step_id=step, run_token=token)[
        "data"
    ]


def finish(client: MCPClient, plan: dict, token: str, step: str, result: dict) -> None:
    call(
        client,
        "runPlan.recordStep",
        run_plan_id=plan["id"],
        step_id=step,
        status="success",
        result_json=result,
        run_token=token,
    )


def test_export_keyword_workflow_retains_refs_before_summary_and_updates_legacy(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    plan, token = start(
        mcp_client,
        pid,
        "seo.keyword-research",
        {
            "goal": "Improve existing welcome-email coverage",
            "evidence_sources": ["exports"],
        },
    )
    packet = {}
    summary = {}
    for step in plan["steps"]:
        key = step["step_id"]
        claim(mcp_client, plan, token, key)
        result = {}
        if key == "collect_evidence":
            packet = collect(mcp_client, mcp_settings, pid, token, "research")
            invalid = mcp_client.call_tool_error(
                "runPlan.recordStep",
                {
                    "run_plan_id": plan["id"],
                    "step_id": key,
                    "status": "success",
                    "result_json": {"evidence_packet": {}},
                    "run_token": token,
                },
            )
            assert invalid["message"] == "ValidationError"
            result = {"evidence_packet": packet}
        elif key == "review_opportunities":
            review_ref = artifact(
                mcp_client,
                mcp_settings,
                pid,
                token,
                "research-review",
                {
                    "reviewer": "independent-fixture",
                    "disposition": "accepted",
                },
            )
            adjudication = artifact(
                mcp_client,
                mcp_settings,
                pid,
                token,
                "research-adjudication",
                {
                    "owner": "main-fixture",
                    "disposition": "accepted",
                },
            )
            row = opportunity()
            row["evidence_index_ref"] = packet["evidence_index_ref"]
            summary = {
                "contract_version": "stackos.seo.opportunity-summary.v1",
                "criteria": {"priority": "Reader need and existing page fit"},
                "opportunities": [row],
                "review_ref": review_ref,
                "adjudication_ref": adjudication,
                "evidence_index_ref": packet["evidence_index_ref"],
                "limitations": [],
            }
            result = {"opportunity_summary": summary}
        elif key == "store_opportunities":
            ref = record(
                mcp_client,
                pid,
                token,
                "keyword-opportunity",
                "current",
                summary["opportunities"][0],
            )
            historical = {
                "schemaVersion": "legacy-v7",
                "collections": {"keywords": ["welcome email"]},
            }
            old_ref = record(
                mcp_client, pid, token, "keyword-opportunity", "historical", historical
            )
            updated = record(
                mcp_client,
                pid,
                token,
                "keyword-opportunity",
                "historical",
                {
                    **historical,
                    "operator_note": "Historical evidence remains readable",
                },
            )
            assert updated == old_ref
            rejected = mcp_client.call_tool_error(
                "resource.upsert",
                {
                    "project_id": pid,
                    "plugin_slug": "seo",
                    "resource_key": "keyword-opportunity",
                    "external_id": "bad",
                    "data_json": {"contract_version": "unknown"},
                    "run_token": token,
                },
            )
            assert rejected["message"] == "ValidationError"
            result = {"opportunity_record_refs": [ref]}
        finish(mcp_client, plan, token, key, result)
    fetched = call(mcp_client, "runPlan.get", run_plan_id=plan["id"])
    assert all(step["status"] == "success" for step in fetched["steps"])


def run_refresh(
    client: MCPClient,
    settings: Settings,
    pid: int,
    mode: str,
    prior_ref: str | None = None,
) -> tuple[str, dict]:
    prior = None
    if prior_ref:
        stored = call(
            client, "resource.get", project_id=pid, record_id=int(prior_ref.split(":")[1])
        )["record"]["data_json"]
        prior = {"ref": prior_ref, "body": stored}
    inputs = {
        "refresh_goal": "Improve navigation",
        "content_refs": ["https://example.com/welcome"],
        "mode": mode,
    }
    if prior:
        inputs["prior_refresh_ref"] = prior["ref"]
    plan, token = start(client, pid, "seo.content-refresh", inputs)
    packet = {}
    summary = refresh()
    summary["mode"] = mode
    summary["content_refs"] = inputs["content_refs"]
    if mode == "prepare":
        summary["implementation"] = {"status": "prepared", "change_results": []}
        summary["followup"] = {
            "status": "blocked",
            "due_at": None,
            "next_mode": "apply",
            "next_action": "Needs an authorized implementation route",
        }
    elif prior:
        summary["prior_refresh_ref"] = prior["ref"]
        summary["implementation"] = copy.deepcopy(prior["body"]["implementation"])
        summary["outcome"].update(
            status="inconclusive",
            summary="Partial implementation and limited followup remain inconclusive",
        )
    final_ref = ""
    for step in plan["steps"]:
        key = step["step_id"]
        claimed = claim(client, plan, token, key)
        result = {}
        if key == "collect_evidence":
            packet = collect(client, settings, pid, token, f"{mode}-{plan['id']}")
            summary["evidence_index_ref"] = packet["evidence_index_ref"]
            result = {"evidence_packet": packet}
        elif key == "diagnose":
            result = {
                "diagnosis": {
                    "contract_version": "stackos.seo.refresh-diagnosis.v1",
                    "summary": "Useful existing-page connection",
                    "evidence_refs": ["ev:page"],
                    "confidence": "medium",
                    "limitations": [],
                }
            }
        elif key == "propose_changes":
            result = {
                "change_proposal": {
                    "contract_version": "stackos.seo.refresh-proposal.v1",
                    "proposal_version": "fixture-v1",
                    "content_refs": inputs["content_refs"],
                    "changes": [],
                    "validation_plan": ["Read back target"],
                    "evidence_index_ref": packet["evidence_index_ref"],
                }
            }
        elif key == "review_changes":
            if prior:
                for field in ("diagnosis_ref", "proposal_ref", "proposal_version", "review"):
                    summary[field] = copy.deepcopy(prior["body"][field])
                finish(client, plan, token, key, {"reviewed_proposal_ref": summary["proposal_ref"]})
                continue
            receipt = artifact(
                client,
                settings,
                pid,
                token,
                f"{mode}-{plan['id']}-review",
                {
                    "reviewer": "independent-fixture",
                    "adjudicator": "main-fixture",
                    "mode": mode,
                    "simulated_change_attempts": summary["implementation"]["change_results"],
                },
            )
            summary.update(
                diagnosis_ref=receipt, proposal_ref=receipt, proposal_version="fixture-v1"
            )
            summary["review"]["adjudication_ref"] = receipt
            if not prior:
                for attempt in summary["implementation"]["change_results"]:
                    attempt["receipt_refs"] = [receipt]
                    if attempt["status"] == "verified":
                        attempt["verification_refs"] = [receipt]
            result = {"reviewed_proposal_ref": receipt}
        elif key == "apply_or_prepare":
            assert "action.execute" in claimed["allowed_tools"]
            # No CMS route is granted, even for apply. Receipts simulate prior host
            # attempts so we can verify retention and later assess-only resumption.
            denied = client.call_tool_error(
                "action.execute",
                {
                    "project_id": pid,
                    "action_ref": "publishing.wordpress.post.create",
                    "input_json": {},
                    "run_token": token,
                },
            )
            assert denied["code"] == -32007
            assert "declared on the active step" in denied["data"]["detail"]
            result = {"implementation_receipts": summary["implementation"]["change_results"]}
        elif key == "verify_changes":
            assert "action.execute" in claimed["allowed_tools"]
        elif key == "assess_outcome":
            summary["outcome"]["baseline_snapshot_refs"] = (
                prior["body"]["outcome"]["baseline_snapshot_refs"]
                if prior
                else packet["snapshot_refs"]
            )
            summary["outcome"]["followup_snapshot_refs"] = packet["snapshot_refs"] if prior else []
            retained_result = artifact(
                client,
                settings,
                pid,
                token,
                f"{mode}-{plan['id']}-attempt-record",
                {"implementation": summary["implementation"], "outcome": summary["outcome"]},
            )
            final_evidence = [
                *packet["evidence_index"],
                {
                    "evidence_ref": "ev:implementation",
                    "kind": "artifact",
                    "source": "scripted-agent-attempt-record",
                    "captured_at": "2026-09-24T12:00:00Z",
                    "lifecycle_state": "current",
                    "scope": {"mode": mode},
                    "receipt_ref": retained_result,
                    "artifact_ref": retained_result,
                    "limitations": ["Fixture simulates host attempts, not live publication"],
                },
            ]
            index_body = {
                "source_ledger": packet["source_ledger"],
                "evidence_index": final_evidence,
                "limitations": summary["outcome"]["limitations"],
            }
            final_index = artifact(
                client, settings, pid, token, f"{mode}-{plan['id']}-final-index", index_body
            )
            old_id = int(packet["evidence_index_ref"].split(":")[1])
            call(
                client,
                "artifact.supersede",
                project_id=pid,
                artifact_id=old_id,
                replacement_artifact_id=int(final_index.split(":")[1]),
                reason="Final retained evidence",
                run_token=token,
            )
            assert (
                call(client, "artifact.get", project_id=pid, artifact_id=old_id)["status"]
                == "superseded"
            )
            summary["evidence_index_ref"] = final_index
            summary["evidence_refs"] = [row["evidence_ref"] for row in final_evidence]
            result = {"refresh_summary": summary}
        elif key == "record_outcome":
            final_ref = record(
                client, pid, token, "content-refresh", f"refresh-{plan['id']}", summary
            )
            result = {"refresh_record_ref": final_ref}
        finish(client, plan, token, key, result)
    assert final_ref
    return final_ref, summary


def test_prepare_and_later_assess_execute_without_provider_or_inapplicable_approval(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
) -> None:
    pid = seeded_project["data"]["id"]
    _, prepared = run_refresh(mcp_client, mcp_settings, pid, "prepare")
    assert prepared["implementation"]["status"] == "prepared"
    assert prepared["followup"]["next_mode"] == "apply"
    prior_ref, partial = run_refresh(mcp_client, mcp_settings, pid, "apply")
    assessed_ref, assessed = run_refresh(mcp_client, mcp_settings, pid, "assess", prior_ref)
    assert assessed_ref != prior_ref
    assert assessed["implementation"] == partial["implementation"]
    assert assessed["proposal_ref"] == partial["proposal_ref"]
    assert assessed["review"] == partial["review"]
    assert [row["status"] for row in assessed["implementation"]["change_results"]] == [
        "verified",
        "unknown",
    ]
    assert assessed["outcome"]["status"] == "inconclusive"
    original = call(
        mcp_client, "resource.get", project_id=pid, record_id=int(prior_ref.split(":")[1])
    )
    assert original["record"]["data_json"] == partial


@pytest.mark.parametrize(
    ("action_ref", "payload"),
    [
        (
            "seo.search-console.search-analytics.query",
            {
                "site_url": "sc-domain:example.com",
                "start_date": "2026-08-01",
                "end_date": "2026-08-28",
                "dimensions": ["page", "query"],
                "type": "web",
                "row_limit": 1000,
                "start_row": 0,
                "data_state": "final",
            },
        ),
        (
            "seo.ga4.properties.run_report",
            {
                "property_ref": "properties/123",
                "request": {
                    "dateRanges": [{"startDate": "2026-08-01", "endDate": "2026-08-28"}],
                    "dimensions": [{"name": "landingPage"}],
                    "metrics": [{"name": "sessions"}],
                    "limit": "1000",
                    "offset": "0",
                },
            },
        ),
    ],
)
def test_optional_measurement_payloads_match_native_action_schema(
    mcp_client: MCPClient,
    action_ref: str,
    payload: dict,
) -> None:
    result = call(mcp_client, "action.validate", action_ref=action_ref, input_json=payload)
    assert result["valid"] is False  # No connected credentials in this fixture.
    assert {issue["code"] for issue in result["issues"]} == {"credential_required"}
    invalid = call(
        mcp_client,
        "action.validate",
        action_ref=action_ref,
        input_json={"invented_wrapper": payload},
    )
    assert invalid["valid"] is False
    assert any(issue["code"] != "credential_required" for issue in invalid["issues"])


@pytest.mark.parametrize("workflow", ["seo.keyword-research", "seo.content-refresh"])
def test_seo_native_resolution_requires_specialist_reviewer_and_main_guidance(
    mcp_client: MCPClient,
    workflow: str,
) -> None:
    resolved = call(
        mcp_client, "agentPreset.resolveForWorkflow", workflow_key=workflow, plugin_slug="seo"
    )
    assert {agent["preset"]["summary"]["key"] for agent in resolved["required_agents"]} == {
        workflow.replace("seo.", "seo.workflow.", 1),
        "stackos.sdlc.delivery-reviewer",
    }
    assert (
        resolved["required_skill_presets"][0]["preset"]["summary"]["key"]
        == "stackos.workflow-orchestrator"
    )
    assert resolved["unresolved_requirements"] == []
