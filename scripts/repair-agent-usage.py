#!/usr/bin/env python3
"""One-off workflow-779 recovery. Dry-run by default; never discovers new work.

Only the separately reviewed apply ticket may use --apply. Native messages are
compared in memory; manifests contain hashes, counters and boundary provenance.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from stackos.host_mcp.result import normalize_usage, usage_snapshot
from stackos.repositories.tracker.agent_stats import invocation_receipts, summarize_receipts

ALLOWLIST_SHA256 = "ee4fc5bdaad34095b6afe2c700eb6cd0335dfa028d4cd66de68c7d716eda94e9"
CUTOFF = "2026-11-07T02:22:45"
ANALYTICS = {"usage", "selected_role", "validated_outcome"}
CLASSIFICATION_REASON = "original_frozen_role_schema_candidate_validation_chain_unproven"
NATIVE_CONTRACT = (
    "https://github.com/openai/codex/blob/rust-v0.166.0/"
    "codex-rs/protocol/src/protocol.rs#L2339-L2372"
)


class Refusal(ValueError):
    """A bounded precondition failed. Never include source content in errors."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_json(path: Path, value: Any) -> None:
    # Exclusive outputs prevent overwriting source evidence or an earlier backup.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def locations(evidence: dict) -> dict[tuple, list[dict]]:
    automatic = evidence.get("automatic_delivery", {})
    found: dict[tuple, list[dict]] = {}
    for occurrence in [*automatic.get("attempts", []), automatic]:
        for phase in ("delivery", "review"):
            receipt = occurrence.get(phase)
            if isinstance(receipt, dict) and isinstance(receipt.get("launch"), dict):
                attempt = occurrence.get("attempt")
                if attempt is not None and (type(attempt) is not int or attempt < 1):
                    raise Refusal("invalid_attempt")
                found.setdefault((attempt, phase), []).append(receipt)
    # Keep the canonical statistics owner's occurrence semantics.
    if len(found) != len(invocation_receipts(evidence)):
        raise Refusal("occurrence_identity_disagrees_with_statistics_owner")
    return found


def immutable_receipt(receipt: dict) -> dict:
    return {k: v for k, v in receipt.items() if k not in ANALYTICS}


def snapshot(info: Any) -> dict | None:
    if not isinstance(info, dict):
        return None
    return usage_snapshot(
        "codex.turn",
        "session",
        info.get("input_tokens"),
        info.get("output_tokens"),
        info.get("total_tokens"),
    )


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_native(path: Path) -> tuple[dict, list[dict]]:
    """Parse complete top-level turns; keep only safe metadata and result hashes.

    The persisted total_token_usage contract is cumulative independently of the
    exec output's historical basis label. Creation CLI version is provenance,
    never an assertion about the executable used for a later resume.
    """
    before = file_digest(path)
    metadata: dict = {}
    turns: list[dict] = []
    active = None
    previous = None
    unseen_usage = False
    seen_ids = set()
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
                payload = event.get("payload", {})
                kind = payload.get("type")
                if event.get("type") == "session_meta":
                    if metadata:
                        raise Refusal("duplicate_metadata")
                    metadata = {
                        k: payload.get(k)
                        for k in ("id", "cwd", "source", "cli_version", "timestamp")
                    }
                elif event.get("type") == "turn_context" and active is not None:
                    if payload.get("turn_id") != active["turn_id"] or payload.get(
                        "cwd"
                    ) != metadata.get("cwd"):
                        raise Refusal("context_turn_mismatch")
                    active["cwd"] = payload.get("cwd")
                elif event.get("type") == "event_msg":
                    if kind == "task_started":
                        turn_id = payload.get("turn_id")
                        if active is not None or not turn_id or turn_id in seen_ids:
                            raise Refusal("ambiguous_turn_boundary")
                        seen_ids.add(turn_id)
                        active = {
                            "turn_id": turn_id,
                            "start": event["timestamp"],
                            "start_line": line_number,
                            "baseline": previous,
                            "snapshot": None,
                            "cwd": None,
                            "invalid": False,
                            "first": not turns and not unseen_usage,
                        }
                    elif kind == "token_count":
                        info = payload.get("info")
                        # Rate-limit-only notifications carry info=null and no usage.
                        if info is None:
                            continue
                        current = snapshot(info.get("total_token_usage"))
                        if active is None:
                            # Unallocated usage cannot be bridged into a later turn.
                            previous = None
                            unseen_usage = True
                            continue
                        prior = active["snapshot"] or active["baseline"]
                        if current is None or (
                            prior
                            and any(
                                current["snapshot"][k] < prior["snapshot"][k]
                                for k in ("input", "output", "total")
                            )
                        ):
                            active["invalid"] = True
                        active["snapshot"] = current
                        active["usage_line"] = line_number
                    elif kind in {"task_complete", "turn_aborted"}:
                        if active is None or payload.get("turn_id") != active["turn_id"]:
                            raise Refusal("unmatched_terminal_boundary")
                        active["end"] = event["timestamp"]
                        active["end_line"] = line_number
                        active["complete"] = kind == "task_complete"
                        try:
                            result = json.loads(payload.get("last_agent_message", ""))
                            active["result_sha256"] = digest(result)
                        except (ValueError, TypeError):
                            active["result_sha256"] = None
                        previous = (
                            active["snapshot"]
                            if active["complete"] and not active["invalid"]
                            else None
                        )
                        turns.append(active)
                        active = None
            except Refusal:
                raise
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise Refusal("unsupported_native_structure") from exc
    if active is not None:
        raise Refusal("native_session_still_running")
    if file_digest(path) != before:
        raise Refusal("native_file_changed")
    metadata.update(path=str(path.resolve()), sha256=before)
    return metadata, turns


