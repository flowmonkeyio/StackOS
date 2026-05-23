"""MCP server initialization, tool registry, and Streamable HTTP mount.

This module:

1. Builds a single ``mcp.server.Server`` instance.
2. Registers ``ToolSpec`` entries for the current generic StackOS tool catalog
   (the ``tools/`` package supplies the per-tool handlers).
3. Wires the lowlevel ``list_tools`` / ``call_tool`` decorators onto the
   server, dispatching to the registered handlers.
4. Mounts the SDK's Streamable HTTP ASGI sub-app at ``/mcp`` on the
   FastAPI app so the existing bearer-token middleware gates every call.

The dispatcher is **single-purpose**: parse → enforce → call →
re-envelope → log. It is deliberately not parameterised by HTTP method
or framework — the tool handlers accept a session-bound ``MCPContext``
plus their pydantic Input model and return either a bare pydantic model
(read tool) or a ``WriteEnvelope`` (mutating tool).

The envelope-discipline check fires once at registration time. If a
mutating tool's output annotation is not a ``WriteEnvelope[...]``, the
daemon refuses to boot — see ``assert_envelope_discipline``.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, cast, get_origin

import mcp.types as mcp_types
from fastapi import FastAPI
from mcp.server.lowlevel.server import Server, request_ctx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete
from sqlmodel import Session
from starlette.types import Receive, Scope, Send

from stackos import __version__
from stackos.db.models import IdempotencyKey
from stackos.logging import get_logger
from stackos.mcp.context import MCPContext, bind_context, build_context
from stackos.mcp.contract import MCPInput, WriteEnvelope, verb_is_mutating
from stackos.mcp.errors import (
    JSONRPC_INTERNAL,
    JSONRPC_VALIDATION,
    ToolNotGrantedError,
    map_repository_error,
)
from stackos.mcp.permissions import check_call_grant
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import ConflictError, RepositoryError
from stackos.repositories.runs import IdempotencyKeyRepository

_log = get_logger(__name__)

# Type alias for tool handlers: (input model, context) -> output (bare or envelope).
ToolHandler = Callable[[Any, MCPContext, ProgressEmitter], Awaitable[Any]]


# ---------------------------------------------------------------------------
# Tool registry data classes.
# ---------------------------------------------------------------------------


@dataclass
class ToolSpec:
    """Static metadata + handler for a registered MCP tool.

    Fields:

    - ``name`` — wire-level dotted name (e.g. ``resource.upsert``).
    - ``description`` — one-line docstring (becomes MCP description).
    - ``input_model`` — pydantic Input class (subclass of MCPInput).
    - ``output_model`` — pydantic Output class. For mutating tools this
      is ``WriteEnvelope[<inner>]``; for reads it's the bare data type.
    - ``handler`` — async callable ``(input, context, emitter) -> output``.
    - ``streaming`` — ``True`` when a tool emits progress notifications.
    - ``read_only`` — derived from ``verb_is_mutating(name)``.
    """

    name: str
    description: str
    input_model: type[MCPInput]
    # ``output_model`` accepts ``Any`` because tool reads frequently
    # return generic containers (``list[X]``, ``dict[str, Any]``,
    # ``Page[X]``, ``WriteEnvelope[X]``, …) which are *not* concrete
    # ``BaseModel`` subclasses. The schema-generation path treats every
    # form via ``pydantic.TypeAdapter`` so it's fine; the type sloppy
    # at the dataclass boundary keeps the tool registrations terse.
    output_model: Any
    handler: ToolHandler
    streaming: bool = False
    read_only: bool = field(init=False)
    operation_name: str | None = None

    def __post_init__(self) -> None:
        self.read_only = not verb_is_mutating(self.name)


class ToolRegistry:
    """Holds every registered ``ToolSpec``; emits the SDK ``Tool`` list.

    The registry is owned by the ``Server`` setup path; ``register_all``
    in ``stackos.mcp.tools`` fills it. Lookups are by name.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Register a single tool; rejects duplicates."""
        if spec.name in self._tools:
            raise RuntimeError(f"duplicate tool registration: {spec.name!r}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        """Look up a tool spec; raises ``KeyError`` if absent."""
        return self._tools[name]

    def all(self) -> list[ToolSpec]:
        """Return every spec sorted by name (stable wire order)."""
        return [self._tools[k] for k in sorted(self._tools)]

    def __contains__(self, name: object) -> bool:  # pragma: no cover — ergonomics
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


# ---------------------------------------------------------------------------
# Envelope discipline — startup-time check.
# ---------------------------------------------------------------------------


def _is_write_envelope(tp: Any) -> bool:
    """Return ``True`` iff ``tp`` is ``WriteEnvelope[...]`` or a subclass."""
    origin = get_origin(tp) or tp
    if origin is WriteEnvelope:
        return True
    try:
        return isinstance(origin, type) and issubclass(origin, WriteEnvelope)
    except TypeError:  # pragma: no cover — non-class origin
        return False


def assert_envelope_discipline(registry: ToolRegistry) -> None:
    """Verify every mutating tool returns a ``WriteEnvelope``.

    Raises ``RuntimeError`` if a mutating verb's tool declares a bare
    data type. Wired into daemon startup so a misconfigured tool fails
    boot rather than slipping into prod (per the deliverables brief).

    Read tools are *not* checked for the inverse — a read tool that
    returns a write-shaped object would be unusual but legal (e.g. a
    diagnostic tool reflecting a recent envelope verbatim).
    """
    offenders: list[str] = []
    for spec in registry.all():
        if spec.read_only:
            continue
        if not _is_write_envelope(spec.output_model):
            offenders.append(spec.name)
    if offenders:
        raise RuntimeError(
            "MCP envelope discipline violated — mutating tools must return WriteEnvelope[...]: "
            + ", ".join(offenders)
        )


# ---------------------------------------------------------------------------
# Wire conversion helpers.
# ---------------------------------------------------------------------------


def _input_schema(model: type[MCPInput]) -> dict[str, Any]:
    """Extract the JSON Schema for a tool's input model.

    Pydantic v2 emits the schema with ``additionalProperties: false``
    when the model uses ``model_config = ConfigDict(extra="forbid")`` —
    our base ``MCPInput`` already does. Subclasses inherit the strict
    behaviour automatically.
    """
    return model.model_json_schema(mode="serialization")


def _output_schema(model: Any) -> dict[str, Any]:
    """Extract the JSON Schema for a tool's output model.

    Accepts any of:

    - Concrete ``BaseModel`` subclass (most read tools).
    - ``WriteEnvelope[X]`` (mutating tools — pydantic v2 generic).
    - ``Page[X]`` (cursor-paginated lists — pydantic v2 generic).
    - ``list[X]`` (composite list returns) — wrapped under ``items``
      to match the dispatcher's serialisation (see ``_result_to_json``)
      so the SDK's output-schema validation accepts the wire shape.
    - ``dict[str, X]`` (e.g. ``cost.queryProject`` aggregate returns).

    We use ``pydantic.TypeAdapter`` for non-BaseModel forms so the
    JSON-schema generator handles the generic / list / dict / typed-dict
    forms the SDK serialises into the ``Tool.outputSchema`` field.
    """
    from pydantic import TypeAdapter

    if isinstance(model, type) and issubclass(model, BaseModel):
        return model.model_json_schema(mode="serialization")
    origin = get_origin(model)
    if origin is list:
        # The dispatcher wraps list returns as ``{"items": [...]}`` so
        # the SDK's ``structuredContent`` (which must be a JSON object)
        # round-trips. Mirror that wrapping in the schema; hoist any
        # ``$defs`` to the outer level so JSON-Schema $ref resolution
        # finds them at the document root.
        inner = TypeAdapter(model).json_schema(mode="serialization")
        defs = inner.pop("$defs", None)
        wrapped: dict[str, Any] = {
            "type": "object",
            "properties": {"items": inner},
            "required": ["items"],
        }
        if defs:
            wrapped["$defs"] = defs
        return wrapped
    return TypeAdapter(model).json_schema(mode="serialization")


def _to_tool(spec: ToolSpec) -> mcp_types.Tool:
    """Convert a ``ToolSpec`` into the SDK's ``Tool`` wire shape."""
    annotations = mcp_types.ToolAnnotations(
        readOnlyHint=spec.read_only,
        idempotentHint=spec.read_only,  # reads are inherently idempotent
        title=spec.name,
    )
    meta: dict[str, Any] = {"streaming": spec.streaming}
    return mcp_types.Tool(
        name=spec.name,
        description=spec.description,
        inputSchema=_input_schema(spec.input_model),
        outputSchema=_output_schema(spec.output_model),
        annotations=annotations,
        _meta=meta,
    )


