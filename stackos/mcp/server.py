"""MCP server composition and Streamable HTTP mount.

Tool contracts and wire schemas live in ``tool_registry``. Dispatch scope,
grants, idempotency, and error handling live in ``dispatcher``. This module
owns the low-level server, FastAPI lifespan integration, and ASGI routes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import mcp.types as mcp_types
from fastapi import FastAPI
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from stackos import __version__
from stackos.logging import get_logger
from stackos.mcp.dispatcher import MCPDispatcher
from stackos.mcp.tool_registry import (
    ToolRegistry,
    ToolSpec,
    _to_tool,
    assert_envelope_discipline,
)

_log = get_logger(__name__)

STACKOS_MCP_INSTRUCTIONS = (
    "StackOS MCP server — local tool runtime for projects, plugins, "
    "workflow templates, run plans, resources, actions, and audit. "
    "Agent bridge sessions bind a workspace first, then inject the current "
    "`project_id` into project-scoped toolbox calls; omit injected fields unless "
    "an exact described schema asks for them. Direct daemon, REST, and CLI calls "
    "still require explicit project scope. Mutating tools return "
    "`{data, run_id, project_id}`; reads return bare data. Pass `run_token` from "
    "`runPlan.start` to execute granted run-plan steps."
)


@asynccontextmanager
async def _mcp_lifespan(_server: Server[Any, Any]) -> AsyncIterator[dict[str, Any]]:
    """Provide the low-level server lifespan context for each transport session."""
    _log.info("mcp.server.lifespan.start")
    try:
        yield {}
    finally:
        _log.info("mcp.server.lifespan.stop")


def build_server(registry: ToolRegistry, dispatcher: MCPDispatcher) -> Server[Any, Any]:
    """Construct the low-level server with list-tools and call-tool wiring."""
    server = Server[Any, Any](
        name="stackos",
        version=__version__,
        instructions=STACKOS_MCP_INSTRUCTIONS,
        lifespan=_mcp_lifespan,
    )

    @server.list_tools()
    async def _handle_list_tools() -> list[
        mcp_types.Tool
    ]:  # pragma: no cover - covered by integration tests
        return [_to_tool(spec) for spec in registry.all()]

    @server.call_tool(validate_input=False)
    async def _handle_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await dispatcher.dispatch(name, arguments)
        if result.is_error:
            err = result.payload["error"]
            error_msg = json.dumps(err, default=str)
            return mcp_types.CallToolResult(
                content=[mcp_types.TextContent(type="text", text=error_msg)],
                structuredContent=err,
                isError=True,
            )  # type: ignore[return-value]
        return result.payload

    return server


def register_mcp(app: FastAPI) -> None:
    """Build and mount the MCP Streamable HTTP transport at ``/mcp``."""
    from stackos.mcp.tools import register_all
    from stackos.operations.registry import build_operation_registry

    operation_registry = build_operation_registry()
    registry = ToolRegistry()
    register_all(registry)
    assert_envelope_discipline(registry)
    _log.info("mcp.server.registered", tool_count=len(registry))

    def _engine_resolver() -> Any:
        engine = getattr(app.state, "engine", None)
        if engine is None:  # pragma: no cover - only hit if lifespan did not run
            raise RuntimeError("MCP engine not initialised on app.state")
        return engine

    def _settings_resolver() -> Any:
        settings = getattr(app.state, "settings", None)
        if settings is None:  # pragma: no cover - lifespan did not finish
            raise RuntimeError("settings not initialised on app.state")
        return settings

    dispatcher = MCPDispatcher(
        registry,
        _engine_resolver,
        _settings_resolver,
        operation_registry=operation_registry,
    )
    server = build_server(registry, dispatcher)

    app.state.mcp_server = server
    app.state.mcp_registry = registry
    app.state.mcp_dispatcher = dispatcher
    app.state.operation_registry = operation_registry

    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
    )
    app.state.mcp_session_manager = session_manager

    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _wrapped_lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(existing_lifespan(fastapi_app))
            await stack.enter_async_context(session_manager.run())
            yield

    app.router.lifespan_context = _wrapped_lifespan

    class _MCPASGIApp:
        """Class wrapper so Starlette treats the endpoint as a raw ASGI app."""

        def __init__(self, manager: StreamableHTTPSessionManager) -> None:
            self._manager = manager

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await self._manager.handle_request(scope, receive, send)

    asgi_app = _MCPASGIApp(session_manager)

    from starlette.routing import Route

    app.router.routes.append(
        Route(
            "/mcp",
            asgi_app,
            methods=["GET", "POST", "DELETE", "OPTIONS"],
        )
    )
    app.mount("/mcp", asgi_app)


__all__ = [
    "MCPDispatcher",
    "ToolRegistry",
    "ToolSpec",
    "assert_envelope_discipline",
    "build_server",
    "register_mcp",
]
