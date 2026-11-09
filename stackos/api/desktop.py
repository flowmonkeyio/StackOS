"""Authenticated native-main stream, independent of browser/window lifetime."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime
from stackos.operations.desktop import AVAILABILITY_PATH
from stackos.operations.registry import build_operation_registry

router = APIRouter(tags=["desktop"])


@router.post(AVAILABILITY_PATH)
async def native_availability(request: Request) -> StreamingResponse:
    build_operation_registry().get("desktop.availability", surface="rest")
    if getattr(request.state, "auth_scope", None) != "daemon-admin":
        raise HTTPException(403, "Native desktop availability requires daemon authentication.")
    runtime: WorkflowScheduleRuntime = request.app.state.workflow_schedules

    class AvailabilityStream(StreamingResponse):
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            try:
                await super().__call__(scope, receive, send)
            finally:
                runtime.disconnect(connection)

        async def listen_for_disconnect(self, receive: Receive) -> None:
            try:
                await super().listen_for_disconnect(receive)
            finally:
                runtime.disconnect(connection)

    async def events():
        try:
            yield "event: ready\ndata: {}\n\n"
            await asyncio.Event().wait()
        finally:
            runtime.disconnect(connection)

    connection = runtime.connect()
    return AvailabilityStream(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )
