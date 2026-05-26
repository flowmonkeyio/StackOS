"""Protocol-neutral operation dispatcher."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast, get_origin

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete
from sqlmodel import Session

from stackos.config import Settings
from stackos.db.models import IdempotencyKey
from stackos.mcp.context import bind_context, build_context
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.permissions import check_call_grant
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.registry import OperationRegistry
from stackos.repositories.base import (
    ConflictError,
    IdempotencyReplayError,
    RepositoryError,
    ValidationError,
)
from stackos.repositories.runs import IdempotencyKeyRepository


@dataclass(frozen=True)
class OperationDispatchResult:
    payload: dict[str, Any]
    duration_ms: int


class OperationDispatcher:
    """Validate, authorize, and execute one operation call."""

    def __init__(self, registry: OperationRegistry) -> None:
        self._registry = registry

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        session: Session,
        surface: str,
        settings: Settings | None = None,
    ) -> OperationDispatchResult:
        spec = self._registry.get(name, surface=surface)
        try:
            parsed = spec.input_model.model_validate(arguments or {})
        except PydanticValidationError as exc:
            raise ValidationError(
                "operation input validation failed",
                data={"operation": name, "errors": exc.errors()},
            ) from exc

        ctx = build_context(arguments, session)
        ctx.extras["surface"] = surface
        if settings is not None:
            ctx.extras["settings"] = settings
        with bind_context(ctx):
            if (
                ctx.run is not None
                and ctx.project_id is not None
                and ctx.run.project_id is not None
                and ctx.project_id != ctx.run.project_id
            ):
                raise ToolNotGrantedError(
                    "run_token is not scoped to this project",
                    data={
                        "operation": spec.name,
                        "run_id": ctx.run_id,
                        "run_project_id": ctx.run.project_id,
                        "requested_project_id": ctx.project_id,
                    },
                )
            check_call_grant(spec.name, ctx, parsed)
            if (
                not spec.read_only
                and ctx.idempotency_key is not None
                and ctx.project_id is not None
            ):
                cached = self._idempotency_check(spec.name, ctx)
                if cached is not None:
                    return OperationDispatchResult(payload=cached, duration_ms=0)
            try:
                started = time.perf_counter()
                result = await spec.handler(parsed, ctx, ProgressEmitter(None, None))
                duration_ms = int((time.perf_counter() - started) * 1000)
            except RepositoryError:
                if (
                    not spec.read_only
                    and ctx.idempotency_key is not None
                    and ctx.project_id is not None
                ):
                    self._idempotency_forget(spec.name, ctx)
                raise
            except Exception as exc:
                if (
                    not spec.read_only
                    and ctx.idempotency_key is not None
                    and ctx.project_id is not None
                ):
                    self._idempotency_forget(spec.name, ctx)
                raise RepositoryError(
                    "operation handler failed",
                    data={"operation": name, "error": f"{type(exc).__name__}: {exc}"},
                ) from exc

            payload = _result_to_json(result)
            if ctx.run is not None and ctx.run.project_id is not None:
                seen = _find_mismatched_project_id(payload, ctx.run.project_id)
                if seen is not None:
                    raise ToolNotGrantedError(
                        "run_token cannot access data from another project",
                        data={
                            "operation": spec.name,
                            "run_id": ctx.run_id,
                            "run_project_id": ctx.run.project_id,
                            "result_project_id": seen,
                        },
                    )
            if (
                not spec.read_only
                and ctx.idempotency_key is not None
                and ctx.project_id is not None
            ):
                self._idempotency_record(spec.name, ctx, payload)
            return OperationDispatchResult(payload=payload, duration_ms=duration_ms)

    def _idempotency_check(self, operation_name: str, ctx: Any) -> dict[str, Any] | None:
        repo = IdempotencyKeyRepository(ctx.session)
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        try:
            repo.check_or_create(
                project_id=ctx.project_id,
                tool_name=operation_name,
                idempotency_key=ctx.idempotency_key,
                run_id=ctx.run_id,
            )
        except IdempotencyReplayError as exc:
            cached = exc.data.get("response_json")
            if cached is not None and isinstance(cached, dict):
                return cached
            raise ConflictError(
                "idempotency key is already in-flight",
                data={
                    "idempotency_key": ctx.idempotency_key,
                    "run_id": exc.data.get("run_id"),
                    "tool_name": operation_name,
                    "project_id": ctx.project_id,
                    "replay": True,
                    "in_flight": True,
                },
            ) from exc
        return None

    def _idempotency_forget(self, operation_name: str, ctx: Any) -> None:
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        ctx.session.exec(
            delete(IdempotencyKey).where(
                cast(Any, IdempotencyKey.project_id) == ctx.project_id,
                cast(Any, IdempotencyKey.tool_name) == operation_name,
                cast(Any, IdempotencyKey.idempotency_key) == ctx.idempotency_key,
            )
        )
        ctx.session.commit()

    def _idempotency_record(
        self,
        operation_name: str,
        ctx: Any,
        payload: dict[str, Any],
    ) -> None:
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        IdempotencyKeyRepository(ctx.session).update_response(
            project_id=ctx.project_id,
            tool_name=operation_name,
            idempotency_key=ctx.idempotency_key,
            response_json=payload,
        )


def _result_to_json(result: Any) -> dict[str, Any]:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json", by_alias=True)
    if isinstance(result, list):
        return {"items": [_item_to_json(item) for item in result]}
    if isinstance(result, dict):
        return {
            key: (_result_to_json(value) if isinstance(value, BaseModel) else value)
            for key, value in result.items()
        }
    return {"value": result}


def _item_to_json(item: Any) -> Any:
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json", by_alias=True)
    if isinstance(item, dict):
        return {
            key: (_item_to_json(value) if not _is_scalar(value) else value)
            for key, value in item.items()
        }
    if isinstance(item, list):
        return [_item_to_json(value) for value in item]
    return item


def _is_scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool | type(None))


def _find_mismatched_project_id(value: Any, expected: int) -> int | None:
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


def is_write_envelope(tp: Any) -> bool:
    origin = get_origin(tp) or tp
    if origin is WriteEnvelope:
        return True
    try:
        return isinstance(origin, type) and issubclass(origin, WriteEnvelope)
    except TypeError:
        return False


__all__ = ["OperationDispatchResult", "OperationDispatcher", "is_write_envelope"]
