"""Bounded live project definitions. Callers separately authorize project and file access.

No DB prompt copy, global scan, registration writer, watcher or native launcher lives here.
Directory-relative, no-follow I/O pins every traversed directory. Writes recheck source
bytes, file identity and registrations immediately before atomic replacement/readback.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tomlkit.exceptions import TOMLKitError

from stackos.host_mcp.adapters.codex_definition import (
    OPTIONAL_SETTINGS,
    AgentFields,
    AgentPatch,
    parse_definition,
    patch_definition,
)

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCES = 1000


class AgentSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host_key: Literal["codex"] = "codex"
    native_name: str = Field(min_length=1, max_length=160)
    source_path: str = Field(min_length=1, max_length=1000)


class AgentDefinition(BaseModel):
    project_id: int | None = None
    project_dir: str
    source_ref: AgentSourceRef
    digest: str | None = None
    fields: AgentFields | None = None
    native_settings: dict[str, Any] = Field(default_factory=dict)
    inherited_fields: list[str] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(default_factory=list)
    registration_keys: list[str] = Field(default_factory=list)
    registration_digest: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class AgentList(BaseModel):
    project_id: int | None = None
    project_dir: str
    host_key: Literal["codex"] = "codex"
    items: list[AgentDefinition] = Field(default_factory=list)
    registration_digest: str | None = None
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class ProjectAgentError(Exception):
    def __init__(self, code: str, *, source_ref: AgentSourceRef | None = None, **details: Any):
        super().__init__(code)
        self.data = {
            "code": code,
            "repair": {
                "stale_source": "Refresh the definition and reconcile the draft before saving.",
                "referenced_identity": "Reassign saved references before changing the native name.",
                "ambiguous_identity": "Give each source a unique native name before selecting it.",
                "outside_root": "Choose a regular project source without symlinks or traversal.",
                "unsupported_setting": "Use supported fields and reduction-only restrictions.",
                "missing_scope": "Choose an accessible folder bound to this project.",
                "write_failed": "Refresh before retrying; the save was not confirmed.",
            }.get(
                code, "Repair the indicated project source and refresh; no substitute was selected."
            ),
            **details,
        }
        if source_ref:
            self.data["source_ref"] = source_ref.model_dump()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or path.is_absolute()
        or any(part in {".", ".."} for part in value.split("/"))
        or str(path) != value
        or path.suffix != ".toml"
    ):
        raise ProjectAgentError("outside_root")
    return value


class ProjectAgentService:
    def __init__(
        self,
        project_dir: Path | str,
        *,
        references: Callable[[AgentSourceRef], list[dict[str, Any]]] | None = None,
    ):
        supplied = Path(project_dir)
        try:
            if not supplied.is_absolute() or supplied.is_symlink():
                raise ProjectAgentError("missing_scope")
            self.root = supplied.resolve(strict=True)
            if not self.root.is_dir() or self.root == Path(self.root.anchor):
                raise ProjectAgentError("missing_scope")
            self.root_identity = self.root.stat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProjectAgentError("missing_scope") from exc
        self.references = references or (lambda ref: [])

    @contextmanager
    def _directory(self, relative: str, *, create: bool = False) -> Iterator[int]:
        descriptors: list[int] = []
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(fd)
            if _identity(os.fstat(fd))[:2] != _identity(self.root_identity)[:2]:
                raise ProjectAgentError("stale_source")
            for part in PurePosixPath(relative).parts:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, mode=0o755, dir_fd=fd)
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            yield fd
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def _read(self, relative: str) -> tuple[bytes, os.stat_result]:
        _relative(relative)
        path = PurePosixPath(relative)
        with self._directory(str(path.parent)) as directory:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ProjectAgentError("outside_root")
                if info.st_size > MAX_SOURCE_BYTES:
                    raise ProjectAgentError("invalid_definition", reason="oversized")
                content = stream.read(MAX_SOURCE_BYTES + 1)
                if len(content) > MAX_SOURCE_BYTES or _identity(info) != _identity(
                    os.fstat(stream.fileno())
                ):
                    raise ProjectAgentError("stale_source")
                return content, info

    def _check_directory(self, relative: str, descriptor: int) -> None:
        with self._directory(relative) as current:
            if _identity(os.fstat(current))[:2] != _identity(os.fstat(descriptor))[:2]:
                raise ProjectAgentError("stale_source")

    def _registrations(
        self,
    ) -> tuple[dict[str, list[dict[str, Any]]], str | None, list[dict[str, Any]]]:
        refs: dict[str, list[dict[str, Any]]] = {}
        diagnostics: list[dict[str, Any]] = []
        digest = None
        try:
            content, _ = self._read(".codex/config.toml")
            digest = _digest(content)
            config = tomlkit.parse(content.decode("utf-8")).unwrap()
            agents = config.get("agents", {})
            if not isinstance(agents, dict):
                raise ValueError("agents table")
            if len(agents) > MAX_SOURCES:
                raise ProjectAgentError("invalid_definition", reason="too_many_sources")
            for key, entry in agents.items():
                if not isinstance(entry, dict):
                    # Native agents also contains scalar global options (max_threads etc.).
                    continue
                try:
                    if not isinstance(entry.get("config_file"), str):
                        raise ProjectAgentError("invalid_definition")
                    target = entry["config_file"]
                    if target.startswith("./"):
                        target = target[2:]
                    source = _relative(f".codex/{target}")
                    refs.setdefault(source, []).append(
                        {"key": key, "description": entry.get("description")}
                    )
                except ProjectAgentError as exc:
                    diagnostics.append({**exc.data, "registration_key": key})
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError, TOMLKitError, ValueError, ProjectAgentError) as exc:
            diagnostics.append(_diagnostic(exc, location=".codex/config.toml"))
        return refs, digest, diagnostics

    def list(self) -> AgentList:
        refs, registration_digest, diagnostics = self._registrations()
        sources = set(refs)
        try:
            with self._directory(".codex/agents") as directory, os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.endswith(".toml"):
                        sources.add(f".codex/agents/{entry.name}")
                        if len(sources) > MAX_SOURCES:
                            raise ProjectAgentError("invalid_definition", reason="too_many_sources")
        except FileNotFoundError:
            pass
        except (OSError, ProjectAgentError) as exc:
            diagnostics.append(_diagnostic(exc, location=".codex/agents"))
        if len(sources) > MAX_SOURCES:
            raise ProjectAgentError("invalid_definition", reason="too_many_sources")
        items = []
        for source in sorted(sources):
            registrations = refs.get(source, [])
            keys = sorted(r["key"] for r in registrations)
            name = keys[0] if keys else PurePosixPath(source).stem
            row = AgentDefinition(
                project_dir=str(self.root),
                source_ref=AgentSourceRef(native_name="unknown", source_path=source),
                registration_keys=keys,
                registration_digest=registration_digest,
            )
            try:
                content, info = self._read(source)
                document, fields, unsupported = parse_definition(
                    content,
                    legacy_name=name if keys else None,
                    legacy_description=registrations[0].get("description") if keys else None,
                )
                if ("name" not in document and len(keys) > 1) or (
                    "name" in document and keys and keys != [fields.name]
                ):
                    raise ProjectAgentError("ambiguous_identity")
                row.source_ref.native_name = fields.name
                row.digest = _digest(content)
                row.fields = _supported_fields(fields, unsupported)
                row.unsupported_fields = unsupported
                row.inherited_fields = [
                    key for key in OPTIONAL_SETTINGS if getattr(fields, key) is None
                ]
                row.native_settings = {
                    key: getattr(row.fields, key)
                    for key in OPTIONAL_SETTINGS
                    if getattr(row.fields, key) is not None
                }
                row.capabilities = {
                    "read": True,
                    "edit": bool(info.st_mode & 0o222),
                    "standalone": "name" in document,
                }
            except (OSError, UnicodeError, TOMLKitError, ValueError, ProjectAgentError) as exc:
                row.diagnostics.append(_diagnostic(exc))
            items.append(row)
        counts: dict[str, int] = {}
        for row in items:
            if row.fields:
                counts[row.source_ref.native_name] = counts.get(row.source_ref.native_name, 0) + 1
        for row in items:
            if counts.get(row.source_ref.native_name, 0) > 1:
                row.diagnostics.append(ProjectAgentError("ambiguous_identity").data)
                row.capabilities["edit"] = False
        return AgentList(
            project_dir=str(self.root),
            items=items,
            registration_digest=registration_digest,
            diagnostics=diagnostics,
        )

    def get(self, source_ref: AgentSourceRef | dict[str, Any]) -> AgentDefinition:
        ref = _ref(source_ref)
        for row in self.list().items:
            if row.source_ref == ref:
                if row.diagnostics:
                    raise ProjectAgentError(row.diagnostics[0]["code"], source_ref=ref)
                return row
        raise ProjectAgentError("unavailable", source_ref=ref)

    def create(self, fields: dict[str, Any]) -> AgentDefinition:
        try:
            native = AgentFields.model_validate(fields)
            content = patch_definition(tomlkit.document(), native.model_dump(exclude_none=True))
            _, _, unsupported = parse_definition(content)
            if unsupported:
                raise ProjectAgentError("unsupported_setting", fields=unsupported)
        except (ValidationError, ValueError, TOMLKitError) as exc:
            raise ProjectAgentError("unsupported_setting") from exc
        ref = AgentSourceRef(
            native_name=native.name, source_path=f".codex/agents/{native.name}.toml"
        )
        inventory = self.list()
        if inventory.diagnostics:
            raise ProjectAgentError("invalid_definition", diagnostics=inventory.diagnostics)
        if any(row.source_ref.native_name == native.name for row in inventory.items):
            raise ProjectAgentError("ambiguous_identity", source_ref=ref)
        if len(content) > MAX_SOURCE_BYTES:
            raise ProjectAgentError("invalid_definition", reason="oversized")
        created_identity = None
        try:
            with self._directory(".codex/agents", create=True) as directory:
                if not os.fstat(directory).st_mode & 0o222:
                    raise ProjectAgentError("write_failed", source_ref=ref)
                # Detect registrations appearing during preparation, including name collisions.
                if self.list().registration_digest != inventory.registration_digest:
                    raise ProjectAgentError("stale_source", source_ref=ref)
                self._check_directory(".codex/agents", directory)
                fd = os.open(
                    f"{native.name}.toml",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o644,
                    dir_fd=directory,
                )
                with os.fdopen(fd, "wb") as stream:
                    created_identity = os.fstat(stream.fileno())
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            saved = self.get(ref)
            if saved.digest != _digest(content):
                raise ProjectAgentError("write_failed", source_ref=ref, saved_digest=saved.digest)
            return saved
        except (OSError, ProjectAgentError) as exc:
            # Roll back only our exact created inode/bytes; never delete a caller's edit.
            write_state = "not_written" if created_identity is None else "unconfirmed"
            if created_identity is not None:
                with suppress(OSError, ProjectAgentError):
                    current, info = self._read(ref.source_path)
                    if (
                        _identity(info)[:2] == _identity(created_identity)[:2]
                        and current == content
                    ):
                        with self._directory(".codex/agents") as directory:
                            os.unlink(f"{native.name}.toml", dir_fd=directory)
                        write_state = "rolled_back"
            if isinstance(exc, ProjectAgentError) and created_identity is None:
                raise
            raise ProjectAgentError(
                "write_failed", source_ref=ref, write_state=write_state
            ) from exc

    def update(
        self,
        source_ref: AgentSourceRef | dict[str, Any],
        expected_digest: str,
        patch: dict[str, Any],
        *,
        expected_registration_digest: str | None = None,
    ) -> AgentDefinition:
        ref = _ref(source_ref)
        row = self.get(ref)
        assert row.fields is not None
        if row.digest != expected_digest:
            raise ProjectAgentError("stale_source", source_ref=ref, current_digest=row.digest)
        if (
            expected_registration_digest is not None
            and expected_registration_digest != row.registration_digest
        ):
            raise ProjectAgentError("stale_source", source_ref=ref)
        try:
            AgentPatch.model_validate(patch)
            if any(
                patch.get(k, "present") is None
                for k in ("name", "description", "developer_instructions")
            ):
                raise ValueError("required native fields cannot inherit")
            original, original_info = self._read(ref.source_path)
            if _digest(original) != expected_digest:
                raise ProjectAgentError("stale_source", source_ref=ref)
            document, _, _ = parse_definition(
                original, legacy_name=row.fields.name, legacy_description=row.fields.description
            )
            # Legacy name comes from config key: changing it would change that registration too.
            if "name" in patch and patch["name"] != ref.native_name and row.registration_keys:
                raise ProjectAgentError(
                    "referenced_identity",
                    source_ref=ref,
                    references=[
                        {"kind": "registration", "key": key} for key in row.registration_keys
                    ],
                )
            replacement = patch_definition(document, patch)
            _, changed, unsupported = parse_definition(
                replacement, legacy_name=row.fields.name, legacy_description=row.fields.description
            )
            changed_unsupported = [key for key in unsupported if _path_touched(patch, key)]
            if changed_unsupported:
                raise ProjectAgentError("unsupported_setting", fields=changed_unsupported)
            if len(replacement) > MAX_SOURCE_BYTES:
                raise ProjectAgentError("invalid_definition", reason="oversized")
        except (ValidationError, ValueError, TOMLKitError, UnicodeError) as exc:
            raise ProjectAgentError("unsupported_setting", source_ref=ref) from exc
        except OSError as exc:
            raise ProjectAgentError("stale_source", source_ref=ref) from exc
        if changed.name != ref.native_name:
            references = self.references(ref)
            if references:
                raise ProjectAgentError(
                    "referenced_identity", source_ref=ref, references=references
                )
            if any(item.source_ref.native_name == changed.name for item in self.list().items):
                raise ProjectAgentError("ambiguous_identity", source_ref=ref)
        temporary = f".stackos-agent-{uuid4().hex}.tmp"
        path = PurePosixPath(ref.source_path)
        replaced = False
        try:
            with self._directory(str(path.parent)) as directory:
                if not original_info.st_mode & 0o222 or not os.fstat(directory).st_mode & 0o222:
                    raise ProjectAgentError("write_failed", source_ref=ref)
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(replacement)
                        stream.flush()
                        os.fchmod(stream.fileno(), stat.S_IMODE(original_info.st_mode))
                        os.fsync(stream.fileno())
                    if self.list().registration_digest != row.registration_digest:
                        raise ProjectAgentError("stale_source", source_ref=ref)
                    if changed.name != ref.native_name:
                        references = self.references(ref)
                        if references:
                            raise ProjectAgentError(
                                "referenced_identity", source_ref=ref, references=references
                            )
                    self._check_directory(str(path.parent), directory)
                    current, current_info = self._read(ref.source_path)
                    if current != original or _identity(current_info) != _identity(original_info):
                        raise ProjectAgentError("stale_source", source_ref=ref)
                    os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                    replaced = True
                finally:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=directory)
            saved = self.get(ref.model_copy(update={"native_name": changed.name}))
            if saved.digest != _digest(replacement):
                raise ProjectAgentError("write_failed", source_ref=ref, saved_digest=saved.digest)
            return saved
        except (OSError, ProjectAgentError) as exc:
            if isinstance(exc, ProjectAgentError) and not replaced:
                raise
            raise ProjectAgentError(
                "write_failed",
                source_ref=ref,
                write_state="unconfirmed" if replaced else "not_written",
            ) from exc


def _ref(value: AgentSourceRef | dict[str, Any]) -> AgentSourceRef:
    try:
        ref = AgentSourceRef.model_validate(value)
        _relative(ref.source_path)
        return ref
    except ValidationError as exc:
        raise ProjectAgentError("invalid_definition") from exc


def _path_touched(patch: dict[str, Any], key: str) -> bool:
    root, _, leaf = key.partition(".")
    return root in patch and (not leaf or (isinstance(patch[root], dict) and leaf in patch[root]))


def _supported_fields(fields: AgentFields, unsupported: list[str]) -> AgentFields:
    values = fields.model_dump(exclude_none=True)
    for key in unsupported:
        root, _, leaf = key.partition(".")
        if leaf and root in {"features", "skills"}:
            values.get(root, {}).pop(leaf, None)
        elif not leaf:
            values.pop(root, None)
    for key in ("features", "skills"):
        if values.get(key) == {}:
            del values[key]
    return AgentFields.model_validate(values)


def _diagnostic(exc: Exception, **details: Any) -> dict[str, Any]:
    if isinstance(exc, ProjectAgentError):
        return {**exc.data, **details}
    code = "invalid_definition"
    if isinstance(exc, OSError):
        code = "outside_root" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "unavailable"
    return ProjectAgentError(code, **details).data
