"""Synthetic proofs for the shipped external finance projections; no provider access."""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "plugins/finance/scripts"


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("finance_records"), importlib.import_module("finance_views")


def row(ref: str, **fields):
    return {"record_id": ref, "status": "observed", "provenance": [], "gaps": [], **fields}


def fixture():
    return {
        "revision": 7,
        "sources": [
            row(
                "source",
                attachment_refs=[],
                coverage_state="partial",
                account_ref="bank:opaque",
                source_kind="bank-export",
                available_window={"start": "2026-01-02", "end": "2026-01-03"},
            )
        ],
        "attachments": [],
        "receipts": [],
        "reviews": [],
        "operating_settings": [
            row(
                "identity-v1",
                status="confirmed",
                version=1,
                decision_refs=["operator:decision"],
                account_identity={"display_name": "Business account", "aliases": ["bank:opaque"]},
            )
        ],
        "bookkeeping": [
            row(
                "old-book",
                status="prepared/unposted",
                date="2026-01-02",
                amount={"amount_minor": -105, "currency": "JPY"},
                account_ref="bank:opaque",
                source_refs=["source"],
                receipt_refs=[],
                category={"state": "unresolved"},
            ),
            row(
                "new-book",
                status="prepared/unposted",
                date="2026-01-02",
                amount={"amount_minor": -105, "currency": "JPY"},
                account_ref="bank:opaque",
                source_refs=["source"],
                receipt_refs=[],
                category={"state": "proposed"},
            ),
        ],
        "corrections": [
            row(
                "correction",
                status="applied",
                superseded_ref="old-book",
                replacement_ref="new-book",
                decision_ref="operator:decision",
            )
        ],
        "reconciliations": [
            row(
                "packet",
                status="incomplete",
                account_refs=["bank:opaque"],
                matched_refs=["old-book"],
                unmatched_refs=[],
                differences=[],
            )
        ],
        "exceptions": [
            row(
                "open-issue",
                kind="missing-evidence",
                status="unresolved",
                reason="=UNTRUSTED()",
                affected_refs=["old-book"],
            ),
            row(
                "resolved-issue",
                kind="missing-evidence",
                status="resolved",
                reason="Resolved on its evidence",
                affected_refs=["old-book"],
            ),
        ],
    }


def csv_rows(outputs, view):
    return list(csv.DictReader(io.StringIO(outputs[f"{view}.csv"].decode())))


@pytest.mark.parametrize(
    "case", ["unknown", "cross-collection", "cycle", "fork", "merge", "decision"]
)
def test_invalid_correction_graphs_fail(modules, case):
    records, _ = modules
    document = fixture()
    correction = document["corrections"][0]
    if case == "unknown":
        correction["replacement_ref"] = "missing"
    elif case == "cross-collection":
        correction["replacement_ref"] = "source"
    elif case == "decision":
        correction.pop("decision_ref")
    else:
        document["bookkeeping"].append(row("third-book"))
        old, new = {
            "cycle": ("new-book", "old-book"),
            "fork": ("old-book", "third-book"),
            "merge": ("third-book", "new-book"),
        }[case]
        document["corrections"].append(
            row(
                "correction-2",
                status="applied",
                superseded_ref=old,
                replacement_ref=new,
                decision_ref="operator:decision",
            )
        )
    with pytest.raises(ValueError):
        records.CurrentRecords(document)


def test_current_resolution_preserves_history_and_rejected_proposals(modules):
    records, _ = modules
    document = fixture()
    document["corrections"].append(
        row(
            "proposal",
            status="proposed",
            superseded_ref="new-book",
            replacement_ref="not-yet-accepted",
        )
    )
    before = copy.deepcopy(document)
    current = records.CurrentRecords(document)
    assert current.resolve("old-book") == "new-book"
    assert current.root("new-book") == "old-book"
    assert current.lineage("new-book") == ["correction"]
    assert [r["record_id"] for r in current.active("bookkeeping")] == ["new-book"]
    assert document == before


