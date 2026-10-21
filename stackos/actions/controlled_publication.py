"""Fixed local synthetic Git promotion; generic connector dispatch is forbidden."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.actions.connectors import ActionConnectorError
from stackos.actions.controlled_command import _canonical_digest
from stackos.repositories.base import ConflictError, ValidationError

PUBLICATION_REF = "engineering.publish-candidate.v1"
TARGET = "owned-synthetic-report-v1"
DESTINATION = "refs/heads/accepted-report"
GIT = Path("/usr/bin/git")
ZERO = "0" * 40
_APPLE_TEMP_WARNING = (
    b"git: warning: confstr() failed with code 5: couldn't get path of "
    b"DARWIN_USER_TEMP_DIR; using /tmp instead"
)
# In-process custody only, not a new effect ledger. An unaccounted directly
# owned child must outlive a failed request; only known reaping releases it.
_OWNED_PUBLICATION_CHILDREN: set[subprocess.Popen[bytes]] = set()


class _PublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    canonical_item_id: str = Field(pattern=r"^(task|ticket):[1-9][0-9]*$", max_length=128)
    owner_ref: str = Field(min_length=1, max_length=160)
    expected_epoch: int = Field(ge=1, le=9223372036854775807)
    expected_revision: int = Field(ge=1, le=9223372036854775807)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent_ref: str = Field(
        min_length=1, max_length=240, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"
    )
    target_ref: Literal["owned-synthetic-report-v1"]
    candidate_commit_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_destination_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    review_record_id: int = Field(ge=1, le=9223372036854775807)
    review_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _intent_key(project: int, request: _PublicationInput) -> str:
    return "publication:" + _canonical_digest(
        dict(
            project_id=project,
            action_ref=PUBLICATION_REF,
            canonical_item_id=request.canonical_item_id,
            scope_digest=request.scope_digest,
            intent_ref=request.intent_ref,
        )
    )


def _candidate_ref(project: int, request: _PublicationInput) -> str:
    return "refs/stackos/candidates/" + _intent_key(project, request).split(":")[1]


def _check_manifest(manifest: Any) -> None:
    if (
        manifest.action_ref != PUBLICATION_REF
        or manifest.plugin_slug != "engineering"
        or manifest.action_key != "publish-candidate.v1"
        or manifest.connector_key != "controlled-publication"
        or manifest.operation != "publish-candidate.v1"
        or manifest.risk_level != "write"
        or manifest.provider_key is not None
        or manifest.requires_credential
        or manifest.config_json.get("allows_credential") is not False
        or manifest.config_json.get("direct_run") != "workflow-only"
        or manifest.config_json.get("execution_mode") != "foreground"
        or manifest.config_json.get("durable_delivery")
    ):
        raise ValidationError("publication manifest changed")


def _seal(seed: bytes, value: Any) -> str:
    return hmac.new(
        seed, b"stackos.publication.v1\0" + _canonical_digest(value).encode(), hashlib.sha256
    ).hexdigest()


def _audit_payload(row: Any) -> dict[str, Any]:
    value = row.model_dump(mode="json")
    value["metadata_json"] = dict(value.get("metadata_json") or {})
    value["metadata_json"].pop("publication_seal", None)
    return value


def _admission_payload(project: int, request: Any, admission: Any) -> dict[str, Any]:
    return dict(project_id=project, request=request, admission=admission)


def _verify_admission(row: Any, seed: bytes) -> _PublicationInput:
    metadata = row.metadata_json or {}
    try:
        request = _PublicationInput.model_validate(row.request_json)
        admission = metadata["publication_private"]
        if (
            row.plugin_slug != "engineering"
            or row.action_key != "publish-candidate.v1"
            or row.provider_key is not None
            or row.credential_id is not None
            or row.dry_run
            or row.connector_key != "controlled-publication"
            or row.operation != "publish-candidate.v1"
            or row.idempotency_key != _intent_key(row.project_id, request)
            or admission["project_id"] != row.project_id
            or admission["canonical_item_id"] != request.canonical_item_id
            or admission["owner_ref"] != request.owner_ref
            or admission["epoch"] != request.expected_epoch
            or admission["revision"] != request.expected_revision
            or admission["scope_digest"] != request.scope_digest
            or admission["request_digest"] != _canonical_digest(request.model_dump())
            or (
                admission["controller_run_id"],
                admission["run_plan_id"],
                admission["run_plan_step_id"],
            )
            != (row.run_id, row.run_plan_id, row.run_plan_step_id)
            or not hmac.compare_digest(
                str(metadata.get("publication_admission_seal", "")),
                _seal(seed, _admission_payload(row.project_id, row.request_json, admission)),
            )
        ):
            raise ValueError("invalid admission")
        return request
    except (KeyError, ValueError, TypeError) as exc:
        raise ConflictError("publication original admission unknown") from exc


def _verify_terminal(row: Any, seed: bytes) -> dict[str, Any]:
    from stackos.db.models import ActionCallStatus

    metadata = row.metadata_json or {}
    result = row.response_json
    try:
        request = _verify_admission(row, seed)
        admission = metadata["publication_private"]
        if (
            row.plugin_slug != "engineering"
            or row.action_key != "publish-candidate.v1"
            or row.connector_key != "controlled-publication"
            or row.operation != "publish-candidate.v1"
            or row.provider_key is not None
            or row.credential_id is not None
            or row.dry_run
            or row.completed_at is None
            or row.id is None
            or row.idempotency_key != _intent_key(row.project_id, request)
            or admission["request_digest"] != _canonical_digest(request.model_dump())
            or admission["project_id"] != row.project_id
            or (
                admission["controller_run_id"],
                admission["run_plan_id"],
                admission["run_plan_step_id"],
            )
            != (row.run_id, row.run_plan_id, row.run_plan_step_id)
            or not hmac.compare_digest(
                str(metadata.get("publication_seal", "")), _seal(seed, _audit_payload(row))
            )
            or type(result) is not dict
            or set(result)
            != {
                "schema_version",
                "action_call_id",
                "intent_ref",
                "outcome",
                "outcome_unknown",
                "retry_safe",
                "work_accepted",
                "target_ref",
                "candidate_commit_oid",
                "expected_destination_oid",
                "observed_destination_oid",
                "review_content_digest",
            }
            or result["schema_version"] != "stackos.publication-result.v1"
            or result["action_call_id"] != row.id
            or result["intent_ref"] != request.intent_ref
            or result["target_ref"] != TARGET
            or result["candidate_commit_oid"] != request.candidate_commit_oid
            or result["expected_destination_oid"] != request.expected_destination_oid
            or result["review_content_digest"] != request.review_content_digest
            or result["outcome_unknown"] is not False
            or result["retry_safe"] is not False
            or result["work_accepted"] is not False
            or result["outcome"] not in {"published", "conflict", "rejected_before_dispatch"}
            or (result["outcome"] == "published") != (row.status == ActionCallStatus.SUCCESS)
            or row.status not in {ActionCallStatus.SUCCESS, ActionCallStatus.FAILED}
            or (
                result["outcome"] == "published"
                and result["observed_destination_oid"] != request.candidate_commit_oid
            )
        ):
            raise ValueError("unknown audit")
    except (KeyError, TypeError, ValueError) as exc:
        raise ConflictError(
            "publication original audit is unknown",
            data={"action_call_id": row.id, "outcome_unknown": True, "retry_safe": False},
        ) from exc
    return dict(result)


class _LocalGit:
    """No caller commands, environment, target paths, remotes or hooks."""

    def __init__(self, state_dir: Path, project: int):
        self.root = state_dir / "engineering-publication" / TARGET
        self.project = project
        self.executable_digest = hashlib.sha256(GIT.read_bytes()).hexdigest()
        self._last_error = b""

    def _run(
        self, *args: str, allowed: tuple[int, ...] = (0,), raw: bool = False
    ) -> tuple[int, bytes]:
        if hashlib.sha256(GIT.read_bytes()).hexdigest() != self.executable_digest:
            raise ConflictError("publication Git executable changed")
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/dev/null",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        process = subprocess.Popen(
            [str(GIT), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
            cwd=self.root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _OWNED_PUBLICATION_CHILDREN.add(process)
        try:
            output, error = process.communicate(timeout=5)
        except BaseException:
            # One directly owned child; reap even when observation fails. Never retry Git.
            try:
                if process.poll() is None:
                    process.kill()
            finally:
                try:
                    process.communicate()
                finally:
                    if process.returncode is not None:
                        _OWNED_PUBLICATION_CHILDREN.discard(process)
            raise
        if process.returncode is not None:
            _OWNED_PUBLICATION_CHILDREN.discard(process)
        if process.returncode not in allowed or len(output) > 1_048_576:
            raise ConflictError("publication Git observation failed")
        self._last_error = error
        return process.returncode, output if raw else output.rstrip(b"\n")

    def validate(self, request: _PublicationInput, manifest_sha256: str) -> None:
        # Inspect every path component and repository entry before trusting Git discovery.
        if not self.root.is_absolute() or any(
            p.is_symlink() for p in (self.root, *self.root.parents)
        ):
            raise ConflictError("publication target is not an owned directory")
        if not (self.root / ".git").is_dir() or any(p.is_symlink() for p in self.root.rglob("*")):
            raise ConflictError("publication repository indirection forbidden")
        marker = self.root / ".stackos-owned-synthetic.json"
        try:
            if json.loads(marker.read_bytes()) != {
                "schema_version": "stackos.owned-synthetic.v1",
                "project_id": self.project,
            }:
                raise ValueError("foreign target")
        except (OSError, ValueError) as exc:
            raise ConflictError("publication target ownership missing") from exc
        for relative in (
            "commondir",
            "worktrees",
            "modules",
            "objects/info/alternates",
            "objects/info/http-alternates",
            "info/grafts",
            "shallow",
        ):
            if (self.root / ".git" / relative).exists():
                raise ConflictError("publication alternate repository forbidden")
        _, config = self._run("config", "--local", "--list")
        if any(
            line.split(b"=", 1)[0]
            not in {
                b"core.repositoryformatversion",
                b"core.filemode",
                b"core.bare",
                b"core.logallrefupdates",
                b"core.ignorecase",
                b"core.precomposeunicode",
            }
            for line in config.splitlines()
        ):
            raise ConflictError("publication repository configuration forbidden")
        if self._run("rev-parse", "--show-object-format")[1] != b"sha1" or self._run(
            "rev-parse", "--absolute-git-dir"
        )[1].decode() != str(self.root / ".git"):
            raise ConflictError("publication Git repository mismatch")
        if self._run("cat-file", "-t", request.candidate_commit_oid)[1] != b"commit":
            raise ConflictError("publication candidate is not a commit")
        if (
            request.expected_destination_oid != ZERO
            and self._run("cat-file", "-t", request.expected_destination_oid)[1] != b"commit"
        ):
            raise ConflictError("publication expected destination is not a commit")
        if (
            self._run("symbolic-ref", "-q", _candidate_ref(self.project, request), allowed=(0, 1))[
                0
            ]
            == 0
        ):
            raise ConflictError("publication symbolic candidate forbidden")
        if (
            self._run("rev-parse", "--verify", _candidate_ref(self.project, request))[1].decode()
            != request.candidate_commit_oid
        ):
            raise ConflictError("publication candidate ref changed")
        tree = self._run("ls-tree", request.candidate_commit_oid)[1].splitlines()
        entries = {}
        for line in tree:
            facts, name = line.split(b"\t")
            mode, kind, oid = facts.split()
            if mode != b"100644" or kind != b"blob":
                raise ConflictError("publication tree shape forbidden")
            entries[name] = oid.decode()
        if set(entries) != {b"report.json", b"manifest.json"}:
            raise ConflictError("publication tree shape forbidden")
        for name, digest in (
            (b"report.json", request.candidate_report_sha256),
            (b"manifest.json", manifest_sha256),
        ):
            if int(self._run("cat-file", "-s", entries[name])[1]) > 262_144:
                raise ConflictError("publication blob too large")
            # Do not strip blob newlines: cat-file output is content, not a Git diagnostic.
            blob = self._run("cat-file", "blob", entries[name], raw=True)[1]
            if hashlib.sha256(blob).hexdigest() != digest:
                raise ConflictError("publication blob checksum changed")
        self.destination()

    def destination(self) -> str:
        if self._run("symbolic-ref", "-q", DESTINATION, allowed=(0, 1))[0] == 0:
            raise ConflictError("publication symbolic destination forbidden")
        code, value = self._run("rev-parse", "--verify", DESTINATION, allowed=(0, 128))
        if code == 128:
            return ZERO
        if not re.fullmatch(b"[0-9a-f]{40}", value):
            raise ConflictError("publication destination object unknown")
        if self._run("cat-file", "-t", value.decode())[1] != b"commit":
            raise ConflictError("publication destination is not a commit")
        return value.decode()

    def promote(self, request: _PublicationInput) -> tuple[str, str]:
        self.destination()
        code, _ = self._run(
            "update-ref",
            "--no-deref",
            DESTINATION,
            request.candidate_commit_oid,
            request.expected_destination_oid,
            allowed=(0, 128, 1),
        )
        error = self._last_error
        observed = self.destination()
        if code == 0 and observed == request.candidate_commit_oid:
            return "published", observed
        prefix = (
            f"fatal: update_ref failed for ref '{DESTINATION}': cannot lock ref '{DESTINATION}': "
        ).encode()
        expected_error = (
            b"reference already exists"
            if request.expected_destination_oid == ZERO
            else f"is at {observed} but expected {request.expected_destination_oid}".encode()
        )
        if (
            code != 0
            and observed != request.expected_destination_oid
            and error.splitlines()
            in (
                [prefix + expected_error],
                [_APPLE_TEMP_WARNING, prefix + expected_error],
            )
        ):
            return "conflict", observed
        raise ConflictError("publication outcome unknown")


class ControlledPublicationConnector:
    key = "controlled-publication"

    def validate(self, request: Any) -> list[Any]:
        return []

    def estimate_cost_cents(self, request: Any) -> int:
        return 0

    async def execute(self, request: Any) -> Any:
        raise ActionConnectorError("publication requires current operation admission")
