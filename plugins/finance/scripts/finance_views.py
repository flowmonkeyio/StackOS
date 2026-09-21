#!/usr/bin/env python3
"""Optional, regenerable views of an external finance workspace.

Validation, current-record lookup, rendering, published freshness and financial
reconciliation are different claims. This tool never matches bank transactions,
posts books, edits the master, or treats generated CSV as editable financial truth.
Run with --help for explicit read-only versus publication commands.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from .finance_records import CurrentRecords
except ImportError:
    from finance_records import CurrentRecords

VIEWS = ("bookkeeping", "accounts", "exceptions", "sources", "receipts")
MANIFEST = "manifest.json"
FORMAT = "finance-views-v1"


def _workspace():
    return (
        importlib.import_module(".finance_workspace", __package__)
        if __package__
        else importlib.import_module("finance_workspace")
    )


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("A verified source SHA-256 is required")


def _csv(rows: list[dict[str, Any]], columns: list[str], revision: int, digest: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["source_revision", "source_sha256", *columns])
    writer.writeheader()
    for row in rows:
        values = {"source_revision": revision, "source_sha256": digest, **row}
        writer.writerow(
            {
                key: "'" + value
                if isinstance(value, str)
                and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                else value
                for key, value in values.items()
            }
        )
    return stream.getvalue().encode("utf-8")


def _evidence(records: CurrentRecords, refs: list[str]) -> list[dict[str, Any]]:
    result = []
    for ref in dict.fromkeys(records.resolve(ref) for ref in refs):
        if records.collections.get(ref) != "attachments":
            raise ValueError("Evidence reference does not name an attachment")
        attachment = records.records[ref]
        result.append(
            {
                "attachment_ref": ref,
                "retention_state": attachment["status"],
                "available_path": attachment.get("relative_path", "")
                if attachment["status"] == "retained"
                else "",
                "sha256": attachment.get("sha256"),
                "bytes": attachment.get("bytes"),
            }
        )
    return result


def _project(
    document: dict[str, Any], current: CurrentRecords
) -> dict[str, tuple[list[dict[str, Any]], list[str]]]:
    def resolve(ref: str) -> str:
        return current.resolve(ref) if ref in current.records else ref

    mapping = current.account_map()
    books = current.active("bookkeeping")
    packets = current.active("reconciliations")
    issues = current.active("exceptions")
    book_ids = {record["record_id"] for record in books}
    packet_ids = {record["record_id"] for record in packets}
    membership: dict[str, set[str]] = defaultdict(set)
    for packet in packets:
        for ref in [*packet.get("matched_refs", []), *packet.get("unmatched_refs", [])]:
            if resolve(ref) in book_ids:
                membership[resolve(ref)].add(packet["record_id"])
    exceptions: dict[str, set[str]] = defaultdict(set)
    for issue in issues:
        for ref in issue["affected_refs"]:
            exceptions[resolve(ref)].add(issue["record_id"])
    ledger = []
    for book in books:
        ref = book["record_id"]
        receipt_refs = list(dict.fromkeys(resolve(r) for r in book.get("receipt_refs", [])))
        if any(current.collections.get(r) != "receipts" for r in receipt_refs):
            raise ValueError("Bookkeeping receipt reference does not name a receipt")
        reconciliation_refs = (
            set(resolve(r) for r in book.get("reconciliation_refs", [])) | membership[ref]
        )
        if not reconciliation_refs <= packet_ids:
            raise ValueError("Bookkeeping reconciliation reference is not a current packet")
        account_ref = book.get("account_ref", "")
        identity = mapping.get(account_ref)
        related = [ref, *book["source_refs"], *receipt_refs, *reconciliation_refs]
        ledger.append(
            {
                "record_id": ref,
                "original_ref": current.root(ref),
                "correction_refs_json": _json(current.lineage(ref)),
                "date": book["date"],
                "amount_minor": book["amount"]["amount_minor"],
                "currency": book["amount"]["currency"],
                "status": book["status"],
                "account_ref": account_ref,
                "confirmed_account_ref": identity["record_id"] if identity else "",
                "confirmed_account_name": identity["account_identity"]["display_name"]
                if identity
                else "",
                "account_state": "confirmed"
                if identity
                else ("unresolved" if account_ref else "unassigned"),
                "business_purpose": book.get("business_purpose", ""),
                "external_project_ref": book.get("external_project_ref", ""),
                "category_json": _json(book["category"]),
                "source_refs_json": _json(book["source_refs"]),
                "receipt_refs_json": _json(receipt_refs),
                "reconciliation_refs_json": _json(sorted(reconciliation_refs)),
                "reconciliation_states_json": _json(
                    {r: current.records[r]["status"] for r in sorted(reconciliation_refs)}
                ),
                "exception_refs_json": _json(
                    sorted({e for r in related for e in exceptions[resolve(r)]})
                ),
                "gaps_json": _json(book.get("gaps", [])),
                "provenance_json": _json(book.get("provenance", [])),
            }
        )
    ledger.sort(key=lambda item: (item["date"], item["record_id"]))
    accounts = []
    explicit_accounts = {
        record["account_ref"]
        for record in [*books, *current.active("sources")]
        if record.get("account_ref")
    }
    explicit_accounts.update(ref for packet in packets for ref in packet.get("account_refs", []))
    for setting in current.account_identities():
        identity = setting["account_identity"]
        aliases = sorted(
            ref
            for ref in explicit_accounts
            if mapping.get(ref, {}).get("record_id") == setting["record_id"]
        )
        accounts.append(
            {
                "record_id": setting["record_id"],
                "display_name": identity["display_name"],
                "purpose": identity.get("purpose", ""),
                "identity_state": "confirmed",
                "aliases_json": _json(identity["aliases"]),
                "observed_account_refs_json": _json(aliases),
                "represented_bookkeeping_count": sum(
                    book.get("account_ref") in aliases for book in books
                ),
                "decision_refs_json": _json(setting["decision_refs"]),
                "gaps_json": _json(setting.get("gaps", [])),
            }
        )
    for alias in sorted(explicit_accounts - mapping.keys()):
        accounts.append(
            {
                "record_id": "",
                "display_name": "",
                "purpose": "",
                "identity_state": "unresolved",
                "aliases_json": _json([alias]),
                "observed_account_refs_json": _json([alias]),
                "represented_bookkeeping_count": sum(
                    book.get("account_ref") == alias for book in books
                ),
                "decision_refs_json": "[]",
                "gaps_json": "[]",
            }
        )
    issue_rows = [
        {
            "record_id": issue["record_id"],
            "kind": issue["kind"],
            "status": issue["status"],
            "reason": issue["reason"],
            "affected_refs_json": _json(issue["affected_refs"]),
            "current_affected_refs_json": _json(
                list(dict.fromkeys(resolve(r) for r in issue["affected_refs"]))
            ),
            "correction_refs_json": _json(current.lineage(issue["record_id"])),
            "gaps_json": _json(issue.get("gaps", [])),
        }
        for issue in issues
    ]
    source_rows = []
    for source in current.active("sources"):
        account_ref = source.get("account_ref", "")
        source_rows.append(
            {
                "record_id": source["record_id"],
                "source_kind": source["source_kind"],
                "account_ref": account_ref,
                "confirmed_account_ref": mapping.get(account_ref, {}).get("record_id", ""),
                "coverage_state": source["coverage_state"],
                "required_start": source.get("required_window", {}).get("start", ""),
                "required_end": source.get("required_window", {}).get("end", ""),
                "available_start": source.get("available_window", {}).get("start", ""),
                "available_end": source.get("available_window", {}).get("end", ""),
                "opening_balance_json": _json(source["opening_balance"])
                if "opening_balance" in source
                else "",
                "closing_balance_json": _json(source["closing_balance"])
                if "closing_balance" in source
                else "",
                "evidence_json": _json(_evidence(current, source["attachment_refs"])),
                "gaps_json": _json(source.get("gaps", [])),
            }
        )
    receipt_rows = _receipts(current)
    return {
        "bookkeeping": (
            ledger,
            [
                "record_id",
                "original_ref",
                "correction_refs_json",
                "date",
                "amount_minor",
                "currency",
                "status",
                "account_ref",
                "confirmed_account_ref",
                "confirmed_account_name",
                "account_state",
                "business_purpose",
                "external_project_ref",
                "category_json",
                "source_refs_json",
                "receipt_refs_json",
                "reconciliation_refs_json",
                "reconciliation_states_json",
                "exception_refs_json",
                "gaps_json",
                "provenance_json",
            ],
        ),
        "accounts": (
            accounts,
            [
                "record_id",
                "display_name",
                "purpose",
                "identity_state",
                "aliases_json",
                "observed_account_refs_json",
                "represented_bookkeeping_count",
                "decision_refs_json",
                "gaps_json",
            ],
        ),
        "exceptions": (
            issue_rows,
            [
                "record_id",
                "kind",
                "status",
                "reason",
                "affected_refs_json",
                "current_affected_refs_json",
                "correction_refs_json",
                "gaps_json",
            ],
        ),
        "sources": (
            source_rows,
            [
                "record_id",
                "source_kind",
                "account_ref",
                "confirmed_account_ref",
                "coverage_state",
                "required_start",
                "required_end",
                "available_start",
                "available_end",
                "opening_balance_json",
                "closing_balance_json",
                "evidence_json",
                "gaps_json",
            ],
        ),
        "receipts": (
            receipt_rows,
            [
                "sha256",
                "bytes",
                "retention_state",
                "available_path",
                "attachment_refs_json",
                "associations_json",
            ],
        ),
    }


def _receipts(current: CurrentRecords) -> list[dict[str, Any]]:
    receipts = current.active("receipts")
    referenced = {
        ref for receipt in current.document["receipts"] for ref in receipt["attachment_refs"]
    }
    associations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        for original_ref in dict.fromkeys(receipt["attachment_refs"]):
            ref = current.resolve(original_ref)
            if current.collections.get(ref) != "attachments":
                raise ValueError("Receipt reference does not name an attachment")
            associations[ref].append(
                {
                    "receipt_ref": receipt["record_id"],
                    "status": receipt["status"],
                    "business_date": receipt.get("business_date"),
                    "merchant": receipt.get("merchant"),
                    "amount": receipt.get("amount"),
                    "gaps": receipt.get("gaps", []),
                }
            )
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for attachment in current.active("attachments"):
        ref = attachment["record_id"]
        if ref not in referenced and attachment.get("role") != "receipt-original":
            continue
        available = attachment["status"] == "retained"
        # Disposed history remains separately visible; it never establishes current custody.
        key = (attachment.get("sha256") or ref, attachment.get("bytes"), attachment["status"])
        group = groups.setdefault(
            key,
            {
                "sha256": attachment.get("sha256", ""),
                "bytes": attachment.get("bytes", ""),
                "retention_state": attachment["status"],
                "available_path": attachment.get("relative_path", "") if available else "",
                "attachments": [],
                "associations": {},
            },
        )
        if available and group["available_path"] != attachment.get("relative_path", ""):
            raise ValueError("Identical receipt bytes require one canonical retained path")
        group["attachments"].append(ref)
        for association in associations[ref]:
            group["associations"][association["receipt_ref"]] = association
    rows = []
    for group in groups.values():
        group["attachment_refs_json"] = _json(sorted(group.pop("attachments")))
        group["associations_json"] = _json(
            sorted(group.pop("associations").values(), key=lambda item: item["receipt_ref"])
        )
        rows.append(group)
    return sorted(
        rows, key=lambda item: (item["available_path"], item["sha256"], item["retention_state"])
    )


def render(
    document: dict[str, Any], source_sha256: str, views: list[str] | tuple[str, ...] = VIEWS
) -> dict[str, bytes]:
    """Pure projection of already validated records; no file reads or financial verification."""
    _valid_hash(source_sha256)
    if not views or any(view not in VIEWS for view in views):
        raise ValueError("Select declared finance views")
    projected = _project(document, CurrentRecords(document))
    return {
        f"{view}.csv": _csv(*projected[view], document["revision"], source_sha256)
        for view in sorted(set(views))
    }


def _metadata(document: dict[str, Any], digest: str, outputs: dict[str, bytes]) -> dict[str, Any]:
    _valid_hash(digest)
    if not outputs or any(name not in {f"{view}.csv" for view in VIEWS} for name in outputs):
        raise ValueError("Only declared generated filenames may be published")
    return {
        "format": FORMAT,
        "source_revision": document["revision"],
        "source_sha256": digest,
        "files": {
            name: {"sha256": _sha(data), "bytes": len(data)}
            for name, data in sorted(outputs.items())
        },
        "scope": "Derived recorded states; not posting or reconciliation certification",
    }


def _write(path: Path, payload: bytes) -> None:
    _workspace().atomic_write(path, payload)


def publish(
    directory: Path, document: dict[str, Any], digest: str, outputs: dict[str, bytes]
) -> None:
    """Publish under caller's finance writer lock; completion marker is written last."""
    metadata = _metadata(document, digest, outputs)
    directory = _workspace().checked_path(Path(directory))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = _workspace().checked_path(directory / MANIFEST, within=directory)
    if marker.exists():
        _workspace().read_regular(marker)
        marker.unlink()
    for name, payload in sorted(outputs.items()):
        _write(_workspace().checked_path(directory / name, within=directory), payload)
    _write(marker, (_json(metadata) + "\n").encode("utf-8"))