def _result_to_json(result: Any) -> dict[str, Any]:
    """Convert a tool's return value to a JSON-serialisable dict.

    Read tools return either ``BaseModel`` (single row), ``Page[...]``
    (cursor-paginated list), or a plain ``list`` / ``dict`` (e.g.
    ``cost.queryProject``). Mutating tools return ``WriteEnvelope``.
    All BaseModel subclasses go through ``model_dump(mode='json')``.
    """
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json", by_alias=True)
    if isinstance(result, list):
        return {"items": [_item_to_json(x) for x in result]}
    if isinstance(result, dict):
        # For dict returns we wrap values with model dump where needed.
        return {
            k: (_result_to_json(v) if isinstance(v, BaseModel) else v) for k, v in result.items()
        }
    return {"value": result}


def _item_to_json(item: Any) -> Any:
    """Serialise a list element.

    Pydantic models go through ``model_dump`` so the JSON shape
    round-trips through ``output_schema``; everything else (str / int /
    bool / None) ships verbatim. Nested lists / dicts recurse — the
    dispatcher's outer schema declares ``items`` as the wrapping key,
    but per-element shape is whatever the tool returned.
    """
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json", by_alias=True)
    if isinstance(item, dict):
        return {
            k: (_item_to_json(v) if not isinstance(v, str | int | float | bool | type(None)) else v)
            for k, v in item.items()
        }
    if isinstance(item, list):
        return [_item_to_json(x) for x in item]
    return item


