"""Explicit offline operator recovery of an unmapped, workspace-owned Finance store.

Not an agent operation: no enablement, grants, legacy mappings or source writes.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from sqlmodel import select

from stackos.backups.filesystem import overlap
from stackos.db.models import WorkspaceBinding
from stackos.plugins.builtin_finance import (
    FinanceConsole,
    _canonical_root,
    _digest,
    _error,
    _publish_directory,
    helpers,
)
from stackos.repositories.projects import ProjectRepository


class FinanceRecovery:
    """Preview a single bound source and copy it only after exact confirmation."""

    def __init__(self, session, settings):
        self.session = session
        self.console = FinanceConsole(session, settings)
        self.settings = settings

    def _inspect(self, project_id, source):
        project = ProjectRepository(self.session).get(project_id)
        source = _canonical_root(source)
        if overlap(source, self.settings.data_dir) or overlap(source, self.settings.state_dir):
            _error("recovery_source_overlap")
        if (source / ".finance-owner.json").exists():
            _error("recovery_source_already_owned")
        matches = []
        for binding in self.session.exec(select(WorkspaceBinding)).all():
            if not binding.last_known_root:
                continue
            workspace = Path(binding.last_known_root)
            if not workspace.is_absolute():
                continue
            resolved = workspace.resolve()
            if not (source.is_relative_to(resolved) or resolved.is_relative_to(source)):
                continue
            # Validate the original spelling, not just its resolved alias: an
            # overlapping symlink binding must not be accepted as custody proof.
            workspace = helpers().workspace.checked_path(workspace)
            if not workspace.is_dir():
                _error("recovery_workspace_unavailable")
            # A broad ancestor binding (including '/') does not override a more
            # specific project workspace. A foreign nested workspace inside the
            # source still makes the source's custody ambiguous.
            if workspace.is_relative_to(source) and binding.project_id != project_id:
                _error("recovery_workspace_ambiguous")
            if source.is_relative_to(workspace):
                matches.append((binding, workspace))
        if not matches:
            _error("recovery_workspace_unbound_or_ambiguous")
        depth = max(len(workspace.parts) for _, workspace in matches)
        matches = [(binding, root) for binding, root in matches if len(root.parts) == depth]
        if len(matches) != 1 or matches[0][0].project_id != project_id:
            _error("recovery_workspace_unbound_or_ambiguous")
        binding, workspace = matches[0]
        if source == workspace:
            _error("recovery_requires_dedicated_finance_directory")
        doc = self.console._legacy()
        if doc["entries"] or doc["intents"]:
            # Legacy access custody has its own migration path; do not reinterpret it.
            _error("recovery_legacy_mapping_present")
        snapshot = self.console._validated(source)
        if snapshot["custody_issues"]:
            _error("recovery_originals_unavailable")
        parent, target, metadata = self.console._paths(project_id)
        info = source.stat()
        facts = {
            "format": "finance-recovery-preview-v1",
            "project_id": project.id,
            "project_created_at": project.created_at.isoformat(),
            "workspace_binding_id": binding.id,
            "workspace_fingerprint": binding.repo_fingerprint,
            "workspace_root": str(workspace),
            "source_root": str(source),
            "source_device": info.st_dev,
            "source_inode": info.st_ino,
            "source_tree_sha256": self.console._tree(source),
            "master_sha256": snapshot["sha256"],
            "revision": snapshot["revision"],
            "schema_version": snapshot["schema_version"],
            "record_counts": {
                key: len(value)
                for key, value in snapshot["document"].items()
                if isinstance(value, list)
            },
            "verified_attachments": len(snapshot["verified_attachment_refs"]),
            "target_root": str(target),
            "legacy_mapping_sha256": _digest(doc),
        }
        return project, facts, parent, target, metadata

    def preview(self, project_id: int, source: Path):
        source = _canonical_root(source)
        # Require the source's existing canonical writer lock. Never create or
        # chmod anything in the source, even during apply.
        with self.console._lock(source / ".writer.lock"):
            _, facts, parent, target, metadata = self._inspect(project_id, source)
            marker = parent / ".publication-pending"
            if marker.exists() or marker.is_symlink():
                _error("recovery_publication_requires_repair")
            if target.exists() or target.is_symlink() or metadata.exists() or metadata.is_symlink():
                _error("recovery_target_exists")
            return {**facts, "confirmation_sha256": _digest(facts), "applied": False}

    def _finish(self, state, target, metadata):
        pending = state["publication"]
        if not re.fullmatch(r"[a-f0-9]{32}", str(pending.get("token", ""))):
            _error("recovery_publication_requires_repair")
        if state["phase"] == "ready" and not target.exists():
            _error("recovery_target_missing")
        stage = helpers().workspace.checked_path(
            self.console.config_dir / "staging" / pending["token"]
        )
        candidate = target if target.exists() or target.is_symlink() else stage
        candidate = _canonical_root(candidate)
        with self.console._lock(candidate / ".writer.lock"):
            snapshot = self.console._validated(candidate, state)
            if (
                snapshot["custody_issues"]
                or self.console._tree(candidate) != pending["tree_sha256"]
            ):
                _error("recovery_copy_mismatch")
        if candidate != target:
            _publish_directory(candidate, target)
        info = target.stat()
        state.update(
            phase="ready",
            canonical_root=str(target),
            root_device=info.st_dev,
            root_inode=info.st_ino,
        )
        state["publication"]["complete"] = True
        self.console._save(metadata, state)
        self.console._clear_publication(metadata.parent, state)

    def apply(self, project_id: int, source: Path, confirmation: str):
        source = _canonical_root(source)
        with self.console._lock(source / ".writer.lock"):
            project, facts, parent, target, metadata = self._inspect(project_id, source)
            if confirmation != _digest(facts):
                _error("recovery_preview_changed", conflict=True)
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.console._lock(parent / ".writer.lock", create=True):
                state = self.console._state(metadata, project)
                if state is not None:
                    expected = {**facts, "confirmation_sha256": confirmation}
                    if state.get("publication", {}).get("operator_recovery") != expected:
                        _error("recovery_target_exists")
                    self._finish(state, target, metadata)
                    return {**expected, "applied": True, "already_applied_or_resumed": True}
                marker = parent / ".publication-pending"
                if marker.exists() or marker.is_symlink():
                    _error("recovery_publication_requires_repair")
                if (
                    target.exists()
                    or target.is_symlink()
                    or metadata.exists()
                    or metadata.is_symlink()
                ):
                    _error("recovery_target_exists")
                staging = helpers().workspace.checked_path(self.console.config_dir / "staging")
                staging.mkdir(parents=True, exist_ok=True, mode=0o700)
                stage = staging / uuid.uuid4().hex
                stage.mkdir(mode=0o700)
                tree = self.console._tree(source, stage)
                with self.console._lock(stage / ".writer.lock", create=True):
                    snapshot = self.console._validated(stage)
                    if snapshot["custody_issues"] or snapshot["sha256"] != facts["master_sha256"]:
                        _error("recovery_copy_mismatch")
                self.session.expire_all()
                _, current, _, _, _ = self._inspect(project_id, source)
                if current != facts or tree != facts["source_tree_sha256"]:
                    _error("recovery_source_changed", conflict=True)
                if self.console._tree(stage) != tree:
                    _error("recovery_copy_mismatch")
                state = self.console._new_state(project, 1)
                self.console._write_owner(stage, state)
                state["publication"] = {
                    "token": stage.name,
                    "tree_sha256": self.console._tree(stage),
                    "legacy_mapping_sha256": facts["legacy_mapping_sha256"],
                    "operator_recovery": {**facts, "confirmation_sha256": confirmation},
                }
                # Block backups before persisting custody. Once custody is saved,
                # exact retries can resume; a marker-only crash requires repair.
                self.console._mark_publication(parent)
                self.console._save(metadata, state)
                self._finish(state, target, metadata)
                return {**facts, "confirmation_sha256": confirmation, "applied": True}
