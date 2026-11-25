"""Authenticated native-main stream, independent of browser/window lifetime."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from stackos.api.deps import get_operation_registry
from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime
from stackos.operations.desktop import AVAILABILITY_PATH

router = APIRouter(tags=["desktop"])


@router.post(AVAILABILITY_PATH)
async def native_availability(request: Request) -> StreamingResponse:
    get_operation_registry(request).get("desktop.availability", surface="rest")
    connection_id = getattr(request.state, "local_runtime_connection_id", None)
    scope = getattr(request.state, "auth_scope", None)
    if request.app.state.settings.process_role == "server":
        if scope != "local-runtime" or connection_id is None:
            raise HTTPException(403, "Native availability requires the bound computer credential.")
        from stackos.api.project_access import bind_user_session
        from stackos.repositories.workspaces import LocalRuntimeConnectionRepository

        with Session(request.app.state.engine) as session:
            bind_user_session(session, request, request.app.state.settings)
            # Middleware authenticated this exact credential; retain its scope.
            from stackos.db.models import LocalRuntimeConnection

            row = session.get(LocalRuntimeConnection, connection_id)
            if row is None:
                raise HTTPException(401, "Computer authorization unavailable")
            LocalRuntimeConnectionRepository(session).owned(row.project_id, connection_id)
    elif scope != "daemon-admin":
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

    connection = runtime.connect(connection_id=connection_id)
    return AvailabilityStream(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )
