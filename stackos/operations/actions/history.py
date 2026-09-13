"""Read-only project action audit history."""

from starlette.concurrency import run_in_threadpool

from stackos.actions import ActionRepository
from stackos.actions.repository.schema import ActionCallAuditOut
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import Page

from .schemas import ActionCallQueryInput


async def action_call_query(
    inp: ActionCallQueryInput, ctx: MCPContext, _emit: ProgressEmitter
) -> Page[ActionCallAuditOut]:
    return await run_in_threadpool(
        ActionRepository(ctx.session).query_calls,
        **inp.model_dump(
            exclude={"run_token", "idempotency_key", "expected_etag", "response_mode"}
        ),
    )