def recover(receipt: dict, native: tuple[dict, list[dict]]) -> tuple[dict, dict]:
    metadata, turns = native
    launch = receipt.get("launch", {})
    sid = receipt.get("thread_id")
    if (
        launch.get("host_key") != "codex"
        or metadata.get("source") != "exec"
        or metadata.get("id") != sid
        or metadata.get("cwd") != launch.get("project_dir")
    ):
        raise Refusal("native_identity_mismatch")
    if metadata.get("cli_version") not in {"0.166.0", "0.167.0"}:
        raise Refusal("unsupported_native_creation_version")
    if launch.get("resume_thread_id") not in {None, sid}:
        raise Refusal("resume_session_mismatch")
    result_hash = digest(receipt.get("result"))
    matches = [t for t in turns if t.get("complete") and t["result_sha256"] == result_hash]
    if len(matches) != 1:
        raise Refusal("terminal_result_missing_or_ambiguous")
    turn = matches[0]
    # Exact result plus session/context and a launch inside the gap preceding this
    # native turn establish the invocation. Time proximity alone never matches.
    index = turns.index(turn)
    launched = timestamp(launch["started_at"])
    if (
        turn["cwd"] != launch.get("project_dir")
        or launched > timestamp(turn["start"])
        or (timestamp(turn["start"]) - launched).total_seconds() > 120
        or (index > 0 and launched <= timestamp(turns[index - 1]["end"]))
    ):
        raise Refusal("launch_boundary_mismatch")
    proof = {
        **metadata,
        "turn_id": turn["turn_id"],
        "start_line": turn["start_line"],
        "end_line": turn["end_line"],
        "launch_started_at": launch["started_at"],
        "native_started_at": turn["start"],
        "native_completed_at": turn["end"],
        "usage_line": turn.get("usage_line"),
        "result_sha256": result_hash,
        "contract": NATIVE_CONTRACT,
        "baseline_turn_id": turns[index - 1]["turn_id"] if index else None,
        "baseline_usage_line": turns[index - 1].get("usage_line") if index else None,
        "resume_executable_version": "not_established_by_session_creation_metadata",
        "classification_reason": CLASSIFICATION_REASON,
    }
    if turn["invalid"]:
        raise Refusal("malformed_or_decreasing_native_counters")
    previous = turn["baseline"]
    if previous:
        previous = {**previous, "session_id": sid}
    fresh = (
        turn["first"]
        and not launch.get("resume_thread_id")
        and metadata.get("timestamp") is not None
        and launched <= timestamp(metadata["timestamp"]) <= timestamp(turn["start"])
    )
    usage = normalize_usage(turn["snapshot"], session_id=sid, resumed=not fresh, previous=previous)
    return {"usage": usage, "selected_role": None, "validated_outcome": None}, proof


