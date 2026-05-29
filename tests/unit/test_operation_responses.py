from __future__ import annotations

import pytest

from stackos.mcp.contract import MCPInput, WriteEnvelope
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


def test_non_side_effect_default_response_mode_uses_policy_default() -> None:
    spec = _spec("tracker.get", mutating=False)

    assert resolve_response_mode(spec, {}, surface="rest") == "compact"
    assert resolve_response_mode(spec, {"response_mode": "standard"}, surface="rest") == "raw"


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
        "groups": [{"category": "communications", "count": 1}],
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
    assert compact["groups"] == [{"category": "communications", "count": 1}]


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
