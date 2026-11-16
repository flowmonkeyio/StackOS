"""Canonical dependencies travel through real catalog storage and execution."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.db.models import Action
from stackos.repositories.base import ValidationError
from tests.integration.test_repositories.trackbooth_test_support import (
    _add_trackbooth_sync_responses,
    _sync_trackbooth_catalog,
    _trackbooth_credential_ref,
    _trackbooth_generated_action_ref,
)

pytestmark = pytest.mark.fresh_catalog


def _assignment_update_detail() -> dict:
    # Synthetic provider schema: these names express the requested edge cases,
    # without claiming that any deployed Trackbooth endpoint has this contract.
    return {
        "operation_id": "FixturesController.updateAssignments",
        "method": "PATCH",
        "path": "/api/fixtures/assignments",
        "checksum": "assignments-v1",
        "body_schema": {
            "json_schema": {
                "type": "object",
                "properties": {
                    "assignments": {"type": "array", "items": {"type": "string"}},
                    "version": {"type": "integer", "minimum": 0},
                },
                "dependentRequired": {"assignments": ["version"]},
                "additionalProperties": False,
                "x-trackbooth-constraints": {"opaque": ["provider-owned"]},
            },
        },
    }


def test_catalog_repairs_dependency_constraints_with_unchanged_provider_hashes(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _assignment_update_detail()
    canonical = deepcopy(detail["body_schema"]["json_schema"])
    repo = ActionRepository(session)
    _add_trackbooth_sync_responses(httpx_mock, detail, catalog_hash="same-provider-hash")
    first = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(first, detail["operation_id"])
    described = repo.describe(project_id=project_id, action_ref=action_ref)
    assert described.manifest.input_schema_json["properties"]["body"] == canonical
    row = next(
        row
        for row in session.exec(select(Action)).all()
        if (row.config_json or {}).get("public_action_key")
        == action_ref.removeprefix("trackbooth.")
    )
    assert row.input_schema_json["properties"]["body"] == canonical
    provider_hash = row.config_json["inventory_catalog_hash"]
    endpoint_checksum = row.config_json["inventory_endpoint_checksum"]
    stale = deepcopy(row.input_schema_json)
    stale["properties"]["body"].pop("dependentRequired")
    stale["properties"]["body"].pop("x-trackbooth-constraints")
    stale["properties"]["body"]["additionalProperties"] = True
    row.input_schema_json = stale
    row.config_json = {**row.config_json, "inventory_manifest_hash": "legacy-projection"}
    session.add(row)
    session.commit()

    _add_trackbooth_sync_responses(httpx_mock, detail, catalog_hash="same-provider-hash")
    repaired = _sync_trackbooth_catalog(session, project_id, credential_ref)
    session.refresh(row)
    assert repaired["updated"] == 1 and repaired["skipped"] == 0
    assert row.input_schema_json["properties"]["body"] == canonical
    assert row.config_json["inventory_catalog_hash"] == provider_hash
    assert row.config_json["inventory_endpoint_checksum"] == endpoint_checksum
    assert detail["body_schema"]["json_schema"] == canonical

    _add_trackbooth_sync_responses(httpx_mock, detail, catalog_hash="same-provider-hash")
    unchanged = _sync_trackbooth_catalog(session, project_id, credential_ref)
    assert unchanged["updated"] == 0 and unchanged["skipped"] == 1


@pytest.mark.parametrize(
    "body,valid",
    [
        ({"assignments": []}, False),
        ({"assignments": ["one"]}, False),
        ({"assignments": [], "version": 0}, True),
        ({"assignments": ["one"], "version": 0}, True),
        ({}, True),
        ({"extra": True}, False),
    ],
)
def test_generated_action_enforces_dependencies_before_http(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    body: dict,
    valid: bool,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _assignment_update_detail()
    _add_trackbooth_sync_responses(httpx_mock, detail)
    synced = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(synced, detail["operation_id"])
    repo = ActionRepository(session)
    args = {
        "project_id": project_id,
        "action_ref": action_ref,
        "credential_ref": credential_ref,
        "input_json": {"body": body},
    }
    before = len(httpx_mock.get_requests())
    validation = repo.validate(**args)
    assert validation.valid is valid
    if valid:
        assert validation.issues == []
        httpx_mock.add_response(
            method="PATCH",
            url="https://trackbooth.local.test/api/fixtures/assignments",
            json={"data": {"updated": True}},
        )
        result = asyncio.run(repo.execute(**args)).data
        assert result.output_json["data"] == {"data": {"updated": True}}
        request = httpx_mock.get_requests()[-1]
        assert json.loads(request.content) == body
    else:
        expected = (
            ("$.body.extra", "additional_property")
            if "extra" in body
            else ("$.body.version", "dependent_required")
        )
        assert expected in {(issue.path, issue.code) for issue in validation.issues}
        with pytest.raises(ValidationError) as failed:
            asyncio.run(repo.execute(**args))
        assert expected in {(issue["path"], issue["code"]) for issue in failed.value.data["issues"]}
    assert len(httpx_mock.get_requests()) - before == int(valid)
