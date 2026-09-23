"""Typed, decision-free serialization to the pinned TDLib messaging API.

https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Protocol

from stackos.actions.media_artifacts import artifact_path
from stackos.actions.telegram_schema import (
    Format,
    TelegramAnimation,
    TelegramAudio,
    TelegramButton,
    TelegramContact,
    TelegramContent,
    TelegramDice,
    TelegramDocument,
    TelegramFile,
    TelegramLocation,
    TelegramOptions,
    TelegramPhoto,
    TelegramPoll,
    TelegramSticker,
    TelegramText,
    TelegramTopic,
    TelegramVenue,
    TelegramVideo,
    TelegramVideoNote,
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
    if not rows:
        return None
    return {
        "@type": "replyMarkupInlineKeyboard",
        "rows": [
            [
                {
                    "@type": "inlineKeyboardButton",
                    "text": button.text,
                    "style": {
                        "@type": {
                            "default": "buttonStyleDefault",
                            "primary": "buttonStylePrimary",
                            "danger": "buttonStyleDanger",
                            "success": "buttonStyleSuccess",
                            "link": "buttonStyleLink",
                        }[button.style]
                    },
                    "type": (
                        {"@type": "inlineKeyboardButtonTypeUrl", "url": button.url}
                        if button.url is not None
                        else {
                            "@type": "inlineKeyboardButtonTypeCallback",
                            "data": base64.b64encode(
                                (button.callback_data or "").encode()
                            ).decode(),
                        }
                    ),
                }
                for button in row
            ]
            for row in rows
        ],
    }


def topic(topic: TelegramTopic | None) -> dict[str, Any] | None:
    if topic is None:
        return None
    kind, field = {
        "thread": ("messageTopicThread", "message_thread_id"),
        "forum": ("messageTopicForum", "forum_topic_id"),
        "direct_messages": ("messageTopicDirectMessages", "direct_messages_chat_topic_id"),
        "saved_messages": ("messageTopicSavedMessages", "saved_messages_topic_id"),
    }[topic.kind]
    return {"@type": kind, field: topic.id}


def send_options(options: TelegramOptions, *, sending_id: int) -> dict[str, Any]:
    return {
        "@type": "messageSendOptions",
        "suggested_post_info": None,
        **options.model_dump(),
        "from_background": True,
        "update_order_of_installed_sticker_sets": False,
        "scheduling_state": None,
        "sending_id": sending_id,
        "only_preview": False,
    }


async def message_content(
    client: TelegramRequester, content: TelegramContent, *, asset_dir: Path | None
) -> dict[str, Any]:
    if isinstance(content, TelegramText):
        return {
            "@type": "inputMessageText",
            "text": await formatted_text(client, content.text, content.format),
            "link_preview_options": {
                "@type": "linkPreviewOptions",
                "is_disabled": content.disable_link_preview,
                "url": "",
                "force_small_media": False,
                "force_large_media": False,
                "show_above_text": False,
            },
            "clear_draft": False,
        }
    if isinstance(content, TelegramPhoto):
        photo: dict[str, Any] = {
            "@type": "inputPhoto",
            "photo": input_file(content.file, asset_dir),
            "thumbnail": _input_thumbnail(content.thumbnail, asset_dir),
            "video": None,
            "added_sticker_file_ids": [],
            "width": content.width,
            "height": content.height,
        }
        return {
            "@type": "inputMessagePhoto",
            "photo": photo,
            "caption": await formatted_text(client, content.caption, content.format),
            "show_caption_above_media": content.show_caption_above_media,
            "self_destruct_type": None,
            "has_spoiler": content.has_spoiler,
        }
    if isinstance(content, TelegramVideo):
        video: dict[str, Any] = {
            "@type": "inputVideo",
            "video": input_file(content.file, asset_dir),
            "thumbnail": _input_thumbnail(content.thumbnail, asset_dir),
            "cover": input_file(content.cover, asset_dir) if content.cover else None,
            "start_timestamp": content.start_timestamp,
            "added_sticker_file_ids": [],
            "duration": content.duration,
            "width": content.width,
            "height": content.height,
            "supports_streaming": content.supports_streaming,
        }
        return {
            "@type": "inputMessageVideo",
            "video": video,
            "caption": await formatted_text(client, content.caption, content.format),
            "show_caption_above_media": content.show_caption_above_media,
            "self_destruct_type": None,
            "has_spoiler": content.has_spoiler,
        }
    if isinstance(content, TelegramAnimation):
        animation: dict[str, Any] = {
            "@type": "inputAnimation",
            "animation": input_file(content.file, asset_dir),
            "thumbnail": _input_thumbnail(content.thumbnail, asset_dir),
            "added_sticker_file_ids": [],
            "duration": content.duration,
            "width": content.width,
            "height": content.height,
        }
        return {
            "@type": "inputMessageAnimation",
            "animation": animation,
            "caption": await formatted_text(client, content.caption, content.format),
            "show_caption_above_media": content.show_caption_above_media,
            "has_spoiler": content.has_spoiler,
        }
    if isinstance(content, TelegramAudio):
        audio: dict[str, Any] = {
            "@type": "inputAudio",
            "audio": input_file(content.file, asset_dir),
            "album_cover_thumbnail": _input_thumbnail(content.thumbnail, asset_dir),
            "duration": content.duration,
            "title": content.title,
            "performer": content.performer,
        }
        return {
            "@type": "inputMessageAudio",
            "audio": audio,
            "caption": await formatted_text(client, content.caption, content.format),
        }
    if isinstance(content, TelegramVoice):
        voice_note: dict[str, Any] = {
            "@type": "inputVoiceNote",
            "voice_note": input_file(content.file, asset_dir),
            "duration": content.duration,
            "waveform": content.waveform,
        }
        return {
            "@type": "inputMessageVoiceNote",
            "voice_note": voice_note,
            "caption": await formatted_text(client, content.caption, content.format),
            "self_destruct_type": None,
        }
    if isinstance(content, TelegramVideoNote):
        video_note: dict[str, Any] = {
            "@type": "inputVideoNote",
            "video_note": input_file(content.file, asset_dir),
            "thumbnail": _input_thumbnail(content.thumbnail, asset_dir),
            "duration": content.duration,
            "length": content.length,
        }
        return {
            "@type": "inputMessageVideoNote",
            "video_note": video_note,
            "self_destruct_type": None,
        }
    if isinstance(content, TelegramDocument):
        document: dict[str, Any] = {
            "@type": "inputDocument",
            "document": input_file(content.file, asset_dir),
            "thumbnail": _input_thumbnail(content.thumbnail, asset_dir),
            "disable_content_type_detection": content.disable_content_type_detection,
        }
        return {
            "@type": "inputMessageDocument",
            "document": document,
            "caption": await formatted_text(client, content.caption, content.format),
        }
    if isinstance(content, TelegramSticker):
        sticker: dict[str, Any] = {
            "@type": "inputSticker",
            "sticker": input_file(content.file, asset_dir),
            "thumbnail": _input_thumbnail(content.thumbnail, asset_dir),
            "width": content.width,
            "height": content.height,
        }
        return {"@type": "inputMessageSticker", "sticker": sticker, "emoji": content.emoji}
    if isinstance(content, TelegramPoll):
        poll_type = (
            {
                "@type": "inputPollTypeQuiz",
                "correct_option_ids": content.correct_option_ids,
                "explanation": await formatted_text(client, content.explanation, "plain"),
                "explanation_media": None,
            }
            if content.quiz
            else {"@type": "inputPollTypeRegular", "allow_adding_options": False}
        )
        return {
            "@type": "inputMessagePoll",
            "question": await formatted_text(client, content.question, "plain"),
            "options": [
                {
                    "@type": "inputPollOption",
                    "text": await formatted_text(client, value, "plain"),
                    "media": None,
                }
                for value in content.options
            ],
            "description": await formatted_text(client, "", "plain"),
            "media": None,
            "is_anonymous": content.is_anonymous,
            "allows_multiple_answers": content.allows_multiple_answers,
            "allows_revoting": content.allows_revoting,
            "members_only": False,
            "country_codes": [],
            "shuffle_options": False,
            "hide_results_until_closes": False,
            "type": poll_type,
            "open_period": content.open_period,
            "close_date": content.close_date,
            "is_closed": content.is_closed,
        }
    if isinstance(content, TelegramLocation):
        location = {
            "@type": "location",
            "latitude": content.latitude,
            "longitude": content.longitude,
            "horizontal_accuracy": getattr(content, "horizontal_accuracy", 0),
        }
        if content.live_period:
            return {
                "@type": "inputMessageLiveLocation",
                "location": {
                    "@type": "liveLocation",
                    "location": location,
                    "live_period": content.live_period,
                    "heading": content.heading,
                    "proximity_alert_radius": content.proximity_alert_radius,
                },
            }
        return {"@type": "inputMessageLocation", "location": location}
    if isinstance(content, TelegramVenue):
        return {
            "@type": "inputMessageVenue",
            "venue": {
                "@type": "venue",
                "location": {
                    "@type": "location",
                    "latitude": content.latitude,
                    "longitude": content.longitude,
                    "horizontal_accuracy": 0,
                },
                "title": content.title,
                "address": content.address,
                "provider": content.provider,
                "id": content.venue_id,
                "type": content.venue_type,
            },
        }
    if isinstance(content, TelegramContact):
        return {
            "@type": "inputMessageContact",
            "contact": {"@type": "contact", **content.model_dump(exclude={"kind"})},
        }
    if isinstance(content, TelegramDice):
        return {"@type": "inputMessageDice", "emoji": content.emoji, "clear_draft": False}
    raise ValidationError("unsupported Telegram message content")


def _input_thumbnail(file: TelegramFile | None, asset_dir: Path | None) -> dict[str, Any] | None:
    if file is None:
        return None
    return {
        "@type": "inputThumbnail",
        "thumbnail": input_file(file, asset_dir),
        "width": 0,
        "height": 0,
    }
