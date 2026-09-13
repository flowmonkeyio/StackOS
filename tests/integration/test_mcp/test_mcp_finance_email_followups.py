"""The real follow-up template dispatches explicitly instructed SMTP messages.

Business review is simulated. SMTP is mocked; acceptance is not inbox delivery.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import MCPClient
from .test_mcp_stripe_actions import _seed_stripe_credential

SMTP_ACTION = "communications.smtp.email.send"


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("protected", [False, True], ids=["plain-input", "secret-refs"])
def test_actual_finance_followup_smtp_step_grant_and_submission_result(
    mcp_client: MCPClient,
    seeded_project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    partial: bool,
    protected: bool,
) -> None:
    project = seeded_project["data"]["id"]
    calls: list[dict[str, Any]] = []
    recipient = "finance-recipient@example.test"
    rejected = "finance-copy@example.test"
    subject = "Synthetic invoice follow-up"
    body = "Synthetic follow-up requested by the fixture operator."
    password = "synthetic-finance-smtp-password"

    class SMTP:
        def __init__(self, host: str, port: int, **_kwargs: Any) -> None:
            assert (host, port) == ("smtp.example.test", 587)

        def ehlo(self) -> None:
            pass

        def starttls(self) -> None:
            pass

        def login(self, username: str, value: str) -> None:
            assert (username, value) == ("mailer@example.test", password)

        def send_message(
            self, message: Any, *, from_addr: str, to_addrs: list[str]
        ) -> dict[str, tuple[int, bytes]]:
            calls.append({"message": message, "from_addr": from_addr, "to_addrs": to_addrs})
            assert message["Subject"] == subject
            assert message.get_content().strip() == body
            return {rejected: (550, b"Mailbox unavailable")} if partial else {}

        def quit(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("stackos.actions.smtp.smtplib.SMTP", SMTP)
    _seed_stripe_credential(mcp_client, project)
    account = mcp_client.test_client.post(
        "/api/v1/auth/accounts/smtp",
        json={
            "auth_method_key": "smtp-password",
            "display_name": "Synthetic finance SMTP",
            "attach_project_id": project,
            "fields": {
                "host": "smtp.example.test",
                "port": 587,
                "tls_mode": "starttls",
                "username": "mailer@example.test",
                "from_email": "mailer@example.test",
                "password": password,
            },
        },
        headers=mcp_client._headers(),
    )
    account.raise_for_status()
    credential = account.json()["data"]["credential_ref"]

    def call(tool: str, **arguments: Any) -> dict[str, Any]:
        result = mcp_client.call_tool_structured(
            tool, {"project_id": project, "response_mode": "raw", **arguments}
        )
        return result.get("data", result)

    def payload_text(value: str) -> str | dict[str, str]:
        if not protected:
            return value
        return {"$secret_ref": call("secret.set", value=value)["secret_ref"]}

    recipients = [recipient, rejected] if partial else [recipient]
    inputs = {
        "workspace_ref": "finance-workspace:smtp-fixture",
        "followup_scope_ref": "followup-scope:smtp-fixture",
        "occurrence_mode": "followup-only",
        "followup_route": "smtp-email",
        "review_window": {"start": "2026-09-01", "end": "2026-09-05"},
    }
    validated = call(
        "runPlan.validate",
        workflow_key="finance.payment-request-followups",
        inputs_json=inputs,
        enforce_required_inputs=True,
    )
    assert validated["valid"] is True, validated
    plan = call(
        "runPlan.create", workflow_key="finance.payment-request-followups", inputs_json=inputs
    )
    plan_id = plan["id"]
    run_token = call("runPlan.start", run_plan_id=plan_id)["run_token"]
    fetched = call("runPlan.get", run_plan_id=plan_id)
    grants = fetched["grant_snapshot_json"]["mcp_tool_grants"]
    smtp_steps = [
        grant["step_id"] for grant in grants if SMTP_ACTION in grant.get("action_refs", [])
    ]
    assert smtp_steps == ["resend-approved"]
    assert not any(
        grant["tool"] in {"communication.send", "communication.reply"} for grant in grants
    )
    args = {
        "project_id": project,
        "run_token": run_token,
        "credential_ref": credential,
        "action_ref": SMTP_ACTION,
        "input_json": {
            "recipients": [payload_text(value) for value in recipients],
            "subject": payload_text(subject),
            "text": payload_text(body),
        },
        "idempotency_key": "synthetic-smtp-occurrence-v1",
    }
    summary = {
        "status": "scoped",
        "occurrence_mode": "followup-only",
        "followup_route": "smtp-email",
        "settlement_state": "not-requested",
        "resend_state": "not-requested",
        "recovery_state": "not-needed",
        "exception_refs": [],
    }
    # Earlier lifecycle/eligibility work is simulated; this regression concerns
    # the existing action path, not agent judgment or a new email approval gate.
    for step in (
        "preflight",
        "read-invoice-lifecycle",
        "prepare-settlement",
        "review-settlement",
        "apply-settlement",
        "suppress-ineligible",
        "review-eligible",
    ):
        call("runPlan.claimStep", run_plan_id=plan_id, step_id=step, run_token=run_token)
        if step == "preflight":
            denied = mcp_client.call_tool_error("action.execute", args)
            assert denied["message"] == "ToolNotGrantedError", denied
            assert calls == []
        call(
            "runPlan.recordStep",
            run_plan_id=plan_id,
            step_id=step,
            run_token=run_token,
            status="success",
            result_json={"followup_summary": summary},
        )
    claimed = call(
        "runPlan.claimStep", run_plan_id=plan_id, step_id="resend-approved", run_token=run_token
    )
    result = mcp_client.call_tool_structured("action.execute", args)["data"]
    assert result.get("status") == "success", result
    assert result["output"]["output_mode"] == "file"
    saved = json.loads(Path(result["output"]["path"]).read_text(encoding="utf-8"))
    output = saved["response"]["output_json"]
    assert output["status"] == ("partial" if partial else "accepted")
    assert output["accepted_recipient_count"] == 1
    assert output["rejected_recipient_count"] == int(partial)
    assert output["accepted_recipients"] == ["[redacted]" if protected else recipient]
    if partial:
        rejected_key = "[redacted]" if protected else rejected
        assert output["rejected_recipients"][rejected_key]["smtp_code"] == 550
    assert output["message_ref"].startswith("smtp-message:")
    assert "delivered" not in output
    assert len(calls) == 1 and calls[0]["to_addrs"] == recipients
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project}/action-calls",
        params={"action_key": "smtp.email.send"},
        headers=mcp_client._headers(),
    )
    audit_response.raise_for_status()
    audit = audit_response.json()["items"][0]
    assert audit["run_plan_id"] == plan_id and audit["run_plan_step_id"] == claimed["id"]
    assert audit["credential_ref"] == credential and audit["status"] == "success"
    assert password not in json.dumps({"audit": audit, "file": saved})
    if protected:
        serialized = json.dumps({"result": result, "audit": audit, "file": saved})
        assert "$secret_ref" in serialized
        for value in [*recipients, subject, body]:
            assert value not in serialized
