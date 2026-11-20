"""Catalog schemas remain authoritative through stored native execution."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select
from stackos_connectors.connectors.trackbooth.schema import _schema_property

from stackos.actions import ActionRepository
from stackos.db.models import Action
from stackos.repositories.base import ConflictError, ValidationError
from tests.integration.test_repositories.trackbooth_test_support import (
    _add_trackbooth_sync_responses,
    _sync_trackbooth_catalog,
    _trackbooth_credential_ref,
    _trackbooth_generated_action_ref,
)

pytestmark = pytest.mark.fresh_catalog

complete_schemas = pytest.mark.skipif(
    _schema_property(True) is not True,
    reason="Complete boolean schema projection requires the companion connector package",
)


@pytest.mark.parametrize(
    "schema,required,payload,valid",
    [
        (True, True, {}, False),
        (True, True, {"body": None}, True),
        (True, False, {}, True),
        (True, False, {"body": False}, True),
        (False, False, {}, True),
        (False, False, {"body": {}}, False),
        (False, True, {}, False),
        ({"type": "array", "items": {"type": "integer"}}, True, {"body": [0, 2]}, True),
        ({"type": "array", "items": {"type": "integer"}}, True, {"body": ["bad"]}, False),
        ({"type": "string", "minLength": 1}, True, {"body": "ok"}, True),
        ({"type": "string", "minLength": 1}, True, {"body": ""}, False),
        ({"type": "null"}, True, {"body": None}, True),
        ({"type": "object", "required": ["name"]}, False, {}, True),
        ({"type": "object", "required": ["name"]}, False, {"body": {}}, False),
    ],
)
@complete_schemas
def test_generated_action_preserves_body_shape_and_presence(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    schema: Any,
    required: bool,
    payload: dict,
    valid: bool,
) -> None:
    detail = {
        "operation_id": "FixturesController.shape",
        "method": "POST",
        "path": "/api/fixtures/shape",
        "body_schema": {"json_schema": schema, "required": required},
    }
    original = deepcopy(detail)
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, detail)
    synced = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(synced, detail["operation_id"])
    repo = ActionRepository(session)
    manifest = repo.describe(project_id=project_id, action_ref=action_ref).manifest
    assert manifest.input_schema_json["properties"]["body"] == schema
    assert ("body" in manifest.input_schema_json.get("required", [])) is required
    args = {
        "project_id": project_id,
        "action_ref": action_ref,
        "credential_ref": credential_ref,
        "input_json": payload,
    }
    before = len(httpx_mock.get_requests())
    assert repo.validate(**args).valid is valid
    if valid:
        httpx_mock.add_response(
            method="POST", url="https://trackbooth.local.test/api/fixtures/shape", json={"ok": True}
        )
        asyncio.run(repo.execute(**args))
        sent = httpx_mock.get_requests()[-1]
        if "body" in payload:
            assert json.loads(sent.content) == payload["body"]
        else:
            assert sent.content == b""
    else:
        with pytest.raises(ValidationError):
            asyncio.run(repo.execute(**args))
    assert len(httpx_mock.get_requests()) - before == int(valid)
    assert detail == original


@pytest.mark.parametrize(
    "schema,query,valid", [(True, {}, True), (False, {}, False), (True, None, False)]
)
@complete_schemas
def test_generated_action_preserves_boolean_query(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    schema: bool,
    query: dict | None,
    valid: bool,
) -> None:
    detail = {
        "operation_id": "FixturesController.queryShape",
        "method": "GET",
        "path": "/api/fixtures/query-shape",
        "query_schema": {"json_schema": schema},
    }
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, detail)
    synced = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(synced, detail["operation_id"])
    repo = ActionRepository(session)
    args = {
        "project_id": project_id,
        "action_ref": action_ref,
        "credential_ref": credential_ref,
        "input_json": {"query": query},
    }
    before = len(httpx_mock.get_requests())
    assert repo.validate(**args).valid is valid
    if valid:
        httpx_mock.add_response(
            method="GET",
            url="https://trackbooth.local.test/api/fixtures/query-shape",
            json={"ok": True},
        )
        asyncio.run(repo.execute(**args))
    else:
        with pytest.raises(ValidationError):
            asyncio.run(repo.execute(**args))
    assert len(httpx_mock.get_requests()) - before == int(valid)


@pytest.mark.skipif(_schema_property(True) is True, reason="Pinned-package rejection control")
@pytest.mark.parametrize(
    "descriptor",
    [
        {"json_schema": True},
        {"json_schema": {"type": "string"}},
        {"json_schema": {"type": "object"}, "required": True},
        {"json_schema": {"type": "object", "required": ["name"]}, "required": False},
    ],
)
def test_unsupported_schema_does_not_publish_partial_inventory(
    session: Session, project_id: int, httpx_mock: HTTPXMock, descriptor: dict
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    endpoints = [
        {"operation_id": "Fixtures.valid", "method": "GET", "path": "/api/fixtures/valid"},
        {
            "operation_id": "Fixtures.unsupported",
            "method": "POST",
            "path": "/api/fixtures/unsupported",
            "body_schema": descriptor,
        },
    ]
    _add_trackbooth_sync_responses(httpx_mock, *endpoints)
    with pytest.raises(ConflictError):
        _sync_trackbooth_catalog(session, project_id, credential_ref)
    assert not any(
        (row.config_json or {}).get("trackbooth_operation_id")
        in {"Fixtures.valid", "Fixtures.unsupported"}
        for row in session.exec(select(Action)).all()
    )
