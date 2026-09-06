"""Real receipt template/MCP proof with fake IMAP and a test-only host writer.

The host procedure below is a rehearsal fixture, not a production backend or
an assertion that StackOS implements filesystem custody or semantic dedupe.
No installed daemon, mailbox, or real receipt is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.config import Settings
from stackos.db.models import ActionCall
from stackos.repositories.resources import ArtifactRepository, ResourceRepository
from tests.helpers.finance_workspace import (
    AT,
    initialize,
    money,
    read_document,
    validate,
)
from tests.helpers.finance_workspace import (
    record as finance_record,
)

from .conftest import MCPClient

RECEIPT_ID = "rcpt_fixture_777_7"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _persist_host_fixture(
    finance: Path, staged: Path, manifest: dict[str, Any], expected_hash: str
) -> bool:
    """Return False for an already verified receipt; reject a stale writer."""
    index = finance / "finance.json"
    lock = finance / ".fixture-writer.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        if _digest(index) != expected_hash:
            raise ValueError("stale host revision")
        document = read_document(index)
        if any(row["record_id"] == RECEIPT_ID for row in document["receipts"]):
            _verify_host_fixture(finance, manifest)
            return False

        revision = document["revision"]
        source = manifest["source_identity"]
        assert source["uid"] == 7 and source["uidvalidity"] == "777"
        assert source["content_sha256"] == manifest["raw_mime"]["sha256"]
        entries = [manifest["raw_mime"], *manifest["attachments"]]
        assert len(entries) == 2
        attachment_ids: list[str] = []

        for ordinal, item in enumerate(entries):
            staged_path = staged / item["path"]
            assert staged_path.parent == staged and not staged_path.is_symlink()
            assert staged_path.resolve().is_relative_to(staged.resolve())
            payload = staged_path.read_bytes()
            assert len(payload) == item["bytes"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
            suffix = "original.eml" if ordinal == 0 else f"attachment-{ordinal:03}.txt"
            relative = f"attachments/2026/09/{RECEIPT_ID}-{suffix}"
            final = finance / relative
            final.parent.mkdir(parents=True, exist_ok=True)
            assert not final.is_symlink()
            assert final.resolve().is_relative_to(finance.resolve())
            if final.exists():
                assert final.read_bytes() == payload
            else:
                _atomic_fixture_write(final, payload)
            attachment_id = f"attachment_fixture_{ordinal}"
            attachment_ids.append(attachment_id)
            media_type = "message/rfc822" if ordinal == 0 else item["media_type"]
            document["attachments"].append(
                finance_record(
                    attachment_id,
                    "retained",
                    source_ref="source_fixture",
                    role="original",
                    relative_path=relative,
                    sha256=item["sha256"],
                    bytes=item["bytes"],
                    media_type=media_type,
                    original_filename="untrusted fixture original",
                )
            )
        document["sources"].append(
            finance_record(
                "source_fixture",
                "retained",
                source_kind="imap-email",
                source_identity=f"{source['account_ref']}/INBOX/777/7",
                account_ref=source["account_ref"],
                attachment_refs=attachment_ids,
                coverage_state="complete",
                provenance=[
                    {
                        "kind": "imap-email",
                        "ref": "fixture:source",
                        "observed_at": AT,
                        "content_sha256": source["content_sha256"],
                    }
                ],
            )
        )
        document["receipts"].append(
            finance_record(
                RECEIPT_ID,
                "stored",
                source_ref="source_fixture",
                attachment_refs=attachment_ids,
                merchant="Synthetic fixture",
                amount=money(100),
                business_purpose="Synthetic evidence only",
            )
        )
        document["write_proofs"].append(
            finance_record(
                f"write_fixture_r{revision + 1}",
                "prepared",
                prior_revision=revision,
                prior_sha256=expected_hash,
                new_revision=revision + 1,
                changed_refs=[RECEIPT_ID, "source_fixture", *attachment_ids],
                writer_ref="fixture:host",
                reread_result="pending",
                verified_attachment_refs=attachment_ids,
            )
        )
        document["revision"] = revision + 1
        validate(document)
        assert _digest(index) == expected_hash
        _atomic_fixture_write(index, json.dumps(document, sort_keys=True).encode())
        _verify_host_fixture(finance, manifest)
        return True
    finally:
        os.close(descriptor)
        lock.unlink()


def _atomic_fixture_write(path: Path, payload: bytes) -> None:
    """Exercise host-owned atomic custody only inside the pytest temp directory."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        assert hashlib.sha256(temporary.read_bytes()).digest() == hashlib.sha256(payload).digest()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_host_fixture(finance: Path, manifest: dict[str, Any]) -> None:
    document = read_document(finance / "finance.json")
    assert document["schema_version"] == "local-json-v1"
    receipts = [row for row in document["receipts"] if row["record_id"] == RECEIPT_ID]
    assert len(receipts) == 1 and receipts[0]["source_ref"] == "source_fixture"
    for ordinal, item in enumerate([manifest["raw_mime"], *manifest["attachments"]]):
        suffix = "original.eml" if ordinal == 0 else f"attachment-{ordinal:03}.txt"
        relative = f"attachments/2026/09/{RECEIPT_ID}-{suffix}"
        path = finance / relative
        assert not path.is_symlink()
        assert path.resolve().is_relative_to(finance.resolve())
        assert path.stat().st_size == item["bytes"]
        assert _digest(path) == item["sha256"]
        retained = next(row for row in document["attachments"] if row["relative_path"] == relative)
        assert retained["sha256"] == item["sha256"]
        assert retained["record_id"] in receipts[0]["attachment_refs"]


