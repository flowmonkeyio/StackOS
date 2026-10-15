"""Assembled local recovery using the finance host helper and Account encryption."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stackos.backups import (
    BackupPolicy,
    BackupTarget,
    backup_status,
    create_backup,
    generate_recovery_key,
    restore_backup,
    run_due_backup,
    save_policy,
)
from stackos.config import Settings
from stackos.crypto.aes_gcm import decrypt_account, encrypt_account
from tests.helpers.finance_workspace import (
    digest,
    initialize,
    money,
    read_document,
    record,
    write_document,
)


def test_manual_and_due_backups_recover_finance_and_matching_account_seed(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.data_dir.mkdir()
    settings.state_dir.mkdir()
    seed = os.urandom(32)
    settings.seed_path.write_bytes(seed)
    settings.token_path.write_text("synthetic-daemon-token-only", encoding="utf-8")
    secret = b"synthetic-provider-secret-only"
    ciphertext, nonce = encrypt_account(
        secret, credential_ref="account_backup_test", provider_key="synthetic", seed=seed
    )
    database = sqlite3.connect(settings.db_path)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE account_snapshot (payload BLOB, nonce BLOB)")
    database.execute("INSERT INTO account_snapshot VALUES (?, ?)", (ciphertext, nonce))
    database.commit()

    finance = tmp_path / "finance"
    master = initialize(finance)
    invoice_bytes = b"%PDF-1.4 synthetic invoice original"
    invoice_rel = "attachments/2026/09/invoice.pdf"
    (finance / invoice_rel).parent.mkdir(parents=True, exist_ok=True)
    (finance / invoice_rel).write_bytes(invoice_bytes)
    document = read_document(master)
    document["sources"].append(
        record(
            "source:backup-invoice",
            "retained",
            source_kind="synthetic-manual",
            source_identity="synthetic-backup-invoice",
            attachment_refs=["attachment:backup-invoice"],
            coverage_state="complete",
        )
    )
    document["attachments"].append(
        record(
            "attachment:backup-invoice",
            "retained",
            source_ref="source:backup-invoice",
            role="original",
            relative_path=invoice_rel,
            sha256=hashlib.sha256(invoice_bytes).hexdigest(),
            bytes=len(invoice_bytes),
            media_type="application/pdf",
            original_filename="invoice.pdf",
        )
    )
    document["receipts"].append(
        record(
            "receipt:backup-invoice",
            "prepared",
            source_ref="source:backup-invoice",
            attachment_refs=["attachment:backup-invoice"],
            amount=money(1200),
        )
    )
    document["revision"] = 1
    write_document(master, document, digest(master))
    document = read_document(master)
    (finance / "reviews").mkdir(exist_ok=True)
    (finance / "reviews/review.json").write_text('{"status":"prepared"}\n', encoding="utf-8")
    originals = {
        path.relative_to(finance).as_posix(): path.read_bytes()
        for path in finance.rglob("*")
        if path.is_file() and path.name != ".writer.lock"
    }
    key_dir = tmp_path / "recovery-keys"
    key_dir.mkdir()
    private = key_dir / "private.pem"
    public = key_dir / "public.pem"
    generate_recovery_key(settings, private, public)
    destination = tmp_path / "archives"
    policy = BackupPolicy(
        recipient_public_key=public.read_text(),
        destination=destination,
        targets=[
            BackupTarget(
                id="business-finance",
                label="Synthetic finance",
                path=finance,
                lock_file=finance / ".writer.lock",
            )
        ],
    )
    save_policy(settings, policy)
    assert backup_status(settings)["enabled"] is False
    run_due_backup(settings)
    assert not list(destination.glob("*.sbak"))
    manual = destination / "manual.sbak"
    create_backup(settings, output=manual)
    now = datetime.now(UTC) + timedelta(days=2)
    save_policy(settings, policy.model_copy(update={"enabled": True}))
    run_due_backup(settings, now=now)
    status = backup_status(settings)
    assert status["last_error"] is None
    automatic = Path(status["last_success"]["path"])
    assert automatic != manual and automatic.is_file()

    for name, archive in (("manual", manual), ("automatic", automatic)):
        assert secret not in archive.read_bytes()
        assert seed not in archive.read_bytes()
        assert settings.token_path.read_bytes() not in archive.read_bytes()
        assert invoice_bytes not in archive.read_bytes()
        recovered = tmp_path / f"recovered-{name}"
        restore_backup(settings, archive, private, recovered)
        recovered_finance = recovered / "external/business-finance"
        recovered_document = read_document(recovered_finance / "finance.json")
        assert recovered_document == document
        assert recovered_document["receipts"][0]["attachment_refs"] == ["attachment:backup-invoice"]
        assert (
            hashlib.sha256((recovered_finance / invoice_rel).read_bytes()).hexdigest()
            == (recovered_document["attachments"][0]["sha256"])
        )
        assert {
            path.relative_to(recovered_finance).as_posix(): path.read_bytes()
            for path in recovered_finance.rglob("*")
            if path.is_file()
        } == originals
        recovered_seed = (recovered / "state/seed.bin").read_bytes()
        assert recovered_seed == seed
        with sqlite3.connect(recovered / "data/stackos.db") as restored_db:
            assert restored_db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            restored_payload, restored_nonce = restored_db.execute(
                "SELECT payload, nonce FROM account_snapshot"
            ).fetchone()
        assert (
            decrypt_account(
                restored_payload,
                nonce=restored_nonce,
                credential_ref="account_backup_test",
                provider_key="synthetic",
                seed=recovered_seed,
            )
            == secret
        )

    before = set(destination.iterdir())
    save_policy(settings, policy.model_copy(update={"enabled": False}))
    run_due_backup(settings, now=now + timedelta(days=2))
    assert set(destination.iterdir()) == before
    assert {rel: (finance / rel).read_bytes() for rel in originals} == originals
    assert json.loads((finance / "finance.json").read_text())["schema_version"]
    database.close()
