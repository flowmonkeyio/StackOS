"""Archive identities use the normal delivery/review lifecycle and retained sessions."""

from __future__ import annotations

import json
import tarfile
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from stackos import task_delivery
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.base import ValidationError
from stackos.repositories.tracker import TrackerRepository
from stackos.task_candidates import archive_candidate
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    commit,
    generic_result,
    git,
    prepared_task,
    submit,
)
from tests.integration.test_repositories.test_task_delivery import (
    controlled_profile_host as controlled_profile_host,
)
from tests.integration.test_repositories.test_task_delivery import (
    git_project as git_project,
)
from tests.integration.test_repositories.test_task_delivery import (
    native_source_fixtures as native_source_fixtures,
)


def page_candidate(root: Path, revision: str) -> dict:
    """Seal source/build while preserving their intentionally ignored live directory."""
    page = root / ".nx" / "page"
    if not page.exists():
        (root / ".gitignore").write_text(".nx/\n")
        git(root, "add", ".gitignore")
        git(root, "commit", "-m", "Ignore standalone page")
    page.mkdir(parents=True, exist_ok=True)
    (page / "src.js").write_text(f"export const order = {revision!r};\n")
    (page / "package.json").write_text('{"name":"standalone-page"}\n')
    dist = page / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text(f"<main>{revision}</main>\n")
    proof = page / "proof" / revision
    proof.mkdir(parents=True, exist_ok=True)
    candidate = {"kind": "archives"}
    for key, live, names, excludes in (
        ("source", page, ["package.json", "src.js"], ["dist", "proof", "node_modules"]),
        ("build", dist, ["index.html"], []),
    ):
        archive = proof / f"{key}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name in names:
                bundle.add(live / name, arcname=name)
        candidate[key] = {
            "path": str(archive.relative_to(root)),
            "sha256": sha256(archive.read_bytes()).hexdigest(),
            "root": str(live.relative_to(root)),
            "exclude": excludes,
        }
    return candidate


@pytest.mark.parametrize("damage", ["archive", "source", "build", "extra", "missing", "symlink"])
def test_archive_candidate_rejects_changed_artifact_or_live_inventory(tmp_path, damage):
    candidate = page_candidate(tmp_path, "original")
    assert archive_candidate(str(tmp_path), candidate) == candidate
    source = tmp_path / candidate["source"]["root"]
    if damage == "archive":
        (tmp_path / candidate["source"]["path"]).write_bytes(b"changed archive")
    elif damage == "source":
        (source / "src.js").write_text("unsealed source change")
    elif damage == "build":
        (source / "dist" / "index.html").write_text("unsealed build change")
    elif damage == "extra":
        (source / "new.js").write_text("not archived")
    elif damage == "missing":
        (source / "src.js").unlink()
    else:
        (source / "link.js").symlink_to(source / "src.js")
    with pytest.raises(ValidationError, match=r"SHA256|live source/build|link"):
        archive_candidate(str(tmp_path), candidate)


@pytest.mark.parametrize("damage", ["traversal", "duplicate", "symlink", "outside-root"])
def test_archive_candidate_rejects_unsafe_members_and_paths(tmp_path, damage):
    candidate = page_candidate(tmp_path, "original")
    entry = candidate["source"]
    if damage == "outside-root":
        entry["root"] = "../outside"
    else:
        archive = tmp_path / entry["path"]
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("../src.js" if damage == "traversal" else "src.js")
            if damage == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "package.json"
                bundle.addfile(member)
            else:
                member.size = 1
                bundle.addfile(member, BytesIO(b"x"))
                if damage == "duplicate":
                    bundle.addfile(member, BytesIO(b"x"))
        entry["sha256"] = sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValidationError, match=r"Unsafe|delivery directory"):
        archive_candidate(str(tmp_path), candidate)


def test_archive_candidate_rejects_unreadable_additional_source_directory(tmp_path, monkeypatch):
    from stackos import task_candidates

    candidate = page_candidate(tmp_path, "original")
    unreadable = tmp_path / candidate["source"]["root"] / "private"
    unreadable.mkdir()
    (unreadable / "unsealed.js").write_text("unsealed extra source")
    scandir = task_candidates.os.scandir

    def deny_private_directory(path):
        if Path(path) == unreadable:
            raise PermissionError("Unreadable source directory")
        return scandir(path)

    monkeypatch.setattr(task_candidates.os, "scandir", deny_private_directory)
    with pytest.raises(ValidationError):
        archive_candidate(str(tmp_path), candidate)


