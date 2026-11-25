"""Artifact retention invariant for sealed durable action payloads."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from stackos.actions.media_artifacts import artifact_path
from stackos.config import Settings
from stackos.db.models import Artifact, DurableActionArtifact
from stackos.repositories.base import ValidationError

_ACTIVE_ARTIFACT_STATUSES = frozenset({"draft", "approved"})


def attach_durable_action_artifact_pins(
    session: Session,
    *,
    project_id: int,
    job_id: int,
    items: list[dict[str, Any]],
    asset_dir: Path | None,
) -> None:
    """Pin each local artifact in a sealed job within the caller's transaction.

    The durable repository owns the call site because it owns the immutable
    item snapshot and its commit.  This helper only derives explicit
    ``artifact_ref`` values; it does not infer provider media or copy files.
    """

    refs = _artifact_refs(items)
    if not refs:
        return
    artifacts = list(
        session.exec(
            select(Artifact).where(
                col(Artifact.project_id) == project_id,
                col(Artifact.uri).in_(refs),
            )
        ).all()
    )
    active_by_ref = {
        artifact.uri: artifact
        for artifact in artifacts
        if artifact.status in _ACTIVE_ARTIFACT_STATUSES and artifact.id is not None
    }
    missing = sorted(refs - active_by_ref.keys())
    if missing:
        raise ValidationError(
            "durable delivery artifact is missing, inactive, or belongs to another project",
            data={"artifact_refs": missing},
        )
    for artifact_ref in sorted(refs):
        artifact = active_by_ref[artifact_ref]
        assert artifact.id is not None
        path = artifact_path(
            (asset_dir or Settings().generated_assets_dir).resolve(),
            artifact.uri,
            label="durable delivery artifact",
        )
        session.add(
            DurableActionArtifact(
                job_id=job_id,
                artifact_id=artifact.id,
                artifact_uri=artifact.uri,
                content_sha256=_sha256(path),
            )
        )


def validate_durable_action_artifact_pins(
    session: Session,
    *,
    project_id: int,
    job_id: int,
    asset_dir: Path | None,
) -> None:
    """Require sealed media still resolves to the exact bytes before an effect."""

    pins = list(
        session.exec(
            select(DurableActionArtifact).where(col(DurableActionArtifact.job_id) == job_id)
        ).all()
    )
    if not pins:
        return
    base = (asset_dir or Settings().generated_assets_dir).resolve()
    for pin in pins:
        artifact = session.get(Artifact, pin.artifact_id)
        if (
            artifact is None
            or artifact.project_id != project_id
            or artifact.status not in _ACTIVE_ARTIFACT_STATUSES
            or artifact.uri != pin.artifact_uri
        ):
            raise ValidationError(
                "durable delivery artifact changed or is no longer active",
                data={"artifact_id": pin.artifact_id, "artifact_ref": pin.artifact_uri},
            )
        path = artifact_path(base, pin.artifact_uri, label="durable delivery artifact")
        if _sha256(path) != pin.content_sha256:
            raise ValidationError(
                "durable delivery artifact bytes changed after the job was sealed",
                data={"artifact_id": pin.artifact_id, "artifact_ref": pin.artifact_uri},
            )


def _artifact_refs(value: Any) -> set[str]:
    refs: set[str] = set()

    def visit(current: Any) -> None:
        if isinstance(current, list):
            for item in current:
                visit(item)
            return
        if not isinstance(current, dict):
            return
        artifact_ref = current.get("artifact_ref")
        if isinstance(artifact_ref, str) and artifact_ref:
            refs.add(artifact_ref)
        for item in current.values():
            visit(item)

    visit(value)
    return refs


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "attach_durable_action_artifact_pins",
    "validate_durable_action_artifact_pins",
]
