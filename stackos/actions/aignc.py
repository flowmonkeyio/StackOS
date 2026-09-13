"""Explicit AIGNC actions; provider contract: docs/integration-contracts/aignc.md.

The caller supplies the model, messages and optional grounding intent. The
connector owns validation and transport dispatch, never model routing or tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.media_artifacts import artifact_path, register_generated_media_artifacts
from stackos.actions.provider_utils import connector_error_from_integration
from stackos.config import Settings
from stackos.integrations.aignc import AigncIntegration
from stackos.integrations.aignc_contract import (
    AIGNC_AUDIO_MODELS,
    AIGNC_GROUNDING_MODELS,
    AIGNC_TEXT_MODELS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    MAX_AUDIO_BYTES,
    MAX_MESSAGES,
    MAX_OUTPUT_TOKENS,
    MAX_READ_TIMEOUT_SECONDS,
    MAX_TEXT_LENGTH,
    MIN_READ_TIMEOUT_SECONDS,
)
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.resources import ArtifactRepository

_AUDIO_MIME_FORMATS = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/aac": "aac",
    "audio/x-aac": "aac",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/ogg": "ogg",
    "application/ogg": "ogg",
}
_OPERATIONS = frozenset({"models.list", "chat.complete", "image.generate", "audio.analyze"})


class AigncActionConnector:
    """One bounded provider request through the normal StackOS action executor."""

    key = "aignc"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation not in _OPERATIONS:
            return [
                ActionValidationIssue(
                    path="$.operation",
                    message="Unsupported AIGNC operation",
                    code="unsupported_operation",
                )
            ]
        if request.operation != "models.list":
            read_timeout = payload.get("read_timeout_seconds", DEFAULT_READ_TIMEOUT_SECONDS)
            if (
                type(read_timeout) is not int
                or not MIN_READ_TIMEOUT_SECONDS <= read_timeout <= MAX_READ_TIMEOUT_SECONDS
            ):
                issues.append(
                    ActionValidationIssue(
                        path="$.read_timeout_seconds",
                        message=(
                            f"Choose an integer from {MIN_READ_TIMEOUT_SECONDS} to "
                            f"{MAX_READ_TIMEOUT_SECONDS} seconds for the provider read timeout"
                        ),
                        code="out_of_range",
                    )
                )
            output_limit = payload.get(
                "output_limit",
                2048 if request.operation == "image.generate" else None,
            )
            if type(output_limit) is not int or not 1 <= output_limit <= MAX_OUTPUT_TOKENS:
                issues.append(
                    ActionValidationIssue(
                        path="$.output_limit",
                        message=f"Choose an integer from 1 to {MAX_OUTPUT_TOKENS}",
                        code="out_of_range",
                    )
                )
        if request.operation == "chat.complete":
            model = payload.get("model")
            if not isinstance(model, str) or model not in AIGNC_TEXT_MODELS:
                issues.append(
                    ActionValidationIssue(
                        path="$.model",
                        message="Choose a reviewed AIGNC text model",
                        code="model_mismatch",
                    )
                )
            if payload.get("google_search") is True and (
                not isinstance(model, str) or model not in AIGNC_GROUNDING_MODELS
            ):
                issues.append(
                    ActionValidationIssue(
                        path="$.google_search",
                        message="Google grounding requires an AIGNC Gemini text model",
                        code="model_mismatch",
                    )
                )
            messages = payload.get("messages")
            if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
                issues.append(
                    ActionValidationIssue(
                        path="$.messages",
                        message=f"Supply 1 to {MAX_MESSAGES} text messages",
                        code="out_of_range",
                    )
                )
            if isinstance(messages, list):
                for index, message in enumerate(messages):
                    if isinstance(message, dict):
                        _nonblank(message.get("content"), f"$.messages[{index}].content", issues)
        elif request.operation == "image.generate":
            _nonblank(payload.get("prompt"), "$.prompt", issues)
        elif request.operation == "audio.analyze":
            model = payload.get("model")
            if not isinstance(model, str) or model not in AIGNC_AUDIO_MODELS:
                issues.append(
                    ActionValidationIssue(
                        path="$.model",
                        message="Choose a documented AIGNC audio model",
                        code="model_mismatch",
                    )
                )
            _nonblank(payload.get("instruction"), "$.instruction", issues)
            try:
                _audio_artifact(request)
            except (ValidationError, NotFoundError, OSError, ValueError):
                issues.append(
                    ActionValidationIssue(
                        path="$.audio_artifact_id",
                        message=(
                            "Audio requires an active artifact in this project, a supported audio "
                            "MIME type and a nonempty managed file of at most 20 MiB "
                            "outside private staging"
                        ),
                        code="invalid_audio_artifact",
                    )
                )
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        # Operator explicitly excludes AIGNC pricing and budget tracking.
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("AIGNC action requires a resolved credential")
        issues = self.validate(request)
        if issues:
            raise ValidationError(issues[0].message)
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        audio = _audio_artifact(request) if request.operation == "audio.analyze" else None
        timeout = (
            httpx.Timeout(180.0)
            if request.operation == "models.list"
            else httpx.Timeout(
                read=payload.get("read_timeout_seconds", DEFAULT_READ_TIMEOUT_SECONDS),
                connect=15.0,
                write=180.0,
                pool=15.0,
            )
        )
        if request.operation != "models.list" and request.progress_callback is not None:
            request.progress_callback({"phase": "requesting", "operation": request.operation})
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http:
                client = AigncIntegration(
                    payload=request.credential.secret_payload,
                    project_id=request.project_id,
                    http=http,
                    asset_dir=asset_dir,
                )
                if request.operation == "models.list":
                    result = await client.models()
                elif request.operation == "chat.complete":
                    result = await client.chat_complete(
                        model=payload["model"],
                        messages=payload["messages"],
                        max_tokens=payload["output_limit"],
                        google_search=payload.get("google_search", False),
                    )
                elif request.operation == "image.generate":
                    result = await client.generate_image(
                        prompt=payload["prompt"],
                        max_tokens=payload.get("output_limit", 2048),
                    )
                else:
                    assert audio is not None
                    result = await client.analyze_audio(
                        model=payload["model"],
                        instruction=payload["instruction"],
                        audio_path=audio[0],
                        format=audio[1],
                        max_tokens=payload["output_limit"],
                    )
        except (IntegrationDownError, RateLimitedError) as exc:
            error = connector_error_from_integration(
                exc,
                provider=self.key,
                operation=request.operation,
            )
            for field in (
                "automatic_retry_count",
                "outcome_unknown",
                "retry_safe",
                "repair_guidance",
            ):
                if field in exc.data:
                    error.output_json[field] = exc.data[field]
            raise error from exc
        output = result.data
        if not isinstance(output, dict):
            raise ValidationError("AIGNC returned an invalid normalized response")
        if request.operation == "image.generate":
            if request.progress_callback is not None:
                request.progress_callback({"phase": "persisting", "operation": request.operation})
            output = register_generated_media_artifacts(
                request,
                output,
                kind="image",
                provider_key=self.key,
                source="aignc-action",
            )
        elif request.operation == "audio.analyze":
            output = {**output, "audio_artifact_id": payload["audio_artifact_id"]}
        return ActionConnectorResult(output_json=output, metadata_json={"vendor": self.key})


def _nonblank(value: Any, path: str, issues: list[ActionValidationIssue]) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        issues.append(
            ActionValidationIssue(
                path=path,
                message=f"Nonblank text of at most {MAX_TEXT_LENGTH} characters is required",
            )
        )


def _audio_artifact(request: ActionConnectorRequest) -> tuple[Path, str]:
    artifact_id = request.input_json.get("audio_artifact_id")
    if type(artifact_id) is not int or artifact_id <= 0 or request.session is None:
        raise ValidationError("audio_artifact_id requires a stored project artifact")
    artifact = ArtifactRepository(request.session).get(artifact_id)
    if artifact.project_id != request.project_id:
        raise NotFoundError("Audio artifact not found in this project")
    if artifact.status not in {"draft", "approved"}:
        raise ValidationError("Audio artifact is archived or superseded")
    if not artifact.uri.startswith("/generated-assets/"):
        raise ValidationError("Audio artifact must reference managed generated assets")
    mime_type = (artifact.mime_type or "").split(";", 1)[0].strip().lower()
    audio_format = _AUDIO_MIME_FORMATS.get(mime_type)
    if audio_format is None:
        raise ValidationError("Audio artifact has an unsupported MIME type")
    path = artifact_path(
        request.asset_dir or Settings().generated_assets_dir,
        artifact.uri,
        label="audio artifact",
    )
    if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        raise ValidationError("Audio file must be nonempty and at most 20 MiB")
    return path, audio_format


__all__ = ["AigncActionConnector"]
