"""Fixed Engineering command description; no process execution capability."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)
from sqlmodel import Session

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.manifest import ExecutableActionManifest
from stackos.config import Settings
from stackos.repositories.base import ConflictError

_CONTROLLED_ACTION_REF: Final[str] = "engineering.controlled-proof.v1"
_REPORT_SCRIPT = "plugins/engineering/scripts/controlled_status_report_v1.py"
_REPORT_INPUT = "plugins/engineering/fixtures/controlled-status-input-v1.json"
_REPORT_INPUT_SHA = "37b752df834a883cf0ca9f83f1ab6df96ead1fcfd91d420b65c85f2201121d7f"
_REPORT_SHA = "ed21d51f672f7c0110a6c256dadf5f7067d282d4fb0ae3a7c4fa37d8fddd760c"
_CommandKey = Literal["noop-v1", "synthetic-status-report-v1"]
_CONTROLLED_SOURCE_FILES: Final[tuple[str, ...]] = (
    "stackos/action_availability.py",
    "stackos/actions/__init__.py",
    "stackos/actions/controlled_command.py",
    "stackos/actions/repository/audit.py",
    "stackos/actions/repository/execution.py",
    "stackos/operations/actions/controlled.py",
    "stackos/operations/actions/execution.py",
    "stackos/operations/actions/specs.py",
    "stackos/operations/dispatcher.py",
    "stackos/config.py",
    "stackos/controlled_process_host.py",
    "plugins/engineering/plugin.yaml",
)


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


class _ControlledCommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    canonical_item_id: str = Field(pattern=r"^(task|ticket):[1-9][0-9]*$", max_length=128)
    owner_ref: str = Field(min_length=1, max_length=160)
    expected_epoch: int = Field(ge=1, le=9223372036854775807)
    expected_revision: int = Field(ge=1, le=9223372036854775807)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent_ref: str = Field(
        min_length=1, max_length=240, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"
    )
    command_key: _CommandKey
    command_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("expected_epoch", "expected_revision", mode="before")
    @classmethod
    def _native_int(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("exact native integer required")
        return value


@dataclass(frozen=True)
class _ResolvedControlledCommand:
    command_key: _CommandKey
    executable: Path
    executable_sha256: str
    argv: tuple[str, ...]
    cwd: Path
    source_root: Path
    source_files: tuple[tuple[str, str], ...]
    environment: tuple[tuple[str, str], ...]
    digest: str
    report_directory: Path | None = None

    def descriptor(self) -> dict[str, Any]:
        value = {
            "command_key": self.command_key,
            "executable": str(self.executable),
            "executable_sha256": self.executable_sha256,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "source_root": str(self.source_root),
            "source_files": dict(self.source_files),
            "environment": dict(self.environment),
        }
        if self.report_directory is not None:
            value["report"] = {
                "directory": str(self.report_directory),
                "input_sha256": _REPORT_INPUT_SHA,
                "report_sha256": _REPORT_SHA,
                "byte_count": 227,
            }
        return value


def _controlled_report_directory(
    settings: Settings, *, project_id: int, request: _ControlledCommandInput
) -> Path:
    """Stable intent storage; no run, epoch, user path or filesystem mutation."""
    state = settings.state_dir
    if not state.is_absolute() or state != Path(os.path.abspath(state)):
        raise ConflictError("controlled report storage is not canonical")
    identity = _canonical_digest(
        dict(
            project_id=project_id,
            canonical_item_id=request.canonical_item_id,
            scope_digest=request.scope_digest,
            intent_ref=request.intent_ref,
        )
    )
    return state / "controlled-reports" / identity


class _ControlledReportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    candidate_ref: str = Field(pattern=r"^controlled-report:[0-9a-f]{64}$")
    report_sha256: Literal["ed21d51f672f7c0110a6c256dadf5f7067d282d4fb0ae3a7c4fa37d8fddd760c"]
    input_sha256: Literal["37b752df834a883cf0ca9f83f1ab6df96ead1fcfd91d420b65c85f2201121d7f"]
    byte_count: int = Field(ge=227, le=227)
    accepted: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def exact_types(cls, value: Any) -> Any:
        if (
            type(value) is not dict
            or type(value.get("byte_count")) is not int
            or value.get("accepted") is not False
        ):
            raise ValueError("exact report result required")
        return value


def _verified_report_result(command: _ResolvedControlledCommand) -> dict[str, Any]:
    """Bounded readback, independently hashed; never creates or promotes output."""
    directory = command.report_directory
    if command.command_key != "synthetic-status-report-v1" or directory is None:
        raise ConflictError("controlled report descriptor is invalid")
    from stackos.controlled_process_host import _ReportFDs

    with _ReportFDs() as owned:
        parent = owned.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for part in directory.parts[1:]:
            child = owned.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, directory=parent)
            owned.close(parent)
            parent = child
        info = os.fstat(parent.fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ConflictError("controlled report storage is unsafe")
        output = owned.open(
            "report.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, directory=parent
        )
        info = os.fstat(output.fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ConflictError("controlled report output is unsafe")
        raw = os.read(output.fd, 228)
        if len(raw) != 227 or hashlib.sha256(raw).hexdigest() != _REPORT_SHA:
            raise ConflictError("controlled report output differs")
    return _ControlledReportResult.model_validate(
        dict(
            candidate_ref="controlled-report:" + directory.name,
            report_sha256=_REPORT_SHA,
            input_sha256=_REPORT_INPUT_SHA,
            byte_count=227,
            accepted=False,
        )
    ).model_dump()


def _runtime_source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _check_controlled_manifest(manifest: ExecutableActionManifest) -> None:
    if (
        manifest.action_ref != _CONTROLLED_ACTION_REF
        or manifest.plugin_slug != "engineering"
        or manifest.action_key != "controlled-proof.v1"
        or manifest.connector_key != "controlled-command"
        or manifest.operation != "controlled-proof.v1"
        or manifest.provider_key is not None
        or manifest.requires_credential
        or manifest.allows_credential
        or manifest.risk_level != "write"
        or manifest.execution_mode != "foreground"
        or manifest.config_json.get("direct_run") != "workflow-only"
        or manifest.config_json.get("controlled_host") not in {None, "unavailable"}
        or manifest.config_json.get("public_action_key") is not None
        or manifest.config_json.get("durable_delivery")
    ):
        raise ConflictError("controlled manifest is incompatible")


def _controlled_host_settings(session: Session) -> Settings | None:
    """Configuration observation only; this creates no execution authority."""
    services = session.info.get("operation_services", {})
    settings = services.get("settings") if type(services) is dict else None
    return settings if type(settings) is Settings else None


def _controlled_host_supported() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64" and struct.calcsize("P") == 8


def _current_original_runtime(
    session: Session, *, binding: Any, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Reconstruct a finite original selection from current trusted sources.

    Stored argv, paths, hashes and environment never select execution inputs.
    The caller must compare this complete reconstruction to the original manifest.
    """
    from stackos import controlled_process_receipt as receipt
    from stackos.controlled_process_host import _native_manifest

    if type(binding) is not receipt._Binding or not receipt._valid_binding(binding):
        raise ConflictError("original command binding is invalid")
    command = manifest.get("command")
    selected = command.get("command_key") if type(command) is dict else None
    if type(selected) is not str or selected not in {"noop-v1", "synthetic-status-report-v1"}:
        raise ConflictError("original command selection is unknown")
    key: _CommandKey = "noop-v1" if selected == "noop-v1" else "synthetic-status-report-v1"
    directory = None
    if key == "synthetic-status-report-v1":
        settings = _controlled_host_settings(session)
        if settings is None:
            raise ConflictError("original report settings are unavailable")
        request = _ControlledCommandInput.model_validate(
            dict(
                canonical_item_id=binding.canonical_item_id,
                owner_ref=binding.owner_ref,
                expected_epoch=binding.epoch,
                expected_revision=binding.reservation_revision,
                scope_digest=binding.scope_digest,
                intent_ref=binding.intent_ref,
                command_key=key,
                command_digest="0" * 64,
            )
        )
        directory = _controlled_report_directory(
            settings, project_id=binding.project_id, request=request
        )
    return _native_manifest(_resolve_controlled_command(key, report_directory=directory))