def _find_mismatched_project_id(value: Any, expected: int) -> int | None:
    """Return the first project_id in a payload that differs from expected."""
    if isinstance(value, dict):
        raw = value.get("project_id")
        if isinstance(raw, int) and raw != expected:
            return raw
        for child in value.values():
            found = _find_mismatched_project_id(child, expected)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_mismatched_project_id(child, expected)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# Dispatcher.
# ---------------------------------------------------------------------------


@dataclass
class _CallResult:
    """Internal result envelope returned by the dispatcher."""

    payload: dict[str, Any]
    is_error: bool = False


class MCPDispatcher:
    """Wraps a ToolRegistry + engine reference; dispatches one tool call.

    Owned by the ``register_mcp`` setup path; the lowlevel ``call_tool``
    decorator forwards every invocation through ``dispatch``. Tests can
    instantiate the dispatcher directly with a synthetic engine to
    exercise the path without spinning up the FastAPI mount.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        engine_resolver: Callable[[], Any],
        settings_resolver: Callable[[], Any] | None = None,
        operation_registry: Any | None = None,
    ) -> None:
        self._registry = registry
        self._engine_resolver = engine_resolver
        self._settings_resolver = settings_resolver
        self._operation_registry = operation_registry

    async def dispatch(self, name: str, arguments: dict[str, Any] | None) -> _CallResult:
        """Resolve a tool, validate input, execute, return JSON-RPC payload.

        Step-by-step:

        1. Look up the ``ToolSpec``; missing → -32601 method-not-found.
        2. Open a session against the configured engine.
        3. Build the ``MCPContext`` (parses run_token / project_id /
           idempotency_key out of the arguments).
        4. Resolve the active session + progress-token from the SDK's
           ``request_ctx``.
        5. Validate input via the pydantic ``MCPInput`` subclass.
        6. Enforce the tool-grant matrix.
        7. Idempotency dispatch (mutating tools only): short-circuits
           replays within 24h. Beyond that, treat as fresh.
        8. Call the handler. ``ProgressEmitter`` is passed even for
           non-streaming tools (it's a no-op when the client did not
           opt into progress).
        9. Persist the response on the idempotency row when the call
           created the key.
        10. Log + return JSON.

        Errors are caught and re-raised inside the dispatcher only when
        they're typed; arbitrary exceptions bubble up as -32603.
        """
        try:
            spec = self._registry.get(name)
        except KeyError:
            _log.warning("mcp.dispatch.unknown_tool", tool=name)
            return _CallResult(
                payload={
                    "error": {
                        "code": -32601,
                        "message": "MethodNotFoundError",
                        "data": {"detail": f"tool {name!r} is not registered"},
                    }
                },
                is_error=True,
            )

        engine = self._engine_resolver()
        with Session(engine) as session:
            if spec.operation_name is not None:
                return await self._dispatch_operation(spec, arguments, session)
            ctx = build_context(arguments, session)
            if self._settings_resolver is not None:
                with suppress(RuntimeError):
                    ctx.extras["settings"] = self._settings_resolver()
            with bind_context(ctx):
                # Resolve session + progress token from SDK request_ctx.
                emitter = self._build_emitter()
                # Validate input.
                try:
                    parsed = spec.input_model.model_validate(arguments or {})
                except PydanticValidationError as exc:
                    return self._validation_failure(exc)
                # If a skill call carries both a resolved run_token and a
                # project_id, the project_id must be the run's project. Tools
                # that only take object IDs are additionally checked on the
                # response payload below before anything leaves the transport.
                scope_error = self._scope_input_error(spec, ctx)
                if scope_error is not None:
                    return scope_error
                # Tool-grant matrix.
                try:
                    check_call_grant(spec.name, ctx, parsed)
                except RepositoryError as exc:
                    return self._repo_error(exc)
                # Idempotency short-circuit (mutating tools that opted in).
                if (
                    not spec.read_only
                    and ctx.idempotency_key is not None
                    and ctx.project_id is not None
                ):
                    cached = self._idempotency_check(spec, ctx)
                    if cached is not None:
                        return cached
                # Execute.
                try:
                    started = time.perf_counter()
                    result = await spec.handler(parsed, ctx, emitter)
                    duration_ms = int((time.perf_counter() - started) * 1000)
                except RepositoryError as exc:
                    if (
                        not spec.read_only
                        and ctx.idempotency_key is not None
                        and ctx.project_id is not None
                    ):
                        self._idempotency_forget(spec, ctx)
                    return self._repo_error(exc)
                except Exception as exc:
                    if (
                        not spec.read_only
                        and ctx.idempotency_key is not None
                        and ctx.project_id is not None
                    ):
                        self._idempotency_forget(spec, ctx)
                    _log.exception("mcp.dispatch.unexpected_error", tool=name, **ctx.to_log_dict())
                    return _CallResult(
                        payload={
                            "error": {
                                "code": JSONRPC_INTERNAL,
                                "message": "RepositoryError",
                                "data": {"detail": f"{type(exc).__name__}: {exc}"},
                            }
                        },
                        is_error=True,
                    )

                # Persist idempotency response (success path).
                payload = _result_to_json(result)
                scope_error = self._scope_output_error(spec, ctx, payload)
                if scope_error is not None:
                    if (
                        not spec.read_only
                        and ctx.idempotency_key is not None
                        and ctx.project_id is not None
                    ):
                        self._idempotency_forget(spec, ctx)
                    return scope_error
                if (
                    not spec.read_only
                    and ctx.idempotency_key is not None
                    and ctx.project_id is not None
                ):
                    self._idempotency_record(spec, ctx, payload)

                _log.info(
                    "mcp.dispatch.ok",
                    tool=name,
                    duration_ms=duration_ms,
                    streaming=spec.streaming,
                    **ctx.to_log_dict(),
                )
                return _CallResult(payload=payload)

    async def _dispatch_operation(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any] | None,
        session: Session,
    ) -> _CallResult:
        """Delegate operation-backed MCP tools to the shared operation dispatcher."""
        if self._operation_registry is None:
            return _CallResult(
                payload={
                    "error": {
                        "code": JSONRPC_INTERNAL,
                        "message": "RepositoryError",
                        "data": {
                            "detail": f"operation registry missing for tool {spec.name!r}",
                        },
                    }
                },
                is_error=True,
            )
        from stackos.operations.dispatcher import OperationDispatcher

        settings = None
        if self._settings_resolver is not None:
            with suppress(RuntimeError):
                settings = self._settings_resolver()
        operation_name = spec.operation_name
        assert operation_name is not None
        try:
            result = await OperationDispatcher(self._operation_registry).dispatch(
                operation_name,
                arguments,
                session=session,
                surface="mcp",
                settings=settings,
            )
        except RepositoryError as exc:
            return self._repo_error(exc)
        return _CallResult(payload=result.payload)

    # -------- helpers --------

    def _build_emitter(self) -> ProgressEmitter:
        """Resolve the active SDK session + progress-token, build emitter."""
        try:
            req_ctx = request_ctx.get()
        except LookupError:
            return ProgressEmitter(None, None)
        token: str | int | None = None
        if req_ctx.meta is not None:
            token = req_ctx.meta.progressToken
        return ProgressEmitter(req_ctx.session, token, request_id=req_ctx.request_id)

    def _validation_failure(self, exc: PydanticValidationError) -> _CallResult:
        """Wrap a pydantic ValidationError as JSON-RPC -32602."""
        return _CallResult(
            payload={
                "error": {
                    "code": JSONRPC_VALIDATION,
                    "message": "ValidationError",
                    "data": {"detail": "input validation failed", "errors": exc.errors()},
                }
            },
            is_error=True,
        )

    def _repo_error(self, exc: RepositoryError) -> _CallResult:
        """Map a repository error to the JSON-RPC envelope."""
        code, message, data = map_repository_error(exc)
        return _CallResult(
            payload={"error": {"code": code, "message": message, "data": data}},
            is_error=True,
        )

    def _scope_input_error(self, spec: ToolSpec, ctx: MCPContext) -> _CallResult | None:
        """Reject a run_token used with another project's explicit project_id."""
        if ctx.run is None or ctx.project_id is None or ctx.run.project_id is None:
            return None
        if ctx.project_id == ctx.run.project_id:
            return None
        return self._repo_error(
            ToolNotGrantedError(
                "run_token is not scoped to this project",
                data={
                    "tool": spec.name,
                    "run_id": ctx.run_id,
                    "run_project_id": ctx.run.project_id,
                    "requested_project_id": ctx.project_id,
                },
            )
        )

    def _scope_output_error(
        self,
        spec: ToolSpec,
        ctx: MCPContext,
        payload: dict[str, Any],
    ) -> _CallResult | None:
        """Reject object-only reads/mutations whose result crosses run scope."""
        if ctx.run is None or ctx.run.project_id is None:
            return None

        expected = ctx.run.project_id
        seen = _find_mismatched_project_id(payload, expected)
        if seen is None:
            return None
        return self._repo_error(
            ToolNotGrantedError(
                "run_token cannot access data from another project",
                data={
                    "tool": spec.name,
                    "run_id": ctx.run_id,
                    "run_project_id": expected,
                    "result_project_id": seen,
                },
            )
        )

    def _idempotency_check(
        self,
        spec: ToolSpec,
        ctx: MCPContext,
    ) -> _CallResult | None:
        """Look up an idempotency key; return cached envelope on hit.

        Re-use ``IdempotencyKeyRepository.check_or_create``, which raises
        ``IdempotencyReplayError`` on hit. We catch it here, return the cached
        envelope, and short-circuit.
        """
        repo = IdempotencyKeyRepository(ctx.session)
        # We don't pre-populate response_json — the dispatcher will write
        # it once the handler returns successfully.
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        try:
            repo.check_or_create(
                project_id=ctx.project_id,
                tool_name=spec.name,
                idempotency_key=ctx.idempotency_key,
                run_id=ctx.run_id,
            )
        except RepositoryError as exc:
            from stackos.repositories.base import IdempotencyReplayError

            if isinstance(exc, IdempotencyReplayError):
                cached = exc.data.get("response_json")
                if cached is not None:
                    log_extra = ctx.to_log_dict()
                    log_extra["replayed_run_id"] = exc.data.get("run_id")
                    _log.info(
                        "mcp.idempotency.replay",
                        tool=spec.name,
                        **log_extra,
                    )
                    return _CallResult(payload=cached)
                return self._repo_error(
                    ConflictError(
                        "idempotency key is already in-flight",
                        data={
                            "idempotency_key": ctx.idempotency_key,
                            "run_id": exc.data.get("run_id"),
                            "tool_name": spec.name,
                            "project_id": ctx.project_id,
                            "replay": True,
                            "in_flight": True,
                        },
                    )
                )
            return self._repo_error(exc)
        return None

    def _idempotency_forget(self, spec: ToolSpec, ctx: MCPContext) -> None:
        """Remove a fresh idempotency key when the handler fails before caching."""
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        ctx.session.exec(
            delete(IdempotencyKey).where(
                cast(Any, IdempotencyKey.project_id) == ctx.project_id,
                cast(Any, IdempotencyKey.tool_name) == spec.name,
                cast(Any, IdempotencyKey.idempotency_key) == ctx.idempotency_key,
            )
        )
        ctx.session.commit()

    def _idempotency_record(
        self,
        spec: ToolSpec,
        ctx: MCPContext,
        response_json: dict[str, Any],
    ) -> None:
        """Persist the cached response for a fresh key."""
        repo = IdempotencyKeyRepository(ctx.session)
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        try:
            repo.update_response(
                project_id=ctx.project_id,
                tool_name=spec.name,
                idempotency_key=ctx.idempotency_key,
                response_json=response_json,
            )
        except RepositoryError as exc:  # pragma: no cover — only if check_or_create lost its row
            _log.warning(
                "mcp.idempotency.record_failed",
                tool=spec.name,
                detail=exc.detail,
                **ctx.to_log_dict(),
            )