def test_account_versions_corrections_and_exact_aliases(modules):
    records, _ = modules
    document = fixture()
    middle = copy.deepcopy(document["operating_settings"][0])
    middle.update(record_id="identity-v2", version=2, supersedes_ref="identity-v1")
    latest = copy.deepcopy(middle)
    latest.update(record_id="identity-v3", version=3, supersedes_ref="identity-v2")
    latest["account_identity"]["display_name"] = "Current business account"
    document["operating_settings"].extend([middle, latest])
    document["corrections"].append(
        row(
            "identity-correction",
            status="applied",
            superseded_ref="identity-v2",
            replacement_ref="identity-v3",
            decision_ref="external:decision",
        )
    )
    mapping = records.CurrentRecords(document).account_map()
    for alias in ("identity-v1", "identity-v2", "identity-v3", "bank:opaque"):
        assert mapping[alias]["record_id"] == "identity-v3"
    assert "Business account" not in mapping


def test_conflicting_current_account_aliases_are_not_merged(modules):
    records, _ = modules
    document = fixture()
    another = copy.deepcopy(document["operating_settings"][0])
    another["record_id"] = "another-identity"
    document["operating_settings"].append(another)
    with pytest.raises(ValueError, match="alias"):
        records.CurrentRecords(document)


def test_unconfirmed_account_does_not_establish_mapping(modules):
    records, _ = modules
    document = fixture()
    document["operating_settings"][0]["status"] = "proposed"
    assert records.CurrentRecords(document).account_map() == {}
    document["operating_settings"][0]["status"] = "confirmed"
    document["operating_settings"][0]["decision_refs"] = []
    with pytest.raises(ValueError, match="decision"):
        records.CurrentRecords(document)


def test_bounded_lookup_resolves_requested_current_ids(modules):
    records, _ = modules
    current = records.CurrentRecords(fixture())
    result = current.select("bookkeeping", record_ids=["old-book", "new-book"], limit=1)
    assert result["total"] == 1
    assert result["records"][0]["record_id"] == "new-book"
    assert current.select("bookkeeping", current=False, limit=1)["has_more"] is True
    for limit in (0, 201):
        with pytest.raises(ValueError):
            current.select("bookkeeping", limit=limit)
    with pytest.raises(ValueError):
        current.select("bookkeeping", record_ids=["source"])


def test_views_keep_current_and_unresolved_distinct_and_preserve_components(modules):
    _, views = modules
    document = fixture()
    duplicate = copy.deepcopy(document["bookkeeping"][1])
    duplicate["record_id"] = "legitimate-second-occurrence"
    document["bookkeeping"].append(duplicate)
    before = copy.deepcopy(document)
    outputs = views.render(document, "a" * 64)
    books = csv_rows(outputs, "bookkeeping")
    assert len(books) == 2
    assert {r["amount_minor"] for r in books} == {"-105"}
    assert {r["currency"] for r in books} == {"JPY"}
    replaced = next(r for r in books if r["record_id"] == "new-book")
    assert json.loads(replaced["reconciliation_refs_json"]) == ["packet"]
    assert json.loads(replaced["reconciliation_states_json"]) == {"packet": "incomplete"}
    assert json.loads(replaced["exception_refs_json"]) == ["open-issue", "resolved-issue"]
    assert {r["status"] for r in csv_rows(outputs, "exceptions")} == {"unresolved", "resolved"}
    assert csv_rows(outputs, "exceptions")[0]["reason"] == "'=UNTRUSTED()"
    assert document == before
    for name, output in outputs.items():
        for record in csv.DictReader(io.StringIO(output.decode())):
            assert record["source_revision"] == "7", name
            assert record["source_sha256"] == "a" * 64, name


def test_sources_show_observed_windows_not_complete_coverage(modules):
    _, views = modules
    source = csv_rows(views.render(fixture(), "b" * 64, ["sources"]), "sources")[0]
    assert source["coverage_state"] == "partial"
    assert source["required_start"] == ""
    assert source["available_start"] == "2026-01-02"
    assert source["opening_balance_json"] == ""


