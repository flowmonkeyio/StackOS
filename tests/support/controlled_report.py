"""Independent frozen-byte report oracle; no producer aggregation imports."""

import hashlib
import stat
from pathlib import Path

INPUT_SHA256 = "37b752df834a883cf0ca9f83f1ab6df96ead1fcfd91d420b65c85f2201121d7f"
REPORT_SHA256 = "ed21d51f672f7c0110a6c256dadf5f7067d282d4fb0ae3a7c4fa37d8fddd760c"
EXPECTED = (
    b'{"blocked_ids":["synthetic-2"],'
    b'"by_status":{"blocked":1,"complete":1,"ready":1},'
    b'"input_sha256":"'
    b"37b752df834a883cf0ca9f83f1ab6df96ead1fcfd91d420b65c85f2201121d7f"
    b'","schema_version":"stackos.synthetic-status-report.v1","total":3}\n'
)


def verify_report(directory: Path, input_path: Path, original: bytes, sentinel: Path) -> None:
    assert input_path.read_bytes() == original
    assert len(original) == 186 and hashlib.sha256(original).hexdigest() == INPUT_SHA256
    assert sentinel.read_bytes() == b"unchanged sentinel\n"
    assert stat.S_ISDIR(directory.lstat().st_mode)
    output = directory / "report.json"
    info = output.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    assert stat.S_IMODE(info.st_mode) == 0o600
    raw = output.read_bytes()
    assert raw == EXPECTED and len(raw) == 227
    assert hashlib.sha256(raw).hexdigest() == REPORT_SHA256


def select_report(case):
    """Prepare disposable test approval using the actual finite command resolver."""
    from stackos.actions import controlled_command as fixed

    root = Path(fixed.__file__).resolve().parents[2]
    for name in (fixed._REPORT_SCRIPT, fixed._REPORT_INPUT):
        target = case.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    case.args["input_json"]["command_key"] = "synthetic-status-report-v1"
    request = fixed._ControlledCommandInput.model_validate(case.args["input_json"])
    directory = fixed._controlled_report_directory(
        case.settings, project_id=case.project, request=request
    )
    command = fixed._resolve_controlled_command(request.command_key, report_directory=directory)
    case.args["input_json"]["command_digest"] = command.digest

    def update(value):
        value["controlled_command"].update(
            command_digest=command.digest, command=command.descriptor()
        )
        if "controlled_host" in value:
            from stackos.controlled_process_host import _digest, _native_manifest

            value["controlled_host"].update(
                command_digest=command.digest, runtime_digest=_digest(_native_manifest(command))
            )

    case.approval(change=update)
    # Native fixtures already pinned their old policy in canonical continuity.
    # Prepare the new exact approval before any execution, through the same
    # disposable fixture records; this is not authority for a real caller.
    from sqlmodel import select

    from stackos.controlled_process_host import _digest
    from stackos.db.models import ApprovalRequest, TrackerTask, WorkItemOwnership

    approval = case.session.exec(
        select(ApprovalRequest).where(ApprovalRequest.run_plan_id == case.plan)
    ).one()
    if "controlled_host" in approval.decision_json:
        deadline_ref = "native:" + _digest(approval.decision_json["controlled_host"])
        task = case.session.get(TrackerTask, case.task)
        owner = case.session.exec(select(WorkItemOwnership)).one()
        task.metadata_json = task.metadata_json | {
            "ownership_continuity": task.metadata_json["ownership_continuity"]
            | {"deadline_ref": deadline_ref}
        }
        owner.continuity_json = owner.continuity_json | {"deadline_ref": deadline_ref}
        case.session.add_all([task, owner])
        case.session.commit()
    return command
