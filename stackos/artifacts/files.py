"""Resolve existing managed files; references never grant filesystem access."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlmodel import Session

from stackos.config import Settings
from stackos.db.models import ActionCall, Artifact
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.project_access import require_project
from stackos.repositories.resources import ArtifactRepository

FileSource = Literal["artifact", "action-output"]
PRIVATE_STAGING_ROOTS = frozenset({"imap-transfers", "stripe-invoice-transfers"})


def generated_file_path(root: Path, uri: str, *, project_id: int | None = None) -> Path:
    """Reject aliases and staging before opening; online paths are project-namespaced.

    A caller-created Artifact row cannot adopt another project's file or an
    arbitrary server file. Legacy standalone paths retain local compatibility.
    """
    prefix = "/generated-assets/"
    if not uri.startswith(prefix):
        raise ValidationError("file is not managed by StackOS; use its owning local tool")
    relative = uri[len(prefix) :]
    parts = relative.split("/")
    if (
        not relative
        or any(p in {"", ".", ".."} for p in parts)
        or any(p.casefold() in PRIVATE_STAGING_ROOTS for p in parts)
        or any(c in relative for c in ("%", "\\", "\x00", "?", "#"))
    ):
        raise ValidationError("file reference is not an allowed generated asset")
    if project_id is not None and not (
        parts[:2] == ["projects", str(project_id)]
        or parts[:2] == ["action-outputs", f"project-{project_id}"]
    ):
        raise NotFoundError("project file not found")
    root = root.resolve()
    path = root.joinpath(*parts)
    # No filesystem alias may substitute another project's file or private staging.
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
        raise ValidationError("file aliases are not downloadable")
    try:
        path.resolve().relative_to(root)
    except ValueError:
        raise ValidationError("artifact path escaped generated assets") from None
    return path


@dataclass(frozen=True)
class StoredFile:
    path: Path
    project_id: int
    source: FileSource
    file_id: int
    content_type: str

    def descriptor(self) -> dict[str, Any]:
        digest = hashlib.sha256()
        size = 0
        with self.path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return {
            "project_id": self.project_id,
            "download_uri": (
                f"/api/v1/projects/{self.project_id}/files/{self.source}/{self.file_id}"
            ),
            "filename": self.path.name,
            "content_type": self.content_type,
            "bytes": size,
            "sha256": digest.hexdigest(),
        }


def resolve_file(
    session: Session,
    settings: Settings,
    *,
    project_id: int,
    source: FileSource,
    file_id: int,
) -> StoredFile:
    require_project(session, project_id)
    if source == "artifact":
        artifact = ArtifactRepository(session).get(file_id)
        if artifact.project_id != project_id:
            raise NotFoundError("project file not found")
        uri = artifact.uri
        row = session.get(Artifact, file_id)
        assert row is not None  # The repository just authorized this persisted row.
        metadata = row.metadata_json or {}
        content_type = artifact.mime_type or "application/octet-stream"
    else:
        call = session.get(ActionCall, file_id)
        if call is None or call.project_id != project_id:
            raise NotFoundError("project file not found")
        response = call.response_json or {}
        pointer = response.get("file")
        if response.get("output_mode") != "file" or not isinstance(pointer, dict):
            raise NotFoundError("action output file not found")
        metadata = pointer
        output_uri = metadata.get("uri")
        if not isinstance(output_uri, str):
            # Standalone legacy output pointers may have a local path. Never
            # open custom output-policy directories through the server API.
            raw_path = metadata.get("path")
            if not isinstance(raw_path, str):
                raise ValidationError("output is local-only and cannot be downloaded")
            try:
                relative = Path(raw_path).relative_to(settings.generated_assets_dir.resolve())
            except (TypeError, ValueError):
                raise ValidationError("output is local-only and cannot be downloaded") from None
            output_uri = "/generated-assets/" + relative.as_posix()
        uri = output_uri
        content_type = "application/json"
    path = generated_file_path(
        settings.generated_assets_dir,
        uri,
        project_id=project_id if settings.process_role == "server" else None,
    )
    metadata_path = metadata.get("absolute_path") or metadata.get("path")
    if isinstance(metadata_path, str) and Path(metadata_path).resolve() != path:
        raise ValidationError("file metadata path does not match generated asset URI")
    if not path.is_file():
        raise NotFoundError("project file not found")
    return StoredFile(path, project_id, source, file_id, content_type)