def test_single_receipt_file_has_separate_associations_and_unprepared_visible(modules):
    _, views = modules
    document = fixture()
    document["attachments"] = [
        row(
            "receipt-a",
            status="retained",
            role="receipt-original",
            relative_path="receipts/2026/01/source.pdf",
            sha256="b" * 64,
            bytes=12,
        ),
        row(
            "receipt-alias",
            status="retained",
            role="receipt-original",
            relative_path="receipts/2026/01/source.pdf",
            sha256="b" * 64,
            bytes=12,
        ),
        row(
            "unprepared",
            status="retained",
            role="receipt-original",
            relative_path="receipts/2026/01/unprepared.pdf",
            sha256="c" * 64,
            bytes=13,
        ),
        row(
            "disposed",
            status="disposed",
            role="receipt-original",
            relative_path="receipts/2026/01/historical.pdf",
            sha256="d" * 64,
            bytes=14,
        ),
    ]
    document["receipts"] = [
        row(
            "receipt-1",
            status="association-disputed",
            attachment_refs=["receipt-a"],
            amount={"amount_minor": 5, "currency": "USD"},
        ),
        row(
            "receipt-2",
            attachment_refs=["receipt-alias"],
            amount={"amount_minor": 8, "currency": "KWD"},
        ),
    ]
    result = csv_rows(views.render(document, "a" * 64, ["receipts"]), "receipts")
    assert len(result) == 3
    active = next(r for r in result if r["sha256"] == "b" * 64)
    assert len(json.loads(active["associations_json"])) == 2
    assert json.loads(active["associations_json"])[0]["status"] == "association-disputed"
    empty = next(r for r in result if r["sha256"] == "c" * 64)
    assert json.loads(empty["associations_json"]) == []
    old = next(r for r in result if r["sha256"] == "d" * 64)
    assert old["available_path"] == ""
    assert old["retention_state"] == "disposed"


def test_published_freshness_and_interrupted_generation(tmp_path, modules, monkeypatch):
    _, views = modules
    outputs = views.render(fixture(), "a" * 64, ["bookkeeping", "exceptions"])
    assert views.check_published(tmp_path, fixture(), "a" * 64, outputs)["status"] == "missing"
    views.publish(tmp_path, fixture(), "a" * 64, outputs)
    assert views.check_published(tmp_path, fixture(), "a" * 64, outputs)["status"] == "current"
    (tmp_path / "bookkeeping.csv").write_bytes(b"edited")
    assert views.check_published(tmp_path, fixture(), "a" * 64, outputs)["status"] == "stale"
    views.publish(tmp_path, fixture(), "a" * 64, outputs)
    original = views._write
    attempts = 0

    def fail_between_files(path, payload):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise OSError("Synthetic interrupted export")
        return original(path, payload)

    monkeypatch.setattr(views, "_write", fail_between_files)
    with pytest.raises(OSError, match="interrupted"):
        views.publish(tmp_path, fixture(), "a" * 64, outputs)
    assert views.check_published(tmp_path, fixture(), "a" * 64, outputs)["status"] != "current"
    monkeypatch.setattr(views, "_write", original)
    views.publish(tmp_path, fixture(), "a" * 64, outputs)
    assert views.check_published(tmp_path, fixture(), "a" * 64, outputs)["status"] == "current"
    assert views.check_published(tmp_path, fixture(), "b" * 64, outputs)["status"] == "stale"


def test_views_require_real_hash_and_allow_only_declared_output_names(tmp_path, modules):
    _, views = modules
    with pytest.raises(ValueError):
        views.render(fixture(), "not-a-digest")
    with pytest.raises(ValueError):
        views.render(fixture(), "a" * 64, ["../../unsafe"])
    with pytest.raises(ValueError):
        views.publish(tmp_path, fixture(), "a" * 64, {"../unsafe.csv": b"x"})