def check_published(
    directory: Path, document: dict[str, Any], digest: str, outputs: dict[str, bytes]
) -> dict[str, Any]:
    """Compare current render, completion marker and every selected published byte."""
    expected = _metadata(document, digest, outputs)
    directory = _workspace().checked_path(Path(directory))
    marker = _workspace().checked_path(directory / MANIFEST, within=directory)
    if not marker.exists():
        return {"status": "missing", "reason": "No complete generation manifest"}
    try:
        if json.loads(_workspace().read_regular(marker)) != expected:
            return {"status": "stale", "reason": "Source or selected generation differs"}
        for name, payload in outputs.items():
            path = _workspace().checked_path(directory / name, within=directory)
            if _workspace().read_regular(path) != payload:
                return {"status": "stale", "reason": "Published view content differs", "file": name}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "stale", "reason": "Incomplete or unreadable generated output"}
    return {
        "status": "current",
        "source_revision": document["revision"],
        "source_sha256": digest,
        "files": sorted(outputs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["lookup", "check", "check-published", "publish"])
    parser.add_argument("--finance-dir", required=True, type=Path)
    parser.add_argument("--view", action="append", choices=VIEWS)
    parser.add_argument("--collection")
    parser.add_argument("--record-id", action="append")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--include-history", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "lookup" and not args.collection:
        parser.error("lookup requires --collection")
    workspace = _workspace()

    def snapshot():
        raw = workspace.read_regular(path)
        document = workspace.read_document(path)
        if workspace.read_regular(path) != raw:
            raise ValueError("Finance snapshot changed during validation; retry from current state")
        return document, raw

    try:
        finance = workspace.checked_path(args.finance_dir)
        path = finance / "finance.json"
        if args.command == "publish":
            with workspace.exclusive_lock(finance):
                document, raw = snapshot()
                outputs = render(document, _sha(raw), args.view or VIEWS)
                publish(finance / "exports/current", document, _sha(raw), outputs)
                if workspace.read_regular(path) != raw:
                    raise ValueError(
                        "Finance snapshot changed during publication; exported views are stale"
                    )
                result = check_published(finance / "exports/current", document, _sha(raw), outputs)
        else:
            document, raw = snapshot()
            if args.command == "lookup":
                result = CurrentRecords(document).select(
                    args.collection,
                    record_ids=args.record_id,
                    offset=args.offset,
                    limit=args.limit,
                    current=not args.include_history,
                )
                result.update(source_revision=document["revision"], source_sha256=_sha(raw))
            else:
                outputs = render(document, _sha(raw), args.view or VIEWS)
                result = (
                    check_published(finance / "exports/current", document, _sha(raw), outputs)
                    if args.command == "check-published"
                    else {
                        "status": "rendered",
                        **_metadata(document, _sha(raw), outputs),
                        "published_freshness": "not_checked",
                    }
                )
            if workspace.read_regular(path) != raw:
                raise ValueError("Finance snapshot changed during read; retry from current state")
        print(_json(result))
        return 1 if result.get("status") in {"missing", "stale"} else 0
    except workspace.ValidationError as exc:
        print(
            _json(
                {
                    "status": "error",
                    "reason": "Schema invalid",
                    "field_path": list(exc.absolute_path),
                    "financial_mutation": False,
                }
            )
        )
        return 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(_json({"status": "error", "reason": str(exc), "financial_mutation": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
