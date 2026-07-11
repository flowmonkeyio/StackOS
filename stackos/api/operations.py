"""Generic StackOS operation REST adapter."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from stackos.api.deps import get_session, get_settings
from stackos.config import Settings
from stackos.logging import get_logger
from stackos.operations.dispatcher import OperationDispatcher
from stackos.operations.registry import build_operation_registry
from stackos.operations.spec import OperationDescribeOut, OperationListOut

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


class OperationCallIn(BaseModel):
    """Generic operation-call envelope for REST and CLI clients."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "arguments": {
                    "project_id": 1,
                    "action_ref": "utils.sitemap.fetch",
                    "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                }
            }
        }
    )

    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("", response_model=OperationListOut)
async def list_operations(surface: str | None = None) -> OperationListOut:
    """List registered StackOS operations with surface and policy metadata."""
    registry = build_operation_registry()
    return registry.list_out(surface=surface)


@router.get("/{operation_name}", response_model=OperationDescribeOut)
async def describe_operation(operation_name: str) -> OperationDescribeOut:
    """Describe one operation with schemas, examples, and agent guidance."""
    registry = build_operation_registry()
    return registry.get(operation_name).describe_out()


@router.post("/{operation_name}/call")
async def call_operation(
    operation_name: str,
    payload: OperationCallIn,
    client_surface: str | None = Header(default=None, alias="X-StackOS-Client-Surface"),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Call one REST-enabled operation through the shared dispatcher."""
    registry = build_operation_registry()
    normalized_client_surface = (
        client_surface.strip().lower() if isinstance(client_surface, str) else None
    )
    if normalized_client_surface not in {"cli"}:
        normalized_client_surface = None
    result = await OperationDispatcher(registry).dispatch(
        operation_name,
        payload.arguments,
        session=session,
        surface="rest",
        client_surface=normalized_client_surface,
        settings=settings,
    )
    if result.duration_ms >= 100:
        get_logger("stackos.api.operations").warning(
            "operation.request.slow",
            operation=operation_name,
            duration_ms=result.duration_ms,
            response_mode=(payload.arguments or {}).get("response_mode"),
        )
    return JSONResponse(
        content=jsonable_encoder(result.payload),
        headers={
            "Server-Timing": f"stackos-operation;dur={result.duration_ms}",
            "X-StackOS-Operation-Duration-Ms": str(result.duration_ms),
        },
    )


__all__ = ["router"]
