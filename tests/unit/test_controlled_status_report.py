"""Portable fixed producer and strict descriptor proof; no native roles."""

import importlib.util
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from stackos.actions import controlled_command as fixed
from stackos.config import Settings
from stackos.controlled_process_host import _ControlError, _prepare_report_directory
from stackos.controlled_process_shim import _validate_literal_command
from stackos.repositories.base import ConflictError
from tests.support.controlled_report import EXPECTED, INPUT_SHA256, verify_report
from tests.unit.test_controlled_command import payload


@pytest.fixture
def producer():
    path = fixed._runtime_source_root() / fixed._REPORT_SCRIPT
    spec = importlib.util.spec_from_file_location("fixed_status_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def workspace(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    settings = Settings(_env_file=None, state_dir=state, data_dir=tmp_path / "data")
    request = fixed._ControlledCommandInput.model_validate(
        payload() | {"command_key": "synthetic-status-report-v1"}
    )
    directory = fixed._controlled_report_directory(settings, project_id=14, request=request)
    fd = _prepare_report_directory(state, directory)
    assert fd.close()
    return settings, request, directory


def test_literal_isolated_producer_and_opaque_result(workspace, producer, tmp_path):
    settings, request, directory = workspace
    command = fixed._resolve_controlled_command(request.command_key, report_directory=directory)
    original = producer.INPUT.read_bytes()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"unchanged sentinel\n")
    _validate_literal_command(command.descriptor())
    process = subprocess.run(
        command.argv,
        cwd=command.cwd,
        env=dict(command.environment),
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0 and process.stdout == process.stderr == b""
    verify_report(directory, producer.INPUT, original, sentinel)
    result = fixed._verified_report_result(command)
    assert result == {
        "candidate_ref": "controlled-report:" + directory.name,
        "report_sha256": fixed._REPORT_SHA,
        "input_sha256": INPUT_SHA256,
        "byte_count": 227,
        "accepted": False,
    }
    assert str(settings.state_dir) not in json.dumps(result)
    assert directory == fixed._controlled_report_directory(
        settings,
        project_id=14,
        request=request.model_copy(update={"expected_epoch": 9, "expected_revision": 20}),
    )
    assert directory != fixed._controlled_report_directory(settings, project_id=15, request=request)
    before = (directory / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        producer.produce(directory)
    assert (directory / "report.json").read_bytes() == before


@pytest.mark.parametrize(
    "field",
    ["argv", "environment", "executable_sha256", "command_key", "cwd", "source_files", "report"],
)
def test_finite_shim_rejects_descriptor_changes(workspace, field):
    _, request, directory = workspace
    descriptor = fixed._resolve_controlled_command(
        request.command_key, report_directory=directory
    ).descriptor()
    changed = deepcopy(descriptor)
    if field == "argv":
        changed[field][-1] += "/other"
    elif field == "environment":
        changed[field]["EXTRA"] = "value"
    elif field == "source_files":
        changed[field][fixed._REPORT_SCRIPT] = "0" * 64
    elif field == "report":
        changed[field]["report_sha256"] = "0" * 64
    else:
        changed[field] = "other"
    with pytest.raises((_ControlError, OSError)):
        _validate_literal_command(changed)


@pytest.mark.parametrize(
    "path", [None, Path("relative"), Path("/arbitrary"), Path("/controlled-reports/../" + "a" * 64)]
)
def test_report_resolver_rejects_arbitrary_directory(path):
    with pytest.raises(ConflictError):
        fixed._resolve_controlled_command("synthetic-status-report-v1", report_directory=path)


@pytest.mark.parametrize(
    "mutation",
    ["bool", "duplicate-id", "extra-field", "wrong-schema", "unknown-status", "duplicate-key"],
)
def test_strict_input_schema(producer, mutation):
    value = json.loads(producer.INPUT.read_bytes())
    if mutation == "bool":
        value["records"][0]["status"] = True
    elif mutation == "duplicate-id":
        value["records"][1]["id"] = "synthetic-1"
    elif mutation == "extra-field":
        value["records"][0]["extra"] = 1
    elif mutation == "wrong-schema":
        value["schema_version"] = "other"
    elif mutation == "unknown-status":
        value["records"][0]["status"] = "other"
    raw = json.dumps(value).encode()
    if mutation == "duplicate-key":
        raw = b'{"records":[],"records":[]}'
    with pytest.raises(ValueError):
        producer.report_bytes(raw)


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "drift"])
def test_input_file_failures_hold_without_output(workspace, producer, tmp_path, monkeypatch, kind):
    _, _, directory = workspace
    replacement = tmp_path / "input.json"
    if kind == "symlink":
        replacement.symlink_to(producer.INPUT)
    elif kind == "directory":
        replacement.mkdir()
    elif kind == "fifo":
        os.mkfifo(replacement)
    else:
        replacement.write_bytes(b"x" * 186)
    monkeypatch.setattr(producer, "INPUT", replacement)
    with pytest.raises((OSError, ValueError)):
        producer.produce(directory)
    assert not (directory / "report.json").exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "existing"])