class _ControlledNativeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["stackos.controlled-command-result.v2"]
    status: Literal["succeeded", "held", "failed"]
    code: Literal[
        "controlled_command_completed",
        "controlled_execution_held",
        "controlled_outcome_unknown",
        "controlled_authority_changed",
        "controlled_before_dispatch_failed",
    ]
    admission_verified: bool
    command_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent_ref: str = Field(
        min_length=1, max_length=240, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"
    )
    host_dispatched: bool | None
    released: bool
    provider_executed: Literal[False]
    outcome_unknown: bool
    cleanup_known: bool
    retry_safe: Literal[False]
    work_accepted: Literal[False]
    report: _ControlledReportResult | None = None

    @model_serializer(mode="wrap")
    def omit_absent_report(self, handler: Any) -> dict[str, Any]:
        value = handler(self)
        if self.report is None:
            value.pop("report", None)
        return value

    @model_validator(mode="before")
    @classmethod
    def native_types(cls, value: Any) -> Any:
        if type(value) is not dict:
            raise ValueError("exact native result required")
        for key in (
            "admission_verified",
            "released",
            "provider_executed",
            "outcome_unknown",
            "cleanup_known",
            "retry_safe",
            "work_accepted",
        ):
            if type(value.get(key)) is not bool:
                raise ValueError("exact native result flags required")
        if (
            value.get("host_dispatched") is not None
            and type(value.get("host_dispatched")) is not bool
        ):
            raise ValueError("exact spawn observation required")
        return value

    @model_validator(mode="after")
    def coherent(self) -> _ControlledNativeResult:
        if self.report is not None and self.status != "succeeded":
            raise ValueError("report requires completed execution")
        if self.host_dispatched is None and not self.outcome_unknown:
            raise ValueError("unknown spawn requires unknown outcome")
        if self.status == "succeeded" and not (
            self.code == "controlled_command_completed"
            and self.admission_verified
            and self.host_dispatched is True
            and self.released
            and not self.outcome_unknown
            and self.cleanup_known
        ):
            raise ValueError("completed result requires known original execution and cleanup")
        if (self.code == "controlled_command_completed") != (self.status == "succeeded"):
            raise ValueError("completed result status differs")
        return self


