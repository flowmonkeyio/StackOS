from __future__ import annotations

import pytest

from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.registry import build_operation_registry
from stackos.operations.tracker.schemas import TrackerUpdateTaskContentInput
from stackos.operations.tracker.write_handlers import tracker_update_task_content
from stackos.repositories.base import NotFoundError, ValidationError


def test_task_content_operation_is_a_narrow_local_console_contract():
    registry = build_operation_registry()
    spec = registry.get("tracker.updateTaskContent", surface="rest")
    assert spec.surfaces.rest.browser_safe is True
    assert spec.surfaces.rest.path == "/api/v1/operations/tracker.updateTaskContent/call"
    assert spec.surfaces.mcp.enabled is False
    assert spec.surfaces.cli.enabled is True
    assert spec.grant_policy == "local-admin-console-write"
    assert spec.idempotency_policy == "handler"
    assert spec.response_policy.allowed_modes == ("raw",)
    assert spec.response_policy.ack_safe is False
    with pytest.raises(NotFoundError):
        registry.get("tracker.updateTaskContent", surface="mcp")


@pytest.mark.asyncio
async def test_task_content_handler_rejects_forged_non_console_context():
    inp = TrackerUpdateTaskContentInput(
        project_id=1,
        task_key="task",
        expected_updated_at="2026-10-28T00:00:00",
        idempotency_key="intent",
        description="Content",
    )
    for extras in ({}, {"surface": "mcp", "trusted_local_admin": True}, {"surface": "rest"}):
        ctx = MCPContext(request_id="test", session=None, extras=extras)
        with pytest.raises(ValidationError, match="authenticated local console"):
            await tracker_update_task_content(inp, ctx, ProgressEmitter(None, None))
