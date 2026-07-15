from __future__ import annotations

import pytest

from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.operations.actions import operation_specs as action_operation_specs
from stackos.operations.responses import resolve_response_mode, shape_operation_response
from stackos.operations.spec import OperationSpec, OperationSurface, OperationSurfaces
from stackos.repositories.base import ValidationError


class _Input(MCPInput):
    project_id: int | None = None


async def _handler(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {}


def _spec(name: str, *, mutating: bool = True) -> OperationSpec:
    return OperationSpec(
        name=name,
        summary=f"{name} summary",
        input_model=_Input,
        output_model=WriteEnvelope[dict],
        handler=_handler,
        surfaces=OperationSurfaces(
            mcp=OperationSurface(enabled=True),
            rest=OperationSurface(enabled=True),
            cli=OperationSurface(enabled=True),
        ),
        purpose=f"{name} purpose",
        mutating=mutating,
    )


def test_provider_side_effect_operations_are_raw_only() -> None:
    spec = _spec("communication.send")

    assert resolve_response_mode(spec, {}, surface="mcp") == "raw"
    assert resolve_response_mode(spec, {"response_mode": "raw"}, surface="mcp") == "raw"

    with pytest.raises(ValidationError) as exc:
        resolve_response_mode(spec, {"response_mode": "ack"}, surface="mcp")

    assert exc.value.data["side_effect"] == "not_started"
    assert exc.value.data["allowed_modes"] == ["raw"]


def test_action_operations_use_surface_response_defaults() -> None:
    specs = {spec.name: spec for spec in action_operation_specs()}

    for name in ("action.run", "action.execute"):
        spec = specs[name]
        assert resolve_response_mode(spec, {}, surface="mcp") == "compact"
        assert resolve_response_mode(spec, {}, surface="rest") == "compact"
        assert resolve_response_mode(spec, {}, surface="cli") == "raw"
        assert resolve_response_mode(spec, {"response_mode": "raw"}, surface="mcp") == "raw"
        with pytest.raises(ValidationError) as exc:
            resolve_response_mode(spec, {"response_mode": "ack"}, surface="mcp")
        assert exc.value.data["side_effect"] == "not_started"
        assert exc.value.data["allowed_modes"] == ["compact", "raw"]


def test_action_compact_response_keeps_file_pointer_and_drops_raw_payload() -> None:
    specs = {spec.name: spec for spec in action_operation_specs()}
    payload = {
        "project_id": 1,
        "data": {
            "action_call": {
                "id": 9,
                "status": "success",
                "plugin_slug": "seo",
                "action_key": "keyword.research",
                "provider_key": "dataforseo",
                "operation": "keyword.research",
                "credential_ref": "cred_dataforseo",
            },
            "output_json": {
                "output_mode": "file",
                "file": {
                    "path": "/tmp/action-output.json",
                    "absolute_path": "/tmp/action-output.json",
                    "uri": "/generated-assets/action-outputs/project-1/action-output.json",
                    "content_type": "application/json",
                    "schema_version": "stackos.action-output.v1",
                    "schema_ref": "stackos.action-output.v1",
                    "schema_operation": "schema.get",
                    "semantic_name": "dataforseo-keyword",
                    "bytes": 1200,
                    "sha256": "abc",
                    "response_summary": {"keys": ["tasks"]},
                },
                "summary": {
                    "provider_key": "dataforseo",
                    "raw_payload": "this should not appear",
                },
            },
            "metadata_json": {"large": "hidden"},
            "cost_cents": 50,
            "dry_run": False,
            "credential_ref": "cred_dataforseo",
        },
    }

    compact = shape_operation_response(
        specs["action.execute"],
        payload,
        response_mode="compact",
    )

    assert compact["data"]["action_call_id"] == 9
    assert compact["data"]["action_ref"] == "seo.keyword.research"
    assert compact["data"]["output"]["path"] == "/tmp/action-output.json"
    assert compact["data"]["output"]["schema_version"] == "stackos.action-output.v1"
    assert compact["data"]["output"]["schema_ref"] == "stackos.action-output.v1"
    assert compact["data"]["output"]["schema_operation"] == "schema.get"
    assert "artifact_id" not in compact["data"]["output"]
    assert "read" not in compact["data"]["output"]
    assert "metadata_json" not in str(compact)
    assert "this should not appear" not in str(compact)


def test_non_side_effect_default_response_mode_uses_policy_default() -> None:
    spec = _spec("tracker.get", mutating=False)

    assert resolve_response_mode(spec, {}, surface="rest") == "compact"
    assert resolve_response_mode(spec, {}, surface="cli") == "compact"
    assert resolve_response_mode(spec, {"response_mode": "standard"}, surface="rest") == "raw"


def test_context_compact_response_preserves_projected_fields() -> None:
    spec = _spec("context.query", mutating=False)
    payload = {
        "project_id": 1,
        "sources": ["learnings"],
        "fields": ["statement", "confidence"],
        "items": [
            {
                "source": "learnings",
                "id": 3,
                "project_id": 1,
                "title": "Founder creative lowered CPA",
                "occurred_at": "2026-06-30T06:41:08",
                "fields": {
                    "statement": "Founder creative lowered CPA with api_key=[redacted]",
                    "confidence": "medium",
                },
                "provenance": {"table": "learnings", "id": 3},
                "metadata_json": {"large": "not part of projected context"},
            }
        ],
        "next_cursor": None,
        "total_estimate": 1,
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["items"][0]["fields"] == {
        "statement": "Founder creative lowered CPA with api_key=[redacted]",
        "confidence": "medium",
    }
    assert compact["items"][0]["occurred_at"] == "2026-06-30T06:41:08"
    assert compact["items"][0]["provenance"] == {"table": "learnings", "id": 3}
    assert "metadata_json" not in compact["items"][0]


def test_resource_compact_responses_preserve_bounded_records() -> None:
    record = {
        "id": 247,
        "project_id": 1,
        "resource_id": 12,
        "plugin_slug": "branding",
        "resource_key": "brand-profile",
        "external_id": "current-brand",
        "title": "Operator Brand",
        "data_json": {"voice_rules": ["Be specific"], "status": "active"},
        "created_at": "2026-07-12T04:52:05",
        "updated_at": "2026-07-12T04:58:27",
    }
    resource = {
        "id": 12,
        "plugin_id": 3,
        "plugin_slug": "branding",
        "key": "brand-profile",
        "name": "Brand Profile",
        "description": "Current voice profile.",
        "schema_json": {"type": "object", "required": ["voice_rules"]},
    }

    queried = shape_operation_response(
        _spec("resource.query", mutating=False),
        {
            "resources": [resource],
            "records": [record],
            "next_cursor": None,
            "total_estimate": 1,
        },
        response_mode="compact",
    )
    fetched = shape_operation_response(
        _spec("resource.get", mutating=False),
        {"resource": resource, "record": record},
        response_mode="compact",
    )

    assert queried["data"]["resources_count"] == 1
    assert queried["data"]["records_count"] == 1
    assert queried["data"]["total_estimate"] == 1
    assert queried["data"]["records"][0]["id"] == 247
    assert queried["data"]["records"][0]["data_json"] == record["data_json"]
    assert queried["data"]["records"][0]["updated_at"] == record["updated_at"]
    assert fetched["data"]["resource"]["key"] == "brand-profile"
    assert fetched["data"]["record"]["data_json"] == record["data_json"]


def test_tracker_bulk_compact_keeps_counts_and_refs_without_full_rows() -> None:
    spec = _spec("tracker.createTicket")
    payload = {
        "project_id": 1,
        "run_id": 9,
        "data": {
            "valid": True,
            "rev": 7,
            "task": {"id": 10, "key": "workflow-9", "title": "Workflow"},
            "tickets": [
                {
                    "id": 11,
                    "key": "a",
                    "title": "A",
                    "source_json": {"large": "payload"},
                    "context_json": {"large": "context"},
                },
                {"id": 12, "key": "b", "title": "B"},
            ],
            "dependencies": [{"depends_on_ticket_key": "a", "ticket_key": "b"}],
            "warnings": ["dependency order checked"],
        },
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["operation"] == "tracker.createTicket"
    assert compact["project_id"] == 1
    assert compact["run_id"] == 9
    assert compact["data"]["task_key"] == "workflow-9"
    assert compact["data"]["ticket_count"] == 2
    assert compact["data"]["ticket_keys"] == ["a", "b"]
    assert compact["data"]["dependency_count"] == 1
    assert compact["warnings"] == ["dependency order checked"]
    assert "source_json" not in str(compact)
    assert "context_json" not in str(compact)


def test_tracker_mutation_compact_exposes_rollup_evidence_gap() -> None:
    spec = _spec("tracker.updateTicket")
    payload = {
        "project_id": 1,
        "data": {
            "valid": True,
            "rev": 8,
            "task": {
                "key": "mock-cleanup",
                "title": "Mock cleanup",
                "status": "skipped",
                "lane_key": "done",
                "completion_evidence_json": None,
            },
            "ticket": {
                "key": "mock-ticket",
                "title": "Mock ticket",
                "status": "skipped",
                "completion_evidence_json": {
                    "reason": "Usability probe intentionally did not execute delivery."
                },
            },
            "tracker": {"id": 1, "project_id": 1, "rev": 8},
        },
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["data"]["task"]["completion_evidence_present"] is False
    assert compact["data"]["ticket"]["completion_evidence_present"] is True
    assert compact["data"]["task_rollup"] == {
        "task_key": "mock-cleanup",
        "status": "skipped",
        "lane_key": "done",
        "completion_evidence_present": False,
        "updated_by_ticket_key": "mock-ticket",
        "completion_evidence_needs_explicit_update": True,
        "note": (
            "Ticket terminal updates can roll up the parent task status; ticket evidence is "
            "not copied to the task."
        ),
    }


def test_run_plan_compact_keeps_consistency_issues() -> None:
    spec = _spec("runPlan.get", mutating=False)
    payload = {
        "project_id": 1,
        "data": {
            "id": 42,
            "project_id": 1,
            "run_id": 9,
            "key": "demo.run",
            "title": "Demo",
            "status": "started",
            "steps": [
                {
                    "id": 101,
                    "step_id": "scope",
                    "title": "Scope",
                    "status": "running",
                    "position": 0,
                    "purpose": "Give the active agent enough context to execute the step.",
                    "success_criteria_json": ["The expected scoped result is verified."],
                    "action_refs_json": ["core.catalog.describe"],
                    "allowed_tools": ["context.query"],
                }
            ],
            "consistency_issues": [
                {
                    "code": "terminal-run-live-plan",
                    "severity": "error",
                    "message": "Linked audit run is terminal while run plan is still live.",
                    "run_plan_id": 42,
                    "run_id": 9,
                    "data": {"run_status": "aborted"},
                }
            ],
        },
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["data"]["run_plan_id"] == 42
    assert compact["data"]["steps"] == [
        {
            "id": 101,
            "step_id": "scope",
            "title": "Scope",
            "status": "running",
            "position": 0,
            "action_refs_json": ["core.catalog.describe"],
            "allowed_tools": ["context.query"],
            "purpose": "Give the active agent enough context to execute the step.",
            "success_criteria_json": ["The expected scoped result is verified."],
        }
    ]
    assert compact["data"]["consistency_issues"][0]["code"] == "terminal-run-live-plan"
    assert compact["data"]["consistency_issues"][0]["run_id"] == 9


def test_tracker_get_compact_summarizes_snapshot_without_full_rows() -> None:
    spec = _spec("tracker.get", mutating=False)
    payload = {
        "tracker": {"id": 3, "project_id": 1, "rev": 17, "name": "Default"},
        "lanes": [{"key": "implementation", "label": "Implementation"}],
        "priorities": [{"key": "p1", "label": "P1"}],
        "tasks": [
            {
                "id": 10,
                "key": "task-a",
                "title": "Task A",
                "goal": "Long task goal hidden from snapshot compact output.",
                "description": "Long task description hidden from snapshot compact output.",
                "status": "in-progress",
                "source_json": {"run_plan_id": 42},
                "context_json": {"large": "hidden"},
            }
        ],
        "tickets": [
            {
                "id": index,
                "key": f"ticket-{index}",
                "title": f"Ticket {index}",
                "task_key": "task-a",
                "status": "not-started",
                "outcome": "Long ticket outcome hidden from snapshot compact output.",
                "context_json": {"large": "hidden"},
            }
            for index in range(45)
        ],
        "dependencies": [{"ticket_key": "ticket-1", "depends_on_ticket_key": "ticket-0"}],
        "links": [{"ref": "slack:thread"}],
        "graph": {
            "nodes": [{"id": "ticket-1"} for _ in range(3)],
            "edges": [{"id": "edge-1"} for _ in range(2)],
            "warnings": [
                "Generic warning 1",
                "Generic warning 2",
                "Generic warning 3",
                "Generic warning 4",
                "Generic warning 5",
                "Generic warning 6",
                "Workflow step workflow-1-deliver has no dependency bridge.",
                "Generic warning 7",
            ],
        },
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["operation"] == "tracker.get"
    assert compact["project_id"] == 1
    assert compact["rev"] == 17
    assert compact["data"]["task_count"] == 1
    assert compact["data"]["ticket_count"] == 45
    assert len(compact["data"]["tickets"]) == 25
    assert compact["data"]["truncated"] == {"tickets": True}
    assert compact["data"]["graph"] == {
        "node_count": 3,
        "edge_count": 2,
        "warnings": [
            "Workflow step workflow-1-deliver has no dependency bridge.",
            "Generic warning 1",
            "Generic warning 2",
            "Generic warning 3",
            "Generic warning 4",
            "Generic warning 5",
            "Generic warning 6",
            "Generic warning 7",
        ],
    }
    assert "context_json" not in str(compact)
    assert "hidden" not in str(compact)
    assert "Long task goal" not in str(compact)
    assert "Long task description" not in str(compact)
    assert "Long ticket outcome" not in str(compact)


def test_operation_list_compact_keeps_agent_decision_fields() -> None:
    spec = _spec("operation.list", mutating=False)
    payload = {
        "items": [
            {
                "name": "communication.send",
                "category": "communications",
                "summary": "Send a provider-neutral message.",
                "read_only": False,
                "mutating": True,
                "grant_policy": "workflow-grant",
                "secret_policy": "no-secret-output",
                "surfaces": {
                    "mcp": {"enabled": True},
                    "rest": {"enabled": True},
                    "cli": {"enabled": False},
                },
                "response_policy": {
                    "default_mode": "raw",
                    "allowed_modes": ["raw"],
                    "ack_safe": False,
                    "raw_only_reason": "Provider side effect.",
                    "compact_notes": ["do not compact"],
                },
            }
        ],
        "groups": [
            {
                "category": "communications",
                "count": 30,
                "operation_names": [f"communication.tool{i}" for i in range(30)],
            }
        ],
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["items"] == [
        {
            "name": "communication.send",
            "category": "communications",
            "summary": "Send a provider-neutral message.",
            "read_only": False,
            "mutating": True,
            "grant_policy": "workflow-grant",
            "secret_policy": "no-secret-output",
            "surfaces": ["mcp", "rest"],
            "response_policy": {
                "default_mode": "raw",
                "allowed_modes": ["raw"],
                "ack_safe": False,
                "raw_only_reason": "Provider side effect.",
            },
        }
    ]
    assert compact["groups"] == [
        {
            "category": "communications",
            "count": 30,
            "operation_names": [f"communication.tool{i}" for i in range(30)],
        }
    ]


def test_plain_object_compact_summarizes_schemas_without_full_body() -> None:
    spec = _spec("operation.describe", mutating=False)
    payload = {
        "name": "demo.tool",
        "category": "demo",
        "summary": "Demo tool.",
        "read_only": True,
        "mutating": False,
        "purpose": "Inspect a demo tool.",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "x" * 1000,
                    "enum": ["one", "two"],
                    "minLength": 1,
                },
                "limit": {
                    "anyOf": [
                        {"type": "integer", "minimum": 1, "maximum": 50},
                        {"type": "null"},
                    ]
                },
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["ref"],
                        "properties": {"ref": {"type": "string", "minLength": 1}},
                    },
                },
            },
            "$defs": {"Nested": {"type": "object", "description": "y" * 1000}},
        },
        "output_schema": {
            "type": "object",
            "properties": {"content": {"type": "string", "description": "z" * 1000}},
        },
        "examples": [
            {
                "title": "Large example",
                "arguments": {"query": "kittens", "raw_payload": "hidden" * 400},
            }
        ],
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["operation"] == "operation.describe"
    assert compact["data"]["input_schema"]["required"] == ["query"]
    assert compact["data"]["input_schema"]["property_count"] == 3
    assert compact["data"]["input_schema"]["definition_count"] == 1
    assert compact["data"]["input_schema"]["properties"]["query"]["description"].endswith("...")
    assert compact["data"]["input_schema"]["properties"]["query"]["enum"] == ["one", "two"]
    assert compact["data"]["input_schema"]["properties"]["query"]["minLength"] == 1
    assert compact["data"]["input_schema"]["properties"]["limit"]["anyOf"][0]["maximum"] == 50
    assert (
        compact["data"]["input_schema"]["properties"]["records"]["items"]["properties"]["ref"][
            "minLength"
        ]
        == 1
    )
    assert "raw_payload" not in str(compact)
    assert "hiddenhiddenhidden" not in str(compact)
    assert "x" * 500 not in str(compact)
    assert "z" * 500 not in str(compact)


def test_compact_file_read_omits_content_body() -> None:
    spec = _spec("artifact.read", mutating=False)
    payload = {
        "artifact_id": 7,
        "path": "/tmp/action-output.json",
        "json_path": "$",
        "content_available": True,
        "content_type": "application/json",
        "bytes": 5000,
        "sha256": "abc",
        "artifact": {
            "id": 7,
            "project_id": 1,
            "kind": "action-output",
            "uri": "/generated-assets/action-output.json",
            "metadata_json": {"raw": "hidden"},
        },
        "content": "raw-content" * 500,
    }

    compact = shape_operation_response(spec, payload, response_mode="compact")

    assert compact["data"]["path"] == "/tmp/action-output.json"
    assert compact["data"]["content_available"] is True
    assert compact["data"]["content_omitted"] is True
    assert compact["data"]["artifact"]["uri"] == "/generated-assets/action-output.json"
    assert "raw-content" not in str(compact)
    assert "metadata_json" not in str(compact)


def test_ack_is_minimal_but_preserves_retry_refs() -> None:
    spec = _spec("resource.upsert")
    payload = {
        "project_id": 1,
        "run_id": 4,
        "data": {
            "id": 55,
            "resource_key": "engineering-evidence",
            "title": "Signoff",
            "data_json": {"large": "evidence"},
            "warnings": ["etag updated"],
        },
    }

    ack = shape_operation_response(spec, payload, response_mode="ack", idempotency_replay=True)

    assert ack == {
        "ok": True,
        "operation": "resource.upsert",
        "status": "success",
        "project_id": 1,
        "run_id": 4,
        "refs": {
            "id": 55,
            "resource_key": "engineering-evidence",
            "title": "Signoff",
        },
        "warnings": ["etag updated"],
        "idempotency_replay": True,
    }


def test_raw_replay_returns_canonical_payload_with_replay_marker() -> None:
    spec = _spec("resource.upsert")
    raw = {
        "project_id": 1,
        "data": {"id": 55, "data_json": {"body": "full payload"}},
    }

    shaped = shape_operation_response(spec, raw, response_mode="raw", idempotency_replay=True)

    assert shaped["data"]["data_json"] == {"body": "full payload"}
    assert shaped["idempotency_replay"] is True
    assert "idempotency_replay" not in raw
