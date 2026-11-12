"""Internal confirmed project cleanup. No route, worker, or provider side effects.

Source rows survive file cleanup so the same immutable ID can be retried after
restart. Only the final database transaction removes those sources and identity.
"""

from __future__ import annotations

import errno
import fcntl
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

from stackos.actions import ActionRepository
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
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import ScheduledJobRepository


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
        with ExitStack() as locks:
            # Reserve/recheck before opening the project lock. Committed purge
            # removes the project identity before retiring the pathname, so a
            # stale retry cannot recreate the lock after that commit.
            self.session.exec(
                update(Project).where(col(Project.id) == project_id).values(id=Project.id)
            )
            row = self._project(project_id)
            if row is None:
                self.session.rollback()
                return ProjectCleanupResult(project_id=project_id, status="missing")
            if row.slug != confirmation["project_slug"]:
                self.session.rollback()
                raise ValidationError(
                    "deletion confirmation does not match the immutable project slug"
                )
            cleanup_lock = self._cleanup_lock_path(project_id)
            _check_ancestors(cleanup_lock, self.settings.state_dir)
            cleanup_lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            blockers = []
            try:
                locks.enter_context(_writer_lock(cleanup_lock, create=True))
                # No DB writer transaction or global launch lock spans recursive
                # discovery. This lock also serializes cleanup after managed
                # writer lock files have themselves been removed.
                self.session.rollback()
                for path in self._writer_locks(project_id):
                    locks.enter_context(_writer_lock(path))
            except BlockingIOError:
                self.session.rollback()
                blockers.append("managed_storage_writer")
            with managed_launch_lock:
                # Recheck quiescence and identity after discovery, then commit
                # the canonical fence before allowing unrelated native launches.
                self.session.exec(
                    update(Project).where(col(Project.id) == project_id).values(id=Project.id)
                )
                row = self._project(project_id)
                if row is None:
                    self.session.rollback()
                    return ProjectCleanupResult(project_id=project_id, status="missing")
                blockers.extend(self._blockers(project_id))
                if any(state.owned for state in states.values()) or self.browser.project_busy(
                    project_id=project_id, data_dir=self.settings.data_dir
                ):
                    blockers.append("browser_session")
                if blockers:
                    self.session.rollback()
                    return ProjectCleanupResult(
                        project_id=project_id,
                        status="blocked",
                        blockers=sorted(set(blockers)),
                        operation_id=(row.cleanup_json or {}).get("operation_id"),
                    )
                operation_id = (row.cleanup_json or {}).get("operation_id") or uuid4().hex
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
                # Recovery can read/hash response files. Persist custody before
                # deleting those sources, outside the global launch boundary.
                ActionRepository(self.session).recover_generated_file_custody(
                    project_id=project_id, asset_root=self.settings.generated_assets_dir
                )
                self.session.commit()
                for job in jobs:
                    ScheduledJobRepository(self.session)._reconcile(project_id, job.id)
                self._clean_files(project_id)
                self._purge(project_id, operation_id=operation_id)
                # Retire the pathname only after purge commits. A failed purge
                # must keep the same inode locked through rollback and error
                # persistence. New deletes reserve/recheck the row before they
                # can open this file, so committed deletion closes that race.
                _remove_owned(self._cleanup_lock_path(project_id), set(), self.settings.state_dir)
            except Exception as exc:
                self.session.rollback()
                retained = self._project(project_id)
                if retained is None:
                    raise  # A committed purge cannot masquerade as retryable.
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
        deliveries = [(t.metadata_json or {}).get("delivery") or {} for t in tasks]
        if any(delivery.get("status") in {"queued", "running"} for delivery in deliveries):
            blockers.append("admitted_delivery")
        # Stopped delivery controller audits are not active writers by themselves.
        stopped_runs = {
            delivery.get("controller_run_id")
            for delivery in deliveries
            if delivery.get("status") in {"stopped", "complete"}
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

    def _cleanup_lock_path(self, project_id: int) -> Path:
        # Reuse the existing flock writer boundary outside the roots it removes.
        # IDs are permanent; callers reserve/recheck the row before opening it.
        return self.settings.state_dir / "project-cleanup" / f"{project_id}.writer.lock"

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
        if custody is None:
            return None
        relative = Path(custody["path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("invalid producer file custody")
        if custody.get("root") == str(self.settings.generated_assets_dir.resolve()):
            return self.settings.generated_assets_dir / relative
        owner = custody.get("owner_project_id")
        if (
            custody.get("root") == str(self.settings.data_dir.resolve())
            and type(owner) is int
            and owner > 0
        ):
            path = self.settings.data_dir / relative
            if any(path.is_relative_to(root) for root in self._roots(owner)):
                return path
            raise ValueError("file custody outside original project roots")
        return None  # User-selected external outputs are not managed storage.

    @staticmethod
    def _custodies(artifact: Artifact) -> list[dict[str, Any]]:
        custody = artifact.managed_file_json
        if custody is None:
            return []
        return custody.get("files", [custody])

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
        references_by_path: dict[Path, set[int]] = {}
        for artifact_id, refs in references.items():
            for path in refs:
                references_by_path.setdefault(path, set()).add(artifact_id)
        custodies = [
            custody
            for source in artifacts
            if source.project_id == project_id
            for custody in self._custodies(source)
        ]
        # _clean_files has already removed unshared leaves. Remaining files in
        # these derived roots are owned without needing a source Artifact (for
        # example ActionCall response files). Never inventory a requested URI.
        for root in self._roots(project_id):
            for path, info in _owned_leaves(root, self.settings.data_dir):
                custodies.append(
                    {
                        "root": str(self.settings.data_dir.resolve()),
                        "path": path.relative_to(self.settings.data_dir).as_posix(),
                        "owner_project_id": project_id,
                        "device": info.st_dev,
                        "inode": info.st_ino,
                        "mode": stat.S_IFMT(info.st_mode),
                    }
                )
        additions: dict[int, list[dict[str, Any]]] = {}
        for custody in custodies:
            path = self._managed_path(custody)
            if path is None:
                continue
            owners: set[int] = set()
            for ancestor in (path, *path.parents):
                owners.update(references_by_path.get(ancestor, ()))
            for artifact_id in owners:
                additions.setdefault(artifact_id, []).append(custody)
        for survivor in surviving:
            assert survivor.id is not None
            if survivor.id not in additions:
                continue
            files = list(self._custodies(survivor))
            seen = {json.dumps(custody, sort_keys=True) for custody in files}
            for custody in additions[survivor.id]:
                key = json.dumps(custody, sort_keys=True)
                if key not in seen:
                    files.append(dict(custody))
                    seen.add(key)
            # Public directory references may retain several verified files.
            # Batch the assignment so a directory handoff is linear in its files.
            survivor.managed_file_json = {"files": files}
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
                    prune_to = None
                    owner = custody.get("owner_project_id")
                    if type(owner) is int:
                        prune_to = next(
                            (root for root in self._roots(owner) if path.is_relative_to(root)),
                            None,
                        )
                    files.append(
                        (
                            path,
                            (
                                custody["device"],
                                custody["inode"],
                                custody.get("mode", stat.S_IFREG),
                            ),
                            prune_to,
                        )
                    )
            if not custodies and ActionRepository.requires_file_reconciliation(artifact):
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
        for path, identity, prune_to in files:
            _check_ancestors(path, self.settings.data_dir)
            _remove_owned(
                path, protected, self.settings.data_dir, identity=identity, prune_to=prune_to
            )
        for path in paths:
            _check_ancestors(path, self.settings.data_dir)
            _remove_owned(path, protected, self.settings.data_dir)

    def _purge(self, project_id: int, *, operation_id: str) -> None:
        # Pins RESTRICT artifact deletion. Retire owned jobs/pins before the
        # project cascade; SET NULL and non-FK project history needs explicit purge.
        s = self.session
        s.exec(update(Project).where(col(Project.id) == project_id).values(id=Project.id))
        row = self._project(project_id)
        if (
            row is None
            or row.is_active
            or row.cleanup_json != {"operation_id": operation_id, "status": "pending"}
        ):
            raise ConflictError("project cleanup fence changed before final purge")
        blockers = self._blockers(project_id)
        if self.browser.project_busy(project_id=project_id, data_dir=self.settings.data_dir):
            blockers.append("browser_session")
        if blockers:
            raise ConflictError(
                "project became busy before final purge", data={"blockers": blockers}
            )
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
    path: Path,
    protected: set[Path],
    root: Path,
    *,
    identity: tuple[int, int, int] | None = None,
    prune_to: Path | None = None,
) -> None:
    """Walk by held descriptors, never following a replaced directory or parent."""
    relative = path.relative_to(root)
    with ExitStack() as opened:
        try:
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened.callback(os.close, parent)
            directories = []
            current = root
            for part in relative.parts[:-1]:
                current = current / part
                directories.append((parent, part, current))
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                opened.callback(os.close, parent)
        except FileNotFoundError:
            return
        _remove_at(parent, relative.name, path, protected, identity=identity)
        # Retired source roots may outlive their project while shared bytes are
        # retained. Prune only empty parents, never recursively delete new files.
        if prune_to is not None:
            for parent, name, directory in reversed(directories):
                if not directory.is_relative_to(prune_to):
                    break
                try:
                    os.rmdir(name, dir_fd=parent)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                        break
                    raise


def _remove_at(
    parent: int,
    name: str,
    path: Path,
    protected: set[Path],
    *,
    identity: tuple[int, int, int] | None = None,
) -> None:
    if path in protected or any(ancestor in protected for ancestor in path.parents):
        return
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if identity is not None and (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)) != identity:
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


def _owned_leaves(path: Path, root: Path) -> list[tuple[Path, os.stat_result]]:
    """Inventory only a derived owned tree, through no-follow directory descriptors."""
    _check_ancestors(path, root)
    leaves = []

    def visit(parent: int, name: str, current: Path) -> None:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            leaves.append((current, info))
            return
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for child in os.listdir(fd):
                visit(fd, child, current / child)
        finally:
            os.close(fd)

    with ExitStack() as opened:
        try:
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened.callback(os.close, parent)
            for part in path.relative_to(root).parts[:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                opened.callback(os.close, parent)
        except FileNotFoundError:
            return []
        visit(parent, path.name, path)
    return leaves


@contextmanager
def _writer_lock(path: Path, *, create: bool = False):
    fd = os.open(
        path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0), 0o600
    )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe managed writer lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)
