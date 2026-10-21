"""Finite test-only frozen body in an isolated, exactly approved runtime copy.

No new production command, hook, supervisor or signal policy. The existing fixed
report command and input remain selected. Only its copied body is delayed; the
counterfactual additionally removes one copied independent deadline guard.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

from sqlmodel import Session, select

from stackos import controlled_process_clock as clock
from stackos import controlled_process_host as host
from stackos.actions import controlled_command as fixed
from stackos.db.models import ApprovalRequest, ApprovalRequestStatus, TrackerTask, WorkItemOwnership
from stackos.repositories.run_plans import RunPlanRepository
from tests.integration import test_controlled_command_loaded as loaded

BODY_SECONDS = 6
DEADLINE_NS = 3_000_000_000
GRACE_NS = 1_000_000_000
CLEANUP_NS = 10_000_000_000


def prepare_copy(work: Path, *, weaken_deadline: bool) -> tuple[Path, dict]:
    source = Path(fixed.__file__).resolve().parents[2]
    copied = work / "frozen-runtime"
    shutil.copytree(
        source,
        copied,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        ),
    )
    script = copied / fixed._REPORT_SCRIPT
    original = script.read_text()
    anchor = "def produce(directory: Path) -> None:\n"
    assert original.count(anchor) == 1
    finite = (
        "    # Test-only finite frozen body; no progress/output is fabricated.\n"
        "    import time\n"
        "    marker = directory.parent / (directory.name + '.fixture-started')\n"
        "    marker.write_text('frozen-body-started\\n')\n"
        f"    end = time.monotonic() + {BODY_SECONDS}\n"
        "    while time.monotonic() < end:\n"
        "        pass\n"
    )
    script.write_text(original.replace(anchor, anchor + finite))
    mutation = None
    if weaken_deadline:
        shim = copied / "stackos/controlled_process_shim.py"
        raw = shim.read_text()
        guard = "            if now >= policy.deadline_continuous_ns:\n"
        assert raw.count(guard) == 1
        # All identity/barrier/cleanup checks remain. Natural body completion is
        # finite and earlier than the original cleanup ceiling.
        changed = raw.replace(
            guard, "            if False and now >= policy.deadline_continuous_ns:\n"
        )
        shim.write_text(changed)
        mutation = dict(
            path="stackos/controlled_process_shim.py",
            guard="independent-watchdog-deadline",
            before=hashlib.sha256(raw.encode()).hexdigest(),
            after=hashlib.sha256(changed.encode()).hexdigest(),
        )
    manifest = []
    for path in sorted(copied.rglob("*")):
        if path.is_file() and not path.is_symlink():
            manifest.append(
                dict(
                    path=path.relative_to(copied).as_posix(),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    record = dict(
        schema_version="stackos.test-frozen-runtime.v1",
        body_seconds=BODY_SECONDS,
        deadline_ns=DEADLINE_NS,
        cleanup_ns=CLEANUP_NS,
        mutation=mutation,
        files=manifest,
    )
    (work / "frozen-runtime-manifest.json").write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n"
    )
    return copied, record


def setup_case(settings, engine, copied: Path, monkeypatch):
    # Existing fixture source-root seam is test-process-only. The owned daemon
    # imports the exact copy via PYTHONPATH; no production option is introduced.
    with monkeypatch.context() as fixture:
        fixture.setattr(fixed, "_runtime_source_root", lambda: copied)
        case = loaded._setup_case(settings, engine)
    observed = clock._sample_clock()
    assert clock._valid_clock(observed)
    with Session(engine) as session:
        task = session.get(TrackerTask, int(case.item.split(":")[1]))
        plan_id = task.source_json["run_plan_id"]
        approval = session.exec(
            select(ApprovalRequest).where(ApprovalRequest.run_plan_id == plan_id)
        ).one()
        decision = deepcopy(approval.decision_json)
        # _native_manifest resolves its own module path. Rebind only its explicit
        # descriptor observations to the actual copied daemon/runtime bytes.
        runtime = host._native_manifest(case.command)
        runtime.update(
            source_root=str(copied),
            shim=str(copied / "stackos/controlled_process_shim.py"),
            watchdog_entry=str(copied / "stackos/controlled_process_shim.py"),
            watchdog_entry_sha256=hashlib.sha256(
                (copied / "stackos/controlled_process_shim.py").read_bytes()
            ).hexdigest(),
            source_files={
                name: hashlib.sha256((copied / name).read_bytes()).hexdigest()
                for name in host._RUNTIME_FILES
            },
        )
        native = decision["controlled_host"]
        native.update(
            runtime_digest=host._digest(runtime),
            boot_uuid=observed.boot_uuid,
            deadline_continuous_ns=observed.continuous_ns + DEADLINE_NS,
            term_grace_ns=GRACE_NS,
            cleanup_deadline_continuous_ns=observed.continuous_ns + CLEANUP_NS,
        )
        RunPlanRepository(session).update(
            run_plan_id=plan_id,
            approval_key="command",
            approval_status=ApprovalRequestStatus.APPROVED,
            decided_by="synthetic:frozen-fixture-operator",
            decision_json=decision,
        )
        owner = session.exec(
            select(WorkItemOwnership).where(
                WorkItemOwnership.project_id == case.project,
                WorkItemOwnership.canonical_item_id == case.item,
            )
        ).one()
        deadline_ref = "native:" + host._digest(native)
        task.metadata_json = {
            "ownership_continuity": task.metadata_json["ownership_continuity"]
            | {"deadline_ref": deadline_ref}
        }
        owner.continuity_json = owner.continuity_json | {"deadline_ref": deadline_ref}
        session.add_all([task, owner])
        session.commit()
        case.run = decision["controlled_command"]["controller_run_id"]
        case.policy = dict(native)
        case.marker = case.command.report_directory.parent / (
            case.command.report_directory.name + ".fixture-started"
        )
        case.approved_runtime_digest = native["runtime_digest"]
        return case