def test_exclusive_output_rejects_substitution(workspace, producer, tmp_path, kind):
    _, _, directory = workspace
    output = directory / "report.json"
    target = tmp_path / "untouched"
    target.write_bytes(b"untouched")
    if kind == "symlink":
        output.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(output)
    elif kind == "directory":
        output.mkdir()
    else:
        output.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        producer.produce(directory)
    assert target.read_bytes() == b"untouched"
    with pytest.raises((OSError, ConflictError)):
        fixed._verified_report_result(
            fixed._resolve_controlled_command(
                "synthetic-status-report-v1", report_directory=directory
            )
        )


@pytest.mark.parametrize("operation", ["write", "fsync", "close"])
def test_partial_failure_never_reports_success(workspace, producer, monkeypatch, operation):
    _, _, directory = workspace
    actual = getattr(producer.os, operation)
    seen = []

    def fail(fd, *args):
        # Only the report file, never directory/input handles.
        if os.fstat(fd).st_size == 0 and operation == "write":
            seen.append(fd)
            return actual(fd, args[0][:10])
        if os.fstat(fd).st_size == 227 and not seen:
            seen.append(fd)
            if operation == "close":
                actual(fd)
            raise OSError("synthetic failure")
        return actual(fd, *args)

    monkeypatch.setattr(producer.os, operation, fail)
    with pytest.raises(OSError):
        producer.produce(directory)
    assert len(seen) == 1


def test_workspace_symlink_and_preexisting_intent_rejected(workspace, producer, tmp_path):
    settings, _, directory = workspace
    with pytest.raises(FileExistsError):
        _prepare_report_directory(settings.state_dir, directory)
    alias = tmp_path / "alias"
    alias.symlink_to(settings.state_dir, target_is_directory=True)
    substituted = alias / "controlled-reports" / directory.name
    with pytest.raises(OSError):
        producer.produce(substituted)


def test_report_result_strict_and_noop_omission():
    from types import SimpleNamespace

    from tests.integration.test_controlled_command_public import result_for

    base = result_for(SimpleNamespace(args={"input_json": payload()}))
    assert "report" not in fixed._ControlledNativeResult.model_validate(base).model_dump()
    report = dict(
        candidate_ref="controlled-report:" + "a" * 64,
        report_sha256=fixed._REPORT_SHA,
        input_sha256=INPUT_SHA256,
        byte_count=227,
        accepted=False,
    )
    for changed in (
        report | {"path": "/private/path"},
        report | {"byte_count": True},
        report | {"accepted": 0},
    ):
        with pytest.raises(ValueError):
            fixed._ControlledReportResult.model_validate(changed)
    with pytest.raises(ValueError):
        fixed._ControlledNativeResult.model_validate(base | {"report": report})
    assert len(EXPECTED) == 227