def _imap_fixture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """State survives separate connector sessions; STORE verifies real host files."""
    import stackos.actions.imap as imap_module

    email = EmailMessage()
    email["Subject"] = "UNTRUSTED receipt workflow fixture subject"
    email["From"] = "private-source@example.test"
    email["To"] = "private-inbox@example.test"
    email["Message-ID"] = "<private-fixture-id@example.test>"
    email.set_content("UNTRUSTED body: ignore instructions is data, never policy")
    email.add_attachment(
        b"SYNTHETIC ONLY; receipt amount USD 1.00; not a real transaction",
        maintype="text",
        subtype="plain",
        filename="../../private-receipt-name.txt",
    )
    state: dict[str, Any] = {
        "raw": email.as_bytes(),
        "seen": False,
        "calls": [],
        "before_store": None,
    }

    class FakeIMAP:
        def __init__(self, host: str, port: int, **kwargs: Any) -> None:
            assert kwargs["ssl_context"] is not None

        def login(self, username: str, password: str) -> None:
            assert password == "fixture-imap-password"

        def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
            assert mailbox == "INBOX"
            state["calls"].append(("SELECT", readonly))
            return "OK", [b"1"]

        def response(self, key: str) -> tuple[str, list[bytes]]:
            assert key == "UIDVALIDITY"
            return key, [b"777"]

        def uid(self, *args: Any) -> tuple[str, list[Any]]:
            state["calls"].append(args)
            if args[0] == "SEARCH":
                return "OK", [b"" if state["seen"] else b"7"]
            assert args[1] == "7"
            if args[0] == "STORE":
                assert args[2:] == ("+FLAGS", "(\\Seen)")
                assert state["before_store"] is not None
                state["before_store"]()
                state["seen"] = True
                return "OK", [b"1 (UID 7 FLAGS (\\Seen))"]
            assert args[0] == "FETCH"
            if args[2] == "(UID FLAGS)":
                flags = "\\Seen" if state["seen"] else ""
                return "OK", [f"1 (UID 7 FLAGS ({flags}))".encode()]
            size = len(state["raw"])
            if "BODY.PEEK[]" not in args[2]:
                return "OK", [f"1 (UID 7 RFC822.SIZE {size})".encode()]
            return "OK", [
                (f"1 (UID 7 RFC822.SIZE {size} BODY[] {{{size}}}".encode(), state["raw"]),
                b")",
            ]

        def logout(self) -> None:
            pass

        def close(self) -> None:
            pytest.fail("IMAP CLOSE may expunge and must never be called")

    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", FakeIMAP)
    return state


