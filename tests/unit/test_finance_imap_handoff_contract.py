from __future__ import annotations

from pathlib import Path

CONTRACT = Path("plugins/finance/references/imap-host-handoff-contract.md")
COMMUNICATIONS_CONTRACT = Path("docs/integration-contracts/communications.md")
ACTION_EXECUTOR_DOC = Path("docs/action-executor.md")
COMMUNICATIONS_README = Path("plugins/communications/README.md")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_imap_host_handoff_contract_keeps_evidence_outside_stackos() -> None:
    contract = _read(CONTRACT)

    for required in (
        "`ActionConnectorRequest.asset_dir`",
        "must **not** create an artifact, resource",
        "neither place raw bytes in `output_json`",
        "host agent—not StackOS—writes",
        "StackOS never",
        "writes the external finance workspace",
        "not copied into `action_calls.response_json`",
        "`communication-message`, finance resource, artifact",
        "No broad StackOS filesystem connector",
    ):
        assert required in contract


def test_imap_host_handoff_contract_requires_non_acknowledging_store_before_ack() -> None:
    contract = _read(CONTRACT)

    for required in (
        "`BODY.PEEK[]`",
        "export must never set",
        "reject `tls_mode=none`",
        "separately granted provider",
        "acknowledgement. It is called only after",
        "Store-before-ack flow",
        "writes the original .eml",
        "Only now execute mark_seen",
        "sole provider acknowledgement",
        "must not participate in finance retry suppression",
    ):
        assert required in contract


def test_imap_host_handoff_contract_names_identity_limits_and_recovery() -> None:
    contract = _read(CONTRACT)

    for required in (
        "safe account ref / mailbox ref / UIDVALIDITY / UID / raw-MIME SHA-256",
        "Raw RFC822 message (`RFC822.SIZE`)",
        "10 MiB",
        "Total decoded attachments",
        "Verified duplicate",
        "identity-conflict",
        "UIDVALIDITY reset",
        "mark_seen fails or its outcome is unknown",
        "Process stops after mark_seen before cleanup",
    ):
        assert required in contract


def test_communications_and_action_docs_link_the_same_handoff_contract() -> None:
    for path in (COMMUNICATIONS_CONTRACT, ACTION_EXECUTOR_DOC, COMMUNICATIONS_README):
        assert "imap-host-handoff-contract.md" in _read(path)