def read_rows(connection: sqlite3.Connection, allowlist: list[dict]) -> list[dict]:
    rows = []
    for target in allowlist:
        row = connection.execute(
            "SELECT * FROM tracker_tickets WHERE id=?", (target["id"],)
        ).fetchone()
        if row is None:
            raise Refusal("missing_allowlisted_ticket")
        row = dict(row)
        if (
            row["project_id"] != 1
            or row["key"] != target["key"]
            or row["status"] != "complete"
            or not row["completed_at"]
            or row["completed_at"] >= CUTOFF
            or (row["run_plan_id"] is not None and row["run_plan_id"] >= 779)
        ):
            raise Refusal("ticket_outside_frozen_scope")
        rows.append(row)
    return rows


def plan_rows(rows: list[dict], allowlist: list[dict], roots: list[Path]) -> dict:
    changes = []
    before_receipts = []
    after_receipts = []
    cache: dict[str, Any] = {}
    for row, target in zip(rows, allowlist, strict=True):
        original = json.loads(row["completion_evidence_json"])
        updated = copy.deepcopy(original)
        found = locations(updated)
        expected = {(o["attempt"], o["phase"]): o for o in target["occurrences"]}
        if found.keys() != expected.keys():
            raise Refusal("allowlist_occurrences_changed")
        proposed = []
        for identity, copies in found.items():
            receipt = copies[-1]
            selected = expected[identity]
            if (
                receipt.get("thread_id") != selected["session_id"]
                or digest(immutable_receipt(receipt)) != selected["receipt_sha256"]
                or any(immutable_receipt(c) != immutable_receipt(receipt) for c in copies)
            ):
                raise Refusal("allowlisted_receipt_or_duplicate_changed")
            sid = selected["session_id"]
            if sid not in cache:
                paths = [p for root in roots for p in root.rglob(f"*-{sid}.jsonl")]
                try:
                    if len(paths) != 1:
                        raise Refusal("native_file_missing_or_ambiguous")
                    cache[sid] = read_native(paths[0])
                except Refusal as exc:
                    cache[sid] = str(exc)
            try:
                if isinstance(cache[sid], str):
                    raise Refusal(cache[sid])
                fields, proof = recover(receipt, cache[sid])
            except (Refusal, KeyError, TypeError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, Refusal) else "unsupported_receipt_structure"
                # Unsupported occurrences stay byte-for-byte facts. In particular,
                # never patch a session whose final native turn is still running.
                fields = {}
                proof = {"reason": reason, "classification_reason": CLASSIFICATION_REASON}
            for duplicate in copies:
                if any(
                    k in duplicate and duplicate[k] is not None and duplicate[k] != fields[k]
                    for k in fields
                ):
                    raise Refusal("existing_analytics_conflict")
                duplicate.update(copy.deepcopy(fields))
            proposed.append(
                {**selected, "fields": fields, "provenance": proof, "copies": len(copies)}
            )
        before_receipts.extend(invocation_receipts(original))
        after_receipts.extend(invocation_receipts(updated))
        # Only safe analytics deltas enter the manifest, never a result/transcript.
        after_row = {
            **row,
            "completion_evidence_json": json.dumps(updated, ensure_ascii=False, sort_keys=True),
        }
        changes.append(
            {
                "id": row["id"],
                "key": row["key"],
                "expected_row_sha256": digest(row),
                "after_row_sha256": digest(after_row),
                "after_evidence_sha256": digest(updated),
                "occurrences": proposed,
            }
        )
    for native in cache.values():
        if (
            not isinstance(native, str)
            and file_digest(Path(native[0]["path"])) != native[0]["sha256"]
        ):
            raise Refusal("native_file_changed")
    return {
        "rows": changes,
        "before": summarize_receipts(before_receipts).model_dump(mode="json"),
        "after": summarize_receipts(after_receipts).model_dump(mode="json"),
        "occurrence_count": len(after_receipts),
        "usage_reasons": dict(
            Counter(
                o["provenance"].get("reason")
                or o["fields"].get("usage", {}).get("reason")
                or "measured"
                for change in changes
                for o in change["occurrences"]
            )
        ),
        "classification_reason": CLASSIFICATION_REASON,
    }


def patched_row(row: dict, change: dict) -> dict:
    evidence = json.loads(row["completion_evidence_json"])
    found = locations(evidence)
    for occurrence in change["occurrences"]:
        fields = occurrence["fields"]
        if not set(fields) <= ANALYTICS:
            raise Refusal("nonanalytics_patch_refused")
        for receipt in found[occurrence["attempt"], occurrence["phase"]]:
            receipt.update(copy.deepcopy(fields))
    if digest(evidence) != change["after_evidence_sha256"]:
        raise Refusal("proposed_evidence_hash_mismatch")
    return evidence


