"""Fixed Engineering command description; no process execution capability."""

from __future__ import annotations

import hashlib
import json
import platform
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
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
    command_key: Literal["noop-v1"]
    command_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("expected_epoch", "expected_revision", mode="before")
    @classmethod
    def _native_int(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("exact native integer required")
        return value


@dataclass(frozen=True)
class _ResolvedControlledCommand:
    command_key: Literal["noop-v1"]
    executable: Path
    executable_sha256: str
    argv: tuple[str, ...]
    cwd: Path
    source_root: Path
    source_files: tuple[tuple[str, str], ...]
    environment: tuple[tuple[str, str], ...]
    digest: str

    def descriptor(self) -> dict[str, Any]:
        return {
            "command_key": self.command_key,
            "executable": str(self.executable),
            "executable_sha256": self.executable_sha256,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "source_root": str(self.source_root),
            "source_files": dict(self.source_files),
            "environment": dict(self.environment),
        }


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


def _resolve_controlled_command() -> _ResolvedControlledCommand:
    """Read fixed runtime bytes only. This is not executable-file custody."""
    try:
        root = _runtime_source_root().resolve(strict=True)
        executable = Path(sys.executable).resolve(strict=True)
        if not executable.is_file():
            raise ValueError("not a file")
        files = tuple(
            (p, hashlib.sha256((root / p).read_bytes()).hexdigest())
            for p in _CONTROLLED_SOURCE_FILES
        )
        base = _ResolvedControlledCommand(
            command_key="noop-v1",
            executable=executable,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            argv=(str(executable), "-I", "-c", "pass"),
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
