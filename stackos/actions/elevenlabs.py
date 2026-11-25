"""Project generated-media adapter for native ElevenLabs file actions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from stackos_connectors.contracts import ConnectorFile, ConnectorResult

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.media_artifacts import artifact_path
from stackos.actions.package_bridge import PackageActionConnector, host_result
from stackos.artifacts.files import generated_output_path
from stackos.config import Settings
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.project_access import require_project
from stackos.repositories.resources import ArtifactRepository

_AUDIO_OPERATIONS = {"speech.generate", "voices.design"}
_MAX_AUDIO_BYTES = 1_000_000_000


class ElevenLabsActionConnector(PackageActionConnector):
    def __init__(self) -> None:
        super().__init__("elevenlabs")

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation in _AUDIO_OPERATIONS:
            request = replace(
                request, asset_dir=request.asset_dir or Settings().generated_assets_dir
            )
        if request.operation == "alignment.create":
            try:
                request = _alignment_request(request)
            except (ValidationError, NotFoundError, OSError):
                return [
                    ActionValidationIssue(
                        path="$.audio_artifact_id", message="project audio artifact is unavailable"
                    )
                ]
        return [
            ActionValidationIssue(path="$.audio_artifact_id", message=issue.message)
            if request.operation == "alignment.create" and issue.path == "$.mime_type"
            else issue
            for issue in super().validate(request)
        ]

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation == "alignment.create":
            try:
                request = _alignment_request(request)
            except (ValidationError, NotFoundError, OSError):
                raise ActionConnectorError("project audio artifact is unavailable") from None
        if request.operation not in _AUDIO_OPERATIONS:
            native = await self.execute_native(request)
            if request.operation == "alignment.create":
                output = dict(native.output_json)
                for key in ("words", "characters"):
                    if key in output:
                        output[key] = [
                            {
                                field: row[field]
                                for field in ("text", "start", "end", "loss")
                                if field in row
                            }
                            for row in output[key]
                        ]
                native = native.model_copy(update={"output_json": output})
            return host_result(native, action_ref=request.action_ref)
        if request.session is None or request.project_id <= 0:
            raise ActionConnectorError(
                "project artifact storage is required before generating audio"
            )
        root = (request.asset_dir or Settings().generated_assets_dir).resolve()
        project = require_project(request.session, request.project_id)
        output_dir = (
            generated_output_path(
                root, Path("elevenlabs"), session=request.session, project_id=request.project_id
            )
            if project.environment_id is not None
            else root
        )
        native = await self.execute_native(replace(request, asset_dir=output_dir))
        try:
            output = _register_audio(request, native, root, output_dir)
        except Exception:
            raise ActionConnectorError(
                "ElevenLabs returned audio but artifact registration failed. "
                "Inspect the original call before explicitly submitting again."
            ) from None
        return host_result(
            native.model_copy(update={"files": [], "output_json": output}),
            action_ref=request.action_ref,
        )


def _alignment_request(request: ActionConnectorRequest) -> ActionConnectorRequest:
    artifact_id = request.input_json.get("audio_artifact_id")
    if type(artifact_id) is not int or artifact_id <= 0 or request.session is None:
        raise ValidationError("audio_artifact_id requires a stored project audio artifact")
    artifact = ArtifactRepository(request.session).get(artifact_id)
    if artifact.project_id != request.project_id:
        raise NotFoundError("audio artifact not found in this project")
    if artifact.status not in {"draft", "approved"}:
        raise ValidationError("audio artifact is archived or superseded")
    if not artifact.uri.startswith("/generated-assets/"):
        raise ValidationError("audio artifact must reference managed generated assets")
    mime_type = (artifact.mime_type or "").split(";", 1)[0].strip().lower()
    root = request.asset_dir or Settings().generated_assets_dir
    path = artifact_path(
        root,
        artifact.uri,
        label="audio artifact",
        session=request.session,
        project_id=request.project_id,
    )
    if not 0 < path.stat().st_size < _MAX_AUDIO_BYTES:
        raise ValidationError("audio file must be nonempty and under 1 GB")
    return replace(
        request,
        input_json={
            "audio_path": str(path),
            "mime_type": mime_type,
            "text": request.input_json.get("text"),
        },
    )


def _register_audio(
    request: ActionConnectorRequest, native: ConnectorResult, root: Path, output_dir: Path
) -> dict:
    assert request.session is not None
    repository = ArtifactRepository(request.session)

    def save(descriptor: ConnectorFile, metadata: dict) -> int:
        path = Path(descriptor.path)
        if not path.resolve().is_relative_to(output_dir.resolve()):
            raise ValidationError("audio descriptor escaped its output directory")
        uri = "/generated-assets/" + path.relative_to(root).as_posix()
        artifact_path(root, uri, session=request.session, project_id=request.project_id)
        return repository.create_generated_file(
            asset_root=root,
            path=path,
            project_id=request.project_id,
            plugin_slug="utils",
            kind="audio",
            uri=uri,
            name=path.name,
            mime_type=descriptor.mime_type,
            size_bytes=path.stat().st_size,
            metadata_json=metadata,
            provenance_json={"source": "elevenlabs", "action_ref": request.action_ref},
        ).data.id

    output = dict(native.output_json)
    if request.operation == "speech.generate":
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
        (descriptor,) = native.files
        output["artifact_id"] = save(descriptor, {"output_format": output["output_format"]})
    else:
        output["previews"] = [dict(row) for row in output["previews"]]
        for preview in output["previews"]:
            if "file_index" in preview:
                index = preview["file_index"]
                preview["artifact_id"] = save(
                    native.files[index],
                    {"generated_voice_id": preview["generated_voice_id"], "file_index": index},
                )
    return output