@pytest.mark.parametrize("identity", ["typo", "missing-kind", "null", "string", "omitted"])
def test_initial_explicit_malformed_candidate_fails_closed(
    session, project_id, tmp_path, monkeypatch, identity
):
    prepared_task(session, project_id)
    candidate = page_candidate(tmp_path, "original")
    (tmp_path / ".nx/page/src.js").write_text("changed after sealing")
    if identity == "typo":
        candidate["kind"] = "archive"
    elif identity == "missing-kind":
        candidate.pop("kind")
    elif identity == "null":
        candidate = None
    elif identity == "string":
        candidate = "archives"
    calls = []

    def launch(_host, _directory, _instructions, prompt, _schema, **selection):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, phase))
        outputs = {} if identity == "omitted" else {"candidate": candidate}
        return AgentRunResult(0, thread_id=f"{key}-{phase}", result=generic_result(outputs=outputs))

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    if identity == "omitted":
        assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    else:
        assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
        assert calls == [("first", "delivery")]
        assert snapshot.tasks[0].metadata_json["delivery"]["stop_context"]["code"] == (
            "candidate_validation"
        )


def archive_loop(session, project_id, tmp_path, monkeypatch, *, legacy=False, repair_fault=None):
    prepared_task(session, project_id)
    current = page_candidate(tmp_path, "original")
    backend = git(tmp_path, "rev-parse", "HEAD")
    calls = []

    def launch(_host, _directory, _instructions, prompt, _schema, **selection):
        nonlocal current
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        phase = packet["phase"]
        previous = [
            p for p, _ in calls if p["brief"]["ticket"]["key"] == key and p["phase"] == phase
        ]
        assert selection.get("resume_thread_id") == (f"{key}-{phase}" if previous else None)
        calls.append((packet, selection))
        if key == "second":
            outputs, status = {}, "success"
        elif phase == "delivery":
            if previous and repair_fault != "unchanged":
                current = page_candidate(tmp_path, "repaired")
            outputs = {
                "backend_commit_sha": backend,
                "source_archive": {
                    "path": current["source"]["path"],
                    "sha256": current["source"]["sha256"],
                    "bytes": (tmp_path / current["source"]["path"]).stat().st_size,
                    "files": 2,
                },
                "built_archive": {
                    "path": current["build"]["path"],
                    "sha256": current["build"]["sha256"],
                    "bytes": (tmp_path / current["build"]["path"]).stat().st_size,
                    "files": 1,
                },
            }
            if legacy:
                outputs["commit_sha"] = backend
            else:
                outputs["candidate"] = deepcopy(current)
                if previous and repair_fault == "omitted":
                    outputs.pop("candidate")
                if previous and repair_fault == "layout":
                    outputs["candidate"]["source"]["exclude"].append("new-exclusion")
            status = "success"
        else:
            outputs = {"commit_sha": backend} if legacy else {"candidate": deepcopy(current)}
            status = "repair" if not previous else "success"
        return AgentRunResult(
            0,
            thread_id=f"{key}-{phase}",
            result=generic_result(
                status, outputs, "Chart order repair" if status == "repair" else "Checked"
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    before = deepcopy(
        repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json
    )
    automatic = before["automatic_delivery"]
    request = {
        "ticket_key": "first",
        "expected_attempt": automatic["attempt"],
        "expected_repair_count": automatic["repair_count"],
        "expected_delivery_thread_id": "first-delivery",
        "expected_review_thread_id": "first-review",
        "expected_backend_commit_sha": backend,
        "expected_delivery_receipt_digest": sha256(
            json.dumps(automatic["delivery"], sort_keys=True).encode()
        ).hexdigest(),
        "candidate": deepcopy(current),
        "reason": "Operator reconciles archive identity after mistaken backend Git enrollment",
    }
    return scheduler, calls, before, request


@pytest.mark.parametrize("repair_fault", [None, "unchanged", "omitted", "layout"])
def test_archive_repair_preserves_automation_and_requires_new_sealed_source(
    session, project_id, tmp_path, monkeypatch, repair_fault
):
    _scheduler, calls, _before, _request = archive_loop(
        session, project_id, tmp_path, monkeypatch, repair_fault=repair_fault
    )
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 1
    if repair_fault:
        assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
        assert len(calls) == 3
    else:
        assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
        assert len(calls) == 6
        assert evidence["candidate"]["kind"] == "archives"
        assert evidence["sessions"]["delivery"]["thread_id"] == "first-delivery"
        assert evidence["sessions"]["review"]["thread_id"] == "first-review"
        assert evidence["attempts"][0]["review"]["result"]["status"] == "repair"
        assert git(tmp_path, "rev-parse", "HEAD") == _request["expected_backend_commit_sha"]


def test_explicit_archive_recovery_resumes_retained_reviewer_and_continues_task(
    session, project_id, tmp_path, monkeypatch
):
    scheduler, calls, before, request = archive_loop(
        session, project_id, tmp_path, monkeypatch, legacy=True
    )
    repo = TrackerRepository(session)
    stopped = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in stopped.tickets] == ["failed", "not-started"]
    assert "Repair requires a new descendant commit" in stopped.tickets[0].outcome
    assert before["automatic_delivery"]["attempt"] == 2
    assert before["automatic_delivery"]["repair_count"] == 1
    old_calls = len(calls)

    def recovered_launch(_host, _directory, _instructions, prompt, _schema, **selection):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((packet, selection))
        if key == "first":
            assert phase == "review"
            assert selection["resume_thread_id"] == "first-review"
            assert packet["candidate"] == request["candidate"]
            outputs = {"candidate": request["candidate"]}
        else:
            outputs = {}
        return AgentRunResult(0, thread_id=f"{key}-{phase}", result=generic_result(outputs=outputs))

    monkeypatch.setattr(task_delivery, "run_agent", recovered_launch)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        reconcile_candidate=request,
        delivery_agent=None,
        review_agent=None,
    )
    scheduler.run()
    session.expire_all()
    final = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in final.tickets] == ["complete", "complete"]
    assert [(p["brief"]["ticket"]["key"], p["phase"]) for p, _ in calls[old_calls:]] == [
        ("first", "review"),
        ("second", "delivery"),
        ("second", "review"),
    ]
    evidence = final.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 1
    assert evidence["sessions"] == before["automatic_delivery"]["sessions"]
    assert evidence["attempts"][0] == before["automatic_delivery"]["attempts"][0]
    assert (
        evidence["candidate_reconciliations"][0]["original_delivery"]
        == before["automatic_delivery"]["delivery"]
    )
    assert evidence["candidate"] == request["candidate"]


