"""First-party Finance adapter over one private managed store per project."""

from __future__ import annotations

import base64
import copy
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import types
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import NoReturn

from stackos.backups.filesystem import (
    directory_inventory,
    fsync_directory,
    identity,
    open_relative,
    read_regular,
)
from stackos.backups.limits import MAX_EXPANDED_BYTES, MAX_MEMBERS
from stackos.backups.models import BackupError
from stackos.config import Settings
from stackos.mcp.errors import ToolNotGrantedError
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.projects import ProjectRepository

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
_MODES = ["correct", "export", "read"]


def _error(code: str, *, conflict: bool = False) -> NoReturn:
    error = ConflictError if conflict else ValidationError
    raise error(
        "Finance operation requires repair",
        data={"finance_code": code, "next_action": "inspect_and_retry"},
    )


def _deny(code: str = "access_denied") -> NoReturn:
    raise ToolNotGrantedError(
        "Finance access denied", data={"finance_code": code, "next_action": "review_finance_access"}
    )


@lru_cache(maxsize=1)
def helpers():
    """Load fixed shipped code, never scripts found in financial storage."""
    bundled = Path(str(resources.files("stackos").joinpath("_assets/plugins/finance/scripts")))
    source = Path(__file__).resolve().parents[2] / "plugins/finance/scripts"
    scripts = bundled if bundled.is_dir() else source
    if not (scripts / "finance_management.py").is_file():
        _error("helper_unavailable")
    package_name = "_stackos_builtin_finance_scripts"
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts.resolve())]
    sys.modules[package_name] = package
    name = package_name + ".finance_management"
    spec = importlib.util.spec_from_file_location(name, scripts / "finance_management.py")
    if spec is None or spec.loader is None:
        _error("helper_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def call_helper(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ToolNotGrantedError, ConflictError, ValidationError):
        raise
    except helpers().ManagementError as exc:
        code = exc.code
        data = {"finance_code": code, "next_action": "inspect_and_retry"}
        if code == "outcome_unknown":
            data.update(exc.receipt)
        error = (
            ConflictError
            if code
            in {
                "stale_snapshot",
                "stale_preview",
                "intent_conflict",
                "writer_busy",
                "outcome_unknown",
                "commit_failed",
            }
            else ValidationError
        )
        raise error("Finance operation requires repair", data=data) from None
    except (OSError, ValueError, KeyError, TypeError):
        _error("unreadable_or_invalid_workspace")


def _canonical_root(root: str | Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute() or candidate == Path("/"):
        _deny("invalid_root")
    try:
        candidate = helpers().workspace.checked_path(candidate)
        if not candidate.is_dir():
            _deny("invalid_root")
        return candidate
    except (OSError, ValueError):
        _deny("invalid_root")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _snapshot_metadata(snapshot):
    return {
        k: copy.deepcopy(snapshot[k])
        for k in ("revision", "sha256", "schema_version", "read_at", "custody_issues")
        if k in snapshot
    }


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _publish_directory(source: Path, target: Path) -> None:
    """Atomic directory publication that cannot replace even an empty target."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(target), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE
    else:
        _error("atomic_publication_unavailable")
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    fsync_directory(target.parent)


class FinanceConsole:
    """Resolve the project's single JSON store; never accept a caller's path."""

    def __init__(self, session, settings: Settings):
        self.session, self.settings = session, settings
        self.config_dir = settings.data_dir / "plugin-state" / "finance"
        self.config_path = self.config_dir / "access.json"  # Legacy migration input only.

    def _project(self, project_id: int):
        project = ProjectRepository(self.session).get(project_id)
        plugin = PluginRepository(self.session).get_plugin("finance", project_id=project_id)
        if plugin.enabled_for_project is not True:
            _deny("plugin_not_enabled")
        return project

    def _paths(self, project_id):
        parent = helpers().workspace.checked_path(
            self.settings.data_dir / "projects" / str(project_id)
        )
        return parent, parent / "finance", parent / "finance-state.json"

    @staticmethod
    def _ref(project_id):
        return f"finance:project:{project_id}"

    @contextmanager
    def _lock(self, path: Path, *, create=False):
        helpers().workspace.checked_path(path)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _error("unsafe_storage_lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _error("writer_busy", conflict=True)
            yield
            if (path.lstat().st_dev, path.lstat().st_ino) != (info.st_dev, info.st_ino):
                _error("storage_lock_changed", conflict=True)
        finally:
            os.close(fd)

    @staticmethod
    def _read_json(path):
        if not path.exists() and not path.is_symlink():
            return None
        try:
            helpers().workspace.checked_path(path)
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _error("unsafe_storage_metadata")
            raw = read_regular(path)
            after = path.lstat()
            if identity(after) != identity(before) or after.st_nlink != 1:
                _error("unsafe_storage_metadata")
            value = json.loads(raw)
        except (BackupError, OSError):
            _error("unsafe_storage_metadata")
        except ValueError:
            _error("storage_metadata_corrupt")
        if not isinstance(value, dict):
            _error("storage_metadata_corrupt")
        return value

    @staticmethod
    def _save(path, value):
        helpers().workspace.atomic_write(
            path, json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        )
        path.chmod(0o600)
        if FinanceConsole._read_json(path) != value:
            _error("storage_outcome_unknown", conflict=True)

    def _state(self, path, project):
        state = self._read_json(path)
        if state is None:
            return None
        if (
            state.get("format") != "finance-project-v1"
            or type(state.get("project_id")) is not int
            or state.get("project_id") != project.id
            or state.get("project_created_at") != project.created_at.isoformat()
            or state.get("workspace_ref") != self._ref(project.id)
            or state.get("modes") != _MODES
            or state.get("backend_key") != "local-json"
            or type(state.get("generation")) is not int
            or state["generation"] < 1
            or not isinstance(state.get("access_id"), str)
            or state.get("phase") not in {"publishing", "ready"}
        ):
            _error("storage_identity_mismatch")
        if state["phase"] == "ready" and (
            not isinstance(state.get("canonical_root"), str)
            or type(state.get("root_device")) is not int
            or type(state.get("root_inode")) is not int
        ):
            _error("storage_metadata_corrupt")
        return state

    def _legacy(self):
        doc = self._read_json(self.config_path)
        if doc is None:
            return {"format": "finance-access-v1", "entries": [], "intents": {}}
        if (
            doc.get("format") != "finance-access-v1"
            or not isinstance(doc.get("entries"), list)
            or not isinstance(doc.get("intents"), dict)
        ):
            _error("legacy_access_corrupt")
        for entry in doc["entries"]:
            if (
                not isinstance(entry, dict)
                or not all(
                    isinstance(entry.get(k), str)
                    for k in ("access_id", "workspace_ref", "canonical_root")
                )
                or not all(
                    type(entry.get(k)) is int
                    for k in ("project_id", "root_device", "root_inode", "generation")
                )
                or entry["generation"] < 1
            ):
                _error("legacy_access_corrupt")
        return doc

    def _legacy_source(self, doc, project):
        entries = [e for e in doc["entries"] if e["project_id"] == project.id]
        if not entries:
            if any(key.startswith(f"{project.id}:") for key in doc["intents"]):
                _error("legacy_history_requires_recovery")
            return None
        if (
            len(entries) != 1
            or entries[0].get("revoked_at")
            or entries[0].get("retired_to_managed_project")
        ):
            _error("legacy_history_requires_recovery")
        entry = entries[0]
        if (
            entry.get("backend_key") != "local-json"
            or not isinstance(entry.get("modes"), list)
            or "read" not in entry["modes"]
        ):
            _error("legacy_history_requires_recovery")
        try:
            selected = datetime.fromisoformat(entry["selected_at"]).replace(tzinfo=UTC)
            created = project.created_at.replace(tzinfo=UTC)
            if selected < created:
                _error("legacy_project_identity_ambiguous")
        except (KeyError, ValueError, TypeError):
            _error("legacy_project_identity_ambiguous")
        root = _canonical_root(entry["canonical_root"])
        info = root.stat()
        if (info.st_dev, info.st_ino) != (entry["root_device"], entry["root_inode"]):
            _error("legacy_root_changed")
        from stackos.backups.filesystem import overlap

        if overlap(root, self.settings.data_dir) or overlap(root, self.settings.state_dir):
            _error("legacy_root_overlap")
        for other in doc["entries"]:
            if other is entry:
                continue
            path = Path(other["canonical_root"])
            if not path.is_absolute():
                _error("legacy_access_corrupt")
            path = helpers().workspace.checked_path(path)
            if (
                not path.is_absolute()
                or overlap(root, path)
                or (other["root_device"], other["root_inode"]) == (info.st_dev, info.st_ino)
            ):
                _error("legacy_root_shared")
        return entry, root

    @staticmethod
    def _tree(root: Path, destination: Path | None = None):
        """Copy/hash the complete pinned tree, retaining every regular original."""
        inventory = directory_inventory(root, root / ".writer.lock")
        if len(inventory) > MAX_MEMBERS:
            _error("migration_too_large")
        manifest: list[list[str | int]] = []
        aliases: set[str] = set()
        total = 0
        for relative, expected in inventory.items():
            if relative == "/":
                continue
            alias = unicodedata.normalize("NFD", relative).casefold()
            if alias in aliases:
                _error("unsafe_storage_alias")
            aliases.add(alias)
            if relative.endswith("/"):
                manifest.append([relative])
                if destination is not None:
                    (destination / relative).mkdir(mode=0o700)
                continue
            fd = open_relative(root, relative)
            out = None
            try:
                before = os.fstat(fd)
                if before.st_nlink != 1 or identity(before) != expected:
                    _error("unsafe_or_changing_storage")
                total += before.st_size
                if total > MAX_EXPANDED_BYTES:
                    _error("migration_too_large")
                if destination is not None:
                    out = os.open(
                        destination / relative,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                    )
                digest, count = hashlib.sha256(), 0
                while chunk := os.read(fd, 1024 * 1024):
                    count += len(chunk)
                    if count > before.st_size:
                        _error("storage_changed", conflict=True)
                    digest.update(chunk)
                    if out is not None:
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(out, view) :]
                if count != before.st_size or identity(os.fstat(fd)) != expected:
                    _error("storage_changed", conflict=True)
                if out is not None:
                    os.fsync(out)
                manifest.append([relative, count, digest.hexdigest()])
            finally:
                os.close(fd)
                if out is not None:
                    os.close(out)
        if directory_inventory(root, root / ".writer.lock") != inventory:
            _error("storage_changed", conflict=True)
        if destination is not None:
            for relative in reversed(list(inventory)):
                if relative.endswith("/") and relative != "/":
                    fsync_directory(destination / relative)
            fsync_directory(destination)
        return _digest(manifest)

    @staticmethod
    def _owner_material(state):
        return {
            "format": "finance-store-v1",
            "project_id": state["project_id"],
            "project_created_at": state["project_created_at"],
            "access_id": state["access_id"],
        }

    def _owner(self, root, state):
        if self._read_json(root / ".finance-owner.json") != self._owner_material(state):
            _error("storage_owner_mismatch")

    def _write_owner(self, root, state):
        # This is a physical-store identity, never a replacement financial record.
        payload = json.dumps(self._owner_material(state), sort_keys=True).encode()
        fd = os.open(
            root / ".finance-owner.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(root)

    def _mark_publication(self, parent):
        marker = parent / ".publication-pending"
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if (
                marker.is_symlink()
                or not stat.S_ISREG(marker.lstat().st_mode)
                or marker.lstat().st_nlink != 1
            ):
                _error("unsafe_storage_metadata")
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"pending\n")
                stream.flush()
                os.fsync(stream.fileno())
        fsync_directory(parent)

    def _clear_publication(self, parent, state):
        marker = parent / ".publication-pending"
        if not marker.exists() and not marker.is_symlink():
            return
        if state["phase"] != "ready" or state.get("publication", {}).get("complete") is not True:
            _error("storage_publication_incomplete")
        prior = state["publication"].get("legacy")
        if prior is not None:
            matches = [e for e in self._legacy()["entries"] if e["access_id"] == prior["access_id"]]
            if (
                len(matches) != 1
                or matches[0].get("retired_to_managed_project") != state["publication"]["token"]
                or not matches[0].get("revoked_at")
            ):
                _error("legacy_mapping_changed", conflict=True)
        if (
            marker.is_symlink()
            or not stat.S_ISREG(marker.lstat().st_mode)
            or marker.lstat().st_nlink != 1
        ):
            _error("unsafe_storage_metadata")
        marker.unlink()
        fsync_directory(parent)

    def _validated(self, root, state=None):
        if state is not None:
            self._owner(root, state)
        master = root / "finance.json"
        info = master.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _error("storage_custody_requires_repair")
        raw = helpers().workspace.read_regular(master)
        document = helpers().workspace.decode_json(raw)
        # read_snapshot deliberately groups unavailable originals together. Check
        # actual paths/bytes here so missing evidence stays an inspectable gap,
        # while containment, file aliases and altered originals block adoption.
        for row in document["attachments"]:
            if row["status"] in {"disposed", "quarantined"} or "quarantine_ref" in row:
                continue
            try:
                path = helpers().workspace.evidence_path(root, row["relative_path"])
                try:
                    info = path.lstat()
                except (FileNotFoundError, PermissionError):
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    _error("storage_custody_requires_repair")
                fd = open_relative(root, path.relative_to(root).as_posix())
                try:
                    before = identity(os.fstat(fd))
                    if before != identity(info) or info.st_size != row["bytes"]:
                        _error("storage_custody_requires_repair")
                    digest, count = hashlib.sha256(), 0
                    while chunk := os.read(fd, 1024 * 1024):
                        count += len(chunk)
                        if count > row["bytes"]:
                            _error("storage_custody_requires_repair")
                        digest.update(chunk)
                    if (
                        count != row["bytes"]
                        or digest.hexdigest() != row["sha256"]
                        or identity(os.fstat(fd)) != before
                        or identity(path.lstat()) != before
                    ):
                        _error("storage_custody_requires_repair")
                finally:
                    os.close(fd)
            except (ValueError, KeyError, OSError, BackupError):
                _error("storage_custody_requires_repair")
        # Keep canonical structural validation/snapshot semantics, after rejecting
        # special files that a normal blocking original read must never open.
        snapshot = call_helper(helpers().read_snapshot, master)
        if snapshot["sha256"] != hashlib.sha256(raw).hexdigest():
            _error("storage_changed", conflict=True)
        return snapshot

    @staticmethod
    def _safe(entry):
        return {
            k: copy.deepcopy(entry[k])
            for k in (
                "access_id",
                "project_id",
                "workspace_ref",
                "backend_key",
                "modes",
                "generation",
            )
        }

    def _new_state(self, project, generation):
        return {
            "format": "finance-project-v1",
            "project_id": project.id,
            "project_created_at": project.created_at.isoformat(),
            "workspace_ref": self._ref(project.id),
            "access_id": "finance-access:" + uuid.uuid4().hex,
            "backend_key": "local-json",
            "modes": _MODES.copy(),
            "generation": generation,
            "phase": "publishing",
        }

    def _retire(self, pending, doc):
        if "legacy" not in pending:
            return
        prior = pending["legacy"]
        matches = [e for e in doc["entries"] if e["access_id"] == prior["access_id"]]
        if len(matches) != 1:
            _error("legacy_mapping_changed", conflict=True)
        entry = matches[0]
        if entry.get("retired_to_managed_project") == pending["token"]:
            return
        if _digest(doc) != prior["mapping_sha256"] or _digest(entry) != prior["entry_sha256"]:
            _error("legacy_mapping_changed", conflict=True)
        entry.update(
            retired_to_managed_project=pending["token"],
            revoked_at=_now(),
            generation=entry["generation"] + 1,
        )
        self._save(self.config_path, doc)

    def _complete(self, root, metadata, state, doc):
        pending = state.get("publication")
        if (
            not isinstance(pending, dict)
            or not isinstance(pending.get("token"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", pending["token"])
        ):
            _error("publication_metadata_corrupt")
        stage = helpers().workspace.checked_path(self.config_dir / "staging" / pending["token"])
        candidate = root if root.exists() or root.is_symlink() else stage
        if not candidate.exists():
            _error("storage_publication_incomplete")
        candidate = _canonical_root(candidate)
        if "legacy" not in pending and _digest(doc) != pending.get("legacy_mapping_sha256"):
            _error("legacy_mapping_changed", conflict=True)
        with self._lock(candidate / ".writer.lock", create=True):
            self._validated(candidate, state)
            if self._tree(candidate) != pending.get("tree_sha256"):
                _error("storage_publication_conflict", conflict=True)
        if candidate != root:
            project = self._project(state["project_id"])
            if project.created_at.isoformat() != state["project_created_at"]:
                _error("storage_identity_mismatch")
            _publish_directory(candidate, root)
        self._retire(pending, doc)
        info = root.stat()
        state.update(
            phase="ready", canonical_root=str(root), root_device=info.st_dev, root_inode=info.st_ino
        )
        # Completed provenance is metadata only; the retained source is never a fallback writer.
        state["publication"]["complete"] = True
        self._save(metadata, state)
        self._clear_publication(metadata.parent, state)

    def _prepare(self, project, root, metadata, doc):
        source = self._legacy_source(doc, project)
        if root.exists() or root.is_symlink():
            _error("storage_metadata_missing")
        self._mark_publication(metadata.parent)
        token = uuid.uuid4().hex
        staging = helpers().workspace.checked_path(self.config_dir / "staging")
        staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        stage = staging / token
        if source is None:
            with self._lock(self.config_dir / ".access.lock", create=True):
                current = self._legacy()
                if current != doc or self._legacy_source(current, project) is not None:
                    _error("legacy_mapping_changed", conflict=True)
                helpers().workspace.initialize(stage)
                state = self._new_state(project, 1)
                self._write_owner(stage, state)
                state["publication"] = {
                    "token": token,
                    "tree_sha256": self._tree(stage),
                    "legacy_mapping_sha256": _digest(current),
                }
                self._validated(stage, state)
                self._save(metadata, state)
                self._complete(root, metadata, state, current)
                return state
        entry, legacy_root = source
        with self._lock(legacy_root / ".writer.lock"):
            self._legacy_source(doc, project)
            with self._lock(self.config_dir / ".access.lock", create=True):
                current = self._legacy()
                if _digest(current) != _digest(doc):
                    _error("legacy_mapping_changed", conflict=True)
                self._legacy_source(current, project)
                self._validated(legacy_root)
                stage.mkdir(mode=0o700)
                tree = self._tree(legacy_root, stage)
                # A new persistent lock has its own inode; never copy an active lock.
                with self._lock(stage / ".writer.lock", create=True):
                    self._validated(stage)
                if (
                    self._tree(stage) != tree
                    or self._tree(legacy_root) != tree
                    or self._legacy() != current
                ):
                    _error("legacy_source_changed", conflict=True)
                state = self._new_state(project, entry["generation"] + 1)
                self._write_owner(stage, state)
                state["publication"] = {
                    "token": token,
                    "tree_sha256": self._tree(stage),
                    "legacy": {
                        "access_id": entry["access_id"],
                        "entry_sha256": _digest(entry),
                        "mapping_sha256": _digest(current),
                        "tree_sha256": tree,
                    },
                }
                self._save(metadata, state)
                self._complete(root, metadata, state, current)
                return state

    def status(self, inp):
        try:
            return self._status(inp)
        except BackupError:
            _error("unsafe_or_changing_storage")
        except (OSError, ValueError, KeyError, TypeError):
            _error("storage_requires_repair")

    def _status(self, inp):
        project = self._project(inp.project_id)
        parent, root, metadata = self._paths(project.id)
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._lock(parent / ".writer.lock", create=True):
            state = self._state(metadata, project)
            if state is None:
                state = self._prepare(project, root, metadata, self._legacy())
            elif state["phase"] == "publishing":
                self._mark_publication(parent)
                if "legacy" in state.get("publication", {}):
                    doc = self._legacy()
                    prior = state["publication"]["legacy"]
                    matches = [e for e in doc["entries"] if e["access_id"] == prior["access_id"]]
                    if len(matches) != 1:
                        _error("legacy_mapping_changed", conflict=True)
                    if (
                        matches[0].get("retired_to_managed_project")
                        == state["publication"]["token"]
                    ):
                        with self._lock(self.config_dir / ".access.lock", create=True):
                            self._complete(root, metadata, state, self._legacy())
                    else:
                        source = self._legacy_source(doc, project)
                        if source is None:
                            _error("legacy_history_requires_recovery")
                        _, legacy_root = source
                        with self._lock(legacy_root / ".writer.lock"):
                            self._legacy_source(doc, project)
                            with self._lock(self.config_dir / ".access.lock", create=True):
                                current = self._legacy()
                                self._legacy_source(current, project)
                                if (
                                    _digest(current) != prior["mapping_sha256"]
                                    or self._tree(legacy_root) != prior["tree_sha256"]
                                ):
                                    _error("legacy_source_changed", conflict=True)
                                self._complete(root, metadata, state, current)
                else:
                    self.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with self._lock(self.config_dir / ".access.lock", create=True):
                        self._complete(root, metadata, state, self._legacy())
            if not root.exists():
                _error("storage_missing")
            root = _canonical_root(root)
            self._owner(root, state)
            with self._lock(root / ".writer.lock", create=True):
                info = root.stat()
                if (info.st_dev, info.st_ino, str(root)) != (
                    state["root_device"],
                    state["root_inode"],
                    state["canonical_root"],
                ):
                    self._validated(root, state)
                    self._tree(root)
                    state.update(
                        root_device=info.st_dev,
                        root_inode=info.st_ino,
                        canonical_root=str(root),
                        generation=state["generation"] + 1,
                    )
                    self._save(metadata, state)
                snapshot = call_helper(helpers().read_snapshot, root / "finance.json")
            self._clear_publication(parent, state)
            access = {
                **self._safe(state),
                "readiness": "ready",
                "snapshot": _snapshot_metadata(snapshot),
            }
            return {"project_id": project.id, "readiness": "ready", "access": [access]}

    def authorize(self, inp, mode="read"):
        project = self._project(inp.project_id)
        _, root, metadata = self._paths(project.id)
        state = self._state(metadata, project)
        if state is None or state["phase"] != "ready":
            _deny("storage_not_ready")
        if inp.workspace_ref != self._ref(project.id):
            _deny("wrong_project_store")
        if inp.access_generation != state["generation"]:
            _deny("stale_access_generation")
        if mode not in state["modes"]:
            _deny("mode_denied")
        root = _canonical_root(root)
        info = root.stat()
        if (info.st_dev, info.st_ino, str(root)) != (
            state["root_device"],
            state["root_inode"],
            state["canonical_root"],
        ):
            _deny("root_changed")
        self._owner(root, state)
        if (root / "finance.json").lstat().st_nlink != 1:
            _deny("unsafe_storage_alias")
        return state, root / "finance.json"

    def snapshot(self, inp, mode="read"):
        _, path = self.authorize(inp, mode)
        snapshot = call_helper(helpers().read_snapshot, path)
        self.authorize(inp, mode)
        return snapshot, path

    def query(self, inp):
        snapshot, _ = self.snapshot(inp)
        return call_helper(helpers().query, snapshot, inp.query_arguments())

    def record(self, inp):
        snapshot, _ = self.snapshot(inp)
        self._expected(snapshot, inp.expected_snapshot)
        return call_helper(helpers().record_detail, snapshot, inp.record_ref)

    @staticmethod
    def _expected(snapshot, expected):
        if expected and (snapshot["revision"], snapshot["sha256"]) != (
            expected.revision,
            expected.sha256,
        ):
            _error("stale_snapshot", conflict=True)

    def preview(self, inp):
        snapshot, _ = self.snapshot(inp, "correct")
        return call_helper(helpers().preview, snapshot, inp.intent_arguments())

    def commit(self, inp):
        _, path = self.authorize(inp, "correct")
        return call_helper(
            helpers().commit,
            path,
            inp.intent_arguments(),
            inp.preview_digest,
            actor="local-user:operator",
            authorize=lambda: self.authorize(inp, "correct"),
        )

    def evidence(self, inp):
        snapshot, path = self.snapshot(inp)
        self._expected(snapshot, inp.expected_snapshot)
        row = next(
            (
                x
                for x in snapshot["document"]["attachments"]
                if x["record_id"] == inp.attachment_ref
            ),
            None,
        )
        if row is None:
            _deny("unknown_attachment")
        if row["status"] in {"disposed", "quarantined"} or "quarantine_ref" in row:
            return {"attachment_ref": inp.attachment_ref, "state": row["status"]}
        if row["bytes"] > MAX_EVIDENCE_BYTES:
            return {
                "attachment_ref": inp.attachment_ref,
                "state": "too_large",
                "next_action": "use_authorized_host_view",
            }
        try:
            target = helpers().workspace.evidence_path(path.parent, row["relative_path"])
            entry, _ = self.authorize(inp)
            payload = _read_contained(
                path.parent,
                target.relative_to(path.parent),
                MAX_EVIDENCE_BYTES,
                (entry["root_device"], entry["root_inode"]),
            )
            self.authorize(inp)
            if len(payload) != row["bytes"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
                _error("evidence_integrity")
        except (OSError, ValueError):
            return {
                "attachment_ref": inp.attachment_ref,
                "state": "unavailable",
                "next_action": "inspect_original_custody",
            }
        name = re.sub(r"[^A-Za-z0-9._-]", "_", target.name)[:160] or "original"
        mime, inline = _media(name, payload)
        return {
            "attachment_ref": inp.attachment_ref,
            "state": "available",
            "filename": name,
            "content_type": mime,
            "inline_allowed": inline,
            "disposition": "inline" if inline else "attachment",
            "bytes": len(payload),
            "sha256": row["sha256"],
            "content_base64": base64.b64encode(payload).decode(),
            "snapshot": _snapshot_metadata(snapshot),
        }

    def report(self, inp):
        snapshot, path = self.snapshot(inp, "export")
        if inp.report_kind == "analysis":
            if inp.analysis_request_id is None:
                _error("analysis_request_required")
            from stackos.repositories.agent_requests import AgentRequestRepository

            request = AgentRequestRepository(self.session).get(
                project_id=inp.project_id, request_id=inp.analysis_request_id
            )
            if request.project_id != inp.project_id:
                _deny("wrong_project")
            meta = request.metadata_json or {}
            if not isinstance(meta, dict):
                _error("analysis_request_invalid")
            selected = meta.get("metadata_json", meta)
            if not isinstance(selected, dict) or selected.get("workspace_ref") != inp.workspace_ref:
                _deny("wrong_workspace")
            if selected.get("access_generation") != inp.access_generation:
                return {
                    "state": "historical_access",
                    "request_id": request.id,
                    "next_action": "request_current_analysis",
                }
            if request.status != "resolved":
                requested_source = selected.get("source_snapshot")
                if not isinstance(requested_source, dict) or not all(
                    key in requested_source for key in ("revision", "sha256")
                ):
                    return {
                        "state": "source_unavailable",
                        "request_id": request.id,
                        "next_action": "request_current_analysis",
                    }
                if requested_source != {key: snapshot[key] for key in ("revision", "sha256")}:
                    return {
                        "state": "historical_source",
                        "request_id": request.id,
                        "next_action": "request_current_analysis",
                    }
                expiry = request.claim_expires_at
                if (
                    request.status == "claimed"
                    and expiry is not None
                    and expiry.replace(tzinfo=UTC) <= datetime.now(UTC)
                ):
                    return {
                        "state": "claim_expired",
                        "request_id": request.id,
                        "next_action": "inspect_agent_request",
                    }
                state = "awaiting_agent" if request.status == "new" else request.status
                if request.run_plan_id is not None:
                    # The request records queue/linkage state; canonical run/step
                    # rows own execution. Read statuses only, never result/error bodies.
                    from sqlmodel import select

                    from stackos.db.models import (
                        RunPlan,
                        RunPlanStatus,
                        RunPlanStep,
                        RunPlanStepStatus,
                    )

                    plan = self.session.get(RunPlan, request.run_plan_id)
                    if plan is None or plan.project_id != inp.project_id:
                        state = "run_unavailable"
                    elif plan.status == RunPlanStatus.DRAFT:
                        state = "run-created"
                    elif plan.status == RunPlanStatus.COMPLETED:
                        state = "completed_unpublished"
                    elif plan.status == RunPlanStatus.FAILED:
                        state = "failed"
                    elif plan.status == RunPlanStatus.ABORTED:
                        state = "run-aborted"
                    elif plan.status == RunPlanStatus.STARTED and plan.run_id is not None:
                        steps = set(
                            self.session.exec(
                                select(RunPlanStep.status).where(RunPlanStep.run_plan_id == plan.id)
                            ).all()
                        )
                        if RunPlanStepStatus.FAILED in steps:
                            state = "failed"
                        elif RunPlanStepStatus.BLOCKED in steps:
                            state = "run-blocked"
                        else:
                            state = "run-started"
                    else:
                        state = "run_unavailable"
                elif state in {"run-created", "run-started"}:
                    state = "run_unavailable"
                return {
                    "state": state,
                    "request_id": request.id,
                    "next_action": "inspect_agent_request",
                }
            ref = meta.get("external_result_ref")
            digest = meta.get("external_result_sha256")
            original_query = selected.get("query")
            requested_source = selected.get("source_snapshot")
            if (
                not isinstance(ref, str)
                or not digest
                or meta.get("state") != "published"
                or not isinstance(original_query, dict)
                or not requested_source
                or meta.get("source_snapshot") != requested_source
            ):
                return {"state": "result_unavailable", "request_id": request.id}
            current_query = call_helper(helpers().normalize_query, inp.query_arguments())
            requested_query = call_helper(helpers().normalize_query, original_query)
            transient = {"expected_snapshot", "offset", "limit"}
            if {k: v for k, v in current_query.items() if k not in transient} != {
                k: v for k, v in requested_query.items() if k not in transient
            }:
                _error("analysis_query_mismatch")
            result = call_helper(
                helpers().read_result,
                path,
                ref,
                expected_digest=digest,
                expected_query=original_query,
                expected_selection={
                    "record_refs": selected.get("record_refs", []),
                    "source_refs": selected.get("source_refs", []),
                },
            )
            if result["result"]["source_snapshot"] != requested_source:
                _error("analysis_source_mismatch")
            if result["result"]["request_id"] != str(request.id):
                _error("analysis_request_mismatch")
            self.authorize(inp, "export")
            return {**result, "request_id": request.id}
        projected = call_helper(helpers().query, snapshot, inp.query_arguments())
        content = call_helper(helpers().render_report, projected, inp.format, snapshot=snapshot)
        self.authorize(inp, "export")
        return {
            "state": "current",
            "snapshot": _snapshot_metadata(snapshot),
            "query": projected["query"],
            "content_type": {
                "json": "application/json",
                "csv": "text/csv",
                "markdown": "text/markdown",
            }[inp.format],
            "filename": "finance-period."
            + {"json": "json", "csv": "csv", "markdown": "md"}[inp.format],
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode(),
            "warnings": projected["warnings"],
        }


def _read_contained(root: Path, relative: Path, cap: int, identity: tuple[int, int]) -> bytes:
    """Anchor every component to no-follow directory descriptors."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != identity:
            raise ValueError("root changed")
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > cap:
                raise ValueError("unavailable evidence")
            payload = stream.read(cap + 1)
            after = os.fstat(stream.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or len(payload) != before.st_size:
                raise ValueError("changed evidence")
            return payload
    finally:
        os.close(fd)


def _media(name: str, payload: bytes) -> tuple[str, bool]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf" and payload.startswith(b"%PDF-"):
        return "application/pdf", True
    if suffix == ".png" and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", True
    if suffix in {".jpg", ".jpeg"} and payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", True
    if suffix == ".webp" and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp", True
    if suffix == ".txt":
        return "text/plain", True
    return "application/octet-stream", False
