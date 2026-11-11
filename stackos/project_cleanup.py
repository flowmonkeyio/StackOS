"""Internal confirmed project cleanup. No route, worker, or provider side effects.

Source rows survive file cleanup so the same immutable ID can be retried after
restart. Only the final database transaction removes those sources and identity.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import delete, update
from sqlmodel import Session, col, select

from stackos.action_output_contract import ACTION_OUTPUT_SCHEMA_REF
from stackos.actions.repository.background import BACKGROUND_ACTION_TASKS
from stackos.browser.runtime import BROWSER_PROFILE_DIRNAME, BrowserRuntime, get_browser_runtime
from stackos.config import Settings
from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    AgentRequest,
    AgentRequestStatus,
    AgentSession,
    Artifact,
    CredentialRefreshEvent,
    CredentialUsageEvent,
    DurableActionArtifact,
    DurableActionItem,
    DurableActionJob,
    DurableActionJobStatus,
    OAuthState,
    Project,
    ProjectWorkflowTemplate,
    Run,
    RunStatus,
    ScheduledJob,
    TrackerTask,
    WorkflowTemplate,
)
from stackos.host_mcp.agent_execution import managed_launch_lock
from stackos.host_mcp.service import usage_executions
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ScheduledJobRepository
from stackos.repositories.resources import ArtifactRepository


class ProjectFileOwnershipRequired(ValueError):
    def __init__(self, artifact_ids: list[int]) -> None:
        super().__init__(
            "trusted action receipt or explicit local-admin file reconciliation required"
        )
        self.artifact_ids = artifact_ids


class ProjectCleanupResult(BaseModel):
    project_id: int
    status: Literal["blocked", "incomplete", "deleted", "missing"]
    operation_id: str | None = None
    blockers: list[str] = []
    retryable: bool = False
    error_code: str | None = None
    ownership_required_artifact_ids: list[int] = []


class ProjectCleanupService:
    def __init__(
        self, session: Session, settings: Settings, *, browser: BrowserRuntime | None = None
    ) -> None:
        self.session = session
        self.settings = settings
        self.browser = browser or get_browser_runtime()

    async def delete(
        self, project_id: int, *, confirmation: dict[str, Any]
    ) -> ProjectCleanupResult:
        # Validate the supplied identity even for a missing ID. Never resolve by name.
        if (
            type(project_id) is not int
            or project_id <= 0
            or confirmation.get("permanent") is not True
            or type(confirmation.get("project_id")) is not int
            or confirmation.get("project_id") != project_id
            or not isinstance(confirmation.get("project_slug"), str)
        ):
            raise ValidationError(
                "permanent deletion requires exact project ID and slug confirmation"
            )
        row = self._project(project_id)
        if row is None:
            return ProjectCleanupResult(project_id=project_id, status="missing")
        if confirmation["project_slug"] != row.slug:
            raise ValidationError("deletion confirmation does not match the immutable project slug")

        # Native discovery checks real process ownership, including unhealthy owners
        # after daemon restart. The final lock check also covers launches mid-await.
        states = await self.browser.discover_sessions(
            project_id=project_id, data_dir=self.settings.data_dir
        )
        with managed_launch_lock, ExitStack() as locks:
            # Serialize with admissions that reserve the existing project row.
            self.session.exec(
                update(Project).where(col(Project.id) == project_id).values(id=Project.id)
            )
            row = self._project(project_id)
            if row is None:
                self.session.rollback()
                return ProjectCleanupResult(project_id=project_id, status="missing")
            blockers = self._blockers(project_id)
            if any(state.owned for state in states.values()) or self.browser.project_busy(
                project_id=project_id, data_dir=self.settings.data_dir
            ):
                blockers.append("browser_session")
            try:
                for path in self._writer_locks(project_id):
                    locks.enter_context(_writer_lock(path))
            except BlockingIOError:
                blockers.append("managed_storage_writer")
            if blockers:
                self.session.rollback()
                return ProjectCleanupResult(
                    project_id=project_id,
                    status="blocked",
                    blockers=sorted(set(blockers)),
                    operation_id=(row.cleanup_json or {}).get("operation_id"),
                )
            operation_id = (row.cleanup_json or {}).get("operation_id") or uuid4().hex
            # Persist recovered custody with the admission marker, before any
            # response files that supply its evidence can be removed.
            self._recover_legacy_files(project_id)
            row.cleanup_json = {"operation_id": operation_id, "status": "pending"}
            row.is_active = False
            jobs = self.session.exec(
                select(ScheduledJob).where(ScheduledJob.project_id == project_id)
            ).all()
            for job in jobs:
                job.enabled = False
                job.next_run_at = None
                self.session.add(job)
            self.session.add(row)
            self.session.commit()
            try:
                for job in jobs:
                    ScheduledJobRepository(self.session)._reconcile(project_id, job.id)
                self._clean_files(project_id)
                self._purge(project_id)
            except Exception as exc:
                self.session.rollback()
                retained = self._project(project_id)
                if retained is None:
                    raise  # A failed final commit must not masquerade as retryable.
                # Do not persist raw paths, provider data, or exception messages.
                retained.cleanup_json = {
                    "operation_id": operation_id,
                    "status": "incomplete",
                    "error_code": type(exc).__name__,
                }
                missing_custody = (
                    exc.artifact_ids if isinstance(exc, ProjectFileOwnershipRequired) else []
                )
                if missing_custody:
                    retained.cleanup_json["ownership_required_artifact_ids"] = missing_custody
                self.session.add(retained)
                self.session.commit()
                return ProjectCleanupResult(
                    project_id=project_id,
                    status="incomplete",
                    operation_id=operation_id,
                    retryable=True,
                    error_code=type(exc).__name__,
                    ownership_required_artifact_ids=missing_custody,
                )
        return ProjectCleanupResult(
            project_id=project_id, status="deleted", operation_id=operation_id
        )

    def _project(self, project_id: int) -> Project | None:
        return self.session.exec(
            select(Project)
            .where(Project.id == project_id)
            .execution_options(populate_existing=True)
        ).first()

    def _blockers(self, project_id: int) -> list[str]:
        s = self.session
        blockers = []
        if usage_executions(project_id):
            blockers.append("native_invocation")
        if BACKGROUND_ACTION_TASKS.foreground_active(project_id):
            blockers.append("foreground_action")
        tasks = s.exec(select(TrackerTask).where(TrackerTask.project_id == project_id)).all()
        if any(
            (t.metadata_json or {}).get("delivery", {}).get("status") in {"queued", "running"}
            for t in tasks
        ):
            blockers.append("admitted_delivery")
        # Stopped delivery controller audits are not active writers by themselves.
        stopped_runs = {
            (t.metadata_json or {}).get("delivery", {}).get("controller_run_id")
            for t in tasks
            if (t.metadata_json or {}).get("delivery", {}).get("status") in {"stopped", "complete"}
        }
        if any(
            r.id not in stopped_runs
            for r in s.exec(
                select(Run).where(Run.project_id == project_id, Run.status == RunStatus.RUNNING)
            ).all()
        ):
            blockers.append("running_work")
        if s.exec(
            select(ActionCall).where(
                ActionCall.project_id == project_id, ActionCall.status == ActionCallStatus.RUNNING
            )
        ).first():
            blockers.append("action_call")
        if s.exec(
            select(DurableActionJob).where(
                DurableActionJob.project_id == project_id,
                col(DurableActionJob.state).in_(
                    [
                        DurableActionJobStatus.SCHEDULED,
                        DurableActionJobStatus.RUNNING,
                        DurableActionJobStatus.PAUSED,
                        DurableActionJobStatus.UNKNOWN_HOLD,
                    ]
                ),
            )
        ).first():
            blockers.append("durable_action")
        claim = s.exec(
            select(AgentRequest).where(
                AgentRequest.project_id == project_id,
                AgentRequest.status == AgentRequestStatus.CLAIMED,
            )
        ).all()
        if any(
            r.claim_expires_at is None
            or r.claim_expires_at > datetime.now(UTC).replace(tzinfo=None)
            for r in claim
        ):
            blockers.append("claimed_request")
        if s.exec(
            select(DurableActionItem)
            .join(DurableActionJob)
            .where(
                DurableActionJob.project_id == project_id,
                col(DurableActionItem.lease_ref).is_not(None),
                col(DurableActionItem.lease_expires_at) > datetime.now(UTC).replace(tzinfo=None),
            )
        ).first():
            blockers.append("durable_lease")
        # A foreign durable pin cannot be silently erased or re-owned.
        if s.exec(
            select(DurableActionArtifact)
            .join(DurableActionJob)
            .join(Artifact, col(Artifact.id) == col(DurableActionArtifact.artifact_id))
            .where(Artifact.project_id == project_id, DurableActionJob.project_id != project_id)
        ).first():
            blockers.append("shared_artifact_pin")
        if s.exec(
            select(ProjectWorkflowTemplate)
            .join(WorkflowTemplate)
            .where(
                WorkflowTemplate.project_id == project_id,
                ProjectWorkflowTemplate.project_id != project_id,
            )
        ).first():
            blockers.append("shared_project_template")
        return blockers

    def _roots(self, project_id: int) -> list[Path]:
        data, assets = self.settings.data_dir, self.settings.generated_assets_dir
        return [
            data / "projects" / str(project_id),
            data / "browser-profiles" / BROWSER_PROFILE_DIRNAME / f"project-{project_id}",
            data / "browser-state" / "gstack" / f"project-{project_id}",
            assets / "telegram" / str(project_id),
            *[
                assets / kind / f"project-{project_id}"
                for kind in ("action-outputs", "imap-transfers", "stripe-invoice-transfers")
            ],
        ]

    def _writer_locks(self, project_id: int) -> list[Path]:
        root = self._roots(project_id)[0]
        _check_ancestors(root, self.settings.data_dir)
        if root.is_symlink():
            return []
        return sorted(root.rglob(".writer.lock")) if root.exists() else []

    def _asset_path(self, uri: str) -> Path | None:
        prefix = "/generated-assets/"
        if not uri.startswith(prefix):
            return None
        relative = Path(uri.removeprefix(prefix))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            return None
        return self.settings.generated_assets_dir / relative

    def _reference_path(self, uri: str) -> Path | None:
        from urllib.parse import unquote, urlsplit

        generated = self._asset_path(uri)
        if generated is not None:
            return generated
        parsed = urlsplit(uri)
        if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
            path = Path(unquote(parsed.path))
        elif not parsed.scheme:
            path = Path(uri)
        else:
            return None
        return path if path.is_absolute() and path.is_relative_to(self.settings.data_dir) else None

    def _managed_path(self, custody: dict[str, Any] | None) -> Path | None:
        if custody is None or custody.get("root") != str(
            self.settings.generated_assets_dir.resolve()
        ):
            return None  # User-selected external output roots are not managed storage.
        relative = Path(custody["path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("invalid producer file custody")
        return self.settings.generated_assets_dir / relative

    @staticmethod
    def _custodies(artifact: Artifact) -> list[dict[str, Any]]:
        custody = artifact.managed_file_json
        if custody is None:
            return []
        return custody.get("files", [custody])

    def _audit_output(self, call: ActionCall) -> dict[str, Any] | None:
        output = call.response_json or {}
        if output.get("output_mode") != "file":
            return output
        pointer = output.get("file") or {}
        path = Path(pointer.get("path", ""))
        root = self.settings.generated_assets_dir
        owner = root / "action-outputs" / f"project-{call.project_id}"
        if (
            not path.is_relative_to(owner)
            or ".." in path.parts
            or type(pointer.get("bytes")) is not int
        ):
            return None
        if not 0 < pointer["bytes"] <= 16 * 1024 * 1024:
            return None
        try:
            with ExitStack() as opened:
                parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                opened.callback(os.close, parent)
                for part in path.relative_to(root).parts[:-1]:
                    parent = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                    )
                    opened.callback(os.close, parent)
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        return None
                    payload = stream.read(pointer["bytes"] + 1)
            if len(payload) != pointer["bytes"] or hashlib.sha256(
                payload
            ).hexdigest() != pointer.get("sha256"):
                return None
            envelope = json.loads(payload)
            if (
                envelope.get("schema_version") != ACTION_OUTPUT_SCHEMA_REF
                or envelope.get("project", {}).get("project_id") != call.project_id
                or envelope.get("action_call", {}).get("id") != call.id
                or envelope.get("action", {}).get("connector_key") != call.connector_key
            ):
                return None
            return envelope["response"]["output_json"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _recover_legacy_files(self, project_id: int) -> None:
        """Recover from daemon-owned receipts, never editable artifact provenance."""
        artifacts = {
            a.id: a
            for a in self.session.exec(
                select(Artifact).where(Artifact.project_id == project_id)
            ).all()
            if a.managed_file_json is None
        }
        if not artifacts:
            return
        media = {
            "openai-images",
            "google-gemini-image",
            "ideogram",
            "byteplus-seedream",
            "reve",
            "xai-imagine",
            "aignc",
            "alibaba-wan",
            "google-veo",
            "byteplus-seedance",
            "kling-video",
        }
        for call in self.session.exec(
            select(ActionCall).where(
                ActionCall.project_id == project_id,
                ActionCall.status == ActionCallStatus.SUCCESS,
            )
        ).all():
            if call.dry_run or call.completed_at is None:
                continue
            if not (
                (
                    call.connector_key in media
                    and call.operation
                    in {"image.generate", "image.edit", "image.remix", "video.generate"}
                )
                or (call.connector_key, call.operation)
                in {("telegram", "file.download"), ("hubspot", "bulk.exports.result")}
            ):
                continue
            output = self._audit_output(call)
            if not isinstance(output, dict):
                continue
            items = (
                [output, *(output.get("data") or [])]
                if isinstance(output.get("data", []), list)
                else [output]
            )
            for item in items:
                if not isinstance(item, dict):
                    continue
                artifact_id = item.get("artifact_id")
                artifact = artifacts.get(artifact_id) if type(artifact_id) is int else None
                uri = item.get("artifact_ref") or item.get("url")
                if (
                    artifact is None
                    or artifact.managed_file_json is not None
                    or artifact.created_at > call.completed_at
                    or not isinstance(uri, str)
                ):
                    continue
                path = self._asset_path(uri)
                if path is None or not path.exists():
                    continue
                try:
                    custody = ArtifactRepository._generated_file_custody(
                        asset_root=self.settings.generated_assets_dir,
                        path=path,
                        project_id=project_id,
                        uri=uri,
                    )
                except (ValueError, OSError, ValidationError):
                    continue
                custody["evidence"] = {"basis": "action-audit", "action_call_id": call.id}
                artifact.managed_file_json = custody
                self.session.add(artifact)

    def _handoff_shared_files(self, project_id: int) -> None:
        """Keep verified custody on surviving references in the final purge transaction."""
        artifacts = self.session.exec(select(Artifact)).all()
        surviving = [a for a in artifacts if a.project_id != project_id]
        references: dict[int, set[Path]] = {}
        for artifact in surviving:
            assert artifact.id is not None
            refs = {
                path
                for custody in self._custodies(artifact)
                if (path := self._managed_path(custody)) is not None
            }
            public = self._reference_path(artifact.uri)
            if public is not None:
                refs.add(public)
            references[artifact.id] = refs
        for pin in self.session.exec(
            select(DurableActionArtifact)
            .join(DurableActionJob)
            .where(DurableActionJob.project_id != project_id)
        ).all():
            path = self._reference_path(pin.artifact_uri)
            if path is not None and pin.artifact_id in references:
                references[pin.artifact_id].add(path)
        for refs in references.values():
            refs.update(path.resolve() for path in list(refs))
        for source in artifacts:
            if source.project_id != project_id:
                continue
            for custody in self._custodies(source):
                path = self._managed_path(custody)
                if path is None:
                    continue
                for survivor in surviving:
                    assert survivor.id is not None
                    if not any(path.is_relative_to(ref) for ref in references[survivor.id]):
                        continue
                    files = self._custodies(survivor)
                    if custody not in files:
                        # Public directory references may retain several verified
                        # files. No deleted-project row or untrusted provenance is kept.
                        survivor.managed_file_json = {"files": [*files, dict(custody)]}
                        self.session.add(survivor)
        self.session.flush()

    def _clean_files(self, project_id: int) -> None:
        from stackos.plugins.builtin_finance import FinanceConsole

        artifacts = self.session.exec(select(Artifact)).all()
        protected = {
            path
            for a in artifacts
            if a.project_id != project_id
            if (path := self._reference_path(a.uri)) is not None
        }
        protected.update(
            path
            for a in artifacts
            if a.project_id != project_id
            for custody in self._custodies(a)
            if (path := self._managed_path(custody)) is not None
        )
        for pin in self.session.exec(
            select(DurableActionArtifact)
            .join(DurableActionJob)
            .where(DurableActionJob.project_id != project_id)
        ).all():
            path = self._reference_path(pin.artifact_uri)
            if path is not None:
                protected.add(path)
        protected.update(path.resolve() for path in list(protected))

        def remove_managed(path: Path) -> None:
            _check_ancestors(path, self.settings.data_dir)
            _remove_owned(path, protected, self.settings.data_dir)

        paths = self._roots(project_id)
        files = []
        missing_custody = []
        for artifact in artifacts:
            if artifact.project_id != project_id:
                continue
            custodies = self._custodies(artifact)
            for custody in custodies:
                path = self._managed_path(custody)
                if path is not None:
                    files.append((path, (custody["device"], custody["inode"])))
            if not custodies and (artifact.provenance_json or {}).get("source") in {
                "aignc-action",
                "alibaba-wan-action",
                "google-veo-action",
                "byteplus-seedance-action",
                "kling-video-action",
                "xai-imagine-action",
                "openai-images-action",
                "google-gemini-image-action",
                "ideogram-action",
                "byteplus-seedream-action",
                "reve-action",
                "telegram.file.download",
                "hubspot-export-action",
            }:
                # Old provenance is editable, so it cannot authorize removal.
                # Keep ambiguous legacy source rows for recovery rather than
                # deleting bytes or claiming a completed cleanup with files left.
                path = self._asset_path(artifact.uri)
                if (
                    path is not None
                    and (path.exists() or path.is_symlink())
                    and not any(path.is_relative_to(root) for root in paths)
                ):
                    assert artifact.id is not None
                    missing_custody.append(artifact.id)
        if missing_custody:
            raise ProjectFileOwnershipRequired(missing_custody)
        # Finance owns its publication/source metadata outside data/projects.
        # Retire it before the source identity is removed from the project tree.
        FinanceConsole(self.session, self.settings).cleanup_sources(
            self._project(project_id), remove_managed
        )
        for path, identity in files:
            _check_ancestors(path, self.settings.data_dir)
            _remove_owned(path, protected, self.settings.data_dir, identity=identity)
        for path in paths:
            _check_ancestors(path, self.settings.data_dir)
            _remove_owned(path, protected, self.settings.data_dir)

    def _purge(self, project_id: int) -> None:
        # Pins RESTRICT artifact deletion. Retire owned jobs/pins before the
        # project cascade; SET NULL and non-FK project history needs explicit purge.
        s = self.session
        self._handoff_shared_files(project_id)
        s.exec(delete(DurableActionJob).where(col(DurableActionJob.project_id) == project_id))
        for model, column in (
            (AgentSession, AgentSession.project_id),
            (OAuthState, OAuthState.attach_project_id),
            (CredentialUsageEvent, CredentialUsageEvent.project_id),
            (CredentialRefreshEvent, CredentialRefreshEvent.project_id),
        ):
            s.exec(delete(model).where(col(column) == project_id))
        s.exec(delete(Project).where(col(Project.id) == project_id))
        s.commit()


def _check_ancestors(path: Path, root: Path) -> None:
    """Never follow an alias into a repository, export, or another owner's tree."""
    if not path.is_relative_to(root):
        raise ValueError("cleanup path outside managed storage")
    for ancestor in (root, *path.relative_to(root).parents):
        candidate = ancestor if ancestor == root else root / ancestor
        if candidate.is_symlink():
            raise ValueError("cleanup ancestor is a symlink")


def _remove_owned(
    path: Path, protected: set[Path], root: Path, *, identity: tuple[int, int] | None = None
) -> None:
    """Walk by held descriptors, never following a replaced directory or parent."""
    relative = path.relative_to(root)
    with ExitStack() as opened:
        try:
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened.callback(os.close, parent)
            for part in relative.parts[:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                opened.callback(os.close, parent)
        except FileNotFoundError:
            return
        _remove_at(parent, relative.name, path, protected, identity=identity)


def _remove_at(
    parent: int,
    name: str,
    path: Path,
    protected: set[Path],
    *,
    identity: tuple[int, int] | None = None,
) -> None:
    if any(path.is_relative_to(shared) for shared in protected):
        return
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if identity is not None and (
        not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity
    ):
        raise ValueError("producer-owned file was replaced")
    if stat.S_ISDIR(info.st_mode):
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for child in os.listdir(directory):
                _remove_at(directory, child, path / child, protected)
            if not os.listdir(directory):
                os.rmdir(name, dir_fd=parent)
        finally:
            os.close(directory)
    else:
        os.unlink(name, dir_fd=parent)  # Unlink a leaf alias, never its target.


@contextmanager
def _writer_lock(path: Path):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe managed writer lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)
