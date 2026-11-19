"""Project native ElevenLabs audio files into the owning project's artifacts."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from stackos_connectors.contracts import ConnectorFile, ConnectorResult

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.package_bridge import PackageActionConnector, host_result
from stackos.actions.provider_utils import (
    ELEVENLABS_AUDIO_OPERATIONS,
    JsonObject,
    elevenlabs_audio_receipt,
)
from stackos.config import Settings
from stackos.repositories.resources import ArtifactRepository


class ElevenLabsActionConnector(PackageActionConnector):
    def __init__(self) -> None:
        super().__init__("elevenlabs")

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation in ELEVENLABS_AUDIO_OPERATIONS:
            request = replace(
                request, asset_dir=_output_dir(_asset_root(request), request.project_id)
            )
        return super().validate(request)

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation not in ELEVENLABS_AUDIO_OPERATIONS:
            return await super().execute(request)
        if request.session is None or request.project_id <= 0:
            raise ActionConnectorError(
                "project artifact storage is required before generating audio"
            )

        try:
            root = _asset_root(request)
            output_dir = _output_dir(root, request.project_id)
            if output_dir.resolve() != output_dir:
                raise ValueError("output directory uses a filesystem alias")
            output_dir.mkdir(mode=0o700, parents=True)
        except (OSError, RuntimeError, ValueError):
            raise ActionConnectorError(
                "project audio output directory is unavailable",
                metadata_json={"provider_executed": False, "retry_safe": True},
            ) from None

        try:
            native = await self.execute_native(replace(request, asset_dir=output_dir))
            output = _project_audio(request, native, root, output_dir)
            return host_result(
                native.model_copy(update={"files": [], "output_json": output}),
                action_ref=request.action_ref,
            )
        finally:
            with suppress(OSError):
                output_dir.rmdir()


def _asset_root(request: ActionConnectorRequest) -> Path:
    return (request.asset_dir or Settings().generated_assets_dir).resolve()


def _output_dir(root: Path, project_id: int) -> Path:
    return root / "elevenlabs" / f"project-{project_id}" / uuid4().hex


def _project_audio(
    request: ActionConnectorRequest, native: ConnectorResult, root: Path, output_dir: Path
) -> JsonObject:
    assert request.session is not None
    repository = ArtifactRepository(request.session)

    def save(descriptor: ConnectorFile, metadata: JsonObject) -> int:
        path = Path(descriptor.path)
        if path.parent != output_dir or not descriptor.mime_type:
            raise ValueError("invalid native audio file descriptor")
        return repository.create_generated_file(
            asset_root=root,
            path=path,
            project_id=request.project_id,
            plugin_slug=request.plugin_slug,
            kind="audio",
            uri="/generated-assets/" + path.relative_to(root).as_posix(),
            name=path.name,
            mime_type=descriptor.mime_type,
            size_bytes=path.lstat().st_size,
            metadata_json=metadata,
            provenance_json={"source": "elevenlabs", "action_ref": request.action_ref},
        ).data.id

    output = dict(native.output_json)
    if request.operation == "speech.generate":
        # Keep the native timing arrays and nulls. Unknown fields inside a provider
        # alignment are outside the host contract and may contain inline audio.
        for name in ("alignment", "normalized_alignment"):
            alignment = output.get(name)
            if alignment is not None:
                output[name] = {
                    field: alignment[field]
                    for field in (
                        "characters",
                        "character_start_times_seconds",
                        "character_end_times_seconds",
                    )
                }
        try:
            (descriptor,) = native.files
            output["artifact_id"] = save(descriptor, {"output_format": output["output_format"]})
        except Exception:
            raise _storage_failure(native, output) from None
        return output

    previews = [dict(preview) for preview in output["previews"]]
    output["previews"] = previews
    storage_failed = False
    for preview in previews:
        if "file_index" not in preview:
            continue
        index = preview["file_index"]
        try:
            preview["artifact_id"] = save(
                native.files[index],
                {"generated_voice_id": preview["generated_voice_id"], "file_index": index},
            )
        except Exception:
            request.session.rollback()
            preview.pop("file_index")
            preview["reason_code"] = "audio_persistence_failed"
            storage_failed = True
    if storage_failed:
        output["status"] = "partial" if any("artifact_id" in row for row in previews) else "failed"
        raise _storage_failure(native, output)
    return output


def _storage_failure(native: ConnectorResult, output: JsonObject) -> ActionConnectorError:
    return ActionConnectorError(
        "ElevenLabs generated audio but project artifact storage failed. "
        "Inspect the original call before retrying.",
        output_json={"output_persistence_failed": True, **elevenlabs_audio_receipt(output)},
        metadata_json={
            **(native.metadata_json or {}),
            "provider_executed": True,
            "outcome_unknown": False,
            "retry_safe": False,
            "charge_possible": True,
        },
    )
