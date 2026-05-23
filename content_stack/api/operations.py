"""Generic StackOS operation REST adapter."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from content_stack.api.deps import get_session, get_settings
from content_stack.config import Settings
from content_stack.operations.dispatcher import OperationDispatcher
from content_stack.operations.registry import build_operation_registry
from content_stack.operations.spec import OperationDescribeOut, OperationListOut

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
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Call one REST-enabled operation through the shared dispatcher."""
    registry = build_operation_registry()
    result = await OperationDispatcher(registry).dispatch(
        operation_name,
        payload.arguments,
        session=session,
        surface="rest",
        settings=settings,
    )
    return JSONResponse(content=jsonable_encoder(result.payload))


__all__ = ["router"]
