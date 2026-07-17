"""Run-plan grant and audit proofs for FTP and Cloudflare DNS actions."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest
from pytest_httpx import HTTPXMock

from .conftest import MCPClient

_ZONE_ID = "023e105f4ecef8ad9ca31a8372d0c353"
_RECORD_ID = "372e67954025e0ba6aaa6d586b9e0b59"


class _FakeFTP:
    deleted_paths: ClassVar[list[str]] = []

    def __init__(
        self,
        *,
        timeout: float | None = None,
        encoding: str = "utf-8",
    ) -> None:
        self.timeout = timeout
        self.encoding = encoding
        self.cwd_path = "/"

    def connect(self, host: str, port: int, timeout: float | None = None) -> str:
        del host, port, timeout
        return "220 ready"

    def login(self, username: str, password: str) -> str:
        del username, password
        return "230 logged in"

    def set_pasv(self, value: bool) -> None:
        del value

    def pwd(self) -> str:
        return self.cwd_path

    def mlsd(
        self,
        path: str,
        facts: list[str] | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        del path, facts
        return [
            ("assets", {"type": "dir", "modify": "20260715120000"}),
            ("index.html", {"type": "file", "size": "4"}),
        ]

    def delete(self, path: str) -> str:
        self.__class__.deleted_paths.append(path)
        return "250 deleted"

    def quit(self) -> str:
        return "221 bye"

    def close(self) -> None:
        return None


def _credential_ref(mcp: MCPClient, project_id: int, provider_key: str) -> str:
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": provider_key, "response_mode": "raw"},
    )
    return status["connections"][0]["credential_ref"]


def _create_ftp_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/ftp/credentials",
        json={
            "auth_method_key": "ftp-password",
            "fields": {
                "host": "ftp.example.test",
                "port": 21,
                "tls_mode": "none",
                "username": "deploy",
                "password": "ftp-secret",
                "passive_mode": True,
            },
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return _credential_ref(mcp, project_id, "ftp")


def _create_cloudflare_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/cloudflare/credentials",
        json={
            "auth_method_key": "api_token",
            "fields": {"api_token": "cloudflare-action-secret"},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return _credential_ref(mcp, project_id, "cloudflare")


def _run_plan(action_ref: str, *, key: str, step_id: str) -> dict[str, Any]:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": key,
        "title": f"Execute {action_ref}",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": step_id,
                    "tool": "action.execute",
                    "action_refs": [action_ref],
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


def _execute(
    mcp: MCPClient,
    *,
    project_id: int,
    action_ref: str,
    input_json: dict[str, Any],
    credential_ref: str,
    run_token: str,
) -> dict[str, Any]:
    return mcp.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": action_ref,
            "input_json": input_json,
            "credential_ref": credential_ref,
            "run_token": run_token,
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )["data"]


def _assert_action_call_audit(
    mcp: MCPClient,
    *,
    project_id: int,
    created: dict[str, Any],
    started: dict[str, Any],
    claimed: dict[str, Any],
    action_key: str,
    expected_call_id: int,
) -> dict[str, Any]:
    response = mcp.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": created["data"]["id"],
            "run_plan_step_id": claimed["data"]["id"],
            "plugin_slug": "utils",
            "action_key": action_key,
            "status": "success",
        },
        headers=mcp._headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_estimate"] == 1
    row = body["items"][0]
    assert row["id"] == expected_call_id
    assert row["run_id"] == started["data"]["run_id"]
    assert row["run_plan_id"] == created["data"]["id"]
    assert row["run_plan_step_id"] == claimed["data"]["id"]
    return row


def test_ftp_directory_list_executes_with_step_grant_and_audit_linkage(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    monkeypatch.setattr(ftp_module.ftplib, "FTP", _FakeFTP)
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_ftp_credential(mcp_client, project_id)
    step_id = "browse-ftp"
    created, started, claimed = _start_and_claim(
        mcp_client,
        project_id,
        _run_plan(
            "utils.ftp.directory.list",
            key="utils.ftp-directory-list.audit",
            step_id=step_id,
        ),
        step_id=step_id,
    )

    data = _execute(
        mcp_client,
        project_id=project_id,
        action_ref="utils.ftp.directory.list",
        input_json={"remote_path": "/public"},
        credential_ref=credential_ref,
        run_token=started["data"]["run_token"],
    )

    assert data["action_call"]["connector_key"] == "ftp"
    assert data["output_json"]["entry_count"] == 2
    assert {item["name"] for item in data["output_json"]["entries"]} == {
        "assets",
        "index.html",
    }
    audit = _assert_action_call_audit(
        mcp_client,
        project_id=project_id,
        created=created,
        started=started,
        claimed=claimed,
        action_key="ftp.directory.list",
        expected_call_id=data["action_call"]["id"],
    )
    assert audit["response_json"]["entry_count"] == 2
    assert "ftp-secret" not in json.dumps({"result": data, "audit": audit})


def test_ftp_file_delete_executes_with_step_grant_and_audit_linkage(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    _FakeFTP.deleted_paths = []
    monkeypatch.setattr(ftp_module.ftplib, "FTP", _FakeFTP)
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_ftp_credential(mcp_client, project_id)
    step_id = "delete-ftp-file"
    created, started, claimed = _start_and_claim(
        mcp_client,
        project_id,
        _run_plan(
            "utils.ftp.file.delete",
            key="utils.ftp-file-delete.audit",
            step_id=step_id,
        ),
        step_id=step_id,
    )

    data = _execute(
        mcp_client,
        project_id=project_id,
        action_ref="utils.ftp.file.delete",
        input_json={"remote_path": "/public/obsolete.txt"},
        credential_ref=credential_ref,
        run_token=started["data"]["run_token"],
    )

    assert _FakeFTP.deleted_paths == ["/public/obsolete.txt"]
    assert data["action_call"]["connector_key"] == "ftp"
    assert data["output_json"] == {
        "provider": "ftp",
        "operation": "file.delete",
        "status": "success",
        "remote_path": "/public/obsolete.txt",
    }
    audit = _assert_action_call_audit(
        mcp_client,
        project_id=project_id,
        created=created,
        started=started,
        claimed=claimed,
        action_key="ftp.file.delete",
        expected_call_id=data["action_call"]["id"],
    )
    assert audit["response_json"]["remote_path"] == "/public/obsolete.txt"
    assert "ftp-secret" not in json.dumps({"result": data, "audit": audit})


def test_cloudflare_dns_create_executes_with_step_grant_and_audit_linkage(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_cloudflare_credential(mcp_client, project_id)
    step_id = "create-dns-record"
    created, started, claimed = _start_and_claim(
        mcp_client,
        project_id,
        _run_plan(
            "utils.cloudflare.dns.records.create",
            key="utils.cloudflare-dns-create.audit",
            step_id=step_id,
        ),
        step_id=step_id,
    )
    record = {
        "type": "A",
        "name": "www.example.org",
        "content": "192.0.2.10",
        "ttl": 1,
        "proxied": False,
    }
    provider_record = {"id": _RECORD_ID, **record, "proxiable": True}
    httpx_mock.add_response(
        method="POST",
        url=f"https://api.cloudflare.com/client/v4/zones/{_ZONE_ID}/dns_records",
        json={
            "success": True,
            "errors": [],
            "messages": [],
            "result": provider_record,
        },
        headers={"CF-Ray": "create-ray-LAX"},
    )

    data = _execute(
        mcp_client,
        project_id=project_id,
        action_ref="utils.cloudflare.dns.records.create",
        input_json={"zone_id": _ZONE_ID, "record": record},
        credential_ref=credential_ref,
        run_token=started["data"]["run_token"],
    )

    assert data["action_call"]["connector_key"] == "cloudflare"
    assert data["output_json"]["body"]["result"] == provider_record
    audit = _assert_action_call_audit(
        mcp_client,
        project_id=project_id,
        created=created,
        started=started,
        claimed=claimed,
        action_key="cloudflare.dns.records.create",
        expected_call_id=data["action_call"]["id"],
    )
    assert audit["response_json"]["body"]["result"] == provider_record
    assert "cloudflare-action-secret" not in json.dumps({"result": data, "audit": audit})
