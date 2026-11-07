"""Synthetic recovery boundaries and isolated SQLite rehearsal; no native transcripts."""

import copy
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "usage_repair", Path(__file__).parents[2] / "scripts/repair-agent-usage.py"
)
assert SPEC and SPEC.loader
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)

SID = "synthetic-session"
CWD = "/synthetic/project"
START = "2026-11-06T10:00:00+00:00"
RESULT = {"status": "success", "summary": "synthetic", "outputs": "{}", "followups": []}


def event(kind, payload, stamp="2026-11-06T10:00:02+00:00"):
    return {"type": kind, "timestamp": stamp, "payload": payload}


def native_events():
    return [
        event(
            "session_meta",
            {
                "id": SID,
                "cwd": CWD,
                "source": "exec",
                "cli_version": "0.167.0",
                "timestamp": "2026-11-06T10:00:01+00:00",
            },
        ),
        *turn("one", 100, 20),
        *turn("two", 180, 30, hour="11", result={**RESULT, "summary": "second"}),
    ]


def turn(turn_id, input_count, output, *, hour="10", result=None):
    stamp = f"2026-11-06T{hour}:00:02+00:00"
    return [
        event("event_msg", {"type": "task_started", "turn_id": turn_id}, stamp),
        event("turn_context", {"turn_id": turn_id, "cwd": CWD}, stamp),
        event(
            "event_msg",
            {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": input_count,
                        "output_tokens": output,
                        "total_tokens": input_count + output,
                        "cached_input_tokens": 50,
                        "reasoning_output_tokens": 10,
                    }
                },
            },
            stamp,
        ),
        event(
            "event_msg",
            {
                "type": "task_complete",
                "turn_id": turn_id,
                "last_agent_message": json.dumps(result or RESULT),
            },
            stamp,
        ),
    ]


def write_native(tmp_path, events=None):
    path = tmp_path / f"rollout-{SID}.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in (events or native_events())))
    return path


def receipt(*, second=False):
    return {
        "agent": "does-not-establish-role",
        "exit_code": 0,
        "error": None,
        "thread_id": SID,
        "launch": {
            "host_key": "codex",
            "project_dir": CWD,
            "started_at": START if not second else START.replace("10:", "11:"),
            **({"resume_thread_id": SID} if second else {}),
        },
        "result": RESULT if not second else {**RESULT, "summary": "second"},
    }


def fixture_row():
    first, second = receipt(), receipt(second=True)
    evidence = {
        "untouched": {"data": [1, 2]},
        "automatic_delivery": {
            "attempt": 2,
            "candidate": {"commit_sha": "c" * 40},
            "sessions": {"delivery": {"thread_id": SID}},
            "repair_pass_used": True,
            "attempts": [
                {"attempt": 1, "delivery": first},
                {"attempt": 2, "review": copy.deepcopy(second)},
            ],
            "review": second,
        },
    }
    row = {
        "id": 10,
        "key": "fixed",
        "project_id": 1,
        "status": "complete",
        "completed_at": "2026-11-06",
        "run_plan_id": 778,
        "task_id": 12,
        "updated_at": "unchanged",
        "outcome": "retained",
        "completion_evidence_json": json.dumps(evidence),
    }
    allow = [
        {
            "id": 10,
            "key": "fixed",
            "occurrences": [
                {
                    "attempt": attempt,
                    "phase": phase,
                    "session_id": SID,
                    "receipt_sha256": repair.digest(repair.immutable_receipt(r)),
                }
                for (attempt, phase), r in [((1, "delivery"), first), ((2, "review"), second)]
            ],
        }
    ]
    return row, allow


def database(tmp_path):
    path = tmp_path / "scratch.db"
    row, allow = fixture_row()
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE tracker_tickets (id INTEGER PRIMARY KEY, key TEXT, project_id INTEGER, "
        "status TEXT, completed_at TEXT, run_plan_id INTEGER, task_id INTEGER, updated_at TEXT, "
        "outcome TEXT, completion_evidence_json TEXT)"
    )
    connection.execute(
        "INSERT INTO tracker_tickets VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(row.values())
    )
    other = {**row, "id": 11, "project_id": 2}
    connection.execute(
        "INSERT INTO tracker_tickets VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(other.values())
    )
    connection.commit()
    connection.close()
    return path, row, allow


