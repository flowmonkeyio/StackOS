"""MCP direct, grant, response-file, and audit proofs for Amazon S3 actions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from .conftest import MCPClient

_ACCESS_KEY_ID = "AKIAEXPLICIT12345678"
_SECRET_ACCESS_KEY = "s3-mcp-secret-access-key"
_SESSION_TOKEN = "s3-mcp-session-token"


class _FakeS3:
    def __init__(self, *, list_error: Exception | None = None) -> None:
        self.list_error = list_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_objects_v2", kwargs))
        if self.list_error is not None:
            raise self.list_error
        return {
            "Contents": [
                {
                    "Key": "reports/current.csv",
                    "Size": 42,
                    "ETag": '"etag-current"',
                    "StorageClass": "STANDARD",
                }
            ],
            "CommonPrefixes": [{"Prefix": "reports/archive/"}],
            "KeyCount": 2,
            "IsTruncated": True,
            "EncodingType": "url",
            "NextContinuationToken": "opaque-next-page",
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-list",
                "HostId": "extended-list",
            },
        }

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        return {
            "ETag": '"marker-etag"',
            "VersionId": "marker-version",
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-marker",
                "HostId": "extended-marker",
            },
        }


def _credential_ref(mcp: MCPClient, project_id: int) -> str:
    status = mcp.call_tool_structured(
        "connection.list",
        {
            "project_id": project_id,
            "provider_key": "aws-s3",
            "response_mode": "raw",
        },
    )
    return status["accounts"][0]["credential_ref"]


def _create_s3_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        "/api/v1/auth/accounts/aws-s3",
        json={
            "auth_method_key": "aws-access-key",
            "display_name": "Amazon S3 - Test Bucket",
            "attach_project_id": project_id,
            "fields": {
                "access_key_id": _ACCESS_KEY_ID,
                "secret_access_key": _SECRET_ACCESS_KEY,
                "session_token": _SESSION_TOKEN,
                "bucket": "stackos-mcp-fixture",
                "region": "us-west-2",
            },
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return _credential_ref(mcp, project_id)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeS3,
) -> list[dict[str, Any]]:
    import stackos.actions.s3 as s3_module

    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> _FakeS3:
        factory_calls.append(kwargs)
        return client

    monkeypatch.setattr(s3_module, "create_s3_client", factory)
    return factory_calls


def _run_plan(
    action_ref: str,
    *,
    key: str,
    step_id: str,
    grant_action_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": key,
        "title": f"Execute {action_ref}",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": step_id,
                    "tool": "action.execute",
                    "action_refs": [grant_action_ref or action_ref],
                }
            ]
        },
        "steps": [
            {
                "id": step_id,
                "title": f"Execute {action_ref}",
                "action_refs": [action_ref],
            }
        ],
    }


def _start_and_claim(
    mcp: MCPClient,
    project_id: int,
    run_plan_json: dict[str, Any],
    *,
    step_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    created = mcp.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": run_plan_json},
    )
    started = mcp.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    claimed = mcp.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": step_id,
            "run_token": started["data"]["run_token"],
        },
    )
    return created, started, claimed


def _action_calls(
    mcp: MCPClient,
    *,
    project_id: int,
    action_key: str,
    status: str,
    run_id: int | None = None,
    run_plan_id: int | None = None,
    run_plan_step_id: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "plugin_slug": "utils",
        "action_key": action_key,
        "status": status,
    }
    if run_id is not None:
        params["run_id"] = run_id
    if run_plan_id is not None:
        params["run_plan_id"] = run_plan_id
    if run_plan_step_id is not None:
        params["run_plan_step_id"] = run_plan_step_id
    response = mcp.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params=params,
        headers=mcp._headers(),
    )
    assert response.status_code == 200
    return response.json()


def _assert_no_credentials(value: Any) -> None:
    rendered = json.dumps(value)
    assert _ACCESS_KEY_ID not in rendered
    assert _SECRET_ACCESS_KEY not in rendered
    assert _SESSION_TOKEN not in rendered


def test_s3_directory_list_action_run_persists_response_file_and_direct_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    factory_calls = _patch_client(monkeypatch, client)
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_s3_credential(mcp_client, project_id)

    arguments = {
        "project_id": project_id,
        "action_ref": "utils.s3.directory.list",
        "credential_ref": credential_ref,
        "input_json": {
            "prefix": "reports/",
            "delimiter": "/",
            "page_size": 25,
        },
        "idempotency_key": "s3-direct-list-1",
    }
    out = mcp_client.call_tool_structured("action.run", arguments)
    replay = mcp_client.call_tool_structured("action.run", arguments)

    data = out["data"]
    assert replay["idempotency_replay"] is True
    assert replay["data"] == data
    assert data["status"] == "success"
    assert data["action_ref"] == "utils.s3.directory.list"
    assert data["provider_key"] == "aws-s3"
    assert data["operation"] == "directory.list"
    pointer = data["output"]
    assert pointer["output_mode"] == "file"
    assert pointer["schema_version"] == "stackos.action-output.v1"
    assert pointer["schema_ref"] == "stackos.action-output.v1"
    assert pointer["schema_operation"] == "schema.get"
    saved = json.loads(Path(pointer["path"]).read_text(encoding="utf-8"))
    response = saved["response"]
    assert saved["action_call"]["id"] == data["action_call_id"]
    assert response["output_json"]["next_cursor"] == "opaque-next-page"
    assert response["output_json"]["objects"][0]["key"] == "reports/current.csv"
    assert factory_calls[0]["total_max_attempts"] == 3
    assert client.calls == [
        (
            "list_objects_v2",
            {
                "Bucket": "stackos-mcp-fixture",
                "Prefix": "reports/",
                "MaxKeys": 25,
                "EncodingType": "url",
                "Delimiter": "/",
            },
        )
    ]

    audit = _action_calls(
        mcp_client,
        project_id=project_id,
        action_key="s3.directory.list",
        status="success",
    )
    assert audit["total_estimate"] == 1
    row = audit["items"][0]
    assert row["id"] == data["action_call_id"]
    assert row["run_id"] is None
    assert row["run_plan_id"] is None
    assert row["response_json"]["output_mode"] == "file"
    assert row["response_json"]["file"]["schema_ref"] == "stackos.action-output.v1"
    _assert_no_credentials({"out": out, "replay": replay, "saved": saved, "audit": audit})


def test_s3_file_upload_action_run_uses_background_poll_and_terminal_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    _patch_client(monkeypatch, client)
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_s3_credential(mcp_client, project_id)
    source = tmp_path / "background.txt"
    source.write_bytes(b"background")

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.s3.file.upload",
            "credential_ref": credential_ref,
            "input_json": {
                "items": [
                    {
                        "local_path": str(source),
                        "destination_key": "uploads/background.txt",
                    }
                ],
                "conflict_policy": "fail",
                "error_policy": "stop",
                "follow_symlinks": False,
            },
            "idempotency_key": "s3-background-upload-1",
            "confirm_direct": True,
            "intent_summary": "Upload the explicit fixture file to the test bucket.",
        },
    )

    accepted = out["data"]
    assert accepted["status"] == "running"
    assert accepted["poll_operation"] == "actionCall.get"
    action_call_id = accepted["action_call_id"]
    deadline = time.monotonic() + 5
    terminal: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        polled = mcp_client.call_tool_structured(
            "actionCall.get",
            {
                "project_id": project_id,
                "action_call_id": action_call_id,
                "response_mode": "raw",
            },
        )
        if polled["status"] != "running":
            terminal = polled
            break
        time.sleep(0.01)

    assert terminal is not None
    assert terminal["status"] == "success"
    assert terminal["poll_operation"] is None
    pointer = terminal["output_json"]
    assert pointer["output_mode"] == "file"
    saved = json.loads(Path(pointer["file"]["path"]).read_text(encoding="utf-8"))
    response = saved["response"]["output_json"]
    assert response["status"] == "success"
    assert response["completed"][0]["destination_key"] == ("uploads/background.txt")
    assert client.calls == [
        (
            "put_object",
            {
                "Bucket": "stackos-mcp-fixture",
                "Key": "uploads/background.txt",
                "Body": b"background",
                "IfNoneMatch": "*",
            },
        )
    ]

    audit = _action_calls(
        mcp_client,
        project_id=project_id,
        action_key="s3.file.upload",
        status="success",
    )
    assert audit["total_estimate"] == 1
    assert audit["items"][0]["id"] == action_call_id
    assert audit["items"][0]["response_json"]["output_mode"] == "file"
    _assert_no_credentials({"out": out, "terminal": terminal, "saved": saved, "audit": audit})


def test_s3_directory_create_action_execute_uses_grant_response_file_and_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    factory_calls = _patch_client(monkeypatch, client)
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_s3_credential(mcp_client, project_id)
    step_id = "create-marker"
    created, started, claimed = _start_and_claim(
        mcp_client,
        project_id,
        _run_plan(
            "utils.s3.directory.create",
            key="utils.s3-directory-create.audit",
            step_id=step_id,
        ),
        step_id=step_id,
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.s3.directory.create",
            "credential_ref": credential_ref,
            "input_json": {"prefix": "reports/new"},
            "run_token": started["data"]["run_token"],
        },
    )

    data = out["data"]
    pointer = data["output"]
    assert data["status"] == "success"
    assert data["action_ref"] == "utils.s3.directory.create"
    assert pointer["output_mode"] == "file"
    assert pointer["schema_ref"] == "stackos.action-output.v1"
    assert pointer["schema_operation"] == "schema.get"
    saved = json.loads(Path(pointer["path"]).read_text(encoding="utf-8"))
    response = saved["response"]
    assert saved["action_call"]["id"] == data["action_call_id"]
    assert saved["run"]["run_id"] == started["data"]["run_id"]
    assert saved["run"]["run_plan_id"] == created["data"]["id"]
    assert saved["run"]["run_plan_step_id"] == claimed["data"]["id"]
    assert response["output_json"]["marker_key"] == "reports/new/"
    assert response["output_json"]["version_id"] == "marker-version"
    assert factory_calls[0]["total_max_attempts"] == 1
    assert client.calls == [
        (
            "put_object",
            {
                "Bucket": "stackos-mcp-fixture",
                "Key": "reports/new/",
                "Body": b"",
                "IfNoneMatch": "*",
            },
        )
    ]

    audit = _action_calls(
        mcp_client,
        project_id=project_id,
        action_key="s3.directory.create",
        status="success",
        run_id=started["data"]["run_id"],
        run_plan_id=created["data"]["id"],
        run_plan_step_id=claimed["data"]["id"],
    )
    assert audit["total_estimate"] == 1
    row = audit["items"][0]
    assert row["id"] == data["action_call_id"]
    assert row["response_json"]["output_mode"] == "file"
    assert row["response_json"]["file"]["schema_operation"] == "schema.get"
    _assert_no_credentials({"out": out, "saved": saved, "audit": audit})


def test_s3_action_execute_rejects_mismatched_step_grant_before_provider_call(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    _patch_client(monkeypatch, client)
    project_id = seeded_project["data"]["id"]
    step_id = "browse-s3"
    _created, started, _claimed = _start_and_claim(
        mcp_client,
        project_id,
        _run_plan(
            "utils.s3.directory.list",
            key="utils.s3-directory-list.denied",
            step_id=step_id,
            grant_action_ref="utils.s3.file.delete",
        ),
        step_id=step_id,
    )

    err = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.s3.directory.list",
            "input_json": {"prefix": "", "delimiter": "/", "page_size": 25},
            "run_token": started["data"]["run_token"],
        },
    )

    assert err["code"] == -32007
    assert "arguments do not match" in err["data"]["detail"]
    assert client.calls == []
    _assert_no_credentials(err)


def test_s3_provider_failure_is_safe_in_mcp_error_and_failed_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": f"denied {_SECRET_ACCESS_KEY}",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 403,
                "RequestId": "request-denied",
                "HostId": "extended-denied",
            },
        },
        "ListObjectsV2",
    )
    client = _FakeS3(list_error=denied)
    _patch_client(monkeypatch, client)
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_s3_credential(mcp_client, project_id)

    err = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.s3.directory.list",
            "credential_ref": credential_ref,
            "input_json": {"prefix": "private/", "delimiter": "/", "page_size": 25},
            "idempotency_key": "s3-direct-list-denied-1",
        },
    )

    assert err["code"] == -32008
    assert err["data"]["status"] == "failed"
    assert err["data"]["action_ref"] == "utils.s3.directory.list"
    assert err["data"]["provider_key"] == "aws-s3"
    assert err["data"]["provider_status_code"] == 403
    assert err["data"]["provider_error"] == {"code": "AccessDenied"}
    assert err["data"]["error"] == "Amazon S3 directory.list failed"
    _assert_no_credentials(err)

    audit = _action_calls(
        mcp_client,
        project_id=project_id,
        action_key="s3.directory.list",
        status="failed",
    )
    assert audit["total_estimate"] == 1
    row = audit["items"][0]
    assert row["id"] == err["data"]["action_call_id"]
    assert row["error"] == "Amazon S3 directory.list failed"
    assert row["response_json"]["aws_error_code"] == "AccessDenied"
    assert row["response_json"]["provider_status_code"] == 403
    assert row["response_json"]["provider_error"] == {"code": "AccessDenied"}
    assert row["response_json"]["request_id"] == "request-denied"
    assert row["response_json"]["retry_safe"] is True
    _assert_no_credentials(audit)
