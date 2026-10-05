"""Generated media artifact helpers for action connectors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from stackos_connectors import CallOptions, ConnectorClient
from stackos_connectors.catalog import load_registry
from stackos_connectors.shared.base import IntegrationCallResult

from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.package_bridge import PackageActionConnector
from stackos.config import Settings
from stackos.integrations._rate_limit import get_bucket
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository


async def execute_media_native(
    request: ActionConnectorRequest,
    data: dict[str, Any],
    *,
    http: httpx.AsyncClient,
    connector: str,
    output_subdir: str,
    qps: float,
    pricing: Callable[[str, dict[str, Any], dict[str, Any]], float] | None = None,
    caller_limits: dict[str, int] | None = None,
) -> IntegrationCallResult:
    """Resolve host choices, execute one named package action, and project files."""

    def plain(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, list):
            return [plain(item) for item in value]
        return value

    prepared = {key: plain(value) for key, value in data.items() if value is not None}
    package_directory = connector.replace("-", "_")
    asset_root = (request.asset_dir or Settings().generated_assets_dir).resolve()
    output_dir = asset_root / output_subdir
    client = ConnectorClient(registry=load_registry(f"connectors/{package_directory}/catalog.json"))
    bridge = PackageActionConnector(
        connector,
        client=client,
        options=CallOptions(
            http=http,
            rate_limiter=get_bucket(project_id=request.project_id, kind=output_subdir, qps=qps),
        ),
    )
    neutral_request = replace(
        request,
        input_json=prepared,
        asset_dir=output_dir,
        provider_context_json={**request.provider_context_json, **(caller_limits or {})},
    )
    try:
        result = await bridge.execute_native(neutral_request)
    except ActionConnectorError as exc:
        if connector == "aignc":
            for key in (
                "automatic_retry_count",
                "outcome_unknown",
                "retry_safe",
                "repair_guidance",
            ):
                if key in exc.metadata_json:
                    exc.output_json[key] = exc.metadata_json[key]
        raise
    try:
        by_path = {}
        for descriptor in result.files:
            path = Path(descriptor.path).resolve()
            if not path.is_relative_to(output_dir.resolve()) or not path.is_file():
                raise ValueError("connector file is outside its output directory or missing")
            by_path[descriptor.path] = (
                "/generated-assets/" + path.relative_to(asset_root).as_posix()
            )
        output = dict(result.output_json)
        if isinstance(output.get("data"), list):
            output["data"] = [
                {
                    **{key: value for key, value in item.items() if key != "path"},
                    "url": by_path[item["path"]],
                }
                if isinstance(item, dict) and item.get("path") in by_path
                else item
                for item in output["data"]
            ]
        cost_usd = (
            pricing(request.operation, data, output)
            if pricing is not None
            else result.cost_cents / 100
        )
        return IntegrationCallResult(
            data=output, cost_usd=cost_usd, duration_ms=0, metadata=result.metadata_json
        )
    except Exception:
        raise ActionConnectorError(
            "generated media host projection failed",
            output_json=result.output_json,
            metadata_json={
                **(result.metadata_json or {}),
                "provider_executed": True,
                "retry_safe": False,
                "outcome_unknown": False,
            },
        ) from None


def media_projection_failure(exc: Exception, result: IntegrationCallResult) -> ActionConnectorError:
    """A successful native call stays recorded when host artifact/progress work fails."""
    if isinstance(exc, ActionConnectorError):
        return exc
    return ActionConnectorError(
        "generated media artifact projection failed",
        output_json=result.data if isinstance(result.data, dict) else {"data": result.data},
        metadata_json={
            **(result.metadata or {}),
            "provider_executed": True,
            "retry_safe": False,
            "outcome_unknown": False,
        },
    )


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
