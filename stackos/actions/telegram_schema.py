"""Explicit Telegram messaging contracts for both kinds of TDLib Account.

Protocol source: the td_api.tl shipped with the pinned managed TDLib runtime.
https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NativeId = Annotated[int, Field(strict=True, ge=-(2**53 - 1), le=2**53 - 1)]
PositiveId = Annotated[int, Field(strict=True, gt=0, le=2**53 - 1)]
NonnegativeId = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
Format = Literal["plain", "markdown", "html"]
_TELEGRAM_FILE_REF = re.compile(r"telegram-file:(cred_[A-Za-z0-9_-]{1,114}):([1-9][0-9]{0,9})\Z")


def telegram_file_ref(*, credential_ref: str, file_id: int) -> str:
    """Build the account-qualified reference required for TDLib native files."""

    if not isinstance(credential_ref, str) or not credential_ref:
        raise ValueError("Telegram file reference requires an Account ref")
    if not isinstance(file_id, int) or isinstance(file_id, bool) or not 0 < file_id <= 2**31 - 1:
        raise ValueError("Telegram file reference requires a positive TDLib file id")
    value = f"telegram-file:{credential_ref}:{file_id}"
    parse_telegram_file_ref(value)
    return value


def parse_telegram_file_ref(value: str) -> tuple[str, int]:
    """Validate and split an Account-qualified TDLib native file reference."""

    if not isinstance(value, str):
        raise ValueError("Telegram file reference must be a string")
    match = _TELEGRAM_FILE_REF.fullmatch(value)
    if match is None:
        raise ValueError("Use telegram-file:<credential_ref>:<positive TDLib file id>")
    file_id = int(match.group(2))
    if file_id > 2**31 - 1:
        raise ValueError("Telegram file reference has an out-of-range TDLib file id")
    return match.group(1), file_id


class TelegramInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TelegramFile(TelegramInput):
    artifact_ref: str | None = None
    file_ref: str | None = None
    url: str | None = None

    @field_validator("file_ref")
    @classmethod
    def qualified_native_file(cls, value: str | None) -> str | None:
        if value is not None:
            parse_telegram_file_ref(value)
        return value

    @model_validator(mode="after")
    def exactly_one_source(self) -> Self:
        if sum(value is not None for value in self.model_dump().values()) != 1:
            raise ValueError("a file requires exactly one artifact_ref, file_ref, or url")
        if any(value == "" for value in self.model_dump().values()):
            raise ValueError("file references must not be empty")
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                raise ValueError("file URL must be HTTP(S) without embedded credentials")
        return self


class TelegramText(TelegramInput):
    kind: Literal["text"]
    text: str = Field(min_length=1)
    format: Format = "plain"
    disable_link_preview: bool = False


class TelegramMedia(TelegramInput):
    file: TelegramFile
    thumbnail: TelegramFile | None = None


class TelegramCaptionMedia(TelegramMedia):
    caption: str = ""
    format: Format = "plain"


class TelegramVisualMedia(TelegramCaptionMedia):
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    has_spoiler: bool = False
    show_caption_above_media: bool = False


class TelegramPhoto(TelegramVisualMedia):
    kind: Literal["photo"]


class TelegramVideo(TelegramVisualMedia):
    kind: Literal["video"]
    duration: int = Field(default=0, ge=0)
    supports_streaming: bool = True
    cover: TelegramFile | None = None
    start_timestamp: int = Field(default=0, ge=0)


class TelegramAnimation(TelegramVisualMedia):
    kind: Literal["animation"]
    duration: int = Field(default=0, ge=0)


class TelegramAudio(TelegramCaptionMedia):
    kind: Literal["audio"]
    duration: int = Field(default=0, ge=0)
    title: str = ""
    performer: str = ""


class TelegramVoice(TelegramInput):
    kind: Literal["voice"]
    file: TelegramFile
    caption: str = ""
    format: Format = "plain"
    duration: int = Field(default=0, ge=0)
    waveform: str = ""


class TelegramVideoNote(TelegramMedia):
    kind: Literal["video_note"]
    duration: int = Field(default=0, ge=0, le=60)
    length: int = Field(default=240, gt=0, le=640)


class TelegramDocument(TelegramCaptionMedia):
    kind: Literal["document"]
    disable_content_type_detection: bool = False


class TelegramSticker(TelegramMedia):
    kind: Literal["sticker"]
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    emoji: str = ""


class TelegramPoll(TelegramInput):
    kind: Literal["poll"]
    question: str = Field(min_length=1, max_length=300)
    options: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    is_anonymous: bool = True
    allows_multiple_answers: bool = False
    allows_revoting: bool = False
    quiz: bool = False
    correct_option_ids: list[int] = Field(default_factory=list)
    explanation: str = ""
    open_period: int = Field(default=0, ge=0)
    close_date: int = Field(default=0, ge=0)
    is_closed: bool = False

    @model_validator(mode="after")
    def valid_quiz(self) -> Self:
        if self.quiz != bool(self.correct_option_ids):
            raise ValueError("quiz polls require correct_option_ids; regular polls cannot use them")
        if any(index < 0 or index >= len(self.options) for index in self.correct_option_ids):
            raise ValueError("correct_option_ids must refer to poll options")
        if self.open_period and self.close_date:
            raise ValueError("choose open_period or close_date")
        return self


class TelegramLocation(TelegramInput):
    kind: Literal["location"]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    horizontal_accuracy: float = Field(default=0, ge=0)
    live_period: int = Field(default=0, ge=0)
    heading: int = Field(default=0, ge=0, le=360)
    proximity_alert_radius: int = Field(default=0, ge=0)


class TelegramVenue(TelegramInput):
    kind: Literal["venue"]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    title: str = Field(min_length=1)
    address: str = Field(min_length=1)
    provider: str = ""
    venue_id: str = ""
    venue_type: str = ""


class TelegramContact(TelegramInput):
    kind: Literal["contact"]
    phone_number: str = Field(min_length=1)
    first_name: str = Field(min_length=1)
    last_name: str = ""
    vcard: str = ""
    user_id: NativeId = 0


class TelegramDice(TelegramInput):
    kind: Literal["dice"]
    emoji: str = "🎲"


TelegramContent = Annotated[
    TelegramText
    | TelegramPhoto
    | TelegramVideo
    | TelegramAnimation
    | TelegramAudio
    | TelegramVoice
    | TelegramVideoNote
    | TelegramDocument
    | TelegramSticker
    | TelegramPoll
    | TelegramLocation
    | TelegramVenue
    | TelegramContact
    | TelegramDice,
    Field(discriminator="kind"),
]


class TelegramButton(TelegramInput):
    text: str = Field(min_length=1)
    url: str | None = None
    callback_data: str | None = None
    style: Literal["default", "primary", "danger", "success", "link"] = "default"

    @model_validator(mode="after")
    def single_action(self) -> Self:
        if (self.url is None) == (self.callback_data is None):
            raise ValueError("a button requires one URL or callback_data")
        if self.callback_data is not None and not 1 <= len(self.callback_data.encode()) <= 64:
            raise ValueError("callback_data must contain 1 to 64 UTF-8 bytes")
        if self.url is not None:
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https", "tg"} or parsed.username:
                raise ValueError("button URL must use HTTP(S) or tg without credentials")
            if self.style == "link":
                raise ValueError("link button style requires callback_data, not a URL")
        return self


class TelegramTopic(TelegramInput):
    kind: Literal["thread", "forum", "direct_messages", "saved_messages"]
    id: PositiveId


class TelegramOptions(TelegramInput):
    disable_notification: bool = False
    protect_content: bool = False
    effect_id: NonnegativeId = 0


class TelegramProfileInput(TelegramInput):
    profile_ref: str = Field(min_length=1)


class TelegramDestinationInput(TelegramProfileInput):
    surface_ref: str = Field(
        pattern=r"^telegram-chat:-?[1-9][0-9]*$",
        description=(
            "TDLib telegram-chat:<signed native id> from chat.list or chat.resolve; "
            "positive IDs are commonly private chats and negative IDs groups/channels. "
            "Do not guess IDs."
        ),
    )


class TelegramSendInput(TelegramDestinationInput):
    sender_ref: str | None = Field(
        default=None,
        pattern=r"^(telegram-user:[1-9][0-9]*|telegram-chat:-?[1-9][0-9]*)$",
        description=(
            "Optional TDLib sender chosen for this destination, from chat.sender.list. "
            "A chat sender changes TDLib's selected sender for the destination before sending."
        ),
    )
    topic: TelegramTopic | None = None
    reply_to_message_id: PositiveId | None = None
    options: TelegramOptions = Field(default_factory=TelegramOptions)
    source_agent_request_id: PositiveId | None = None


class TelegramMessageSend(TelegramSendInput):
    content: TelegramContent
    buttons: list[list[TelegramButton]] = Field(default_factory=list)
    control_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TelegramBroadcastMessage(TelegramProfileInput):
    """Frozen content for one recipient, before its TDLib chat is resolved."""

    sender_ref: str | None = Field(
        default=None,
        pattern=r"^(telegram-user:[1-9][0-9]*|telegram-chat:-?[1-9][0-9]*)$",
        description="One explicit TDLib sender to validate separately for each recipient chat.",
    )
    content: TelegramContent
    buttons: list[list[TelegramButton]] = Field(default_factory=list)
    control_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    options: TelegramOptions = Field(default_factory=TelegramOptions)
    source_agent_request_id: PositiveId | None = None


class TelegramMessageBroadcast(TelegramBroadcastMessage):
    target_ref: str = Field(min_length=1)
    recipients: list[
        Annotated[str, Field(pattern=r"^telegram-(user:[1-9][0-9]*|chat:-?[1-9][0-9]*)$")]
    ] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_recipients(self) -> Self:
        if len(set(self.recipients)) != len(self.recipients):
            raise ValueError("recipient refs must be unique")
        return self


class TelegramBroadcastItem(TelegramInput):
    target_ref: str = Field(min_length=1)
    recipient_ref: str = Field(pattern=r"^telegram-(user:[1-9][0-9]*|chat:-?[1-9][0-9]*)$")
    message: TelegramBroadcastMessage


class TelegramAlbumSend(TelegramSendInput):
    contents: list[TelegramContent] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def valid_album(self) -> Self:
        kinds = {content.kind for content in self.contents}
        if not kinds <= {"photo", "video", "audio", "document"}:
            raise ValueError("albums support photo, video, audio, and document content")
        if kinds & {"audio", "document"} and len(kinds) != 1:
            raise ValueError("audio and document albums cannot mix content types")
        above = {getattr(content, "show_caption_above_media", False) for content in self.contents}
        if len(above) != 1:
            raise ValueError("album captions must use the same placement")
        return self


class TelegramMessageForward(TelegramDestinationInput):
    sender_ref: str | None = Field(
        default=None,
        pattern=r"^(telegram-user:[1-9][0-9]*|telegram-chat:-?[1-9][0-9]*)$",
        description="Optional TDLib sender from chat.sender.list for this destination.",
    )
    from_surface_ref: str = Field(min_length=1)
    message_ids: list[PositiveId] = Field(min_length=1, max_length=100)
    topic: TelegramTopic | None = None
    options: TelegramOptions = Field(default_factory=TelegramOptions)
    send_copy: bool = False
    remove_caption: bool = False

    @model_validator(mode="after")
    def ordered_ids(self) -> Self:
        if self.message_ids != sorted(set(self.message_ids)):
            raise ValueError("message_ids must be unique and in increasing order")
        if self.remove_caption and not self.send_copy:
            raise ValueError("remove_caption requires send_copy")
        if self.topic and self.topic.kind == "thread":
            raise ValueError("forwarding does not support message thread topics")
        return self


class TelegramMessageInput(TelegramDestinationInput):
    message_id: PositiveId


class TelegramMessageEdit(TelegramMessageInput):
    kind: Literal["text", "media", "caption", "buttons", "live_location"]
    content: TelegramContent | None = None
    caption: str | None = None
    format: Format = "plain"
    show_caption_above_media: bool = False
    buttons: list[list[TelegramButton]] | None = None
    location: TelegramLocation | None = None

    @model_validator(mode="after")
    def required_edit(self) -> Self:
        if self.kind in {"text", "media"}:
            if self.content is None:
                raise ValueError("text and media edits require content")
            if (self.kind == "text") != (self.content.kind == "text"):
                raise ValueError("content must match the edit kind")
            if self.kind == "media" and self.content.kind not in {
                "photo",
                "video",
                "animation",
                "audio",
                "document",
            }:
                raise ValueError("this message content cannot be edited as media")
        if self.kind == "caption" and self.caption is None:
            raise ValueError("caption edits require caption (empty clears it)")
        return self


class TelegramMessageDelete(TelegramDestinationInput):
    message_ids: list[PositiveId] = Field(min_length=1, max_length=100)
    revoke: bool = True


class TelegramMessageReact(TelegramMessageInput):
    emojis: list[str] = Field(default_factory=list)
    custom_emoji_ids: list[PositiveId] = Field(default_factory=list)
    is_big: bool = False


class TelegramCallbackAnswer(TelegramProfileInput):
    callback_query_id: str = Field(min_length=1)
    text: str = ""
    show_alert: bool = False
    url: str = ""
    cache_time: int = Field(default=0, ge=0)


class TelegramFileDownload(TelegramProfileInput):
    file_ref: str

    @field_validator("file_ref")
    @classmethod
    def qualified_native_file(cls, value: str) -> str:
        parse_telegram_file_ref(value)
        return value


class TelegramChatResolve(TelegramProfileInput):
    username: str | None = None
    user_id: PositiveId | None = None
    chat_id: NativeId | None = None

    @model_validator(mode="after")
    def one_peer(self) -> Self:
        if sum(value is not None for value in (self.username, self.user_id, self.chat_id)) != 1:
            raise ValueError("resolve one username, known user_id, or native chat_id")
        return self


class TelegramChatList(TelegramProfileInput):
    chat_list: Literal["main", "archive"] = "main"
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(
        default=None, pattern=r"^(main|archive):(?:[1-9][0-9]{0,2}|[1-4][0-9]{3})$"
    )

    @model_validator(mode="after")
    def valid_cursor(self) -> Self:
        if self.cursor is not None:
            list_name, offset = self.cursor.split(":", 1)
            if list_name != self.chat_list or int(offset) + self.limit > 5000:
                raise ValueError("chat cursor must match the list and stay within 5000 chats")
        return self


class TelegramMessageHistory(TelegramDestinationInput):
    limit: int = Field(default=20, ge=1, le=50)
    before_message_id: PositiveId | None = None
    include_content: bool = False


TELEGRAM_ACTION_MODELS: dict[str, type[TelegramInput]] = {
    "identity.get": TelegramProfileInput,
    "chat.list": TelegramChatList,
    "chat.resolve": TelegramChatResolve,
    "chat.inspect": TelegramDestinationInput,
    "chat.sender.list": TelegramDestinationInput,
    "message.history": TelegramMessageHistory,
    "message.get": TelegramMessageInput,
    "message.send": TelegramMessageSend,
    "message.broadcast": TelegramMessageBroadcast,
    "album.send": TelegramAlbumSend,
    "message.forward": TelegramMessageForward,
    "message.edit": TelegramMessageEdit,
    "message.delete": TelegramMessageDelete,
    "message.react": TelegramMessageReact,
    "poll.stop": TelegramMessageInput,
    "callback.answer": TelegramCallbackAnswer,
    "file.download": TelegramFileDownload,
}
