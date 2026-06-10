"""Reve image action connector."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.config import Settings
from stackos.integrations.reve_images import ReveImagesIntegration
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository


class ReveImagesActionConnector:
    """Decision-free adapter from utils Reve actions to the provider wrapper."""

    key = "reve"
    _ASPECT_RATIOS = ReveImagesIntegration.ASPECT_RATIOS
    _CREATE_VERSIONS = ReveImagesIntegration.CREATE_VERSIONS
    _EDIT_VERSIONS = ReveImagesIntegration.EDIT_VERSIONS
    _REMIX_VERSIONS = ReveImagesIntegration.REMIX_VERSIONS
    _PROMPT_MAX_CHARS = ReveImagesIntegration.PROMPT_MAX_CHARS
    _MAX_REMIX_REFS = 6

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "image.create":
                issues.extend(self._validate_prompt(payload, key="prompt"))
                issues.extend(self._validate_common(payload, versions=self._CREATE_VERSIONS))
            case "image.edit":
                issues.extend(self._validate_prompt(payload, key="edit_instruction"))
                issues.extend(self._validate_common(payload, versions=self._EDIT_VERSIONS))
                input_image_ref = payload.get("input_image_ref")
                if not isinstance(input_image_ref, str):
                    issues.append(
                        ActionValidationIssue(
                            path="$.input_image_ref",
                            message="input_image_ref is required for Reve image edit",
                            code="required",
                        )
                    )
                else:
                    issues.extend(
                        self._validate_reference_image_path(
                            request,
                            path="$.input_image_ref",
                            artifact_ref=input_image_ref,
                        )
                    )
            case "image.remix":
                issues.extend(self._validate_prompt(payload, key="prompt"))
                issues.extend(self._validate_common(payload, versions=self._REMIX_VERSIONS))
                refs = payload.get("input_image_refs")
                if (
                    not isinstance(refs, list)
                    or not refs
                    or not all(isinstance(ref, str) for ref in refs)
                ):
                    issues.append(
                        ActionValidationIssue(
                            path="$.input_image_refs",
                            message=(
                                "input_image_refs must be a non-empty list of generated asset refs"
                            ),
                            code="required",
                        )
                    )
                elif len(refs) > self._MAX_REMIX_REFS:
                    issues.append(
                        ActionValidationIssue(
                            path="$.input_image_refs",
                            message="Reve remix accepts at most 6 reference images",
                            code="range",
                        )
                    )
                else:
                    issues.extend(self._validate_remix_reference_images(request, refs))
            case _:
                issues.append(
                    ActionValidationIssue(
                        path="$.operation",
                        message=f"unsupported Reve operation {request.operation!r}",
                        code="unknown_operation",
                    )
                )
        return issues

    def _validate_reference_image_path(
        self,
        request: ActionConnectorRequest,
        *,
        path: str,
        artifact_ref: str,
    ) -> list[ActionValidationIssue]:
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        try:
            image_path = _artifact_path(asset_dir, artifact_ref)
            ReveImagesIntegration.ensure_image_preflight(image_path)
        except (IntegrationDownError, ValidationError) as exc:
            return [
                ActionValidationIssue(
                    path=path,
                    message=getattr(exc, "detail", str(exc)),
                    code="invalid_image_ref",
                )
            ]
        return []

    def _validate_remix_reference_images(
        self,
        request: ActionConnectorRequest,
        refs: list[str],
    ) -> list[ActionValidationIssue]:
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        paths: list[Path] = []
        for index, artifact_ref in enumerate(refs):
            try:
                paths.append(_artifact_path(asset_dir, artifact_ref))
            except ValidationError as exc:
                return [
                    ActionValidationIssue(
                        path=f"$.input_image_refs[{index}]",
                        message=exc.detail,
                        code="invalid_image_ref",
                    )
                ]
        try:
            ReveImagesIntegration.ensure_remix_image_preflight(paths)
        except IntegrationDownError as exc:
            return [
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message=exc.detail,
                    code="invalid_image_ref",
                )
            ]
        return []

    def _validate_prompt(self, payload: dict[str, Any], *, key: str) -> list[ActionValidationIssue]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            if len(value) <= self._PROMPT_MAX_CHARS:
                return []
            return [
                ActionValidationIssue(
                    path=f"$.{key}",
                    message=f"{key} must be at most {self._PROMPT_MAX_CHARS} characters",
                    code="range",
                )
            ]
        return [
            ActionValidationIssue(
                path=f"$.{key}",
                message=f"{key} is required",
                code="required",
            )
        ]

    def _validate_common(
        self,
        payload: dict[str, Any],
        *,
        versions: frozenset[str],
    ) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        aspect_ratio = payload.get("aspect_ratio")
        if aspect_ratio is not None and (
            not isinstance(aspect_ratio, str) or aspect_ratio not in self._ASPECT_RATIOS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.aspect_ratio",
                    message="aspect_ratio must be a supported Reve ratio",
                    code="enum_mismatch",
                )
            )
        version = payload.get("version", "latest")
        if not isinstance(version, str) or version not in versions:
            issues.append(
                ActionValidationIssue(
                    path="$.version",
                    message="version must be a supported Reve model version for this action",
                    code="enum_mismatch",
                )
            )
        test_time_scaling = payload.get("test_time_scaling", 1)
        if (
            not isinstance(test_time_scaling, int)
            or isinstance(test_time_scaling, bool)
            or test_time_scaling < 1
            or test_time_scaling > 15
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.test_time_scaling",
                    message="test_time_scaling must be an integer between 1 and 15",
                    code="range",
                )
            )
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        payload = request.input_json
        version = str(payload.get("version", "latest"))
        raw_scaling = payload.get("test_time_scaling", 1)
        test_time_scaling = (
            raw_scaling if isinstance(raw_scaling, int) and not isinstance(raw_scaling, bool) else 1
        )
        return _cost_usd_to_cents(
            ReveImagesIntegration.estimate_cost_usd(
                op=request.operation,
                version=version,
                test_time_scaling=test_time_scaling,
            )
        )

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("Reve action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        async with httpx.AsyncClient(timeout=180.0) as http:
            client = ReveImagesIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                asset_dir=asset_dir,
            )
            match request.operation:
                case "image.edit":
                    result = await client.edit_image(
                        edit_instruction=str(payload["edit_instruction"]),
                        reference_image_path=_artifact_path(
                            asset_dir,
                            str(payload["input_image_ref"]),
                        ),
                        aspect_ratio=(
                            str(payload["aspect_ratio"])
                            if isinstance(payload.get("aspect_ratio"), str)
                            else None
                        ),
                        version=str(payload.get("version", "latest")),
                        test_time_scaling=int(payload.get("test_time_scaling", 1)),
                    )
                case "image.remix":
                    result = await client.remix_image(
                        prompt=str(payload["prompt"]),
                        reference_image_paths=[
                            _artifact_path(asset_dir, str(ref))
                            for ref in payload["input_image_refs"]
                        ],
                        aspect_ratio=(
                            str(payload["aspect_ratio"])
                            if isinstance(payload.get("aspect_ratio"), str)
                            else None
                        ),
                        version=str(payload.get("version", "latest")),
                        test_time_scaling=int(payload.get("test_time_scaling", 1)),
                    )
                case _:
                    result = await client.create_image(
                        prompt=str(payload["prompt"]),
                        aspect_ratio=str(payload.get("aspect_ratio", "3:2")),
                        version=str(payload.get("version", "latest")),
                        test_time_scaling=int(payload.get("test_time_scaling", 1)),
                    )
        output_json = result.data if isinstance(result.data, dict) else {"data": result.data}
        output_json = _register_generated_image_artifacts(request, output_json)
        return ActionConnectorResult(
            output_json=output_json,
            metadata_json={"vendor": "reve"},
            cost_cents=_cost_usd_to_cents(result.cost_usd),
        )


def _artifact_path(asset_dir: Path, artifact_ref: str) -> Path:
    if artifact_ref.startswith("/generated-assets/"):
        relative = artifact_ref.removeprefix("/generated-assets/")
    elif artifact_ref.startswith("generated-assets/"):
        relative = artifact_ref.removeprefix("generated-assets/")
    else:
        relative = artifact_ref.lstrip("/")
    base = asset_dir.resolve()
    candidate = (base / relative).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValidationError("image refs must stay inside generated assets")
    if not candidate.is_file():
        raise ValidationError(f"image ref {artifact_ref!r} does not point to a file")
    return candidate


def _register_generated_image_artifacts(
    request: ActionConnectorRequest,
    output_json: dict[str, Any],
) -> dict[str, Any]:
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
        path = _artifact_path(asset_dir, uri)
        file_format = str(item.get("file_format") or path.suffix.removeprefix(".") or "png")
        artifact = repository.create(
            project_id=request.project_id,
            plugin_slug="utils",
            kind="image",
            uri=uri,
            name=path.name,
            mime_type=_mime_type(file_format),
            size_bytes=path.stat().st_size,
            metadata_json={
                "provider_key": "reve",
                "operation": request.operation,
                "model": item.get("source_model"),
                "request_id": item.get("request_id"),
                "file_format": file_format,
                "content_violation": item.get("content_violation"),
                "credits_used": item.get("credits_used"),
            },
            provenance_json={
                "source": "reve-action",
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


def _mime_type(file_format: str) -> str:
    if file_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    if file_format == "webp":
        return "image/webp"
    return "image/png"


def _cost_usd_to_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, round(cost_usd * 100))


__all__ = ["ReveImagesActionConnector"]