class _ControlledNativeAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["stackos.controlled-native-audit.v1"]
    action_call_id: int = Field(ge=1, le=9223372036854775807)
    binding: dict[str, Any]
    command_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_revision_at_finalize: int = Field(ge=1, le=9223372036854775807)
    owner_basis_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_receipt_digest: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_slot_revision: int = Field(ge=0, le=9223372036854775807)
    control_revision: int = Field(ge=0, le=9223372036854775807)
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "action_call_id",
        "owner_revision_at_finalize",
        "terminal_slot_revision",
        "control_revision",
        mode="before",
    )
    @classmethod
    def native_integer(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("exact native integer required")
        return value

    @field_validator("binding")
    @classmethod
    def original_binding(cls, value: dict[str, Any]) -> dict[str, Any]:
        from stackos import controlled_process_receipt as receipt

        try:
            binding = receipt._Binding(**value)
        except TypeError as exc:
            raise ValueError("invalid original binding") from exc
        if not receipt._valid_binding(binding):
            raise ValueError("invalid original binding")
        return value


def _resolve_controlled_command(
    command_key: _CommandKey = "noop-v1", *, report_directory: Path | None = None
) -> _ResolvedControlledCommand:
    """Read fixed runtime bytes only. This is not executable-file custody."""
    try:
        if command_key not in {"noop-v1", "synthetic-status-report-v1"}:
            raise ValueError("unknown command")
        report = command_key == "synthetic-status-report-v1"
        if report:
            if (
                not isinstance(report_directory, Path)
                or not report_directory.is_absolute()
                or report_directory != Path(os.path.abspath(report_directory))
                or report_directory.parent.name != "controlled-reports"
                or len(report_directory.name) != 64
                or any(c not in "0123456789abcdef" for c in report_directory.name)
            ):
                raise ValueError("invalid report directory")
        elif report_directory is not None:
            raise ValueError("noop has no report directory")
        root = _runtime_source_root().resolve(strict=True)
        if report:
            for name in (_REPORT_SCRIPT, _REPORT_INPUT):
                path = root
                for part in Path(name).parts:
                    path /= part
                    if stat.S_ISLNK(path.lstat().st_mode):
                        raise ValueError("report source symlink")
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("report source is not regular")
        executable = Path(sys.executable).resolve(strict=True)
        if not executable.is_file():
            raise ValueError("not a file")
        files = tuple(
            (p, hashlib.sha256((root / p).read_bytes()).hexdigest())
            for p in _CONTROLLED_SOURCE_FILES + ((_REPORT_SCRIPT, _REPORT_INPUT) if report else ())
        )
        if report and dict(files)[_REPORT_INPUT] != _REPORT_INPUT_SHA:
            raise ValueError("fixed input changed")
        base = _ResolvedControlledCommand(
            command_key=command_key,
            executable=executable,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            argv=(str(executable), "-I", "-S", str(root / _REPORT_SCRIPT), str(report_directory))
            if report
            else (str(executable), "-I", "-c", "pass"),
            cwd=root,
            source_root=root,
            source_files=files,
            environment=(
                ("LANG", "C.UTF-8"),
                ("PATH", "/usr/bin:/bin"),
                ("PYTHONNOUSERSITE", "1"),
                ("PYTHONDONTWRITEBYTECODE", "1"),
            ),
            digest="",
            report_directory=report_directory,
        )
    except (OSError, ValueError) as exc:
        raise ConflictError("controlled command source is unavailable") from exc
    from dataclasses import replace

    return replace(base, digest=_canonical_digest(base.descriptor()))


class ControlledCommandConnector:
    """Registry fallback always rejects, including forged in-process requests."""

    key = "controlled-command"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        try:
            _ControlledCommandInput.model_validate(request.input_json)
        except ValidationError:
            return [
                ActionValidationIssue(
                    path="input_json",
                    message="invalid controlled command binding",
                    code="controlled_binding_invalid",
                )
            ]
        return []

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        raise ActionConnectorError(
            "controlled_host_unavailable",
            output_json={"code": "controlled_host_unavailable", "host_dispatched": False},
        )
