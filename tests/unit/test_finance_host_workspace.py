"""Synthetic proof for the optional, shipped local-json host tools."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.finance_workspace import add_settlement_fixture, money, record

ROOT = Path(__file__).resolve().parents[2]


def host():
    return importlib.import_module("plugins.finance.scripts.finance_workspace")


def test_public_helper_initializes_and_read_only_validate_needs_no_lock(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    lock = path.parent / ".writer.lock"
    lock.unlink()  # Synthetic absence proves read-only validation never creates one.
    before = {
        p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()
    }
    assert module.read_document(path)["revision"] == 0
    assert not lock.exists()
    assert before == {
        p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()
    }
    with pytest.raises(ValueError, match="not empty bootstrap overwrite"):
        module.initialize(path.parent)


def test_persistent_lock_blocks_another_process_and_survives_reuse(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    lock = path.parent / ".writer.lock"
    inode = lock.stat().st_ino
    code = (
        "from plugins.finance.scripts.finance_workspace import exclusive_lock; "
        "from pathlib import Path; import sys;\n"
        "with exclusive_lock(Path(sys.argv[1])): pass"
    )
    with module.writer(path, module.digest(path), expected_revision=0):
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(path.parent)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0 and "writer busy" in result.stderr
    with module.writer(path, module.digest(path), expected_revision=0):
        assert lock.stat().st_ino == inode
    assert lock.stat().st_ino == inode


def test_retention_is_no_clobber_and_retry_reuses_exact_original(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        retained = session.retain_original("receipts/2026/09/example.txt", b"original")
        assert retained["sha256"] == hashlib.sha256(b"original").hexdigest()
        assert session.retain_original(retained["relative_path"], b"original") == retained
        with pytest.raises(ValueError, match="conflict"):
            session.retain_original(retained["relative_path"], b"changed")
        for unsafe in ("../escape.txt", "/tmp/escape.txt", "attachments/2026/09/../escape.txt"):
            with pytest.raises(ValueError):
                session.retain_original(unsafe, b"data")
    assert module.read_document(path)["revision"] == 0
    assert (path.parent / retained["relative_path"]).read_bytes() == b"original"


def test_writer_rejects_stale_revision_and_preserves_permissions(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    path.chmod(0o640)
    before = module.digest(path)
    with module.writer(path, before, expected_revision=0) as session:
        candidate = copy.deepcopy(session.document)
        candidate["revision"] = 1
        proof = session.commit(candidate)
        assert proof["reread_result"] == "verified" and proof["new_revision"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    with pytest.raises(ValueError, match="stale host revision"), module.writer(path, before):
        pass
    with (
        pytest.raises(ValueError, match="stale host revision"),
        module.writer(path, module.digest(path), expected_revision=0),
    ):
        pass


def test_relocated_package_cli_without_repository_or_test_helpers(tmp_path):
    module = host()
    package = tmp_path / "installed-finance"
    shutil.copytree(ROOT / "plugins/finance", package, ignore=shutil.ignore_patterns("__pycache__"))
    script = package / "scripts/finance_workspace.py"
    env = dict(os.environ, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1")
    workspace = tmp_path / "outside-source" / "finance"
    for command in ("initialize", "validate"):
        result = subprocess.run(
            [sys.executable, "-B", str(script), command, "--finance-dir", str(workspace)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "verified"
    assert module.read_document(workspace / "finance.json")["revision"] == 0


@pytest.mark.parametrize(
    "payload", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}']
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(payload):
    with pytest.raises(ValueError):
        host().decode_json(payload)


def add_original(session):
    document = copy.deepcopy(session.document)
    meta = session.retain_original("attachments/source-data/2026/09/raw.txt", b"exact source")
    document["sources"].append(
        record(
            "source:test",
            "retained",
            source_kind="manual",
            source_identity="manual:1",
            attachment_refs=["attachment:test"],
            coverage_state="partial",
        )
    )
    document["attachments"].append(
        record(
            "attachment:test",
            "retained",
            source_ref="source:test",
            role="original",
            media_type="text/plain",
            original_filename="raw.txt",
            **meta,
        )
    )
    document["revision"] += 1
    return document


def test_original_hash_change_and_custody_identity_rewrite_are_rejected(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        document = add_original(session)
        session.commit(document)
        candidate = copy.deepcopy(document)
        candidate["revision"] += 1
        candidate["attachments"][0]["original_filename"] = "rewritten.txt"
        with pytest.raises(ValueError, match="custody identity is immutable"):
            session.commit(candidate)
    original = path.parent / document["attachments"][0]["relative_path"]
    original.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="custody hash/size"):
        module.read_document(path)


def test_failed_replace_preserves_master_and_exact_orphan_retry(tmp_path, monkeypatch):
    module = host()
    path = module.initialize(tmp_path / "finance")
    before = path.read_bytes()
    with module.writer(path, module.digest(path)) as session:
        candidate = add_original(session)
        with monkeypatch.context() as patch:
            patch.setattr(
                module.os,
                "replace",
                lambda *_: (_ for _ in ()).throw(OSError("injected replacement failure")),
            )
            with pytest.raises(OSError, match="injected"):
                session.commit(candidate)
    assert path.read_bytes() == before
    with module.writer(path, module.digest(path)) as session:
        same = add_original(session)
        assert same == candidate
        assert session.commit(same)["reread_result"] == "verified"
    assert len(list((path.parent / "attachments").rglob("*.txt"))) == 1


def test_post_replace_readback_failure_is_not_success_or_blind_retry(tmp_path, monkeypatch):
    module = host()
    path = module.initialize(tmp_path / "finance")
    expected = module.digest(path)
    with module.writer(path, expected) as session:
        candidate = copy.deepcopy(session.document)
        candidate["revision"] += 1
        with monkeypatch.context() as patch:
            patch.setattr(
                module,
                "read_document",
                lambda *_: (_ for _ in ()).throw(OSError("injected readback failure")),
            )
            with pytest.raises(OSError, match="readback failure"):
                session.commit(candidate)
    assert module.read_document(path)["revision"] == 1
    with pytest.raises(ValueError, match="stale host revision"), module.writer(path, expected):
        pass


def test_noncooperating_change_is_rechecked_before_replace(tmp_path, monkeypatch):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        candidate = copy.deepcopy(session.document)
        candidate["revision"] = 1
        original_check = module.verify_originals
        changed = copy.deepcopy(session.document)
        changed["revision"] = 9

        def external_change(document, finance):
            result = original_check(document, finance)
            path.write_bytes(module.encode_json(changed))
            return result

        monkeypatch.setattr(module, "verify_originals", external_change)
        with pytest.raises(ValueError, match="stale host revision"):
            session.commit(candidate)
    assert module.decode_json(path.read_bytes())["revision"] == 9


def test_symlink_original_and_lock_are_rejected(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"untouched")
    target = path.parent / "attachments/2026/09/unsafe.txt"
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)
    with (
        module.writer(path, module.digest(path)) as session,
        pytest.raises(ValueError, match="symlink"),
    ):
        session.retain_original("attachments/2026/09/unsafe.txt", b"replace")
    lock = path.parent / ".writer.lock"
    lock.unlink()  # Deliberately broken isolated fixture; never a production repair.
    lock.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"), module.writer(path, module.digest(path)):
        pass
    assert outside.read_bytes() == b"untouched"


def test_same_hash_different_label_and_source_associations_reuse_bytes(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        candidate = add_original(session)
        repeated = session.retain_original("receipts/2026/09/different.txt", b"exact source")
        assert repeated["relative_path"] == candidate["attachments"][0]["relative_path"]
        another = copy.deepcopy(candidate["attachments"][0])
        another["record_id"] = "attachment:second"
        candidate["attachments"].append(another)
        candidate["sources"][0]["attachment_refs"].append(another["record_id"])
        session.commit(candidate)
    assert len(module.read_document(path)["attachments"]) == 2
    assert len(list((path.parent / "attachments").rglob("*.txt"))) == 1


def test_disposed_history_is_not_current_original_availability(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        document = add_original(session)
    row = document["attachments"][0]
    row["status"] = row["status_history"][-1]["to_status"] = "disposed"
    (path.parent / row["relative_path"]).unlink()  # Synthetic historical disposal only.
    check = module.verify_originals(document, path.parent)
    assert check["verified_attachment_refs"] == []
    assert check["unavailable_attachments"] == [
        {"attachment_ref": "attachment:test", "state": "disposed"}
    ]


def test_no_write_after_writer_context_has_ended(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        pass
    with pytest.raises(ValueError, match="no longer active"):
        session.retain_original("attachments/2026/09/late.txt", b"late")


def test_missing_dependency_cli_is_concise_and_never_initializes(tmp_path):
    script = ROOT / "plugins/finance/scripts/finance_workspace.py"
    destination = tmp_path / "not-created"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(script),
            "initialize",
            "--finance-dir",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "require the jsonschema Python package" in result.stderr
    assert "Traceback" not in result.stderr
    assert not destination.exists()


def test_typed_references_status_history_and_source_backlinks(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        candidate = add_original(session)
    wrong = copy.deepcopy(candidate)
    wrong["attachments"][0]["source_ref"] = "attachment:test"
    with pytest.raises(ValueError, match="unresolved local reference"):
        module.validate(wrong)
    wrong = copy.deepcopy(candidate)
    wrong["sources"][0]["status"] = "observed"
    with pytest.raises(ValueError, match="status history"):
        module.validate(wrong)
    wrong = copy.deepcopy(candidate)
    wrong["sources"][0]["attachment_refs"] = []
    with pytest.raises(ValueError, match="source backlink"):
        module.validate(wrong)


def test_read_only_validation_opens_no_writable_descriptors(tmp_path, monkeypatch):
    module = host()
    path = module.initialize(tmp_path / "finance")
    before = path.read_bytes()
    original_open = module.os.open

    def only_readonly(target, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", only_readonly)
    assert module.read_document(path)["revision"] == 0
    assert path.read_bytes() == before


def bookkeeping_row():
    return record(
        "book:test",
        "prepared/unposted",
        date="2026-09-01",
        amount=money(1000),
        source_refs=["external:source"],
        category={"state": "unresolved"},
    )


@pytest.mark.parametrize(
    "token", ["1000.0", "1e3", "9007199254740993.0", "9.007199254740993e15", "true"]
)
def test_read_rejects_non_integer_money_tokens_without_rounding(tmp_path, token):
    module = host()
    path = module.initialize(tmp_path / "finance")
    document = module.new_document()
    document["bookkeeping"].append(bookkeeping_row())
    payload = module.encode_json(document).replace(
        b'"amount_minor": 1000', f'"amount_minor": {token}'.encode()
    )
    path.write_bytes(payload)  # Deliberately malformed isolated input, not a host writer.
    with pytest.raises(module.ValidationError, match="not of type 'integer'"):
        module.read_document(path)
    assert path.read_bytes() == payload


@pytest.mark.parametrize(
    "field,value", [("amount_minor", 1000.0), ("amount_minor", True), ("revision", 1.0)]
)
def test_commit_rejects_non_integer_candidate_without_replacement(tmp_path, field, value):
    module = host()
    path = module.initialize(tmp_path / "finance")
    before = path.read_bytes()
    with module.writer(path, module.digest(path)) as session:
        candidate = copy.deepcopy(session.document)
        candidate["revision"] = 1
        candidate["bookkeeping"].append(bookkeeping_row())
        if field == "revision":
            candidate[field] = value
        else:
            candidate["bookkeeping"][0]["amount"][field] = value
        with pytest.raises(module.ValidationError, match="not of type 'integer'"):
            session.commit(candidate)
    assert path.read_bytes() == before


def reference_candidate(session, collection):
    document = add_original(session)
    document["bookkeeping"].append(bookkeeping_row())
    if collection == "receipts":
        row = record(
            "receipt:test",
            source_ref="source:test",
            attachment_refs=["attachment:test"],
            amount=money(1000),
        )
    elif collection == "settlements":
        row = add_settlement_fixture(document)
        return document, row
    elif collection == "collection_decisions":
        row = record(
            "collection:test",
            version=1,
            invoice_ref="provider:invoice",
            eligibility="unknown",
            decision_reason="Synthetic case",
            reminder_owner="manual",
            send_outcome="not-attempted",
        )
    elif collection == "mutation_attempts":
        row = record(
            "attempt:test",
            action_key="provider:action",
            idempotency_key="test-key",
            correlation_key="test-correlation",
            outcome="not-attempted",
        )
    elif collection == "write_proofs":
        row = record(
            "proof:test",
            prior_revision=0,
            prior_sha256="a" * 64,
            new_revision=1,
            changed_refs=["book:test"],
            writer_ref="external:writer",
            reread_result="pending",
        )
    elif collection == "tax_packets":
        row = record(
            "tax:test",
            version=1,
            tax_year=2026,
            as_of="2026-09-01",
            profile_refs=[],
            annual_inputs={},
            source_refs=["external:tax-source"],
            obligations=[],
            review_state={
                "control": "not-reviewed",
                "advisor": "not-reviewed",
                "owner": "not-adopted",
            },
        )
    elif collection == "cashflow_forecasts":
        from datetime import date, timedelta

        weeks = [
            {
                "week_number": i + 1,
                "week_start": (date(2026, 9, 1) + timedelta(weeks=i)).isoformat(),
                "source_refs": [],
                "gaps": [{"field": "cash", "reason": "Unknown", "source_refs": []}],
            }
            for i in range(13)
        ]
        row = record(
            "forecast:test",
            version=1,
            as_of="2026-09-01",
            week_1_start="2026-09-01",
            source_refs=[],
            assumptions=[],
            scenarios={"base": weeks, "downside": copy.deepcopy(weeks)},
        )
    else:
        return document, document["bookkeeping"][0]
    document[collection].append(row)
    return document, row


@pytest.mark.parametrize("target", ["missing:record", "source:test"])
@pytest.mark.parametrize(
    "collection,field",
    [
        ("bookkeeping", "receipt_refs"),
        ("bookkeeping", "reconciliation_refs"),
        ("bookkeeping", "correction_refs"),
        ("bookkeeping", "conversion.review_ref"),
        ("receipts", "duplicate_candidate_refs"),
        ("receipts", "exception_refs"),
        ("settlements", "review_refs"),
        ("settlements", "approval_refs"),
        ("settlements", "attempt_refs"),
        ("settlements", "write_proof_refs"),
        ("settlements", "recovery.original_attempt_ref"),
        ("collection_decisions", "invoice_observation_ref"),
        ("collection_decisions", "settlement_refs"),
        ("collection_decisions", "attempt_ref"),
        ("mutation_attempts", "original_attempt_ref"),
        ("write_proofs", "verified_attachment_refs"),
        ("tax_packets", "profile_refs"),
        ("tax_packets", "handoff_refs"),
        ("cashflow_forecasts", "reserve_application_ref"),
    ],
)
def test_commit_rejects_missing_or_wrong_collection_links(tmp_path, collection, field, target):
    module = host()
    path = module.initialize(tmp_path / "finance")
    before = path.read_bytes()
    with module.writer(path, module.digest(path)) as session:
        candidate, row = reference_candidate(session, collection)
        module.validate(candidate)  # The fixture is valid before the one broken reference.
        if field == "conversion.review_ref":
            row["conversion"] = {
                "original_amount": money(1000),
                "converted_amount": money(1000),
                "date": "2026-09-01",
                "rate": "1.0",
                "rate_source_ref": "external:rate",
            }
        for part in field.split(".")[:-1]:
            row = row[part]
        key = field.split(".")[-1]
        row[key] = [target] if key.endswith("_refs") else target
        with pytest.raises(ValueError, match=f"unresolved local reference: {key}"):
            session.commit(candidate)
    assert path.read_bytes() == before


def test_commit_preserves_opaque_external_refs_and_provider_payload_numbers(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        candidate, row = reference_candidate(session, "collection_decisions")
        row["outreach"] = {
            "route": "smtp-email",
            "account_ref": "external:account",
            "input_json": {"provider_specific_number": 1.25, "receipt_refs": ["provider:opaque"]},
        }
        candidate["bookkeeping"][0]["account_ref"] = "external:bank-account"
        session.commit(candidate)
    observed = module.read_document(path)
    assert (
        observed["collection_decisions"][0]["outreach"]["input_json"]["provider_specific_number"]
        == 1.25
    )


def test_commit_accepts_resolving_bookkeeping_links(tmp_path):
    module = host()
    path = module.initialize(tmp_path / "finance")
    with module.writer(path, module.digest(path)) as session:
        candidate, receipt = reference_candidate(session, "receipts")
        candidate["reconciliations"].append(
            record(
                "reconciliation:test",
                period={"start": "2026-09-01", "end": "2026-09-30"},
                account_refs=["external:account"],
                source_refs=["source:test"],
                matched_refs=["book:test"],
                unmatched_refs=[],
                differences=[],
            )
        )
        candidate["bookkeeping"].append({**bookkeeping_row(), "record_id": "book:replacement"})
        candidate["corrections"].append(
            record(
                "correction:test",
                "proposed",
                superseded_ref="book:test",
                replacement_ref="book:replacement",
                reason="Synthetic proposed correction",
            )
        )
        row = candidate["bookkeeping"][0]
        row.update(
            receipt_refs=[receipt["record_id"]],
            reconciliation_refs=["reconciliation:test"],
            correction_refs=["correction:test"],
        )
        session.commit(candidate)
    assert module.read_document(path)["bookkeeping"][0] == row