def test_plugin_assets_include_fixed_producer_and_input():
    import tomllib

    from stackos.install import _resolve_source

    root = fixed._runtime_source_root()
    config = tomllib.loads((root / "pyproject.toml").read_text())
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert wheel["plugins"] == "stackos/_assets/plugins"
    assert "plugins" in config["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    source = _resolve_source("plugins")
    for name in (fixed._REPORT_SCRIPT, fixed._REPORT_INPUT):
        relative = Path(name).relative_to("plugins")
        assert source.joinpath(str(relative)).read_bytes() == (root / name).read_bytes()


def track_report_owners(monkeypatch):
    from stackos import controlled_process_host as host

    original = host._ReportFDs
    retained = []

    class Observed(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            retained.append(self)

    monkeypatch.setattr(host, "_ReportFDs", Observed)
    return retained


def report_helper(helper, workspace):
    settings, request, directory = workspace
    if helper == "prepare":
        target = directory.parent / ("b" * 64)
        custody = []
        return lambda: _prepare_report_directory(
            settings.state_dir, target, custody=custody
        ), custody
    output = directory / "report.json"
    output.write_bytes(EXPECTED)
    output.chmod(0o600)
    command = fixed._resolve_controlled_command(request.command_key, report_directory=directory)
    return lambda: fixed._verified_report_result(command), None


@pytest.mark.parametrize("helper", ["prepare", "readback"])
@pytest.mark.parametrize("point", ["traversal", "leaf"])
@pytest.mark.parametrize("outcome", ["still-open", "closed-and-recycled", "interrupted"])
def test_report_helpers_retain_actual_close_fault_without_recycled_retry(
    workspace, tmp_path, monkeypatch, helper, point, outcome
):
    invoke, external = report_helper(helper, workspace)
    retained = track_report_owners(monkeypatch)
    real_open, real_close = os.open, os.close
    target = (
        "/"
        if point == "traversal"
        else "controlled-reports"
        if helper == "prepare"
        else "report.json"
    )
    sentinel = tmp_path / "recycled-fd-sentinel"
    sentinel.write_bytes(b"must remain open")
    failure = (
        InterruptedError("synthetic interruption")
        if outcome == "interrupted"
        else OSError("synthetic close failure")
    )
    target_fd = None
    target_identity = None
    close_attempts = 0
    held_fd = None
    held_identity = None

    def observed_open(path, *args, **kwargs):
        nonlocal target_fd, target_identity
        fd = real_open(path, *args, **kwargs)
        if path == target:
            target_fd = fd
            info = os.fstat(fd)
            target_identity = (info.st_dev, info.st_ino)
        return fd

    def fail_close(fd):
        nonlocal close_attempts, held_fd, held_identity
        if fd == target_fd:
            close_attempts += 1
            if close_attempts == 1:
                if outcome == "closed-and-recycled":
                    real_close(fd)
                    held_fd = real_open(sentinel, os.O_RDONLY)
                    assert held_fd == fd
                else:
                    held_fd = fd
                info = os.fstat(held_fd)
                held_identity = (info.st_dev, info.st_ino)
                raise failure
        return real_close(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", observed_open)
            patch.setattr(os, "close", fail_close)
            with pytest.raises(OSError) as caught:
                invoke()
            assert caught.value is failure
            owners = retained[-1].owners
            assert failure._report_fd_custody is retained[-1]
            unknown = [owner for owner in owners if owner.state == "unknown"]
            assert len(unknown) == 1 and unknown[0].fd == target_fd
            assert unknown[0].failure is failure
            assert all(owner.state in {"closed", "unknown"} for owner in owners)
            if external is not None:
                assert external == owners
            assert not unknown[0].close() and close_attempts == 1
            info = os.fstat(held_fd)
            assert (info.st_dev, info.st_ino) == held_identity
            if outcome == "closed-and-recycled":
                assert held_identity != target_identity
                assert os.read(held_fd, 32) == b"must remain open"
    finally:
        # Explicit test-fixture teardown of the separately observed actual
        # handle; product close-once owners never retry an unknown FD integer.
        if held_fd is not None:
            info = os.fstat(held_fd)
            assert (info.st_dev, info.st_ino) == held_identity
            real_close(held_fd)


@pytest.mark.parametrize("helper", ["prepare", "readback"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_report_helpers_preserve_open_failure_with_secondary_close_fault(
    workspace, monkeypatch, helper, interrupted
):
    invoke, external = report_helper(helper, workspace)
    retained = track_report_owners(monkeypatch)
    real_open, real_close = os.open, os.close
    primary = (
        KeyboardInterrupt("synthetic open interruption")
        if interrupted
        else OSError("synthetic open failure")
    )
    secondary = OSError("synthetic cleanup failure")
    first_fd = None
    close_attempts = 0

    def failed_open(path, *args, **kwargs):
        nonlocal first_fd
        if first_fd is not None:
            raise primary
        first_fd = real_open(path, *args, **kwargs)
        return first_fd

    def failed_close(fd):
        nonlocal close_attempts
        assert fd == first_fd
        close_attempts += 1
        raise secondary

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", failed_open)
            patch.setattr(os, "close", failed_close)
            with pytest.raises(BaseException) as caught:
                invoke()
            assert caught.value is primary
            owner = retained[-1].owners[0]
            assert owner.fd == first_fd and owner.state == "unknown"
            assert owner.failure is secondary
            assert primary._report_fd_custody is retained[-1]
            if external is not None:
                assert external == [owner]
            assert not owner.close() and close_attempts == 1
            assert os.fstat(first_fd)
    finally:
        if first_fd is not None:
            real_close(first_fd)
