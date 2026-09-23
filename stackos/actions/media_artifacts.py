"""Generated media artifact helpers for action connectors."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest
from stackos.config import Settings
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository


def artifact_path(asset_dir: Path, artifact_ref: str, *, label: str = "media ref") -> Path:
    """Resolve a generated-assets ref without allowing directory escape."""
    if artifact_ref.startswith("/generated-assets/"):
        relative = artifact_ref.removeprefix("/generated-assets/")
    elif artifact_ref.startswith("generated-assets/"):
        relative = artifact_ref.removeprefix("generated-assets/")
    else:
        relative = artifact_ref.lstrip("/")
    base = asset_dir.resolve()
    candidate = (base / relative).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValidationError(f"{label} must stay inside generated assets")
    # The same private staging boundary applies to provider inputs and HTTP
    # media serving. Resolve aliases as well as the lexical name; a registered
    # artifact must not turn a private evidence transfer into public media.
    relative_parts = Path(relative).parts
    candidate_parts = tuple(part.casefold() for part in candidate.parts)
    for staging_name in ("imap-transfers", "stripe-invoice-transfers"):
        staging_parts = tuple(part.casefold() for part in (base / staging_name).resolve().parts)
        if (relative_parts and relative_parts[0].casefold() == staging_name) or candidate_parts[
            : len(staging_parts)
        ] == staging_parts:
            raise ValidationError(f"{label} cannot reference private staging")
    if not candidate.is_file():
        raise ValidationError(f"{label} {artifact_ref!r} does not point to a file")
    return candidate


def register_generated_media_artifacts(
    request: ActionConnectorRequest,
    output_json: dict[str, Any],
    *,
    kind: str,
    provider_key: str,
    source: str,
    metadata_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Register generated-assets URLs returned by a connector as artifacts."""
    if request.session is None:
        return output_json
    items = output_json.get("data")
    if not isinstance(items, list):
        return output_json
    asset_dir = (request.asset_dir or Settings().generated_assets_dir).resolve()
    repository = ArtifactRepository(request.session)
    registered_items: list[Any] = []
    artifact_refs: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            registered_items.append(item)
            continue
        uri = item.get("url")
        if not isinstance(uri, str) or not uri.startswith("/generated-assets/"):
            registered_items.append(item)
            continue
        path = artifact_path(asset_dir, uri)
        file_format = str(item.get("file_format") or path.suffix.removeprefix(".") or "bin")
        metadata = {
            "provider_key": provider_key,
            "operation": request.operation,
            "model": item.get("source_model") or output_json.get("model"),
            "file_format": file_format,
        }
        if metadata_builder is not None:
            metadata.update(metadata_builder(item))
        artifact = repository.create(
            project_id=request.project_id,
            plugin_slug="utils",
            kind=kind,
            uri=uri,
            name=path.name,
            mime_type=media_mime_type(kind=kind, file_format=file_format),
            size_bytes=path.stat().st_size,
            metadata_json=metadata,
            provenance_json={
                "source": source,
                "action_ref": request.action_ref,
            },
        ).data
        clean = dict(item)
        clean["artifact_ref"] = uri
        clean["artifact_id"] = artifact.id
        registered_items.append(clean)
        artifact_refs.append(uri)
    if not artifact_refs:
        return output_json
    out = dict(output_json)
    out["data"] = registered_items
    out["artifact_refs"] = artifact_refs
    return out


def media_mime_type(*, kind: str, file_format: str) -> str:
    if kind == "video":
        return "video/mp4"
    if file_format == "png":
        return "image/png"
    if file_format == "webp":
        return "image/webp"
    if file_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    return "application/octet-stream"


def cost_usd_to_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, round(cost_usd * 100))


__all__ = [
    "artifact_path",
    "cost_usd_to_cents",
    "media_mime_type",
    "register_generated_media_artifacts",
]