def test_native_cumulative_deltas_exact_result_and_classification(tmp_path):
    native = repair.read_native(write_native(tmp_path))
    first, first_proof = repair.recover(receipt(), native)
    second, second_proof = repair.recover(receipt(second=True), native)
    assert first["usage"]["measured"] == {
        "input": 100,
        "output": 20,
        "total": 120,
        "cache_read": None,
        "cache_write": None,
        "reasoning": None,
    }
    assert second["usage"]["measured"] == {
        "input": 80,
        "output": 10,
        "total": 90,
        "cache_read": None,
        "cache_write": None,
        "reasoning": None,
    }
    assert first["selected_role"] is None and second["validated_outcome"] is None
    assert first_proof["result_sha256"] == repair.digest(RESULT)
    assert second_proof["baseline_turn_id"] == "one"
    assert "last_agent_message" not in first_proof and "outputs" not in first_proof


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda r: r.update(thread_id="wrong"), "native_identity_mismatch"),
        (lambda r: r["result"].update(summary="wrong"), "terminal_result_missing_or_ambiguous"),
        (
            lambda r: r["launch"].update(started_at="2026-11-06T09:00:00+00:00"),
            "launch_boundary_mismatch",
        ),
        (lambda r: r["launch"].update(resume_thread_id="wrong"), "resume_session_mismatch"),
    ],
)
def test_receipt_mismatch_refuses(tmp_path, mutation, reason):
    native = repair.read_native(write_native(tmp_path))
    r = copy.deepcopy(receipt())
    mutation(r)
    with pytest.raises(repair.Refusal, match=reason):
        repair.recover(r, native)


@pytest.mark.parametrize(
    "field,value",
    [("id", "wrong"), ("cwd", "/other"), ("source", "vscode"), ("cli_version", "9.0.0")],
)
def test_metadata_guards(tmp_path, field, value):
    events = native_events()
    events[0]["payload"][field] = value
    with pytest.raises(repair.Refusal):
        repair.recover(receipt(), repair.read_native(write_native(tmp_path, events)))


def test_missing_intervening_snapshot_is_not_bridged(tmp_path):
    events = native_events()
    events[3]["payload"]["info"] = None
    fields, _ = repair.recover(
        receipt(second=True), repair.read_native(write_native(tmp_path, events))
    )
    assert fields["usage"]["measured"] is None
    assert fields["usage"]["reason"] == "missing_baseline"


@pytest.mark.parametrize(
    "mode", ["running", "duplicate", "decreasing", "bad_total", "context", "ambiguous_result"]
)
def test_unsupported_turns_are_never_measured(tmp_path, mode):
    events = native_events()
    if mode == "running":
        events.pop()
    if mode == "duplicate":
        events.insert(2, copy.deepcopy(events[1]))
    if mode == "decreasing":
        events[7]["payload"]["info"]["total_token_usage"].update(input_tokens=1, total_tokens=31)
    if mode == "bad_total":
        events[7]["payload"]["info"]["total_token_usage"]["total_tokens"] = 999
    if mode == "context":
        events[6]["payload"]["turn_id"] = "wrong"
    if mode == "ambiguous_result":
        events[8]["payload"]["last_agent_message"] = json.dumps(RESULT)
    with pytest.raises(repair.Refusal):
        parsed = repair.read_native(write_native(tmp_path, events))
        repair.recover(receipt(second=(mode != "ambiguous_result")), parsed)


def test_plan_is_read_only_deterministic_duplicate_safe_and_private(tmp_path):
    path, row, allow = database(tmp_path)
    write_native(tmp_path)
    before = path.read_bytes()
    with repair.connect(path) as connection:
        rows = repair.read_rows(connection, allow)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM tracker_tickets")
        plan = repair.plan_rows(rows, allow, [tmp_path])
        assert plan == repair.plan_rows(rows, allow, [tmp_path])
    assert path.read_bytes() == before
    assert plan["occurrence_count"] == 2
    assert plan["after"]["reported_usage"]["total"] == 210
    assert plan["after"]["launch_attempts"] == 2
    assert plan["after"]["recorded_reviewer_rejects"] == 0
    assert "does-not-establish-role" not in json.dumps(plan)
    patched = repair.patched_row(row, plan["rows"][0])
    a = patched["automatic_delivery"]
    assert a["review"] == a["attempts"][1]["review"]
    for copies in repair.locations(patched).values():
        for r in copies:
            for k in repair.ANALYTICS:
                r.pop(k, None)
    assert patched == json.loads(row["completion_evidence_json"])


