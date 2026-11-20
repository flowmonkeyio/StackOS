"""Request-id middleware tests.

Every response carries an ``X-Request-Id`` header — fresh on outbound
requests, echoed back when the client supplied one inbound.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from stackos.api.errors import request_id_var
from stackos.logging import get_logger


def test_health_response_carries_request_id(api: TestClient) -> None:
    """Outbound requests get a freshly minted UUID."""
    resp = api.get("/api/v1/health")
    rid = resp.headers.get("x-request-id")
    assert rid is not None and len(rid) >= 32


def test_request_id_echoed_back_when_inbound_provided(api: TestClient) -> None:
    """A caller-supplied id passes through verbatim."""
    fake = "abcd-1234-deadbeef"
    resp = api.get("/api/v1/health", headers={"x-request-id": fake})
    assert resp.headers["x-request-id"] == fake


def test_request_id_unique_across_requests(api: TestClient) -> None:
    """Two consecutive requests get distinct ids by default."""
    rid1 = api.get("/api/v1/health").headers["x-request-id"]
    rid2 = api.get("/api/v1/health").headers["x-request-id"]
    assert rid1 != rid2


@pytest.mark.parametrize(
    "inbound", ["x" * 129, "private@example.test", "has space", "bad\nvalue", "\x7f", ""]
)
def test_unsafe_request_id_is_replaced(api, inbound):
    response = api.get("/api/v1/health", headers={"X-Request-Id": inbound})
    rid = response.headers["x-request-id"]
    assert str(UUID(rid)) == rid
    assert rid != inbound


def test_request_ids_reach_real_log_pipeline_without_cross_request_leakage(api, emitted_log):
    barrier = Barrier(2)

    @api.app.get("/request-id-proof/{key}")
    def log_request(key: str):
        if key in {"first", "second"}:
            barrier.wait(timeout=5)
        get_logger("request-id-proof").info("request-id-proof", key=key)
        return {"request_id": request_id_var.get()}

    # The source app's SPA mount is a catch-all; place this test-owned route before it.
    api.app.router.routes.insert(0, api.app.router.routes.pop())

    def call(key, rid):
        return api.get(f"/request-id-proof/{key}", headers={"X-Request-Id": rid})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(call, key, rid)
            for key, rid in [("first", "case-one"), ("second", "case-two")]
        ]
        for future, rid in zip(futures, ["case-one", "case-two"], strict=True):
            response = future.result()
            assert response.json()["request_id"] == response.headers["x-request-id"] == rid
    later = api.get("/request-id-proof/later")
    later_id = later.headers["x-request-id"]
    assert later_id not in {"case-one", "case-two"}
    invalid = call("invalid", "private@example.test")
    invalid_id = invalid.headers["x-request-id"]
    assert str(UUID(invalid_id)) == invalid_id
    bounded_id = "a" * 128
    assert call("bounded", bounded_id).headers["x-request-id"] == bounded_id
    get_logger("request-id-proof").info("request-id-proof", key="outside")
    events = [
        json.loads(line) for line in emitted_log.read_text().splitlines() if line.startswith("{")
    ]
    events = {event["key"]: event for event in events if event.get("event") == "request-id-proof"}
    assert events["first"]["request_id"] == "case-one"
    assert events["second"]["request_id"] == "case-two"
    assert events["later"]["request_id"] == later_id
    assert events["invalid"]["request_id"] == invalid_id
    assert events["bounded"]["request_id"] == bounded_id
    assert "private@example.test" not in emitted_log.read_text()
    assert "request_id" not in events["outside"]
    assert request_id_var.get() is None
