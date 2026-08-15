"""Unit tests for the MCP contract module — verbs, inputs, envelope discipline."""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from pydantic import ValidationError

from stackos.mcp.contract import (
    MCPInput,
    WriteEnvelope,
    verb_is_mutating,
)
from stackos.mcp.dispatcher import MCPDispatcher
from stackos.mcp.server import (
    ToolRegistry,
    ToolSpec,
    assert_envelope_discipline,
)

# ---------------------------------------------------------------------------
# verb_is_mutating contract.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "schedule.remove",
        "run.start",
        "run.finish",
        "run.heartbeat",
        "run.abort",
        "project.delete",
        "workflowExtension.delete",
        "workspace.bootstrap",
        "workspace.connect",
        "plugin.enable",
        "plugin.disable",
        "resource.upsert",
        "workflowExtension.upsert",
        "workflowTemplate.save",
        "workflowTemplate.fork",
        "runPlan.abort",
        "runPlan.create",
        "runPlan.start",
        "runPlan.update",
        "runPlan.claimStep",
        "runPlan.recordStep",
        "agentRequest.claim",
        "agentRequest.release",
        "agentRequest.linkRunPlan",
        "agentRequest.prepareRunPlan",
        "agentRequest.complete",
        "agentRequest.ignore",
        "account.start",
        "account.test",
        "account.revoke",
        "action.execute",
        "action.run",
        "context.snapshot",
        "learning.create",
        "learning.update",
        "experiment.create",
        "experiment.recordObservation",
        "experiment.recordDecision",
        "decision.record",
    ],
)
def test_mutating_verb_classification(name: str) -> None:
    """Names with mutating verbs report as mutating."""
    assert verb_is_mutating(name)


@pytest.mark.parametrize(
    "name",
    [
        "resource.get",
        "resource.query",
        "meta.enums",
        "cost.queryAll",
        "workflowExtension.get",
        "workflowExtension.list",
        "workflowExtension.validate",
        "workflowTemplate.validate",
        "runPlan.validate",
        "action.validate",
    ],
)
def test_read_verb_not_mutating(name: str) -> None:
    """Read verbs are not mutating."""
    assert not verb_is_mutating(name)


# ---------------------------------------------------------------------------
# MCPInput strictness.
# ---------------------------------------------------------------------------


class _ExampleInput(MCPInput):
    """Sample subclass for strict-extra tests."""

    project_id: int


def test_mcp_input_rejects_extra_fields() -> None:
    """MCPInput subclasses reject unknown fields with ValidationError."""
    with pytest.raises(ValidationError):
        _ExampleInput.model_validate({"project_id": 1, "bogus": True})


def test_mcp_input_accepts_cross_cutting_fields() -> None:
    """Cross-cutting execution and response fields are accepted on every input."""
    inp = _ExampleInput.model_validate(
        {
            "project_id": 1,
            "idempotency_key": "abc",
            "run_token": "tok",
            "expected_etag": "etag",
            "response_mode": "ack",
        }
    )
    assert inp.idempotency_key == "abc"
    assert inp.run_token == "tok"
    assert inp.response_mode == "ack"


# ---------------------------------------------------------------------------
# Envelope-discipline check.
# ---------------------------------------------------------------------------


class _DummyOutput(MCPInput):
    """Sample bare output (not a WriteEnvelope) for the negative test."""

    payload: str


async def _noop_handler(*_args: object, **_kwargs: object) -> dict:  # pragma: no cover
    return {}


class _ProgressSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_progress_notification(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_dispatcher_builds_progress_emitter_from_explicit_request_context() -> None:
    """SDK 2 request metadata reaches tools without an ambient ContextVar."""
    session = _ProgressSession()
    context = ServerRequestContext(
        session=cast(ServerSession, session),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tools/call",
        request_id="request-1",
        meta={"progress_token": "progress-1"},
    )

    emitter = MCPDispatcher._build_emitter(context)
    await emitter.emit(1, 2, "halfway")

    assert session.calls == [
        {
            "progress_token": "progress-1",
            "progress": 1.0,
            "total": 2.0,
            "message": "halfway",
            "related_request_id": "request-1",
        }
    ]


def test_envelope_discipline_rejects_mutating_with_bare_output() -> None:
    """Registering a mutating tool with a bare output raises at startup."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="example.create",  # mutating verb
            description="Bad registration",
            input_model=_ExampleInput,
            output_model=_DummyOutput,  # bare; should fail
            handler=_noop_handler,
        )
    )
    with pytest.raises(RuntimeError, match="envelope discipline"):
        assert_envelope_discipline(registry)


def test_envelope_discipline_accepts_write_envelope() -> None:
    """Registering a mutating tool with WriteEnvelope passes the check."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="example.create",
            description="Good registration",
            input_model=_ExampleInput,
            output_model=WriteEnvelope[_DummyOutput],
            handler=_noop_handler,
        )
    )
    # No error.
    assert_envelope_discipline(registry)


def test_envelope_discipline_accepts_read_with_bare_output() -> None:
    """Read tools may return bare output."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="example.get",  # read verb
            description="Fetch an example",
            input_model=_ExampleInput,
            output_model=_DummyOutput,
            handler=_noop_handler,
        )
    )
    assert_envelope_discipline(registry)


def test_registry_rejects_duplicate_registration() -> None:
    """ToolRegistry rejects duplicate names."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="example.get",
            description="d",
            input_model=_ExampleInput,
            output_model=_DummyOutput,
            handler=_noop_handler,
        )
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        registry.register(
            ToolSpec(
                name="example.get",
                description="d2",
                input_model=_ExampleInput,
                output_model=_DummyOutput,
                handler=_noop_handler,
            )
        )