def test_apply_backup_nonanalytics_preserved_and_repeat_noop(tmp_path):
    path, row, allow = database(tmp_path)
    write_native(tmp_path)
    with repair.connect(path) as c:
        plan = repair.plan_rows(repair.read_rows(c, allow), allow, [tmp_path])
        before_other = dict(c.execute("SELECT * FROM tracker_tickets WHERE id=11").fetchone())
    backup = tmp_path / "backup.json"
    with repair.connect(path, writable=True) as c:
        assert repair.apply_manifest(c, allow, plan, backup) == 1
        assert repair.apply_manifest(c, allow, plan, tmp_path / "unused.json") == 0
        after = dict(c.execute("SELECT * FROM tracker_tickets WHERE id=10").fetchone())
        assert {k: v for k, v in after.items() if k != "completion_evidence_json"} == {
            k: v for k, v in row.items() if k != "completion_evidence_json"
        }
        assert (
            dict(c.execute("SELECT * FROM tracker_tickets WHERE id=11").fetchone()) == before_other
        )
    assert json.loads(backup.read_text())["rows"] == [row]
    assert backup.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "unused.json").exists()


@pytest.mark.parametrize("after_apply", [False, True])
def test_stale_guard_even_on_repeat(tmp_path, after_apply):
    path, _, allow = database(tmp_path)
    write_native(tmp_path)
    with repair.connect(path) as c:
        plan = repair.plan_rows(repair.read_rows(c, allow), allow, [tmp_path])
    with repair.connect(path, writable=True) as c:
        if after_apply:
            repair.apply_manifest(c, allow, plan, tmp_path / "first.json")
        c.execute("UPDATE tracker_tickets SET outcome='concurrent'")
        c.commit()
        before = [dict(r) for r in c.execute("SELECT * FROM tracker_tickets")]
        with pytest.raises(repair.Refusal, match="stale_row"):
            repair.apply_manifest(c, allow, plan, tmp_path / "never.json")
        assert [dict(r) for r in c.execute("SELECT * FROM tracker_tickets")] == before
    assert not (tmp_path / "never.json").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", 2),
        ("status", "in-progress"),
        ("run_plan_id", 779),
        ("completed_at", "2026-11-08"),
    ],
)
def test_scope_exclusions(tmp_path, field, value):
    path, _, allow = database(tmp_path)
    with repair.connect(path, writable=True) as c:
        c.execute(f"UPDATE tracker_tickets SET {field}=? WHERE id=10", (value,))
        c.commit()
        with pytest.raises(repair.Refusal, match="ticket_outside_frozen_scope"):
            repair.read_rows(c, allow)


def test_missing_native_keeps_unknown_and_launch(tmp_path):
    row, allow = fixture_row()
    plan = repair.plan_rows([row], allow, [tmp_path])
    assert plan["usage_reasons"] == {"native_file_missing_or_ambiguous": 2}
    assert plan["after"]["launch_attempts"] == 2
    assert plan["after"]["reported_usage"]["total"] is None
    assert all(not o["fields"] for o in plan["rows"][0]["occurrences"])
    assert repair.patched_row(row, plan["rows"][0]) == json.loads(row["completion_evidence_json"])


def test_conflicting_duplicate_and_existing_analytics_refused(tmp_path):
    row, allow = fixture_row()
    write_native(tmp_path)
    evidence = json.loads(row["completion_evidence_json"])
    evidence["automatic_delivery"]["review"]["usage"] = {"measured": {"total": 1}}
    row["completion_evidence_json"] = json.dumps(evidence)
    with pytest.raises(repair.Refusal, match="existing_analytics_conflict"):
        repair.plan_rows([row], allow, [tmp_path])
    evidence["automatic_delivery"]["review"].pop("usage")
    evidence["automatic_delivery"]["attempts"][1]["review"]["exit_code"] = 2
    row["completion_evidence_json"] = json.dumps(evidence)
    with pytest.raises(repair.Refusal, match="allowlisted_receipt_or_duplicate_changed"):
        repair.plan_rows([row], allow, [tmp_path])


def test_cli_read_only_reviewed_apply_repeat_and_native_guard(tmp_path, monkeypatch):
    path, _, allow = database(tmp_path)
    native = write_native(tmp_path)
    allow_path = tmp_path / "allow.json"
    allow_path.write_text(json.dumps(allow))
    monkeypatch.setattr(repair, "ALLOWLIST_SHA256", repair.digest(allow))
    manifest = tmp_path / "dry-run.json"
    argv = [
        "repair-agent-usage.py",
        "--database",
        str(path),
        "--allowlist",
        str(allow_path),
        "--manifest",
        str(manifest),
        "--source-commit",
        "a" * 40,
        "--native-root",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    before = path.read_bytes()
    assert repair.main() == 0
    assert before == path.read_bytes()
    assert manifest.stat().st_mode & 0o777 == 0o600
    plan = json.loads(manifest.read_text())
    assert plan["after"]["reported_usage"]["total"] == 210
    backup = tmp_path / "backup.json"
    apply = [*argv, "--apply", "--backup", str(backup), "--reviewed-manifest-sha256"]
    monkeypatch.setattr(sys, "argv", [*apply, "wrong"])
    assert repair.main() == 2
    assert before == path.read_bytes() and not backup.exists()
    monkeypatch.setattr(sys, "argv", [*apply, repair.file_digest(manifest)])
    assert repair.main() == 0
    applied = path.read_bytes()
    assert repair.main() == 0
    assert applied == path.read_bytes()
    native.write_text(native.read_text() + "\n")
    assert repair.main() == 2
    assert applied == path.read_bytes()


def test_cli_fixed_allowlist_refuses_substitution(tmp_path, monkeypatch):
    path, _, allow = database(tmp_path)
    allow_path = tmp_path / "wrong-allow.json"
    allow_path.write_text(json.dumps(allow))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repair",
            "--database",
            str(path),
            "--allowlist",
            str(allow_path),
            "--manifest",
            str(tmp_path / "never.json"),
            "--source-commit",
            "a" * 40,
        ],
    )
    assert repair.main() == 2
    assert not (tmp_path / "never.json").exists()