def apply_manifest(
    connection: sqlite3.Connection, allowlist: list[dict], manifest: dict, backup: Path
) -> int:
    """Guard all rows before any write; exact private row backup precedes commit.

    Deliberately bypass normal tracker mutations: those change lifecycle/history.
    This separately authorized narrow repair updates only existing evidence JSON.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        rows = read_rows(connection, allowlist)
        changes = manifest["rows"]
        if [r["id"] for r in rows] != [c["id"] for c in changes]:
            raise Refusal("manifest_allowlist_mismatch")
        pending = []
        for row, change in zip(rows, changes, strict=True):
            evidence = patched_row(row, change)
            if json.loads(row["completion_evidence_json"]) == evidence:
                # Also guard all nonanalytics fields on a repeated apply.
                if digest(row) not in {change["expected_row_sha256"], change["after_row_sha256"]}:
                    raise Refusal("stale_row")
                continue
            if digest(row) != change["expected_row_sha256"]:
                raise Refusal("stale_row")
            pending.append((row, evidence))
        if pending:
            private_json(
                backup,
                {"schema": "stackos.usage-repair-backup.v1", "rows": [r for r, _ in pending]},
            )
            for row, evidence in pending:
                connection.execute(
                    "UPDATE tracker_tickets SET completion_evidence_json=? "
                    "WHERE id=? AND project_id=1",
                    (json.dumps(evidence, ensure_ascii=False, sort_keys=True), row["id"]),
                )
        connection.commit()
        return len(pending)
    except BaseException:
        connection.rollback()
        raise


def connect(database: Path, *, writable: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(
        database.resolve().as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True
    )
    connection.row_factory = sqlite3.Row
    if not writable:
        connection.execute("PRAGMA query_only=ON")
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--native-root", type=Path, action="append")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewed-manifest-sha256")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        allowlist = json.loads(args.allowlist.read_text())
        if digest(allowlist) != ALLOWLIST_SHA256:
            raise Refusal("fixed_allowlist_hash_mismatch")
        roots = args.native_root or [
            Path.home() / ".codex/sessions",
            Path.home() / ".codex-personal/sessions",
        ]
        if args.apply:
            if not args.backup or file_digest(args.manifest) != args.reviewed_manifest_sha256:
                raise Refusal("reviewed_manifest_and_backup_required")
            manifest = json.loads(args.manifest.read_text())
            if (
                manifest["source_commit"] != args.source_commit
                or manifest["allowlist_sha256"] != ALLOWLIST_SHA256
                or manifest["database"] != str(args.database.resolve())
            ):
                raise Refusal("manifest_context_mismatch")
            for row in manifest["rows"]:
                for occurrence in row["occurrences"]:
                    proof = occurrence["provenance"]
                    if "path" in proof and file_digest(Path(proof["path"])) != proof["sha256"]:
                        raise Refusal("native_file_changed")
            # Original full rows stay in the private pre-apply snapshot, not the
            # safe manifest. Read them only after expected hashes are checked.
            with connect(args.database, writable=True) as connection:
                count = apply_manifest(connection, allowlist, manifest, args.backup)
            print(f"Applied {count} ticket rows")
        else:
            with connect(args.database) as connection:
                connection.execute("BEGIN")
                rows = read_rows(connection, allowlist)
                manifest = plan_rows(rows, allowlist, roots)
                connection.rollback()
            manifest.update(
                schema="stackos.usage-repair.v1",
                project_id=1,
                contract_revision=1,
                allowlist_sha256=ALLOWLIST_SHA256,
                source_commit=args.source_commit,
                database=str(args.database.resolve()),
            )
            private_json(args.manifest, manifest)
            print(
                f"Dry-run: {len(rows)} tickets, {manifest['occurrence_count']} occurrences; "
                "no database writes"
            )
        return 0
    except Refusal as exc:
        print(f"Recovery refused: {exc}. No partial database commit.")
        return 2
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        # Avoid exception strings from native JSON, paths or database content.
        print("Recovery refused; inspect bounded inputs and guards. No partial database commit.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
