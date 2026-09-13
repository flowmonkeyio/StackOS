"""IMAP completeness remains usable through MCP files, audit, and scoped grants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import MCPClient


@pytest.mark.parametrize("route", ["direct", "granted"])
def test_imap_output_completeness_mcp_file_audit_and_grants(
    mcp_client: MCPClient,
    seeded_project: dict,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    calls: list[tuple[Any, ...]] = []
    raw = b"Subject: Fixture\r\nContent-Type: text/plain\r\n\r\n" + b"x" * 900

    class Mailbox:
        def __init__(self, _host: str, _port: int, **_kwargs: Any) -> None:
            pass

        def login(self, _username: str, password: str) -> tuple[str, list[Any]]:
            assert password == "synthetic-imap-output-password"
            return "OK", []

        def select(self, mailbox: str, *, readonly: bool) -> tuple[str, list[bytes]]:
            assert mailbox == "INBOX" and readonly is True
            return "OK", [b"2"]

        def response(self, key: str) -> tuple[str, list[bytes]]:
            assert key == "UIDVALIDITY"
            return "UIDVALIDITY", [b"777"]

        def uid(self, *args: Any) -> tuple[str, list[Any]]:
            calls.append(args)
            if args[0] == "SEARCH":
                # Deliberately include lower UIDs, as n:* may do at the endpoint.
                return "OK", [b"3 5"]
            assert args == ("FETCH", "5", "(UID FLAGS RFC822.SIZE BODY.PEEK[]<0.64>)")
            return "OK", [
                (f"2 (UID 5 FLAGS () RFC822.SIZE {len(raw)} BODY[]<0> {{64}})".encode(), raw[:64])
            ]

        def logout(self) -> tuple[str, list[Any]]:
            return "BYE", []

    monkeypatch.setattr("stackos.actions.imap.imaplib.IMAP4_SSL", Mailbox)
    created = mcp_client.test_client.post(
        "/api/v1/auth/accounts/imap",
        json={
            "auth_method_key": "imap-password",
            "display_name": "Synthetic IMAP output",
            "attach_project_id": project_id,
            "fields": {
                "host": "mail.example.test",
                "port": 993,
                "tls_mode": "ssl",
                "username": "synthetic-user",
                "password": "synthetic-imap-output-password",
            },
        },
        headers=mcp_client._headers(),
    )
    created.raise_for_status()
    credential_ref = created.json()["data"]["credential_ref"]
    args: dict[str, Any] = {"project_id": project_id, "credential_ref": credential_ref}
    tool = "action.run"
    step_id = None
    if route == "granted":
        actions = ["communications.imap.messages.search", "communications.imap.message.fetch"]
        plan = mcp_client.call_tool_structured(
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "imap.output-proof",
                    "title": "IMAP output proof",
                    "grants": {
                        "mcp_tool_grants": [
                            {
                                "step_id": "read-mail",
                                "tool": "action.execute",
                                "action_refs": actions,
                            }
                        ]
                    },
                    "steps": [{"id": "read-mail", "title": "Read mail", "action_refs": actions}],
                },
            },
        )["data"]
        started = mcp_client.call_tool_structured(
            "runPlan.start", {"project_id": project_id, "run_plan_id": plan["id"]}
        )["data"]
        claimed = mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {"run_plan_id": plan["id"], "step_id": "read-mail", "run_token": started["run_token"]},
        )["data"]
        args["run_token"] = started["run_token"]
        step_id = claimed["id"]
        tool = "action.execute"
        denied = mcp_client.call_tool_error(
            tool,
            {
                **args,
                "action_ref": "communications.imap.message.mark_seen",
                "input_json": {"mailbox_ref": "imap-mailbox:INBOX", "uid": 5},
            },
        )
        assert denied["message"] == "ToolNotGrantedError"
        assert calls == []

    def read(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = mcp_client.call_tool_structured(
            tool,
            {**args, "action_ref": f"communications.imap.{action}", "input_json": payload},
        )["data"]
        assert result["status"] == "success", result
        assert result["output"]["output_mode"] == "file"
        saved = json.loads(Path(result["output"]["path"]).read_text(encoding="utf-8"))
        audit_response = mcp_client.test_client.get(
            f"/api/v1/projects/{project_id}/action-calls",
            params={"action_key": f"imap.{action}", "status": "success"},
            headers=mcp_client._headers(),
        )
        audit_response.raise_for_status()
        audit = next(
            row
            for row in audit_response.json()["items"]
            if row["response_json"]["file"]["path"] == result["output"]["path"]
        )
        assert audit["run_plan_step_id"] == step_id
        assert "synthetic-imap-output-password" not in json.dumps({"file": saved, "audit": audit})
        return saved["response"]["output_json"]

    base = {"mailbox_ref": "imap-mailbox:INBOX", "limit": 1}
    first = read("messages.search", base)
    assert first["uids"] == [3]
    assert first["matched_count"] == 2 and first["has_more"] is True
    second = read(
        "messages.search",
        {
            **base,
            "after_uid": first["next_after_uid"],
            "expected_uidvalidity": first["uidvalidity"],
        },
    )
    assert second["uids"] == [5] and second["matched_count"] == 1
    assert second["has_more"] is False and second["next_after_uid"] is None
    fetched = read(
        "message.fetch",
        {"mailbox_ref": base["mailbox_ref"], "uid": second["uids"][0], "max_body_bytes": 64},
    )
    details = fetched["content_completeness"]
    assert details["fetched_bytes"] == 64 and details["parsed_bytes"] == 64
    assert details["raw_message_complete"] is False and details["raw_message_truncated"] is True
    assert details["full_content_action_ref"] == "communications.imap.message.export"
    assert all(call[0] in {"SEARCH", "FETCH"} for call in calls)