def test_backup_failure_and_nonanalytics_manifest_refuse_before_write(tmp_path):
    path, row, allow = database(tmp_path)
    write_native(tmp_path)
    with repair.connect(path) as c:
        plan = repair.plan_rows(repair.read_rows(c, allow), allow, [tmp_path])
    backup = tmp_path / "already-exists.json"
    backup.write_text("retained")
    with repair.connect(path, writable=True) as c:
        with pytest.raises(FileExistsError):
            repair.apply_manifest(c, allow, plan, backup)
        assert dict(c.execute("SELECT * FROM tracker_tickets WHERE id=10").fetchone()) == row
        plan["rows"][0]["occurrences"][0]["fields"]["result"] = {"status": "erased"}
        with pytest.raises(repair.Refusal, match="nonanalytics_patch_refused"):
            repair.apply_manifest(c, allow, plan, tmp_path / "never.json")
        assert dict(c.execute("SELECT * FROM tracker_tickets WHERE id=10").fetchone()) == row


def test_unsupported_native_occurrence_is_unchanged_on_apply(tmp_path):
    path, row, allow = database(tmp_path)
    with repair.connect(path) as c:
        plan = repair.plan_rows(repair.read_rows(c, allow), allow, [tmp_path])
    with repair.connect(path, writable=True) as c:
        assert repair.apply_manifest(c, allow, plan, tmp_path / "unused.json") == 0
        assert dict(c.execute("SELECT * FROM tracker_tickets WHERE id=10").fetchone()) == row


@pytest.mark.parametrize(
    "result", [{"decision": "reject", "commit_sha": "a" * 40}, {**RESULT, "status": "repair"}]
)
def test_raw_reject_and_phase_do_not_prove_validated_rejection(tmp_path, result):
    r = receipt()
    r["result"] = result
    events = native_events()
    events[4]["payload"]["last_agent_message"] = json.dumps(result)
    fields, _ = repair.recover(r, repair.read_native(write_native(tmp_path, events)))
    metrics = repair.summarize_receipts([{**r, **fields}])
    assert fields["selected_role"] is None and fields["validated_outcome"] is None
    assert metrics.recorded_reviewer_rejects == 0
    assert metrics.coverage.classification_unknown_attempts == 1


def test_later_stale_row_prevents_all_writes_and_backup(tmp_path):
    path, row, allow = database(tmp_path)
    write_native(tmp_path)
    with repair.connect(path, writable=True) as c:
        second = {**row, "id": 12, "key": "second"}
        c.execute(
            "INSERT INTO tracker_tickets VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(second.values())
        )
        c.commit()
        allow.append({**allow[0], "id": 12, "key": "second"})
        plan = repair.plan_rows(repair.read_rows(c, allow), allow, [tmp_path])
        c.execute("UPDATE tracker_tickets SET outcome='concurrent' WHERE id=12")
        c.commit()
        with pytest.raises(repair.Refusal, match="stale_row"):
            repair.apply_manifest(c, allow, plan, tmp_path / "never.json")
        assert dict(c.execute("SELECT * FROM tracker_tickets WHERE id=10").fetchone()) == row
    assert not (tmp_path / "never.json").exists()


def test_ambiguous_native_files_remain_unknown(tmp_path):
    row, allow = fixture_row()
    write_native(tmp_path)
    duplicate = tmp_path / "duplicate"
    duplicate.mkdir()
    write_native(duplicate)
    plan = repair.plan_rows([row], allow, [tmp_path])
    assert plan["usage_reasons"] == {"native_file_missing_or_ambiguous": 2}
    assert all(not o["fields"] for o in plan["rows"][0]["occurrences"])
