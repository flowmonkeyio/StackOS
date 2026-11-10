"""Exact computer credentials, never browser login, own online native streams."""

import asyncio

from tests.integration.test_routes.test_local_runtime_exchange import connected as connected


def test_server_stream_is_bound_and_disconnect_retires_it(connected):
    client, _settings, identity = connected

    async def probe():
        messages = asyncio.Queue()
        await messages.put({"type": "http.request", "body": b"", "more_body": False})
        ready = asyncio.Event()
        statuses = []

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])
                assert message["status"] == 200
            if message["type"] == "http.response.body" and b"event: ready" in message.get(
                "body", b""
            ):
                ready.set()

        path = "/api/v1/desktop/availability"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "http_version": "1.1",
            "server": ("127.0.0.1", 18510),
            "client": ("127.0.0.1", 12345),
            "headers": [
                (b"host", b"127.0.0.1:18510"),
                (b"origin", b"http://127.0.0.1:18510"),
                (b"authorization", client.headers["Authorization"].encode()),
            ],
        }
        request = asyncio.create_task(client.app(scope, messages.get, send))
        runtime = client.app.state.workflow_schedules
        waiter = asyncio.create_task(ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {waiter, request}, timeout=5, return_when=asyncio.FIRST_COMPLETED
            )
            if request in done:
                await request
            assert waiter in done, statuses
            assert statuses == [200]
            assert set(runtime.connections) == {identity["connection_id"]}
            assert runtime.connection is None
            await messages.put({"type": "http.disconnect"})
            await asyncio.wait_for(request, 5)
            assert runtime.connections == {}
        finally:
            waiter.cancel()
            request.cancel()
            await asyncio.gather(waiter, request, return_exceptions=True)

    client.portal.call(probe)


def test_browser_login_cannot_advertise_native_availability(connected):
    client, _settings, _identity = connected
    client.headers.pop("Authorization")
    login = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": "password123"}
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.post("/api/v1/desktop/availability").status_code == 403
    assert client.app.state.workflow_schedules.connections == {}


def test_runtime_credential_cannot_setup_schedules(connected):
    client, _settings, identity = connected
    result = client.post(
        "/api/v1/operations/schedule.set/call",
        json={
            "arguments": {
                "project_id": identity["project_id"],
                "kind": "draft",
                "cron_expr": "* * * * *",
            }
        },
    )
    assert result.status_code == 403


def test_owner_schedule_operations_preserve_project_scope(connected):
    client, _settings, identity = connected
    client.headers.pop("Authorization")
    login = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": "password123"}
    )
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    def operation(name, **arguments):
        return client.post(
            f"/api/v1/operations/schedule.{name}/call",
            json={"arguments": arguments | {"response_mode": "raw"}},
        )

    project_id = identity["project_id"]
    created = operation("set", project_id=project_id, kind="owned", cron_expr="* * * * *")
    assert created.status_code == 200, created.text
    job_id = created.json()["data"]["id"]
    assert client.get(f"/api/v1/projects/{project_id}/schedules").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/workflow-templates").status_code == 200
    assert (
        operation("toggle", project_id=project_id, job_id=job_id, enabled=False).status_code == 200
    )
    other = client.post(
        "/api/v1/projects", json={"slug": "other", "name": "Other", "domain": "example.test"}
    ).json()["data"]["id"]
    assert operation("remove", project_id=other, job_id=job_id).status_code == 404
    assert operation("runs", project_id=other, schedule_id=job_id).status_code == 404
    assert operation("remove", project_id=project_id, job_id=job_id).status_code == 200