def test_generation_is_deterministic_and_manifest_has_no_financial_values(tmp_path, modules):
    _, views = modules
    outputs = views.render(fixture(), "a" * 64, ["exceptions"])
    assert outputs == views.render(fixture(), "a" * 64, ["exceptions"])
    views.publish(tmp_path, fixture(), "a" * 64, outputs)
    manifest = json.loads((tmp_path / "manifest.json").read_bytes())
    assert manifest["source_revision"] == 7
    assert (
        manifest["files"]["exceptions.csv"]["sha256"]
        == hashlib.sha256(outputs["exceptions.csv"]).hexdigest()
    )
    assert "UNTRUSTED" not in json.dumps(manifest)


def test_cli_check_is_no_write_and_separates_freshness(tmp_path, modules, capsys):
    _, views = modules
    workspace = views._workspace()
    finance = tmp_path / "finance"
    master = workspace.initialize(finance)
    (finance / ".writer.lock").unlink()  # Fixture: read checks must not recreate a lock.
    before = {
        str(path.relative_to(finance)): path.read_bytes()
        for path in finance.rglob("*")
        if path.is_file()
    }
    assert views.main(["check", "--finance-dir", str(finance)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "rendered"
    assert result["published_freshness"] == "not_checked"
    assert views.main(["check-published", "--finance-dir", str(finance)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "missing"
    assert not (finance / "exports").exists()
    assert not (finance / ".writer.lock").exists()
    after = {
        str(path.relative_to(finance)): path.read_bytes()
        for path in finance.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert master.read_bytes() == before["finance.json"]
    assert views.main(["lookup", "--finance-dir", str(finance), "--collection", "bookkeeping"]) == 0
    assert json.loads(capsys.readouterr().out)["records"] == []


def test_cli_publish_freshness_and_busy_writer(tmp_path, modules, capsys):
    _, views = modules
    workspace = views._workspace()
    finance = tmp_path / "finance"
    master = workspace.initialize(finance)
    before = master.read_bytes()
    with workspace.exclusive_lock(finance):
        assert views.main(["publish", "--finance-dir", str(finance)]) == 1
    assert "writer busy" in capsys.readouterr().out
    assert not (finance / "exports").exists()
    assert views.main(["publish", "--finance-dir", str(finance), "--view", "sources"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "current"
    assert views.main(["check-published", "--finance-dir", str(finance), "--view", "sources"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == ["sources.csv"]
    assert master.read_bytes() == before


def test_cli_missing_capability_and_schema_errors_do_not_dump_records(
    tmp_path, modules, capsys, monkeypatch
):
    _, views = modules
    finance = tmp_path / "finance"
    workspace = views._workspace()
    master = workspace.initialize(finance)
    invalid = json.loads(master.read_bytes())
    invalid["unexpected_field"] = "Synthetic confidential content"
    master.write_text(json.dumps(invalid))
    assert views.main(["check", "--finance-dir", str(finance)]) == 1
    response = capsys.readouterr().out
    assert "Schema invalid" in response
    assert "Synthetic confidential" not in response

    def denied(path):
        raise PermissionError("Host file capability unavailable")

    monkeypatch.setattr(workspace, "read_regular", denied)
    assert views.main(["check", "--finance-dir", str(finance)]) == 1
    assert "Host file capability unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("retention_state", ["retained", "disposed", "quarantined"])
def test_source_and_receipt_evidence_follow_attachment_correction(modules, retention_state):
    _, views = modules
    document = fixture()
    document["attachments"] = [
        row(
            "old-file",
            status="retained",
            role="receipt-original",
            relative_path="receipts/2026/01/old.pdf",
            sha256="b" * 64,
            bytes=12,
        ),
        row(
            "replacement-file",
            status=retention_state,
            role="receipt-original",
            relative_path="receipts/2026/01/new.pdf",
            sha256="c" * 64,
            bytes=13,
        ),
    ]
    document["receipts"] = [
        row("receipt", attachment_refs=["old-file"], status="association-disputed")
    ]
    document["sources"][0]["attachment_refs"] = ["old-file", "replacement-file"]
    document["corrections"].append(
        row(
            "file-correction",
            status="applied",
            superseded_ref="old-file",
            replacement_ref="replacement-file",
            decision_ref="external:decision",
        )
    )
    original = copy.deepcopy(document)
    outputs = views.render(document, "a" * 64, ["sources", "receipts"])
    rows = csv_rows(outputs, "receipts")
    assert len(rows) == 1
    expected_path = "receipts/2026/01/new.pdf" if retention_state == "retained" else ""
    assert rows[0]["available_path"] == expected_path
    assert rows[0]["retention_state"] == retention_state
    assert json.loads(rows[0]["attachment_refs_json"]) == ["replacement-file"]
    assert json.loads(rows[0]["associations_json"])[0]["receipt_ref"] == "receipt"
    evidence = json.loads(csv_rows(outputs, "sources")[0]["evidence_json"])
    assert evidence == [
        {
            "attachment_ref": "replacement-file",
            "retention_state": retention_state,
            "available_path": expected_path,
            "sha256": "c" * 64,
            "bytes": 13,
        }
    ]
    assert document == original


def test_source_alias_correction_preserves_matching_and_detects_ambiguity(modules):
    records, _ = modules
    document = fixture()
    document["sources"].extend([row("old-account"), row("new-account")])
    document["operating_settings"][0]["account_identity"]["aliases"] = ["old-account"]
    document["corrections"].append(
        row(
            "account-correction",
            status="applied",
            superseded_ref="old-account",
            replacement_ref="new-account",
            decision_ref="external:decision",
        )
    )
    mapping = records.CurrentRecords(document).account_map()
    assert mapping["new-account"] == mapping["old-account"]
    conflicting = copy.deepcopy(document["operating_settings"][0])
    conflicting["record_id"] = "conflicting-identity"
    conflicting["account_identity"]["aliases"] = ["new-account"]
    document["operating_settings"].append(conflicting)
    with pytest.raises(ValueError, match="alias"):
        records.CurrentRecords(document)


def test_unknown_reconciliation_backlink_and_unsafe_exports_fail(tmp_path, modules):
    _, views = modules
    document = fixture()
    document["bookkeeping"][1]["reconciliation_refs"] = ["source"]
    with pytest.raises(ValueError, match="reconciliation"):
        views.render(document, "a" * 64)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        views.publish(link, fixture(), "a" * 64, views.render(fixture(), "a" * 64))


def test_unknown_hash_never_collapses_distinct_quarantined_receipts(modules):
    _, views = modules
    document = fixture()
    document["attachments"] = [
        row("quarantine-a", status="quarantined", role="receipt-original"),
        row("quarantine-b", status="quarantined", role="receipt-original"),
    ]
    result = csv_rows(views.render(document, "a" * 64, ["receipts"]), "receipts")
    assert len(result) == 2
    assert all(record["available_path"] == "" for record in result)


def test_relocated_scripts_can_publish_and_check_without_source_checkout(tmp_path):
    package = tmp_path / "relocated-finance-package"
    shutil.copytree(SCRIPTS.parent, package)
    finance = tmp_path / "business" / "finance"
    helper = package / "scripts" / "finance_workspace.py"
    view = package / "scripts" / "finance_views.py"
    initialize = subprocess.run(
        [sys.executable, "-B", str(helper), "initialize", "--finance-dir", str(finance)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert initialize.returncode == 0, initialize.stderr
    before = (finance / "finance.json").read_bytes()
    for command in ("check", "publish", "check-published"):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(view),
                command,
                "--finance-dir",
                str(finance),
                "--view",
                "bookkeeping",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] in {"rendered", "current"}
    assert (finance / "finance.json").read_bytes() == before
