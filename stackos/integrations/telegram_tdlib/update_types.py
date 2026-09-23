"""Native TDLib update types that StackOS can normalize for project retention."""

from __future__ import annotations

RETAINABLE_TELEGRAM_UPDATE_TYPES = (
    "updateNewMessage",
    "updateMessageContent",
    "updateMessageEdited",
    "updateDeleteMessages",
    "updateNewCallbackQuery",
    "updateNewChat",
    "updateChatTitle",
    "updateChatPhoto",
    "updateChatPermissions",
    "updateChatPosition",
    "updateChatLastMessage",
    "updateChatMember",
    "updateUser",
    "updateFile",
)

TELEGRAM_ACCOUNT_UPDATE_TYPES = ("updateUser", "updateFile")
TELEGRAM_CHAT_UPDATE_TYPES = tuple(
    update_type
    for update_type in RETAINABLE_TELEGRAM_UPDATE_TYPES
    if update_type not in TELEGRAM_ACCOUNT_UPDATE_TYPES
)

TELEGRAM_CHAT_SURFACE_REF_PATTERN = r"^telegram-chat:-?[1-9][0-9]*$"
