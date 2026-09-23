"""Typed messaging contracts shared by bot and user TDLib actions."""

import asyncio

import pytest
from pydantic import ValidationError

from stackos.actions.telegram_payloads import message_content, reply_markup, send_options
from stackos.actions.telegram_schema import TelegramMessageSend, TelegramOptions


class _FormattingClient:
    async def _request(self, request: dict, **_kwargs: object) -> dict:
        assert request["@type"] == "parseTextEntities"
        return {"@type": "formattedText", "text": request["text"], "entities": []}


@pytest.mark.parametrize(
    "content",
    [
        {"kind": "text", "text": "Hello"},
        *[
            {"kind": kind, "file": {"file_ref": "telegram-file:cred_test:42"}}
            for kind in (
                "photo",
                "video",
                "animation",
                "audio",
                "voice",
                "video_note",
                "document",
                "sticker",
            )
        ],
        {"kind": "poll", "question": "Pick", "options": ["One", "Two"]},
        {"kind": "location", "latitude": 1.2, "longitude": 3.4},
        {"kind": "venue", "latitude": 1.2, "longitude": 3.4, "title": "Office", "address": "Main"},
        {"kind": "contact", "phone_number": "+10000000000", "first_name": "Test"},
        {"kind": "dice", "emoji": "🎲"},
    ],
)
def test_bot_and_user_share_typed_message_content(content: dict) -> None:
    payload = TelegramMessageSend.model_validate(
        {
            "profile_ref": "communication-profile:ops",
            "surface_ref": "telegram-chat:-123",
            "content": content,
        }
    )
    assert payload.content.kind == content["kind"]


@pytest.mark.parametrize(
    "content",
    [
        {"kind": "text", "text": ""},
        {"kind": "photo", "file": {}},
        {"kind": "photo", "file": {"file_id": 1}},
        {
            "kind": "photo",
            "file": {"file_ref": "telegram-file:cred_test:1", "url": "https://example.test/a.png"},
        },
        {"kind": "location", "latitude": 95, "longitude": 0},
        {"kind": "poll", "question": "Quiz", "options": ["One", "Two"], "quiz": True},
        {"kind": "text", "text": "Hello", "unknown_native_field": True},
    ],
)
def test_invalid_rich_content_rejects_before_provider_dispatch(content: dict) -> None:
    with pytest.raises(ValidationError):
        TelegramMessageSend.model_validate(
            {
                "profile_ref": "communication-profile:ops",
                "surface_ref": "telegram-chat:-123",
                "content": content,
            }
        )


def test_buttons_are_bounded_and_cannot_mix_url_and_callback() -> None:
    for button in (
        {"text": "Bad", "url": "https://example.test", "callback_data": "x"},
        {"text": "Bad", "callback_data": "é" * 33},
    ):
        with pytest.raises(ValidationError):
            TelegramMessageSend.model_validate(
                {
                    "profile_ref": "ops",
                    "surface_ref": "telegram-chat:1",
                    "content": {"kind": "text", "text": "Hello"},
                    "buttons": [[button]],
                }
            )


@pytest.mark.parametrize(
    ("style", "native_style"),
    [
        ("default", "buttonStyleDefault"),
        ("primary", "buttonStylePrimary"),
        ("danger", "buttonStyleDanger"),
        ("success", "buttonStyleSuccess"),
    ],
)
def test_inline_button_style_reaches_tdlib(style: str, native_style: str) -> None:
    payload = TelegramMessageSend.model_validate(
        {
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "content": {"kind": "text", "text": "Hello"},
            "buttons": [[{"text": "Open", "url": "https://example.test", "style": style}]],
        }
    )
    markup = reply_markup(payload.buttons)
    assert markup is not None
    assert markup["rows"][0][0]["style"] == {"@type": native_style}


def test_inline_callback_button_link_style_reaches_tdlib() -> None:
    payload = TelegramMessageSend.model_validate(
        {
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "content": {"kind": "text", "text": "Hello"},
            "buttons": [[{"text": "Act", "callback_data": "act", "style": "link"}]],
        }
    )
    markup = reply_markup(payload.buttons)
    assert markup is not None
    assert markup["rows"][0][0]["style"] == {"@type": "buttonStyleLink"}


def test_inline_url_button_rejects_link_style() -> None:
    with pytest.raises(ValueError, match="link"):
        TelegramMessageSend.model_validate(
            {
                "profile_ref": "ops",
                "surface_ref": "telegram-chat:1",
                "content": {"kind": "text", "text": "Hello"},
                "buttons": [[{"text": "Open", "url": "https://example.test", "style": "link"}]],
            }
        )


def test_payloads_include_required_pinned_tdlib_defaults() -> None:
    client = _FormattingClient()
    text = asyncio.run(
        message_content(
            client,
            TelegramMessageSend.model_validate(
                {
                    "profile_ref": "ops",
                    "surface_ref": "telegram-chat:1",
                    "content": {"kind": "text", "text": "Hello"},
                }
            ).content,
            asset_dir=None,
        )
    )
    assert text["clear_draft"] is False
    assert text["link_preview_options"] == {
        "@type": "linkPreviewOptions",
        "is_disabled": False,
        "url": "",
        "force_small_media": False,
        "force_large_media": False,
        "show_above_text": False,
    }

    photo = asyncio.run(
        message_content(
            client,
            TelegramMessageSend.model_validate(
                {
                    "profile_ref": "ops",
                    "surface_ref": "telegram-chat:1",
                    "content": {
                        "kind": "photo",
                        "file": {"file_ref": "telegram-file:cred_test:42"},
                    },
                }
            ).content,
            asset_dir=None,
        )
    )
    assert photo["photo"] == {
        "@type": "inputPhoto",
        "photo": {"@type": "inputFileId", "id": 42},
        "thumbnail": None,
        "video": None,
        "added_sticker_file_ids": [],
        "width": 0,
        "height": 0,
    }
    assert photo["self_destruct_type"] is None

    poll = asyncio.run(
        message_content(
            client,
            TelegramMessageSend.model_validate(
                {
                    "profile_ref": "ops",
                    "surface_ref": "telegram-chat:1",
                    "content": {"kind": "poll", "question": "Pick", "options": ["One"]},
                }
            ).content,
            asset_dir=None,
        )
    )
    assert poll["type"] == {"@type": "inputPollTypeRegular", "allow_adding_options": False}
    assert poll["options"][0]["media"] is None
    assert poll["description"]["text"] == ""
    assert poll["media"] is None
    assert poll["country_codes"] == []

    assert send_options(TelegramOptions(), sending_id=1) == {
        "@type": "messageSendOptions",
        "suggested_post_info": None,
        "disable_notification": False,
        "protect_content": False,
        "effect_id": 0,
        "from_background": True,
        "update_order_of_installed_sticker_sets": False,
        "scheduling_state": None,
        "sending_id": 1,
        "only_preview": False,
    }


@pytest.mark.parametrize("field", ["allow_paid_broadcast", "paid_message_star_count"])
def test_paid_telegram_send_options_are_not_exposed(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TelegramOptions.model_validate({field: True})


def test_telegram_effect_id_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        TelegramOptions.model_validate({"effect_id": -1})