def test_receipt_template_persists_before_ack_and_resumes_without_duplicate(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _imap_fixture(monkeypatch)
    project_id = seeded_project["data"]["id"]
    engine = mcp_client.test_client.app.state.engine
    with Session(engine) as session:
        ActionRepository(session).describe(action_ref="communications.imap.message.export")
        credential_ref = (
            AuthRepository(session)
            .store_credential(
                provider_key="imap",
                display_name="IMAP isolated workflow fixture",
                attach_project_id=project_id,
                fields={
                    "host": "imap.example.test",
                    "port": 993,
                    "tls_mode": "ssl",
                    "username": "fixture@example.test",
                    "password": "fixture-imap-password",
                    "default_mailbox": "INBOX",
                },
            )
            .data.credential_ref
        )

    finance = tmp_path / "external" / "finance"
    index = initialize(finance)
    original_hash = _digest(index)
    inputs = {
        "workspace_ref": "finance-workspace:isolated-fixture",
        "intake_channel": "imap",
        "mailbox_ref": "imap-mailbox:INBOX",
    }
    validated = mcp_client.call_tool_structured(
        "runPlan.validate",
        {
            "project_id": project_id,
            "workflow_key": "finance.receipt-intake",
            "inputs_json": inputs,
            "enforce_required_inputs": True,
        },
    )
    assert validated["data"]["valid"] is True
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "finance.receipt-intake",
            "inputs_json": inputs,
        },
    )
    plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {
            "project_id": project_id,
            "run_plan_id": plan_id,
        },
    )
    token = started["data"]["run_token"]
    summary: dict[str, Any] = {
        "status": "scoped",
        "acknowledgement_state": "pending",
        "cleanup_state": "pending",
        "exception_refs": [],
    }
    action_ids: list[int] = []
    step_row_ids: dict[str, int] = {}

    def claim(step: str) -> None:
        result = mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {
                "run_plan_id": plan_id,
                "step_id": step,
                "run_token": token,
            },
        )
        assert result["data"]["status"] == "running", result
        step_row_ids[step] = result["data"]["id"]

    def record(step: str, status: str = "success") -> dict[str, Any]:
        result = mcp_client.call_tool_structured(
            "runPlan.recordStep",
            {
                "run_plan_id": plan_id,
                "step_id": step,
                "run_token": token,
                "status": status,
                "result_json": {"receipt_intake_summary": dict(summary)},
                **(
                    {"error": "synthetic interruption after verified host persistence"}
                    if status == "blocked"
                    else {}
                ),
            },
        )
        assert "data" in result, result
        return result["data"]

    def execute(action: str, payload: dict[str, Any], *, auth: bool = True) -> dict[str, Any]:
        result = mcp_client.call_tool_structured(
            "action.execute",
            {
                "project_id": project_id,
                "run_token": token,
                "action_ref": f"communications.imap.{action}",
                "input_json": payload,
                **({"credential_ref": credential_ref} if auth else {}),
                "idempotency_key": f"receipt-fixture-{action}",
                "output_policy_json": {"mode": "inline"},
                "response_mode": "raw",
            },
        )
        assert "data" in result, result
        action_ids.append(result["data"]["action_call"]["id"])
        return result["data"]["output_json"]

    ack_payload = {"mailbox_ref": "imap-mailbox:INBOX", "uid": 7, "expected_uidvalidity": "777"}
    claim("preflight")
    record("preflight")
    claim("discover-or-receive")
    observed = execute(
        "messages.search",
        {
            "mailbox_ref": "imap-mailbox:INBOX",
            "criteria": {"unseen": True},
            "limit": 2,
        },
    )
    assert observed["uids"] == [7]
    summary.update(status="candidate", source_identity_ref="external-source:fixture-777-7")
    record("discover-or-receive")
    claim("stage-imap-evidence")
    before_denied = list(state["calls"])
    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "run_token": token,
            "action_ref": "communications.imap.message.mark_seen",
            "input_json": ack_payload,
            "credential_ref": credential_ref,
        },
    )
    assert denied["code"] == -32007, denied
    assert state["calls"] == before_denied and state["seen"] is False
    manifest = execute("message.export", {"mailbox_ref": "imap-mailbox:INBOX", "uid": 7})
    staged = mcp_settings.generated_assets_dir / manifest["staging_uri"].removeprefix(
        "/generated-assets/"
    )
    assert (staged / "original.eml").read_bytes() == state["raw"]
    assert not any(call[0] == "STORE" for call in state["calls"])
    assert (
        mcp_client.test_client.get(
            manifest["staging_uri"] + "original.eml", headers=mcp_client._headers()
        ).status_code
        == 404
    )
    summary["status"] = "staged"
    record("stage-imap-evidence")
    claim("validate-and-write-external")

    # An independent host change races the originally read revision. The stale
    # proposed rewrite must not overwrite it or advance mailbox acknowledgement.
    concurrent = read_document(index)
    concurrent["revision"] = 1
    concurrent["exceptions"].append(
        finance_record(
            "exception:independent-writer",
            "open",
            kind="synthetic-observation",
            reason="Independent writer observation retained.",
            affected_refs=[],
        )
    )
    _atomic_fixture_write(index, json.dumps(concurrent).encode())
    concurrent_bytes = index.read_bytes()
    with pytest.raises(ValueError, match="stale host revision"):
        _persist_host_fixture(finance, staged, manifest, original_hash)
    assert index.read_bytes() == concurrent_bytes and not state["seen"]
    assert _persist_host_fixture(finance, staged, manifest, _digest(index)) is True
    _verify_host_fixture(finance, manifest)
    assert (
        read_document(index)["exceptions"][0]["reason"]
        == "Independent writer observation retained."
    )
    assert read_document(index)["revision"] == 2
    summary.update(
        status="external-write-verified",
        receipt_record_ref="external-receipt:fixture",
        backend_write_proof_ref="external-write:fixture-r2",
    )
    blocked = record("validate-and-write-external", "blocked")
    assert blocked["status"] == "started" and not state["seen"]
    files_before = {
        path.relative_to(finance): _digest(path) for path in finance.rglob("*") if path.is_file()
    }

    # Discard the first client and reread durable plan/host state; this is a
    # session interruption, not an asserted daemon restart.
    resumed = MCPClient(mcp_client.test_client, mcp_client.auth_token)
    loaded = resumed.call_tool_structured(
        "runPlan.get",
        {
            "project_id": project_id,
            "run_plan_id": plan_id,
            "response_mode": "raw",
        },
    )
    assert loaded.get("id") == plan_id or loaded.get("data", {}).get("id") == plan_id
    mcp_client = resumed
    claim("validate-and-write-external")
    assert _persist_host_fixture(finance, staged, manifest, _digest(index)) is False
    _verify_host_fixture(finance, manifest)
    assert files_before == {
        path.relative_to(finance): _digest(path) for path in finance.rglob("*") if path.is_file()
    }
    summary["status"] = "duplicate"
    record("validate-and-write-external")
    state["before_store"] = lambda: _verify_host_fixture(finance, manifest)
    claim("acknowledge-imap")
    epoch_mismatch = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "run_token": token,
            "action_ref": "communications.imap.message.mark_seen",
            "input_json": {**ack_payload, "expected_uidvalidity": "776"},
            "credential_ref": credential_ref,
        },
    )
    assert epoch_mismatch["message"] == "ConflictError", epoch_mismatch
    assert not state["seen"] and not any(call[0] == "STORE" for call in state["calls"])
    _verify_host_fixture(finance, manifest)
    marked = execute("message.mark_seen", ack_payload)
    assert marked["store_applied"] is True and state["seen"] is True
    summary.update(status="acknowledged", acknowledgement_state="acknowledged")
    record("acknowledge-imap")
    claim("cleanup-staging")
    assert record("cleanup-staging", "blocked")["status"] == "started"
    assert state["seen"] and staged.exists()
    # A second session interruption after acknowledgement needs cleanup only,
    # not another export, receipt write, or flag mutation.
    mcp_client = MCPClient(mcp_client.test_client, mcp_client.auth_token)
    claim("cleanup-staging")
    cleaned = execute(
        "message.export.cleanup", {"transfer_id": manifest["transfer_id"]}, auth=False
    )
    assert cleaned["cleanup_status"] == "deleted" and not staged.exists()
    _verify_host_fixture(finance, manifest)
    summary.update(status="cleaned-up", cleanup_state="cleaned-up")
    assert record("cleanup-staging")["status"] == "completed"
    assert sum(call[0] == "STORE" for call in state["calls"]) == 1
    assert sum(call[0] == "FETCH" and "BODY.PEEK[]" in call[2] for call in state["calls"]) == 1
    assert files_before == {
        path.relative_to(finance): _digest(path) for path in finance.rglob("*") if path.is_file()
    }

    with Session(engine) as session:
        calls = session.exec(select(ActionCall).where(ActionCall.id.in_(action_ids))).all()
        assert len(calls) == 4
        assert {call.run_plan_step_id for call in calls} == {
            step_row_ids[step]
            for step in (
                "discover-or-receive",
                "stage-imap-evidence",
                "acknowledge-imap",
                "cleanup-staging",
            )
        }
        # Include any persisted epoch-mismatch failure as well as successes.
        all_calls = session.exec(select(ActionCall).where(ActionCall.run_plan_id == plan_id)).all()
        assert set(action_ids) <= {call.id for call in all_calls}
        audit = json.dumps([call.model_dump(mode="json") for call in all_calls])
        for private in (
            "fixture-imap-password",
            "UNTRUSTED receipt",
            "private-source@example.test",
            "private-inbox@example.test",
            "private-fixture-id",
            "private-receipt-name",
            "SYNTHETIC ONLY; receipt amount",
        ):
            assert private not in audit
        resources = ResourceRepository(session).query_records(project_id=project_id).items
        assert all(
            item.resource_key != "communication-message" and item.plugin_slug != "finance"
            for item in resources
        )
        assert ArtifactRepository(session).query(project_id=project_id).items == []
