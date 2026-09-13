"""AIGNC's supplied OpenAI-compatible transport contract.

Endpoint evidence and deliberate limits: docs/integration-contracts/aignc.md.
Each method performs one explicit request; the calling agent owns model choice
and interpretation. Provider pricing is deliberately excluded from this wrapper.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

import httpx

from stackos.artifacts.redaction import redact_secrets
from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations._media import write_generated_media
from stackos.integrations.aignc_contract import (
    AIGNC_AUDIO_FORMATS,
    AIGNC_AUDIO_MODELS,
    AIGNC_GROUNDING_MODELS,
    AIGNC_IMAGE_MODEL,
    AIGNC_TEXT_MODELS,
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    MAX_MESSAGES,
    MAX_OUTPUT_TOKENS,
    MAX_RESPONSE_BYTES,
    MAX_TEXT_LENGTH,
)
from stackos.mcp.errors import IntegrationDownError, RateLimitedError, ValidationError
from stackos.secret_refs import redact_secret_values


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for source, target in (
        ("prompt_tokens", "prompt_count"),
        ("completion_tokens", "completion_count"),
        ("total_tokens", "total_count"),
        ("google_searches", "google_searches"),
    ):
        count = value.get(source)
        if type(count) is int and count >= 0:
            result[target] = count
    for parent, source, target in (
        ("completion_tokens_details", "reasoning_tokens", "reasoning_count"),
        ("prompt_tokens_details", "cached_tokens", "cached_count"),
    ):
        details = value.get(parent)
        count = details.get(source) if isinstance(details, dict) else None
        if type(count) is int and count >= 0:
            result[target] = count
    return result


def _grounding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    queries = value.get("webSearchQueries")
    if isinstance(queries, list):
        result["webSearchQueries"] = [query for query in queries if isinstance(query, str)]
    chunks = value.get("groundingChunks")
    if isinstance(chunks, list):
        # Supports address this array by index; omitted chunk data must not
        # shift a later source into another source's citation position.
        result["groundingChunks"] = [
            {
                "web": {
                    key: item["web"][key]
                    for key in ("uri", "title")
                    if isinstance(item["web"].get(key), str)
                }
            }
            if isinstance(item, dict) and isinstance(item.get("web"), dict)
            else {}
            for item in chunks
        ]
    supports = value.get("groundingSupports")
    if isinstance(supports, list):
        clean_supports: list[dict[str, Any]] = []
        for support in supports:
            if not isinstance(support, dict):
                continue
            segment = support.get("segment")
            indices = support.get("groundingChunkIndices")
            if not isinstance(segment, dict) or not isinstance(indices, list):
                continue
            clean_segment: dict[str, Any] = {
                key: segment[key]
                for key in ("startIndex", "endIndex")
                if type(segment.get(key)) is int and segment[key] >= 0
            }
            if (
                "startIndex" in clean_segment
                and "endIndex" in clean_segment
                and clean_segment["endIndex"] < clean_segment["startIndex"]
            ):
                clean_segment = {}
            if isinstance(segment.get("text"), str):
                clean_segment["text"] = segment["text"]
            clean_indices = [
                index
                for index in indices
                if type(index) is int
                and index >= 0
                and (not isinstance(chunks, list) or index < len(chunks))
            ]
            if clean_indices and clean_segment:
                clean_supports.append(
                    {
                        "segment": clean_segment,
                        "groundingChunkIndices": clean_indices,
                    }
                )
        result["groundingSupports"] = clean_supports
    return result


class AigncIntegration(BaseIntegration):
    """Fixed-endpoint, stateless AIGNC requests using daemon-held bearer auth."""

    kind = "aignc"
    vendor = "aignc"
    default_qps = 1.0  # Conservative StackOS default; provider quota is undocumented.
    BASE_URL = "https://cli-api.f2nd.com/v1"

    def __init__(
        self,
        *,
        asset_dir: Path | None = None,
        asset_url_prefix: str = "/generated-assets",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = self.payload.decode("utf-8").strip()
        if not self._api_key or any(ord(char) < 32 or ord(char) == 127 for char in self._api_key):
            raise ValidationError(
                "AIGNC requires a nonempty bearer credential without control characters"
            )
        self._asset_dir = asset_dir
        self._asset_url_prefix = asset_url_prefix.rstrip("/")

    def _clean(self, value: Any) -> Any:
        return redact_secrets(redact_secret_values(value, (self._api_key,)))

    def _record_call(
        self,
        *,
        op: str,
        request: Any,
        response: Any,
        duration_ms: int,
        error: str | None,
        cost_cents: int,
    ) -> None:
        super()._record_call(
            op=op,
            request=self._clean(request),
            response=self._clean(response),
            duration_ms=duration_ms,
            error=self._clean(error),
            cost_cents=cost_cents,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}

    @staticmethod
    def _response_metadata(response: httpx.Response) -> dict[str, Any]:
        metadata: dict[str, Any] = {"status_code": response.status_code}
        for header, key in (
            ("cf-aig-log-id", "provider_request_id"),
            ("retry-after", "retry_after"),
        ):
            value = response.headers.get(header)
            if value:
                metadata[key] = redact_secrets(value)
        return metadata

    def _extract_response_metadata(
        self, op: str, *, request: Any, response: Any, http_response: httpx.Response
    ) -> dict[str, Any]:
        del op, request, response
        return self._clean(self._response_metadata(http_response))

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> dict[str, Any]:
        try:
            raw = response.json()
        except ValueError:
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("error"), dict):
            raw = raw["error"]
        error = {
            key: raw[key]
            for key in ("message", "type", "code", "param")
            if isinstance(raw, dict) and isinstance(raw.get(key), str | int)
        }
        if not error:
            error["message"] = "AIGNC returned an undocumented error response"
        error.update(cls._response_metadata(response))
        return redact_secrets(error)

    def _failure(
        self,
        *,
        op: str,
        status: int | None,
        message: str,
        provider_error: dict[str, Any] | None = None,
    ) -> IntegrationDownError:
        return IntegrationDownError(
            message,
            data={
                "vendor": self.vendor,
                "op": op,
                "status": status,
                "provider_error": provider_error or {"message": message},
                "automatic_retry_count": 0,
                "outcome_unknown": op != "models.list",
                "retry_safe": op == "models.list",
                "repair_guidance": (
                    "Inspect this request's provider diagnostics before deciding to retry."
                ),
            },
        )

    async def _request_with_retry(
        self, method: str, url: str, *, op: str, **kwargs: Any
    ) -> httpx.Response:
        # Normalize before BaseIntegration audits the response. In particular,
        # an image's large content must never become a raw audit preview.
        request = kwargs.get("json") or {}
        audio_values = tuple(
            part["input_audio"]["data"]
            for message in request.get("messages", [])
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if isinstance(part, dict)
            and isinstance(part.get("input_audio"), dict)
            and isinstance(part["input_audio"].get("data"), str)
        )
        try:
            response = await super()._request_with_retry(method, url, op=op, **kwargs)
        except (IntegrationDownError, RateLimitedError) as exc:
            data = self._clean(redact_secret_values(exc.data, audio_values))
            provider_error = data.get("provider_error")
            if isinstance(provider_error, dict):
                data["provider_error"] = {
                    key: value[:1000] if isinstance(value, str) else value
                    for key, value in provider_error.items()
                }
            status = data.get("status")
            data.update(
                {
                    "automatic_retry_count": 0,
                    "retry_safe": method == "GET",
                    "outcome_unknown": method != "GET" and (status is None or status >= 500),
                    "repair_guidance": "Inspect the provider error and request ID before retrying.",
                }
            )
            raise type(exc)(f"AIGNC {op} request failed", data=data) from None
        metadata = self._clean(self._response_metadata(response))
        if not 200 <= response.status_code < 300:
            raise self._failure(
                op=op,
                status=response.status_code,
                message="AIGNC returned an unexpected HTTP status",
                provider_error=metadata,
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise self._failure(
                op=op,
                status=response.status_code,
                message="AIGNC response exceeds the StackOS 30 MiB limit",
                provider_error=metadata,
            )
        try:
            raw = response.json()
        except ValueError:
            raw = None
        if not isinstance(raw, dict) or "error" in raw:
            raise self._failure(
                op=op,
                status=response.status_code,
                message="AIGNC returned an invalid response envelope",
                provider_error=self._clean(
                    redact_secret_values(self._provider_error(response), audio_values)
                ),
            )
        try:
            clean = self._normalize(raw, op=op, request=request)
        except (ValueError, OSError) as exc:
            raise self._failure(
                op=op, status=response.status_code, message=str(exc), provider_error=metadata
            ) from None
        if "provider_request_id" in metadata:
            clean["provider_request_id"] = metadata["provider_request_id"]
        return httpx.Response(
            response.status_code,
            json=self._clean(redact_secret_values(clean, audio_values)),
            request=response.request,
            headers={
                key: response.headers[key]
                for key in ("cf-aig-log-id", "retry-after")
                if key in response.headers
            },
        )

    def _normalize(
        self, raw: dict[str, Any], *, op: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        if op == "models.list":
            rows = raw.get("data")
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                for row in rows
            ):
                raise ValueError("AIGNC returned an invalid models list")
            return {"data": [{"id": row["id"]} for row in rows]}
        choices = raw.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("AIGNC returned an invalid completion choice")
        choice = choices[0]
        message = choice.get("message")
        if (
            not isinstance(message, dict)
            or (
                op != "image.generate"
                and (not isinstance(message.get("content"), str) or message.get("images"))
            )
            or message.get("tool_calls")
            or message.get("function_call")
            or not isinstance(choice.get("finish_reason"), str)
            or not isinstance(raw.get("model"), str)
        ):
            raise ValueError("AIGNC returned an unsupported completion message")
        data: dict[str, Any] = {
            "requested_model": request["model"],
            "returned_model": raw["model"],
            "finish_reason": choice["finish_reason"],
            "usage": _usage(raw.get("usage")),
        }
        if isinstance(raw.get("id"), str):
            data["id"] = raw["id"]
        if op == "image.generate":
            encoded = self._image_base64(message)
            if not encoded or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
                raise ValueError("AIGNC image exceeds the StackOS 20 MiB limit or is empty")
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("AIGNC returned invalid base64 image data") from None
            if (
                len(image_bytes) > MAX_IMAGE_BYTES
                or not image_bytes.startswith(b"\xff\xd8\xff")
                or not image_bytes.endswith(b"\xff\xd9")
            ):
                raise ValueError("AIGNC returned invalid JPEG image data")
            assert self._asset_dir is not None
            try:
                image = write_generated_media(
                    image_bytes,
                    asset_dir=self._asset_dir,
                    asset_url_prefix=self._asset_url_prefix,
                    subdir="aignc",
                    prefix="aignc",
                    ext="jpg",
                )
            except OSError:
                raise ValueError("AIGNC image was generated but local persistence failed") from None
            data["data"] = [{**image, "source_model": request["model"]}]
        else:
            data["text"] = message["content"]
            if isinstance(raw.get("grounding_metadata"), dict):
                data["grounding_metadata"] = _grounding(raw["grounding_metadata"])
        return data

    @staticmethod
    def _image_base64(message: dict[str, Any]) -> str:
        # The supplied guide uses content; live replies use one inline image
        # with null content. Never select between multiple image payloads.
        if "images" not in message:
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError("AIGNC returned an unsupported image message")
            return content
        images = message["images"]
        if (
            message.get("content") not in (None, "")
            or not isinstance(images, list)
            or len(images) != 1
        ):
            raise ValueError("AIGNC must return exactly one image without accompanying content")
        image = images[0]
        if (
            not isinstance(image, dict)
            or image.get("type") != "image_url"
            or not isinstance(image.get("image_url"), dict)
        ):
            raise ValueError("AIGNC returned an unsupported image entry")
        url = image["image_url"].get("url")
        prefix = "data:image/jpeg;base64,"
        if not isinstance(url, str) or not url.startswith(prefix):
            raise ValueError("AIGNC must return inline base64 JPEG image data")
        return url[len(prefix) :]

    @staticmethod
    def _text(value: Any, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH:
            raise ValidationError(
                f"{field} must be nonblank text of at most {MAX_TEXT_LENGTH} characters"
            )
        return value

    @staticmethod
    def _max_tokens(value: Any) -> int:
        if type(value) is not int or not 1 <= value <= MAX_OUTPUT_TOKENS:
            raise ValidationError(f"max_tokens must be an integer from 1 to {MAX_OUTPUT_TOKENS}")
        return value

    async def models(self) -> IntegrationCallResult:
        """GET /models; only availability is learned, never capability or routing."""
        return await self.call(
            op="models.list",
            method="GET",
            url=f"{self.BASE_URL}/models",
            headers=self._headers(),
            max_retries=0,
        )

    async def chat_complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        google_search: bool = False,
    ) -> IntegrationCallResult:
        """POST /chat/completions with text messages and optional documented grounding."""
        if model not in AIGNC_TEXT_MODELS:
            raise ValidationError("model must be a reviewed AIGNC text model")
        if type(google_search) is not bool or (
            google_search and model not in AIGNC_GROUNDING_MODELS
        ):
            raise ValidationError(
                "google_search requires a reviewed Gemini text model and boolean flag"
            )
        if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
            raise ValidationError(f"messages must contain 1 to {MAX_MESSAGES} text messages")
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message.get("role") not in {"system", "user", "assistant"}
            ):
                raise ValidationError(
                    "messages require only role and text content; tool messages are unsupported"
                )
            self._text(message["content"], field="message content")
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": self._max_tokens(max_tokens),
            "stream": False,
        }
        if google_search:
            body["tools"] = [{"google_search": {}}]
        return await self.call(
            op="chat.complete",
            url=f"{self.BASE_URL}/chat/completions",
            headers=self._headers(),
            json_body=body,
            request_log_body={
                "model": model,
                "messages": messages,
                "output_limit": max_tokens,
                "google_search": google_search,
            },
            max_retries=0,
        )

    async def generate_image(self, *, prompt: str, max_tokens: int = 2048) -> IntegrationCallResult:
        """POST /chat/completions with the documented image model; persist its JPEG."""
        if self._asset_dir is None:
            raise ValidationError(
                "AIGNC image generation requires configured generated-assets storage"
            )
        body = {
            "model": AIGNC_IMAGE_MODEL,
            "messages": [{"role": "user", "content": self._text(prompt, field="prompt")}],
            "max_tokens": self._max_tokens(max_tokens),
            "stream": False,
        }
        return await self.call(
            op="image.generate",
            url=f"{self.BASE_URL}/chat/completions",
            headers=self._headers(),
            json_body=body,
            request_log_body={
                "model": AIGNC_IMAGE_MODEL,
                "prompt": prompt,
                "output_limit": max_tokens,
            },
            max_retries=0,
        )

    async def analyze_audio(
        self, *, model: str, instruction: str, audio_path: Path, format: str, max_tokens: int
    ) -> IntegrationCallResult:
        """POST /chat/completions; action context resolves a project-owned audio artifact."""
        if model not in AIGNC_AUDIO_MODELS:
            raise ValidationError("model must be an explicitly documented AIGNC audio model")
        instruction = self._text(instruction, field="instruction")
        max_tokens = self._max_tokens(max_tokens)
        if format not in AIGNC_AUDIO_FORMATS:
            raise ValidationError("audio format must be wav, mp3, aac, flac, or ogg")
        try:
            if not audio_path.is_file() or not 0 < audio_path.stat().st_size <= MAX_AUDIO_BYTES:
                raise ValidationError(
                    "audio must be a nonempty regular file within the StackOS 20 MiB limit"
                )
            with audio_path.open("rb") as stream:
                raw = stream.read(MAX_AUDIO_BYTES + 1)
        except OSError:
            raise ValidationError("audio artifact cannot be read") from None
        if len(raw) > MAX_AUDIO_BYTES:
            raise ValidationError("audio exceeds the StackOS 20 MiB limit")
        valid = {
            "wav": raw.startswith(b"RIFF") and raw[8:12] == b"WAVE",
            "mp3": raw.startswith(b"ID3")
            or (len(raw) > 1 and raw[0] == 255 and raw[1] & 0xE6 == 0xE2),
            "aac": len(raw) > 1 and raw[0] == 255 and raw[1] & 0xF6 == 0xF0,
            "flac": raw.startswith(b"fLaC"),
            "ogg": raw.startswith(b"OggS"),
        }[format]
        if not valid:
            raise ValidationError("audio bytes do not match the declared format")
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64.b64encode(raw).decode("ascii"),
                                "format": format,
                            },
                        },
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }
        return await self.call(
            op="audio.analyze",
            url=f"{self.BASE_URL}/chat/completions",
            headers=self._headers(),
            json_body=body,
            max_retries=0,
            request_log_body={
                "model": model,
                "instruction": instruction,
                "format": format,
                "audio_byte_count": len(raw),
                "output_limit": max_tokens,
            },
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.models()
        return {
            "ok": True,
            "vendor": self.vendor,
            "model_count": len(result.data["data"]),
            "generation_verified": False,
        }


__all__ = ["AigncIntegration"]
