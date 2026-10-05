"""Prepare authorized host files and formatted text for the native content codec."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from stackos_connectors.connectors.telegram import payloads as native_payloads

from stackos.actions.media_artifacts import artifact_path
from stackos.actions.telegram_schema import (
    Format,
    TelegramButton,
    TelegramCaptionMedia,
    TelegramContent,
    TelegramFile,
    TelegramOptions,
    TelegramTopic,
    TelegramVoice,
    parse_telegram_file_ref,
)
from stackos.repositories.base import ValidationError


class TelegramRequester(Protocol):
    async def _request(self, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...


async def formatted_text(client: TelegramRequester, text: str, format: Format) -> dict[str, Any]:
    if format == "plain" or not text:
        return {"@type": "formattedText", "text": text, "entities": []}
    parse_mode: dict[str, Any] = {"@type": "textParseModeHTML"}
    if format == "markdown":
        parse_mode = {"@type": "textParseModeMarkdown", "version": 2}
    parsed = await client._request(
        {"@type": "parseTextEntities", "text": text, "parse_mode": parse_mode}
    )
    if parsed.get("@type") != "formattedText":
        raise ValidationError("Telegram could not parse the requested text format")
    return {"@type": "formattedText", "text": parsed["text"], "entities": parsed["entities"]}


def input_file(file: TelegramFile, asset_dir: Path | None) -> dict[str, Any]:
    if file.file_ref is not None:
        _credential_ref, file_id = parse_telegram_file_ref(file.file_ref)
        return {"@type": "inputFileId", "id": file_id}
    if file.url is not None:
        # The pinned FileManager::from_persistent_id also accepts HTTP(S) URLs.
        return {"@type": "inputFileRemote", "id": file.url}
    if asset_dir is None or file.artifact_ref is None:
        raise ValidationError("Telegram upload requires a generated asset reference")
    path = artifact_path(asset_dir, file.artifact_ref, label="Telegram upload")
    return {"@type": "inputFileLocal", "path": str(path)}


def reply_markup(rows: list[list[TelegramButton]]) -> dict[str, Any] | None:
    return native_payloads.reply_markup([[button.model_dump() for button in row] for row in rows])


def topic(value: TelegramTopic | None) -> dict[str, Any] | None:
    return native_payloads.topic(value.model_dump() if value is not None else None)


def send_options(options: TelegramOptions, *, sending_id: int) -> dict[str, Any]:
    return native_payloads.send_options(
        {
            "suggested_post_info": None,
            **options.model_dump(),
            "from_background": True,
            "update_order_of_installed_sticker_sets": False,
            "scheduling_state": None,
            "only_preview": False,
        },
        sending_id=sending_id,
    )


async def message_content(
    client: TelegramRequester, content: TelegramContent, *, asset_dir: Path | None
) -> dict[str, Any]:
    data = content.model_dump()
    for key in ("file", "thumbnail", "cover"):
        value = getattr(content, key, None)
        if value is not None:
            data[key] = input_file(value, asset_dir)
    if content.kind == "text":
        data["text"] = await formatted_text(client, content.text, content.format)
    if isinstance(content, TelegramCaptionMedia | TelegramVoice):
        data["caption"] = await formatted_text(client, content.caption, content.format)
    if content.kind == "poll":
        data["question"] = await formatted_text(client, content.question, "plain")
        data["options"] = [
            await formatted_text(client, value, "plain") for value in content.options
        ]
        data["explanation"] = await formatted_text(client, content.explanation, "plain")
    return native_payloads.message_content(data)
