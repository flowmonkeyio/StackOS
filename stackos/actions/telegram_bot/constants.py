"""Telegram Bot API connector constants."""

from __future__ import annotations

import re

_BASE_URL = "https://api.telegram.org"
_MAX_MESSAGE_TEXT = 4096
_MAX_CAPTION_TEXT = 1024
_MAX_CALLBACK_TEXT = 200
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_FILE_DOWNLOAD_BYTES = 20 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_MAX_MEDIA_GROUP_ITEMS = 10
_MAX_INLINE_ROWS = 20
_MAX_INLINE_BUTTONS_PER_ROW = 8
_ALLOWED_PARSE_MODES = {"Markdown", "MarkdownV2", "HTML"}
_ALLOWED_UPDATES = {
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "message_reaction",
    "message_reaction_count",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "purchased_paid_media",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "chat_boost",
    "removed_chat_boost",
}
_SECRETISH_CALLBACK_RE = re.compile(
    r"(?i)(bearer\s+|sk-[a-z0-9]|api[_-]?key|client[_-]?secret|"
    r"refresh[_-]?token|access[_-]?token|password|secret)"
)
