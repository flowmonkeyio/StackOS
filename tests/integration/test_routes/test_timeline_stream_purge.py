"""Global HTTP streams retain freshness when project purge reuses event IDs."""

import asyncio
import json
import threading
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import event
from sqlmodel import Session

from stackos.api import context
from stackos.context import ContextRepository
from stackos.events import StackOSEvent, StackOSEventSource
from stackos.repositories.project_events import StackOSEventEmitter
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from tests.helpers.agent_statistics import seed_statistics_workload
from tests.integration.test_repositories.test_agent_statistics import _receipt


class GlobalHTTPStream:
    def __init__(self, api, params):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "root_path": "",
            "path": "/api/v1/context/timeline/stream",
            "query_string": urlencode(params).encode(),
            "headers": [
                (b"host", b"127.0.0.1:5180"),
                *[(key.encode(), value.encode()) for key, value in api.headers.items()],
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 5180),
        }
        self.task = asyncio.create_task(api.app(scope, self.incoming.get, self.outgoing.put))

    async def frame(self, name):
        async with asyncio.timeout(5):
            while True:
                message = await self.outgoing.get()
                if message["type"] == "http.response.start":
                    assert message["status"] == 200, message
                body = message.get("body", b"").decode()
                if f"event: {name}\n" in body:
                    data = next(line[6:] for line in body.splitlines() if line.startswith("data: "))
                    return body, json.loads(data)

    async def close(self):
        await self.incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(self.task, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("ui", [False, True])
@pytest.mark.parametrize("regrowth", ["below", "equal", "above"])
async def test_global_private_watermark_survives_purge_and_regrowth(
    api, project_id, monkeypatch, record_property, ui, regrowth
):
    engine = api.app.state.engine
    with Session(engine) as session:
        seed_statistics_workload(session, project_id, tasks=1, receipts=1)
        TrackerRepository(session).update_task(
            project_id=project_id, task_key="perf-task-0", patch_json={"status": "in-progress"}
        )
        baseline = ContextRepository(session).latest_tracker_status_event_id(project_id=None)
        assert baseline is not None
        other = (
            ProjectRepository(session)
            .create(slug="watermark-purge", name="Watermark purge fixture", domain="fixture.test")
            .data
        )
        emitter = StackOSEventEmitter(session)
        for _ in range(20):
            emitter.emit(
                StackOSEvent(
                    project_id=other.id,
                    event_type="artifact.created",
                    source=StackOSEventSource(type="artifact"),
                )
            )
        session.commit()
        watermark = ContextRepository(session).latest_event_id(project_id=None)
        assert watermark is not None and watermark > baseline

    # Hold the next stream SQL read while real cleanup/receipt writes commit.
    # This proves regrowth before observation, without relying on poll timing.
    freeze, paused, release = (threading.Event() for _ in range(3))
    owned = threading.local()

    class StreamSession(Session):
        def __enter__(self):
            owned.active = True
            return super().__enter__()

        def __exit__(self, *args):
            try:
                return super().__exit__(*args)
            finally:
                owned.active = False

    def before_read(_connection, _cursor, sql, *_):
        if getattr(owned, "active", False) and freeze.is_set() and "FROM project_events" in sql:
            paused.set()
            assert release.wait(5)

    monkeypatch.setattr(context, "Session", StreamSession)
    event.listen(engine, "before_cursor_execute", before_read)
    params = {"after": baseline}
    if ui:
        params["ui_demand"] = json.dumps(
            {
                "projects": {
                    str(project_id): {
                        "task_keys": ["perf-task-0"],
                        "ticket_keys": ["perf-ticket-0"],
                    }
                }
            }
        )
    stream = GlobalHTTPStream(api, params)
    try:
        _, initial = await stream.frame("tracker-usage")
        if ui:
            assert initial["updates"]["tasks"][0]["agent_stats"]["reported_usage"]["total"] == 11
        freeze.set()
        async with asyncio.timeout(2):
            while not paused.is_set():
                await asyncio.sleep(0.001)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://127.0.0.1:5180",
            headers=api.headers,
        ) as client:
            deleted = await client.post(
                "/api/v1/operations/project.delete/call",
                json={
                    "arguments": {
                        "project_id": other.id,
                        "confirmation": {
                            "permanent": True,
                            "project_id": other.id,
                            "project_slug": other.slug,
                        },
                        "response_mode": "raw",
                    }
                },
            )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"]["status"] == "deleted"
        with Session(engine) as session:
            TrackerRepository(session).update_ticket(
                project_id=project_id,
                ticket_key="perf-ticket-0",
                patch_json={
                    "completion_evidence_json": {
                        "automatic_delivery": {"attempt": 1, "delivery": _receipt(total=73)}
                    }
                },
            )
            repository = ContextRepository(session)
            expected = repository.tracker_status_timeline(
                project_id=None, after_id=baseline, include_total=False
            ).items
            assert expected and baseline < expected[-1].id < watermark
            target = watermark + (regrowth == "above")
            if regrowth != "below":
                emitter = StackOSEventEmitter(session)
                for _ in range(target - expected[-1].id):
                    emitter.emit(
                        StackOSEvent(
                            project_id=project_id,
                            event_type="artifact.created",
                            source=StackOSEventSource(type="artifact"),
                        )
                    )
                session.commit()
            latest = repository.latest_event_id(project_id=None)
            assert latest == (expected[-1].id if regrowth == "below" else target)
        record_property(
            "purge_cursor",
            json.dumps(
                {
                    "baseline": baseline,
                    "old_watermark": watermark,
                    "current_max": latest,
                    "expected_status": [item.id for item in expected],
                }
            ),
        )
        release.set()
        usage_body, updated = await stream.frame("tracker-usage")
        assert "\nid:" not in usage_body
        if ui:
            updates = updated["updates"]
            assert updates["replace"] and updates["scope_complete"]
            assert {item["project_id"] for item in updates["projects"]} == {project_id}
            assert updates["tasks"][0]["agent_stats"]["reported_usage"]["total"] == 73
            assert updates["tickets"][0]["agent_stats"]["reported_usage"]["total"] == 73
        else:
            assert updated["updates"] is None
        for item in expected:
            body, status = await stream.frame("tracker-status")
            assert status["id"] == item.id and f"id: {item.id}\n" in body
    finally:
        release.set()
        await stream.close()
        event.remove(engine, "before_cursor_execute", before_read)