# ---------------------------------------------------------------------------
# Server setup + ASGI mount.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _mcp_lifespan(_server: Server[Any, Any]) -> AsyncIterator[dict[str, Any]]:
    """Lifespan for the lowlevel ``Server`` instance.

    The lowlevel ``Server.run`` builds and tears down its session per call in
    stateless mode, so emitting from here happens on every transport
    connection; clients de-dup that notification.

    The yielded dict is the ``lifespan_context`` passed into every handler via
    ``request_ctx.lifespan_context``. The dispatcher reads its engine from
    ``app.state`` directly, not from lifespan, so the context is empty.
    """
    _log.info("mcp.server.lifespan.start")
    try:
        yield {}
    finally:
        _log.info("mcp.server.lifespan.stop")


def build_server(registry: ToolRegistry, dispatcher: MCPDispatcher) -> Server[Any, Any]:
    """Construct the lowlevel ``Server`` with list_tools / call_tool wired."""
    server = Server[Any, Any](
        name="stackos",
        version=__version__,
        instructions=(
            "StackOS MCP server — local tool runtime for projects, plugins, "
            "workflow templates, run plans, resources, actions, and audit. "
            "All project-scoped tools require explicit `project_id`. Mutating tools "
            "return `{data, run_id, project_id}`; reads return bare data. Pass "
            "`run_token` from `runPlan.start` to execute granted run-plan steps."
        ),
        lifespan=_mcp_lifespan,
    )

    @server.list_tools()
    async def _handle_list_tools() -> list[
        mcp_types.Tool
    ]:  # pragma: no cover — covered by integration tests
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
    """Mount the MCP Streamable HTTP transport at ``/mcp`` on a FastAPI app.

    Wires:

    1. Build the ``ToolRegistry`` and call ``stackos.mcp.tools.register_all``
       to populate it.
    2. Run the envelope-discipline check; raises if a mutating tool
       declared a bare output type.
    3. Build the dispatcher with a closure over ``app.state.engine``.
    4. Build the lowlevel ``Server`` with list/call decorators wired.
    5. Construct ``StreamableHTTPSessionManager`` and mount its ASGI
       handler at ``/mcp``.
    6. Chain the manager's lifespan into FastAPI's lifespan so the
       transport task group is alive whenever the daemon serves
       requests.

    The bearer-token middleware in ``stackos.auth`` already covers
    ``/mcp/*`` via ``PROTECTED_PREFIXES``; we don't re-check auth here.
    """
    # Lazy import to avoid a top-level cycle during package import.
    from stackos.mcp.tools import register_all
    from stackos.operations.registry import build_operation_registry

    operation_registry = build_operation_registry()
    registry = ToolRegistry()
    register_all(registry)
    assert_envelope_discipline(registry)
    _log.info("mcp.server.registered", tool_count=len(registry))

    def _engine_resolver() -> Any:
        engine = getattr(app.state, "engine", None)
        if engine is None:  # pragma: no cover — only hit if lifespan didn't run
            raise RuntimeError("MCP engine not initialised on app.state")
        return engine

    def _settings_resolver() -> Any:
        settings = getattr(app.state, "settings", None)
        if settings is None:  # pragma: no cover — lifespan did not finish
            raise RuntimeError("settings not initialised on app.state")
        return settings

    dispatcher = MCPDispatcher(
        registry,
        _engine_resolver,
        _settings_resolver,
        operation_registry=operation_registry,
    )
    server = build_server(registry, dispatcher)

    # Stash on app.state so tests / introspection can reach the registry.
    app.state.mcp_server = server
    app.state.mcp_registry = registry
    app.state.mcp_dispatcher = dispatcher
    app.state.operation_registry = operation_registry

    # Build the streamable HTTP manager. ``json_response=True`` returns
    # JSON for non-streaming POSTs (the dominant Codex / Claude Code
    # case); streaming tools fall back to SSE automatically. ``stateless``
    # skips session-id correlation between calls — every JSON-RPC POST creates a
    # fresh transport. The run token correlates execution context, not the
    # transport session id, so stateless keeps the surface simple.
    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
    )
    app.state.mcp_session_manager = session_manager

    # Chain the manager's lifespan into FastAPI's existing lifespan. We
    # do this by wrapping the existing lifespan with one that enters the
    # manager's run() context.
    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _wrapped_lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            # Run FastAPI's existing lifespan (DB, auth, etc.) first so
            # ``app.state.engine`` is populated before the MCP manager
            # starts handling requests.
            await stack.enter_async_context(existing_lifespan(fastapi_app))
            await stack.enter_async_context(session_manager.run())
            yield

    app.router.lifespan_context = _wrapped_lifespan

    class _MCPASGIApp:
        """Class wrapper so Starlette's ``Route`` treats it as ASGI.

        Starlette inspects whether ``endpoint`` is a function or a class;
        functions are wrapped in ``request_response`` (request→response
        adapter), classes are called directly with ``(scope, receive,
        send)``. We need the latter so the session manager sees the raw
        ASGI scope and can drive its own request/response lifecycle.
        """

        def __init__(self, manager: StreamableHTTPSessionManager) -> None:
            self._manager = manager

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await self._manager.handle_request(scope, receive, send)

    asgi_app = _MCPASGIApp(session_manager)

    # Register a route that matches ``/mcp`` exactly (the JSON-RPC POST
    # endpoint Codex / Claude Code use) plus a mount that catches any
    # ``/mcp/<sub>`` path the SSE stream / DELETE-terminate flows want.
    # The transport itself routes on HTTP method (POST = JSON-RPC,
    # GET = SSE stream, DELETE = session terminate).
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