@pytest.mark.parametrize(
    "damage",
    [
        "attempt",
        "count",
        "delivery-session",
        "review-session",
        "receipt",
        "backend",
        "hash",
        "live-source",
    ],
)
def test_archive_recovery_rejects_stale_target_without_mutation(
    session, project_id, tmp_path, monkeypatch, damage
):
    scheduler, calls, before, request = archive_loop(
        session, project_id, tmp_path, monkeypatch, legacy=True
    )
    if damage == "attempt":
        request["expected_attempt"] += 1
    elif damage == "count":
        request["expected_repair_count"] += 1
    elif damage == "delivery-session":
        request["expected_delivery_thread_id"] = "stale-delivery"
    elif damage == "review-session":
        request["expected_review_thread_id"] = "stale-review"
    elif damage == "receipt":
        request["expected_delivery_receipt_digest"] = "0" * 64
    elif damage == "backend":
        request["expected_backend_commit_sha"] = "0" * 40
    elif damage == "hash":
        request["candidate"]["source"]["sha256"] = "0" * 64
    else:
        (tmp_path / ".nx/page/src.js").write_text("changed after delivery")
    with pytest.raises(ValidationError):
        submit(
            session,
            project_id,
            tmp_path,
            scheduler,
            reconcile_candidate=request,
            delivery_agent=None,
            review_agent=None,
        )
    session.expire_all()
    actual = TrackerRepository(session).brief(project_id=project_id, ticket_key="first").ticket
    assert actual.completion_evidence_json == before
    assert actual.status == "failed"
    assert not scheduler.jobs
    assert len(calls) == 3


@pytest.mark.parametrize("when", ["queued", "review"])
@pytest.mark.parametrize("artifact", ["source", "build"])
def test_recovered_candidate_is_revalidated_before_and_after_retained_review(
    session, project_id, tmp_path, monkeypatch, when, artifact
):
    scheduler, calls, before, request = archive_loop(
        session, project_id, tmp_path, monkeypatch, legacy=True
    )
    reviewed = []
    relative = ".nx/page/src.js" if artifact == "source" else ".nx/page/dist/index.html"

    def launch(_host, _directory, _instructions, prompt, _schema, **selection):
        packet = json.loads(prompt)
        assert packet["phase"] == "review"
        assert selection["resume_thread_id"] == "first-review"
        reviewed.append(packet)
        (tmp_path / relative).write_text("changed during independent review")
        return AgentRunResult(
            0,
            thread_id="first-review",
            result=generic_result(outputs={"candidate": request["candidate"]}),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        reconcile_candidate=request,
        delivery_agent=None,
        review_agent=None,
    )
    if when == "queued":
        (tmp_path / relative).write_text("changed after recovery admission")
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert len(reviewed) == (0 if when == "queued" else 1)
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 1
    assert evidence["sessions"] == before["automatic_delivery"]["sessions"]
    assert len(calls) == 3
    assert "live source/build" in snapshot.tickets[0].outcome


def test_recovered_review_rejection_requeues_failed_repair_without_recovery_marker(
    session, project_id, tmp_path, monkeypatch
):
    scheduler, _calls, before, request = archive_loop(
        session, project_id, tmp_path, monkeypatch, legacy=True
    )
    recovery_calls = []

    def interrupted_repair(_host, _directory, _instructions, prompt, _schema, **selection):
        packet = json.loads(prompt)
        phase = packet["phase"]
        recovery_calls.append(phase)
        assert selection["resume_thread_id"] == f"first-{phase}"
        if phase == "review":
            return AgentRunResult(
                0,
                thread_id="first-review",
                result=generic_result("repair", {"candidate": request["candidate"]}),
            )
        return AgentRunResult(1, thread_id="first-delivery", error="Interrupted ordinary repair")

    monkeypatch.setattr(task_delivery, "run_agent", interrupted_repair)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        reconcile_candidate=request,
        delivery_agent=None,
        review_agent=None,
    )
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    stopped = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = deepcopy(stopped.completion_evidence_json["automatic_delivery"])
    assert stopped.status == "failed"
    assert recovery_calls == ["review", "delivery"]
    assert evidence["repair_count"] == 2
    assert "candidate_reconciliation_active" not in evidence
    assert "candidate_reconciliation_pending" not in evidence
    assert evidence["sessions"] == before["automatic_delivery"]["sessions"]
    recovered_original = evidence["candidate_reconciliations"][0]["original_delivery"]
    resumed_calls = []
    current = None

    def resumed_repair(_host, _directory, _instructions, prompt, _schema, **selection):
        nonlocal current
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        resumed_calls.append((key, phase))
        outputs = {}
        if key == "first":
            assert selection["resume_thread_id"] == f"first-{phase}"
            assert packet["occurrence"]["repair_count"] == 2
            if phase == "delivery":
                current = page_candidate(tmp_path, "second-repair")
            outputs = {"candidate": current}
        return AgentRunResult(0, thread_id=f"{key}-{phase}", result=generic_result(outputs=outputs))

    monkeypatch.setattr(task_delivery, "run_agent", resumed_repair)
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    assert resumed_calls == [
        ("first", "delivery"),
        ("first", "review"),
        ("second", "delivery"),
        ("second", "review"),
    ]
    final = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    assert final["repair_count"] == 2
    assert final["sessions"] == evidence["sessions"]
    assert final["candidate_reconciliations"][0]["original_delivery"] == recovered_original


@pytest.mark.parametrize("repair", ["descendant", "same", "omitted", "archives"])
def test_git_repair_retains_descendant_contract(session, project_id, tmp_path, monkeypatch, repair):
    prepared_task(session, project_id)
    candidate = page_candidate(tmp_path, "original")
    calls = []

    def launch(_host, _directory, _instructions, prompt, _schema, **selection):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, phase))
        if key == "second":
            outputs, status = {}, "success"
        elif phase == "review":
            outputs = {"commit_sha": packet["candidate"]["commit_sha"]}
            status = "repair" if len(calls) == 2 else "success"
        else:
            sha = (
                commit(tmp_path, f"git-source-{len(calls)}")
                if len(calls) == 1 or repair == "descendant"
                else git(tmp_path, "rev-parse", "HEAD")
            )
            outputs, status = {"commit_sha": sha}, "success"
            if len(calls) == 3 and repair in {"omitted", "archives"}:
                outputs = {} if repair == "omitted" else {"candidate": candidate}
        return AgentRunResult(0, thread_id=f"{key}-{phase}", result=generic_result(status, outputs))

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert snapshot.tickets[0].completion_evidence_json["automatic_delivery"]["repair_count"] == 1
    if repair == "descendant":
        assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    else:
        assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
        assert len(calls) == 3
