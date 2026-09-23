"""TDLib messaging through project, Account, action, and durable receipt boundaries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRegistry,
    ActionConnectorRequest,
)
from stackos.actions.telegram import (
    TelegramActionConnector,
    _AccountClient,
    _safe_native_with_file_refs,
)
from stackos.actions.telegram_schema import TelegramButton
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall, Artifact, Credential, DurableActionJob, ResourceRecord
from stackos.integrations.telegram_tdlib.native import (
    TelegramTdlibNativeError,
    TelegramTdlibRequestError,
)
from stackos.integrations.telegram_tdlib.service import TelegramTdlibServiceError
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ArtifactRepository, ResourceRepository


class FakeTelegram:
    def __init__(self, files: Path | None = None) -> None:
        self.calls: list[dict] = []
        self.files = files

    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        self.calls.append({**request, "account_ref": account_ref, **kwargs})
        match request["@type"]:
            case "getChat":
                return {
                    "@type": "chat",
                    "id": request["chat_id"],
                    "type": {"@type": "chatTypePrivate"},
                }
            case "getUser":
                return {"@type": "user", "id": request["user_id"]}
            case "createPrivateChat":
                return {
                    "@type": "chat",
                    "id": request["user_id"],
                    "type": {"@type": "chatTypePrivate"},
                }
            case "sendMessage":
                return {
                    "@type": "message",
                    "id": -100,
                    "chat_id": request["chat_id"],
                    "sending_state": {"@type": "messageSendingStatePending"},
                }
            case "downloadFile":
                assert self.files
                return {
                    "@type": "file",
                    "id": request["file_id"],
                    "local": {
                        "path": str(self.files / "test.txt"),
                        "is_downloading_completed": True,
                    },
                }
        return {"@type": "ok"}

    async def wait_message(
        self, account_ref: str, chat_id: int, temporary_message_id: int, **kwargs: object
    ) -> dict:
        assert temporary_message_id == -100
        return {
            "@type": "updateMessageSendSucceeded",
            "old_message_id": -100,
            "message": {
                "@type": "message",
                "id": 1048576,
                "chat_id": chat_id,
                "sending_state": None,
            },
        }

    def files_directory(self, _account_ref: str) -> Path:
        assert self.files
        return self.files


class AlbumTimeoutTelegram(FakeTelegram):
    async def wait_message(
        self, account_ref: str, chat_id: int, temporary_message_id: int, **kwargs: object
    ) -> dict:
        del account_ref, kwargs
        if temporary_message_id == -101:
            return {
                "@type": "updateMessageSendSucceeded",
                "old_message_id": -101,
                "message": {
                    "@type": "message",
                    "id": 1001,
                    "chat_id": chat_id,
                    "sending_state": None,
                },
            }
        raise TimeoutError("native receipt wait timed out")


class FailedSendTelegram(FakeTelegram):
    def __init__(self, *, error_message: str, retry_after: float = 0.0) -> None:
        super().__init__()
        self.error_message = error_message
        self.retry_after = retry_after

    async def wait_message(
        self, account_ref: str, chat_id: int, temporary_message_id: int, **kwargs: object
    ) -> dict:
        del account_ref, kwargs
        return {
            "@type": "updateMessageSendFailed",
            "old_message_id": temporary_message_id,
            "error": {"@type": "error", "code": 429, "message": self.error_message},
            "message": {
                "@type": "message",
                "id": temporary_message_id,
                "chat_id": chat_id,
                "sending_state": {
                    "@type": "messageSendingStateFailed",
                    "can_retry": True,
                    "need_another_sender": False,
                    "retry_after": self.retry_after,
                },
            },
        }


class WaitBeforeSendTelegram(FakeTelegram):
    def __init__(self, *, error_name: str) -> None:
        super().__init__()
        self.error_name = error_name

    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        if request["@type"] == "sendMessage":
            self.calls.append({**request, "account_ref": account_ref, **kwargs})
            raise TelegramTdlibRequestError(
                code=420,
                phase="sendMessage",
                error_name=self.error_name,
                retry_after_seconds=17,
            )
        return await super().request(account_ref, request, **kwargs)


class FileReadTelegram(FakeTelegram):
    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        if request["@type"] == "getMessage":
            return {
                "@type": "message",
                "id": request["message_id"],
                "chat_id": request["chat_id"],
                "content": {
                    "@type": "messageDocument",
                    "document": {"document": {"@type": "file", "id": 42, "size": 12}},
                },
            }
        return await super().request(account_ref, request, **kwargs)


class PhotoReadTelegram(FakeTelegram):
    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        if request["@type"] == "getMessage":
            return {
                "@type": "message",
                "id": request["message_id"],
                "chat_id": request["chat_id"],
                "content": {
                    "@type": "messagePhoto",
                    "photo": {
                        "sizes": [
                            _photo_size("m", 1522, 0),
                            _photo_size("i", 1521, 0),
                            _photo_size("j", 1523, 100),
                        ]
                    },
                },
            }
        return await super().request(account_ref, request, **kwargs)


class DownloadTimeoutTelegram(FakeTelegram):
    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        if request["@type"] == "downloadFile":
            self.calls.append({**request, "account_ref": account_ref, **kwargs})
            raise TelegramTdlibNativeError("TDLib request timed out awaiting a response.")
        return await super().request(account_ref, request, **kwargs)


def _photo_size(kind: str, file_id: int, size: int) -> dict:
    return {
        "@type": "photoSize",
        "type": kind,
        "photo": {"@type": "file", "id": file_id, "size": size},
    }


class PagedTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.chat_ids = {"chatListMain": [11, 22, 33], "chatListArchive": [44]}
        self.loaded = {"chatListMain": 0, "chatListArchive": 0}
        self.short_anchor_once = False

    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        self.calls.append({**request, "account_ref": account_ref, **kwargs})
        kind = request["@type"]
        if kind in {"getChats", "loadChats"}:
            chat_list = request["chat_list"]["@type"]
            ids = self.chat_ids[chat_list]
            if kind == "loadChats":
                if self.loaded[chat_list] == len(ids):
                    raise TelegramTdlibRequestError(code=404, phase="loadChats")
                self.loaded[chat_list] = min(len(ids), self.loaded[chat_list] + 2)
                return {"@type": "ok"}
            return {
                "@type": "chats",
                "total_count": len(ids),
                "chat_ids": ids[: self.loaded[chat_list]][: request["limit"]],
            }
        if kind == "getChat":
            chat_id = request["chat_id"]
            return {
                "@type": "chat",
                "id": chat_id,
                "type": {"@type": "chatTypePrivate"},
                "title": f"Chat {chat_id}",
                "unread_count": 3,
                "last_message": {
                    "id": chat_id * 10,
                    "date": 1234,
                    "content": {"text": {"text": "private last message"}},
                },
                "photo": {"small": {"local": {"path": "/secret/native/path"}}},
            }
        if kind == "getChatHistory":
            messages = [
                {
                    "@type": "message",
                    "id": message_id,
                    "chat_id": request["chat_id"],
                    "date": message_id,
                    "sender_id": {"@type": "messageSenderUser", "user_id": 999},
                    "content": {
                        "@type": "messageDocument",
                        "caption": {"text": "a" * 200},
                        "document": {
                            "document": {
                                "@type": "file",
                                "id": 42,
                                "local": {"path": "/secret/native/path"},
                            }
                        },
                    },
                }
                for message_id in (30, 20, 10)
            ]
            anchor = request["from_message_id"]
            candidates = [message for message in messages if not anchor or message["id"] <= anchor]
            if anchor == 20 and self.short_anchor_once:
                self.short_anchor_once = False
                return {"@type": "messages", "total_count": 3, "messages": candidates[:1]}
            # TDLib is allowed to return fewer messages than the requested limit.
            return {"@type": "messages", "total_count": 3, "messages": candidates[:2]}
        if kind == "getMessage":
            return {
                "@type": "message",
                "id": request["message_id"],
                "chat_id": request["chat_id"],
                "content": {"@type": "messageText", "text": {"text": "selected message text"}},
            }
        return await super().request(account_ref, request, **kwargs)


class SecretChatTelegram(PagedTelegram):
    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        if request["@type"] == "getChat":
            self.calls.append({**request, "account_ref": account_ref, **kwargs})
            return {
                "@type": "chat",
                "id": request["chat_id"],
                "type": {"@type": "chatTypeSecret"},
            }
        return await super().request(account_ref, request, **kwargs)


class DisconnectedTelegram(FakeTelegram):
    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        raise TelegramTdlibServiceError("TDLib session is not configured")


class SenderTelegram(FakeTelegram):
    def __init__(
        self,
        *,
        status: dict | None = None,
        channel: bool = False,
        basic_group: bool = False,
        available: list[dict] | None = None,
        receipt_sender: dict | None = None,
        permissions: dict | None = None,
    ) -> None:
        super().__init__()
        self.status = status or {"@type": "chatMemberStatusMember"}
        self.channel = channel
        self.basic_group = basic_group
        self.selected = {"@type": "messageSenderUser", "user_id": 101}
        self.available = available or [
            {"sender": self.selected, "needs_premium": False},
            {
                "sender": {"@type": "messageSenderChat", "chat_id": -900},
                "needs_premium": False,
            },
        ]
        self.receipt_sender = receipt_sender
        self.permissions = permissions or {"can_send_basic_messages": True}
        self.sent_sender: dict | None = None

    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        self.calls.append({**request, "account_ref": account_ref, **kwargs})
        kind = request["@type"]
        if kind == "getChat":
            return {
                "@type": "chat",
                "id": request["chat_id"],
                "type": (
                    {"@type": "chatTypeBasicGroup", "basic_group_id": 901}
                    if self.basic_group
                    else {
                        "@type": "chatTypeSupergroup",
                        "supergroup_id": 901,
                        "is_channel": self.channel,
                    }
                ),
                "title": "Channel" if self.channel else "Group",
                "permissions": self.permissions,
                "message_sender_id": self.selected,
            }
        if kind in {"getSupergroup", "getBasicGroup"}:
            return {
                "@type": "supergroup" if kind == "getSupergroup" else "basicGroup",
                "status": self.status,
            }
        if kind == "getChatMember":
            raise AssertionError("self membership must use group.status")
        if kind == "getMe":
            return {"@type": "user", "id": 101}
        if kind == "getChatAvailableMessageSenders":
            return {"@type": "chatMessageSenders", "senders": self.available}
        if kind == "setChatMessageSender":
            self.selected = request["message_sender_id"]
            return {"@type": "ok"}
        if kind == "sendMessage":
            self.sent_sender = dict(self.selected)
            return {
                "@type": "message",
                "id": -100,
                "chat_id": request["chat_id"],
                "sender_id": self.sent_sender,
                "sending_state": {"@type": "messageSendingStatePending"},
            }
        raise AssertionError(f"Unexpected TDLib method: {kind}")

    async def wait_message(
        self, account_ref: str, chat_id: int, temporary_message_id: int, **kwargs: object
    ) -> dict:
        assert temporary_message_id == -100
        return {
            "@type": "updateMessageSendSucceeded",
            "old_message_id": -100,
            "message": {
                "@type": "message",
                "id": 1048576,
                "chat_id": chat_id,
                "sender_id": self.receipt_sender or self.sent_sender,
                "sending_state": None,
            },
        }


def _account(session: Session, project_id: int, *, kind: str = "bot") -> str:
    fields = {"api_id": 123, "api_hash": "test-private-api-hash"}
    if kind == "bot":
        fields["bot_token"] = "123:test-private-bot-token"
    created = (
        AuthRepository(session)
        .store_credential(
            provider_key="telegram",
            auth_method_key="tdlib-bot-token" if kind == "bot" else "tdlib-user-session",
            display_name=f"Test {kind}",
            fields=fields,
            attach_project_id=project_id,
        )
        .data
    )
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == created.credential_ref)
    ).one()
    credential.status = "connected"
    session.add(credential)
    session.commit()
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id="communication-profile:ops",
        title="Ops",
        data_json={
            "key": "ops",
            "enabled": True,
            "provider_facets": {"telegram": {"credential_ref": created.credential_ref}},
        },
    )
    return created.credential_ref


async def _sender_send_request(
    session: Session, project_id: int, account_ref: str, *, sender_ref: str | None
) -> ActionConnectorRequest:
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.message.send",
    )
    input_json = {
        "profile_ref": "ops",
        "surface_ref": "telegram-chat:-901",
        "content": {"kind": "text", "text": "Hello"},
    }
    if sender_ref is not None:
        input_json["sender_ref"] = sender_ref
    return ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="telegram.message.send",
        action_ref="communications.telegram.message.send",
        provider_key="telegram",
        operation="message.send",
        config_json={},
        credential=resolved,
        session=session,
        input_json=input_json,
        action_call_id=1,
        delivery_item_id=1,
        attempt_ref="test-attempt",
    )


def test_native_button_link_style_requires_callback() -> None:
    assert TelegramButton(text="Open", callback_data="open:1", style="link").style == "link"
    with pytest.raises(ValueError, match="requires callback_data"):
        TelegramButton(text="Open", url="https://example.test", style="link")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "can_post"),
    [
        (
            {"@type": "chatMemberStatusAdministrator", "rights": {"can_post_messages": True}},
            True,
        ),
        (
            {"@type": "chatMemberStatusAdministrator", "rights": {"can_post_messages": False}},
            False,
        ),
        ({"@type": "chatMemberStatusCreator", "is_member": True}, True),
        ({"@type": "chatMemberStatusCreator", "is_member": False}, False),
        ({"@type": "chatMemberStatusMember"}, False),
    ],
)
async def test_channel_post_rights_come_from_current_account_group_status(
    status: dict, can_post: bool
) -> None:
    runtime = SenderTelegram(status=status, channel=True)
    connector = TelegramActionConnector(runtime)
    inspected = await connector._inspect(_AccountClient(runtime, "cred_test"), -901)
    assert inspected["can_post_messages"] is can_post
    assert inspected["is_member"] is (status.get("is_member", True))
    if not can_post:
        with pytest.raises(ValidationError, match="cannot send"):
            await connector._inspect(_AccountClient(runtime, "cred_test"), -901, require_write=True)
    assert "getChatMember" not in {call["@type"] for call in runtime.calls}
    assert "getSupergroup" in {call["@type"] for call in runtime.calls}
    assert "sendMessage" not in {call["@type"] for call in runtime.calls}


@pytest.mark.asyncio
async def test_basic_group_uses_current_account_status_without_member_lookup() -> None:
    runtime = SenderTelegram(basic_group=True)
    inspected = await TelegramActionConnector(runtime)._inspect(
        _AccountClient(runtime, "cred_test"), -901, require_write=True
    )
    assert inspected["can_post_messages"] is True
    assert [call["@type"] for call in runtime.calls] == ["getChat", "getBasicGroup"]


@pytest.mark.asyncio
async def test_chat_sender_list_reports_current_and_available_options(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.chat.sender.list",
    )
    runtime = SenderTelegram(
        available=[
            {
                "sender": {"@type": "messageSenderUser", "user_id": 101},
                "needs_premium": False,
            },
            {
                "sender": {"@type": "messageSenderChat", "chat_id": -900},
                "needs_premium": True,
            },
        ]
    )
    result = await TelegramActionConnector(runtime).execute(
        ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.chat.sender.list",
            action_ref="communications.telegram.chat.sender.list",
            provider_key="telegram",
            operation="chat.sender.list",
            config_json={},
            credential=resolved,
            session=session,
            input_json={"profile_ref": "ops", "surface_ref": "telegram-chat:-901"},
        )
    )
    assert result.output_json["selected_sender_ref"] == "telegram-user:101"
    assert result.output_json["available_senders"] == [
        {"sender_ref": "telegram-user:101", "needs_premium": False},
        {"sender_ref": "telegram-chat:-900", "needs_premium": True},
    ]


@pytest.mark.asyncio
async def test_explicit_channel_sender_is_selected_only_during_leased_send(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    runtime = SenderTelegram()
    connector = TelegramActionConnector(runtime)
    request = await _sender_send_request(
        session, project_id, account_ref, sender_ref="telegram-chat:-900"
    )
    await connector.prepare_delivery(request)
    assert runtime.selected == {"@type": "messageSenderUser", "user_id": 101}
    assert "sendMessage" not in {call["@type"] for call in runtime.calls}
    result = await connector.execute(request)
    kinds = [call["@type"] for call in runtime.calls]
    assert kinds.index("setChatMessageSender") < kinds.index("sendMessage")
    assert runtime.sent_sender == {"@type": "messageSenderChat", "chat_id": -900}
    assert result.output_json["message_ref"] == "telegram-message:-901:1048576"
    assert result.output_json["sender_ref"] == "telegram-chat:-900"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("available", "sender_ref", "reason"),
    [
        (
            [{"sender": {"@type": "messageSenderUser", "user_id": 101}, "needs_premium": False}],
            "telegram-chat:-900",
            "unavailable",
        ),
        (
            [{"sender": {"@type": "messageSenderChat", "chat_id": -900}, "needs_premium": True}],
            "telegram-chat:-900",
            "Premium",
        ),
        (
            [
                {"sender": {"@type": "messageSenderUser", "user_id": 101}, "needs_premium": False},
                {"sender": {"@type": "messageSenderChat", "chat_id": -900}, "needs_premium": False},
            ],
            None,
            "Choose",
        ),
    ],
)
async def test_sender_preflight_rejects_unavailable_premium_or_ambiguous_choice(
    session: Session,
    project_id: int,
    available: list[dict],
    sender_ref: str | None,
    reason: str,
) -> None:
    account_ref = _account(session, project_id, kind="user")
    runtime = SenderTelegram(available=available)
    request = await _sender_send_request(session, project_id, account_ref, sender_ref=sender_ref)
    with pytest.raises(ValidationError, match=reason):
        await TelegramActionConnector(runtime).prepare_delivery(request)
    assert "setChatMessageSender" not in {call["@type"] for call in runtime.calls}
    assert "sendMessage" not in {call["@type"] for call in runtime.calls}


@pytest.mark.asyncio
async def test_final_receipt_sender_mismatch_never_claims_send_success(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    runtime = SenderTelegram(receipt_sender={"@type": "messageSenderUser", "user_id": 101})
    request = await _sender_send_request(
        session, project_id, account_ref, sender_ref="telegram-chat:-900"
    )
    with pytest.raises(ActionConnectorError) as error:
        await TelegramActionConnector(runtime).execute(request)
    assert error.value.output_json["message_ref"] == "telegram-message:-901:1048576"
    assert error.value.output_json["sender_ref"] == "telegram-chat:-900"
    assert error.value.output_json["retry_safe"] is False
    assert error.value.output_json["status"] != "sent"
    assert "sendMessage" in {call["@type"] for call in runtime.calls}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bot", "user"])
async def test_channel_admin_post_selects_sole_available_sender(
    session: Session, project_id: int, kind: str
) -> None:
    account_ref = _account(session, project_id, kind=kind)
    runtime = SenderTelegram(
        channel=True,
        status={"@type": "chatMemberStatusAdministrator", "rights": {"can_post_messages": True}},
        permissions={"can_send_basic_messages": False},
        available=[
            {
                "sender": {"@type": "messageSenderChat", "chat_id": -901},
                "needs_premium": False,
            }
        ],
    )
    request = await _sender_send_request(session, project_id, account_ref, sender_ref=None)
    connector = TelegramActionConnector(runtime)
    await connector.prepare_delivery(request)
    result = await connector.execute(request)
    assert runtime.sent_sender == {"@type": "messageSenderChat", "chat_id": -901}
    assert result.output_json["status"] == "sent"
    assert result.output_json["sender_ref"] == "telegram-chat:-901"
    assert "setChatMessageSender" in {call["@type"] for call in runtime.calls}
    assert "getChatMember" not in {call["@type"] for call in runtime.calls}


@pytest.mark.asyncio
async def test_channel_post_requires_explicit_sender_when_multiple_are_available(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    runtime = SenderTelegram(
        channel=True,
        status={"@type": "chatMemberStatusAdministrator", "rights": {"can_post_messages": True}},
        available=[
            {"sender": {"@type": "messageSenderUser", "user_id": 101}, "needs_premium": False},
            {"sender": {"@type": "messageSenderChat", "chat_id": -901}, "needs_premium": False},
        ],
    )
    request = await _sender_send_request(session, project_id, account_ref, sender_ref=None)
    connector = TelegramActionConnector(runtime)
    with pytest.raises(ValidationError, match="Choose a Telegram sender"):
        await connector.prepare_delivery(request)
    assert "setChatMessageSender" not in {call["@type"] for call in runtime.calls}
    assert "sendMessage" not in {call["@type"] for call in runtime.calls}
    request.input_json["sender_ref"] = "telegram-chat:-901"
    await connector.prepare_delivery(request)
    result = await connector.execute(request)
    assert result.output_json["sender_ref"] == "telegram-chat:-901"
    assert runtime.sent_sender == {"@type": "messageSenderChat", "chat_id": -901}


@pytest.mark.asyncio
async def test_send_timeout_after_sender_selection_preserves_unknown_outcome(
    session: Session, project_id: int
) -> None:
    class TimedOutSender(SenderTelegram):
        async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
            if request["@type"] == "sendMessage":
                self.calls.append({**request, "account_ref": account_ref, **kwargs})
                raise TelegramTdlibNativeError("TDLib request timed out awaiting a response.")
            return await super().request(account_ref, request, **kwargs)

    account_ref = _account(session, project_id, kind="user")
    runtime = TimedOutSender()
    request = await _sender_send_request(
        session, project_id, account_ref, sender_ref="telegram-chat:-900"
    )
    with pytest.raises(ActionConnectorError) as error:
        await TelegramActionConnector(runtime).execute(request)
    output = error.value.output_json
    assert output["status"] == "send_outcome_unknown"
    assert output["sender_ref"] == "telegram-chat:-900"
    assert output["sender_selection_changed"] is True
    assert output["provider_result_known"] is False
    assert output["retry_safe"] is False


def test_static_validation_uses_attached_account_kind_without_secret_resolution(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    connector = TelegramActionConnector(FakeTelegram())
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="telegram.message.edit",
        action_ref="communications.telegram.message.edit",
        provider_key="telegram",
        operation="message.edit",
        config_json={},
        credential_ref=account_ref,
        session=session,
        input_json={
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "message_id": 1,
            "kind": "buttons",
            "buttons": [],
        },
    )
    issues = connector.validate(request)
    assert len(issues) == 1
    assert issues[0].path == "input_json.kind"
    assert "bot Accounts" in issues[0].message


@pytest.mark.asyncio
async def test_photo_only_group_can_send_photo_but_rejects_text(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    runtime = SenderTelegram(
        available=[
            {"sender": {"@type": "messageSenderUser", "user_id": 101}, "needs_premium": False}
        ],
        permissions={"can_send_basic_messages": False, "can_send_photos": True},
    )
    connector = TelegramActionConnector(runtime)
    request = await _sender_send_request(session, project_id, account_ref, sender_ref=None)
    request.input_json["content"] = {
        "kind": "photo",
        "file": {"url": "https://example.test/photo.jpg"},
    }
    await connector.prepare_delivery(request)
    inspected = await connector.execute(
        replace(
            request,
            operation="chat.inspect",
            input_json={"profile_ref": "ops", "surface_ref": "telegram-chat:-901"},
        )
    )
    assert inspected.output_json["can_post_messages"] is True
    assert inspected.output_json["send_permissions"]["can_send_basic_messages"] is False
    assert inspected.output_json["send_permissions"]["can_send_photos"] is True
    request.input_json["content"] = {"kind": "text", "text": "Hello"}
    with pytest.raises(ValidationError, match="can_send_basic_messages"):
        await connector.prepare_delivery(request)
    assert "sendMessage" not in {call["@type"] for call in runtime.calls}


@pytest.mark.asyncio
async def test_group_rejects_photo_when_photo_permission_is_false(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    runtime = SenderTelegram(
        available=[
            {"sender": {"@type": "messageSenderUser", "user_id": 101}, "needs_premium": False}
        ],
        permissions={"can_send_basic_messages": True, "can_send_photos": False},
    )
    request = await _sender_send_request(session, project_id, account_ref, sender_ref=None)
    request.input_json["content"] = {
        "kind": "photo",
        "file": {"url": "https://example.test/photo.jpg"},
    }
    with pytest.raises(ValidationError, match="can_send_photos"):
        await TelegramActionConnector(runtime).prepare_delivery(request)
    assert "setChatMessageSender" not in {call["@type"] for call in runtime.calls}
    assert "sendMessage" not in {call["@type"] for call in runtime.calls}


@pytest.mark.asyncio
async def test_tdlib_send_is_sealed_before_effect_and_returns_final_native_ids(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    runtime = FakeTelegram()
    connector = TelegramActionConnector(runtime)
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry)
    action_ref = "communications.telegram.message.send"
    env = await repo.execute(
        project_id=project_id,
        action_ref=action_ref,
        credential_ref=account_ref,
        idempotency_key="native-send-1",
        input_json={
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "content": {"kind": "text", "text": "Hello"},
            "buttons": [[{"text": "Open", "callback_data": "open-1"}]],
        },
    )
    assert env.data.poll_operation == "actionCall.get"
    assert all(call["@type"] != "sendMessage" for call in runtime.calls)
    job = session.exec(
        select(DurableActionJob).where(DurableActionJob.action_call_id == env.data.action_call.id)
    ).one()
    item = repo.claim_durable_action_items(project_id=project_id, job_id=job.id)[0]
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation=action_ref,
    )
    progress: list[dict] = []
    result = await connector.execute(
        ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.message.send",
            action_ref=action_ref,
            provider_key="telegram",
            operation="message.send",
            input_json=item.input_json,
            config_json={},
            credential=resolved,
            session=session,
            action_call_id=env.data.action_call.id,
            delivery_item_id=item.id,
            attempt_ref=item.attempt_ref,
            correlation_ref=item.correlation_ref,
            progress_callback=progress.append,
        )
    )
    assert result.output_json["message_ref"] == "telegram-message:1:1048576"
    send = next(call for call in runtime.calls if call["@type"] == "sendMessage")
    assert send["options"]["sending_id"] == item.id
    assert send["correlation_id"] == item.correlation_ref
    assert send["reply_markup"]["rows"][0][0]["type"]["data"] == "b3Blbi0x"
    assert "test-private" not in str(result.model_dump())
    assert progress[-1] == {
        "phase": "provider_accepted",
        "chat_id": 1,
        "temporary_message_ids": [-100],
        "confirmed_message_refs": ["telegram-message:1:1048576"],
        "provider_receipts": {
            "-100": {"status": "sent", "message_ref": "telegram-message:1:1048576"}
        },
    }


@pytest.mark.asyncio
async def test_album_receipt_keeps_each_confirmed_message_before_a_later_timeout(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    runtime = AlbumTimeoutTelegram()
    connector = TelegramActionConnector(runtime)
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="test",
    )
    progress: list[dict] = []
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="telegram.album.send",
        action_ref="communications.telegram.album.send",
        provider_key="telegram",
        operation="album.send",
        config_json={},
        credential=resolved,
        session=session,
        input_json={
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "contents": [
                {"kind": "photo", "file": {"file_ref": "telegram-file:cred_test:1"}},
                {"kind": "photo", "file": {"file_ref": "telegram-file:cred_test:2"}},
            ],
        },
        progress_callback=progress.append,
    )
    with pytest.raises(TimeoutError, match="receipt wait"):
        await connector._send_receipt(
            connector._client(request),
            request,
            1,
            {
                "@type": "messages",
                "messages": [
                    {"@type": "message", "id": -101, "sending_state": {"@type": "pending"}},
                    {"@type": "message", "id": -102, "sending_state": {"@type": "pending"}},
                ],
            },
        )
    assert progress[-1] == {
        "phase": "provider_accepted",
        "chat_id": 1,
        "temporary_message_ids": [-101, -102],
        "confirmed_message_refs": ["telegram-message:1:1001"],
        "provider_receipts": {"-101": {"status": "sent", "message_ref": "telegram-message:1:1001"}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_name", "retry_scope"),
    [("FLOOD_WAIT", "account"), ("SLOWMODE_WAIT", "destination")],
)
async def test_pre_send_wait_names_shared_backoff_scope(
    session: Session, project_id: int, error_name: str, retry_scope: str
) -> None:
    account_ref = _account(session, project_id)
    request = await _sender_send_request(session, project_id, account_ref, sender_ref=None)
    request.input_json["surface_ref"] = "telegram-chat:1"
    with pytest.raises(ActionConnectorError) as caught:
        await TelegramActionConnector(WaitBeforeSendTelegram(error_name=error_name)).execute(
            request
        )
    output = caught.value.output_json
    assert output["provider_error"] == error_name
    assert output["retry_after_seconds"] == 17
    assert output["provider_executed"] is False
    assert output["retry_safe"] is True
    assert output["retry_scope"] == retry_scope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_message", "retry_after", "provider_error", "account_restricted", "retry_scope"),
    [
        ("FLOOD_WAIT_17", 17.0, "FLOOD_WAIT", False, "account"),
        ("SLOWMODE_WAIT_11", 11.0, "SLOWMODE_WAIT", False, "destination"),
        ("PEER_FLOOD", 0.0, "PEER_FLOOD", True, None),
        ("token-like unreviewed text", 0.0, None, False, None),
    ],
)
async def test_final_failed_send_receipt_exposes_only_safe_provider_feedback(
    session: Session,
    project_id: int,
    error_message: str,
    retry_after: float,
    provider_error: str | None,
    account_restricted: bool,
    retry_scope: str | None,
) -> None:
    account_ref = _account(session, project_id)
    connector = TelegramActionConnector(
        FailedSendTelegram(error_message=error_message, retry_after=retry_after)
    )
    request = await _sender_send_request(session, project_id, account_ref, sender_ref=None)
    with pytest.raises(ActionConnectorError) as failure:
        await connector._send_receipt(
            connector._client(request),
            request,
            1,
            {
                "@type": "message",
                "id": -100,
                "chat_id": 1,
                "sending_state": {"@type": "messageSendingStatePending"},
            },
        )
    result = failure.value.output_json
    assert result["provider_error"] == provider_error
    assert result["provider_status_code"] == 429
    assert result["retry_after_seconds"] == (retry_after or None)
    assert result["tdlib_can_retry"] is True
    assert result["account_restricted"] is account_restricted
    assert result["retry_scope"] == retry_scope
    assert result["provider_executed"] is True
    assert result["retry_safe"] is False
    assert error_message not in str(result) or error_message == provider_error


@pytest.mark.asyncio
@pytest.mark.parametrize("recipient_list", [False, True])
async def test_broadcast_requires_explicit_recipient_target_and_seals_supplied_refs_without_lookup(
    session: Session,
    project_id: int,
    recipient_list: bool,
) -> None:
    account_ref = _account(session, project_id)
    runtime = FakeTelegram()
    resources = ResourceRepository(session)
    resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-target",
        external_id="communication-target:subscribers",
        title="Subscribers",
        data_json={
            "key": "subscribers",
            "enabled": True,
            "provider_key": "telegram",
            "action_ref": "communications.telegram.message.broadcast",
            "profile_ref": "communication-profile:ops",
            "surface_ref": "telegram-chat:-100123",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:ops"],
                "destination_mode": "recipient-list" if recipient_list else "fixed",
            },
        },
    )
    registry = ActionConnectorRegistry()
    registry.register(TelegramActionConnector(runtime))
    repo = ActionRepository(session, connectors=registry)
    kwargs = dict(
        project_id=project_id,
        action_ref="communications.telegram.message.broadcast",
        credential_ref=account_ref,
        idempotency_key="broadcast-1",
        input_json={
            "profile_ref": "ops",
            "target_ref": "subscribers",
            "recipients": ["telegram-user:1", "telegram-user:2"],
            "content": {"kind": "text", "text": "Hello subscribers"},
        },
    )
    if not recipient_list:
        with pytest.raises(ValidationError, match="recipient-list"):
            await repo.execute(**kwargs)
        assert runtime.calls == []
        return
    receipt = await repo.execute(**kwargs)
    assert receipt.data.poll_operation == "actionCall.get"
    job = repo.get_durable_action_job_for_action_call(
        project_id=project_id,
        action_call_id=receipt.data.action_call.id,
    )
    items = repo.list_durable_action_items(project_id=project_id, job_id=job.id)
    assert [item.destination_ref for item in items] == ["telegram-chat:1", "telegram-chat:2"]
    assert items[0].input_json["recipient_ref"] == "telegram-user:1"
    assert runtime.calls == []
    with pytest.raises(ValidationError, match="same Telegram chat"):
        await repo.execute(
            **{
                **kwargs,
                "idempotency_key": "broadcast-alias-duplicate",
                "input_json": {
                    **kwargs["input_json"],
                    "recipients": ["telegram-user:1", "telegram-chat:1"],
                },
            }
        )
    with pytest.raises(ValidationError, match="Secret chats"):
        await repo.execute(
            **{
                **kwargs,
                "idempotency_key": "broadcast-secret-chat",
                "input_json": {
                    **kwargs["input_json"],
                    "recipients": ["telegram-chat:-2000000000000"],
                },
            }
        )
    assert runtime.calls == []


class RejectFirstBroadcastRecipient(FakeTelegram):
    async def request(self, account_ref: str, request: dict, **kwargs: object) -> dict:
        if request["@type"] == "getChatAvailableMessageSenders":
            self.calls.append({**request, "account_ref": account_ref, **kwargs})
            chat_id = request["chat_id"]
            choices = [
                {
                    "sender": {"@type": "messageSenderChat", "chat_id": chat_id},
                    "needs_premium": False,
                }
            ]
            if chat_id == -100124:
                choices.append(
                    {
                        "sender": {"@type": "messageSenderUser", "user_id": 99},
                        "needs_premium": False,
                    }
                )
            return {"@type": "chatMessageSenders", "senders": choices}
        if request["@type"] == "getChat" and request["chat_id"] < 0:
            self.calls.append({**request, "account_ref": account_ref, **kwargs})
            return {
                "@type": "chat",
                "id": request["chat_id"],
                "type": {"@type": "chatTypeSupergroup", "supergroup_id": 123, "is_channel": True},
                "message_sender_id": {
                    "@type": "messageSenderChat",
                    "chat_id": request["chat_id"],
                },
            }
        if request["@type"] == "sendMessage" and request["chat_id"] in {1, -100123}:
            self.calls.append({**request, "account_ref": account_ref, **kwargs})
            raise TelegramTdlibRequestError(
                code=403,
                phase="sendMessage",
                error_name="USER_DEACTIVATED" if request["chat_id"] == 1 else None,
            )
        return await super().request(account_ref, request, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bot", "user"])
async def test_broadcast_recipient_rejection_is_individual_and_next_item_can_send(
    session: Session, project_id: int, kind: str
) -> None:
    account_ref = _account(session, project_id, kind=kind)
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-target",
        external_id="communication-target:subscribers",
        title="Subscribers",
        data_json={
            "key": "subscribers",
            "enabled": True,
            "provider_key": "telegram",
            "action_ref": "communications.telegram.message.broadcast",
            "profile_ref": "communication-profile:ops",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:ops"],
                "destination_mode": "recipient-list",
            },
        },
    )
    runtime = RejectFirstBroadcastRecipient()
    connector = TelegramActionConnector(runtime)
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry)
    action_ref = "communications.telegram.message.broadcast"
    receipt = await repo.execute(
        project_id=project_id,
        action_ref=action_ref,
        credential_ref=account_ref,
        idempotency_key="broadcast-recipient-failure",
        input_json={
            "profile_ref": "ops",
            "target_ref": "subscribers",
            "recipients": [
                "telegram-user:1",
                "telegram-user:2",
                "telegram-chat:-100123",
                "telegram-chat:-100124",
                "telegram-chat:3",
            ],
            "content": {"kind": "text", "text": "Hello"},
        },
    )
    assert runtime.calls == []
    job = repo.get_durable_action_job_for_action_call(
        project_id=project_id, action_call_id=receipt.data.action_call.id
    )
    items = repo.list_durable_action_items(project_id=project_id, job_id=job.id)
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation=action_ref,
    )

    def delivery_request(item: object) -> ActionConnectorRequest:
        return ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.message.broadcast",
            action_ref=action_ref,
            provider_key="telegram",
            operation="message.broadcast",
            input_json=item.input_json,
            config_json={},
            credential=resolved,
            session=session,
            action_call_id=receipt.data.action_call.id,
            delivery_item_id=item.id,
            attempt_ref="durable-attempt:test",
            correlation_ref=item.correlation_ref,
        )

    with pytest.raises(ActionConnectorError) as rejected:
        await connector.execute(delivery_request(items[0]))
    assert rejected.value.output_json["recipient_ref"] == "telegram-user:1"
    assert rejected.value.output_json["provider_error"] == "USER_DEACTIVATED"
    assert rejected.value.output_json["provider_status_code"] == 403
    assert rejected.value.output_json["provider_executed"] is False
    result = await connector.execute(delivery_request(items[1]))
    assert result.output_json["recipient_ref"] == "telegram-user:2"
    assert result.output_json["message_ref"] == "telegram-message:2:1048576"
    with pytest.raises(ActionConnectorError) as channel_rejected:
        await connector.execute(delivery_request(items[2]))
    assert channel_rejected.value.output_json["recipient_ref"] == "telegram-chat:-100123"
    assert channel_rejected.value.output_json["provider_status_code"] == 403
    assert channel_rejected.value.output_json["provider_error"] == "TDLIB_REQUEST_REJECTED"
    with pytest.raises(ActionConnectorError) as ambiguous:
        await connector.execute(delivery_request(items[3]))
    assert ambiguous.value.output_json["recipient_ref"] == "telegram-chat:-100124"
    assert ambiguous.value.output_json["status"] == "recipient_rejected"
    assert "Choose a Telegram sender" in ambiguous.value.output_json["reason"]
    assert ambiguous.value.output_json["repair_context"]["available_sender_count"] == 2
    assert "chat.sender.list" in ambiguous.value.output_json["repair_context"]["next_action"]
    private_chat = await connector.execute(delivery_request(items[4]))
    assert private_chat.output_json["recipient_ref"] == "telegram-chat:3"
    assert private_chat.output_json["message_ref"] == "telegram-message:3:1048576"
    assert [call["@type"] for call in runtime.calls] == [
        "createPrivateChat",
        "sendMessage",
        "createPrivateChat",
        "sendMessage",
        "getChatAvailableMessageSenders",
        "getChat",
        "sendMessage",
        "getChatAvailableMessageSenders",
        "sendMessage",
    ]


@pytest.mark.asyncio
async def test_broadcast_explicit_channel_sender_is_selected_without_membership_inspection(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-target",
        external_id="communication-target:subscribers",
        title="Subscribers",
        data_json={
            "key": "subscribers",
            "enabled": True,
            "provider_key": "telegram",
            "action_ref": "communications.telegram.message.broadcast",
            "profile_ref": "communication-profile:ops",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:ops"],
                "destination_mode": "recipient-list",
            },
        },
    )
    runtime = SenderTelegram()
    connector = TelegramActionConnector(runtime)
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry)
    action_ref = "communications.telegram.message.broadcast"
    receipt = await repo.execute(
        project_id=project_id,
        action_ref=action_ref,
        credential_ref=account_ref,
        idempotency_key="broadcast-explicit-channel-sender",
        input_json={
            "profile_ref": "ops",
            "target_ref": "subscribers",
            "recipients": ["telegram-chat:-901"],
            "sender_ref": "telegram-chat:-900",
            "content": {"kind": "text", "text": "Hello channel"},
        },
    )
    assert runtime.calls == []
    job = repo.get_durable_action_job_for_action_call(
        project_id=project_id, action_call_id=receipt.data.action_call.id
    )
    item = repo.list_durable_action_items(project_id=project_id, job_id=job.id)[0]
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation=action_ref,
    )
    result = await connector.execute(
        ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.message.broadcast",
            action_ref=action_ref,
            provider_key="telegram",
            operation="message.broadcast",
            input_json=item.input_json,
            config_json={},
            credential=resolved,
            session=session,
            action_call_id=receipt.data.action_call.id,
            delivery_item_id=item.id,
            attempt_ref="durable-attempt:test",
            correlation_ref=item.correlation_ref,
        )
    )
    assert result.output_json["recipient_ref"] == "telegram-chat:-901"
    assert result.output_json["sender_ref"] == "telegram-chat:-900"
    assert result.output_json["message_ref"] == "telegram-message:-901:1048576"
    kinds = [call["@type"] for call in runtime.calls]
    assert kinds == [
        "getChatAvailableMessageSenders",
        "getChat",
        "setChatMessageSender",
        "getChat",
        "sendMessage",
    ]
    assert runtime.sent_sender == {"@type": "messageSenderChat", "chat_id": -900}


@pytest.mark.asyncio
async def test_broadcast_rejects_a_target_without_the_broadcast_action_variant(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-target",
        external_id="communication-target:subscribers",
        title="Subscribers",
        data_json={
            "key": "subscribers",
            "enabled": True,
            "provider_key": "telegram",
            "action_ref": "communications.telegram.message.send",
            "profile_ref": "communication-profile:ops",
            "surface_ref": "telegram-chat:-100123",
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:ops"],
                "destination_mode": "recipient-list",
            },
        },
    )
    registry = ActionConnectorRegistry()
    runtime = FakeTelegram()
    registry.register(TelegramActionConnector(runtime))
    with pytest.raises(ValidationError, match="action variant"):
        await ActionRepository(session, connectors=registry).execute(
            project_id=project_id,
            action_ref="communications.telegram.message.broadcast",
            credential_ref=account_ref,
            idempotency_key="broadcast-unlisted-variant",
            input_json={
                "profile_ref": "ops",
                "target_ref": "subscribers",
                "recipients": ["telegram-user:1"],
                "content": {"kind": "text", "text": "Hello subscribers"},
            },
        )
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_user_rejects_bot_markup_before_dispatch(session: Session, project_id: int) -> None:
    account_ref = _account(session, project_id, kind="user")
    runtime = FakeTelegram()
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id, provider_key="telegram", credential_ref=account_ref, operation="test"
    )
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="telegram.message.send",
        action_ref="communications.telegram.message.send",
        provider_key="telegram",
        operation="message.send",
        config_json={},
        credential=resolved,
        session=session,
        input_json={
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "content": {"kind": "text", "text": "Hello"},
            "buttons": [[{"text": "Open", "callback_data": "open-1"}]],
        },
    )
    with pytest.raises(ValidationError, match="only by bot"):
        await TelegramActionConnector(runtime).prepare_delivery(request)
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_telegram_profile_facet_must_remain_enabled(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id="communication-profile:ops",
        title="Ops",
        data_json={
            "key": "ops",
            "enabled": True,
            "provider_facets": {"telegram": {"credential_ref": account_ref, "enabled": False}},
        },
    )
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="test",
    )
    runtime = FakeTelegram()
    with pytest.raises(ValidationError, match="missing or disabled"):
        await TelegramActionConnector(runtime).prepare_delivery(
            ActionConnectorRequest(
                project_id=project_id,
                plugin_slug="communications",
                action_key="telegram.message.send",
                action_ref="communications.telegram.message.send",
                provider_key="telegram",
                operation="message.send",
                config_json={},
                credential=resolved,
                session=session,
                input_json={
                    "profile_ref": "ops",
                    "surface_ref": "telegram-chat:1",
                    "content": {"kind": "text", "text": "Hello"},
                },
            )
        )
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_telegram_media_rejects_an_artifact_owned_by_another_project(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    other = (
        ProjectRepository(session)
        .create(
            slug="telegram-foreign-artifact",
            name="Foreign Telegram Artifact",
            domain="foreign-artifact.example.test",
            locale="en-US",
        )
        .data
    )
    assert other.id is not None
    foreign_ref = "/generated-assets/telegram/foreign/secret.jpg"
    ArtifactRepository(session).create(
        project_id=other.id,
        plugin_slug="communications",
        kind="file",
        uri=foreign_ref,
        status="approved",
    )
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="test",
    )
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="telegram.message.send",
        action_ref="communications.telegram.message.send",
        provider_key="telegram",
        operation="message.send",
        config_json={},
        credential=resolved,
        session=session,
        input_json={
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "content": {"kind": "photo", "file": {"artifact_ref": foreign_ref}},
        },
    )
    runtime = FakeTelegram()
    with pytest.raises(ValidationError, match="not an active artifact"):
        await TelegramActionConnector(runtime).prepare_delivery(request)
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_telegram_media_rejects_native_file_owned_by_another_account(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="test",
    )
    runtime = FakeTelegram()
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="telegram.message.send",
        action_ref="communications.telegram.message.send",
        provider_key="telegram",
        operation="message.send",
        config_json={},
        credential=resolved,
        session=session,
        input_json={
            "profile_ref": "ops",
            "surface_ref": "telegram-chat:1",
            "content": {
                "kind": "photo",
                "file": {"file_ref": "telegram-file:cred_another_account:42"},
            },
        },
    )
    with pytest.raises(ValidationError, match="belongs to a different Account"):
        await TelegramActionConnector(runtime).prepare_delivery(request)
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_message_read_returns_an_account_qualified_native_file_ref(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="test",
    )
    result = await TelegramActionConnector(FileReadTelegram()).execute(
        ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.message.get",
            action_ref="communications.telegram.message.get",
            provider_key="telegram",
            operation="message.get",
            config_json={},
            credential=resolved,
            session=session,
            input_json={
                "profile_ref": "ops",
                "surface_ref": "telegram-chat:1",
                "message_id": 7,
            },
        )
    )
    selected = result.output_json["message"]
    assert selected["file_refs"] == [f"telegram-file:{account_ref}:42"]
    assert selected["message_ref"] == "telegram-message:1:7"
    assert "content" not in selected


@pytest.mark.asyncio
async def test_live_chat_list_pages_main_and_archive_without_native_message_bodies(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.chat.list",
    )
    runtime = PagedTelegram()
    connector = TelegramActionConnector(runtime)

    def request(input_json: dict) -> ActionConnectorRequest:
        return ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.chat.list",
            action_ref="communications.telegram.chat.list",
            provider_key="telegram",
            operation="chat.list",
            config_json={},
            credential=resolved,
            session=session,
            input_json=input_json,
        )

    first = (await connector.execute(request({"profile_ref": "ops", "limit": 2}))).output_json
    assert [chat["surface_ref"] for chat in first["chats"]] == [
        "telegram-chat:11",
        "telegram-chat:22",
    ]
    assert first["next_cursor"] == "main:2"
    assert first["end_reached"] is False
    assert first["chats"][0]["last_message_ref"] == "telegram-message:11:110"
    assert first["chats"][0]["history_supported"] is True
    assert "private last message" not in str(first)
    assert "/secret/native/path" not in str(first)
    second = (
        await connector.execute(request({"profile_ref": "ops", "limit": 2, "cursor": "main:2"}))
    ).output_json
    assert [chat["surface_ref"] for chat in second["chats"]] == ["telegram-chat:33"]
    assert second["next_cursor"] is None
    assert second["end_reached"] is True
    archive = (
        await connector.execute(request({"profile_ref": "ops", "chat_list": "archive"}))
    ).output_json
    assert [chat["surface_ref"] for chat in archive["chats"]] == ["telegram-chat:44"]
    assert archive["next_cursor"] is None
    assert connector.validate(request({"profile_ref": "ops", "limit": 50, "cursor": "main:4951"}))
    assert connector.validate(request({"profile_ref": "ops", "cursor": "main:5001"}))
    assert connector.validate(
        request({"profile_ref": "ops", "chat_list": "archive", "cursor": "main:2"})
    )
    assert max(call["limit"] for call in runtime.calls if call["@type"] == "getChats") <= 5000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("chat.list", {}),
        ("message.history", {"surface_ref": "telegram-chat:11"}),
    ],
)
async def test_bot_navigation_rejects_user_only_pagination_before_native_call(
    session: Session, project_id: int, operation: str, payload: dict
) -> None:
    account_ref = _account(session, project_id, kind="bot")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation=f"communications.telegram.{operation}",
    )
    runtime = PagedTelegram()
    connector = TelegramActionConnector(runtime)
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key=f"telegram.{operation}",
        action_ref=f"communications.telegram.{operation}",
        provider_key="telegram",
        operation=operation,
        config_json={},
        credential=resolved,
        session=session,
        input_json={"profile_ref": "ops", **payload},
    )
    assert "require a user Account" in connector.validate(request)[0].message
    with pytest.raises(ValidationError, match="require a user Account") as error:
        await connector.execute(request)
    assert error.value.data == {
        "path": "operation",
        "account_kind": "bot",
        "supported_account_kinds": ["user"],
        "next_action": "Use retained Telegram updates or resolve a known chat/message",
        "provider_executed": False,
    }
    assert runtime.calls == []

    resolved_chat = await connector.execute(
        replace(
            request,
            action_key="telegram.chat.resolve",
            action_ref="communications.telegram.chat.resolve",
            operation="chat.resolve",
            input_json={"profile_ref": "ops", "chat_id": 11},
        )
    )
    assert resolved_chat.output_json["chat"]["history_supported"] is False
    assert [call["@type"] for call in runtime.calls] == ["getChat"]


@pytest.mark.asyncio
async def test_user_account_cannot_answer_bot_callback_before_native_call(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.callback.answer",
    )
    runtime = FakeTelegram()
    connector = TelegramActionConnector(runtime)
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="telegram.callback.answer",
        action_ref="communications.telegram.callback.answer",
        provider_key="telegram",
        operation="callback.answer",
        config_json={},
        credential=resolved,
        session=session,
        input_json={"profile_ref": "ops", "callback_query_id": "123456789"},
        action_call_id=1,
        attempt_ref="test-callback-attempt",
        delivery_item_id=1,
    )
    assert connector.validate(request)[0].message == (
        "This Telegram action is supported only by bot Accounts"
    )
    with pytest.raises(ValidationError, match="only by bot Accounts"):
        await connector.execute(request)
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_live_history_pages_short_results_and_exposes_only_selected_content(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.message.history",
    )
    runtime = PagedTelegram()
    connector = TelegramActionConnector(runtime)

    def request(input_json: dict) -> ActionConnectorRequest:
        return ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.message.history",
            action_ref="communications.telegram.message.history",
            provider_key="telegram",
            operation="message.history",
            config_json={},
            credential=resolved,
            session=session,
            input_json={"profile_ref": "ops", "surface_ref": "telegram-chat:11", **input_json},
        )

    first = (await connector.execute(request({"limit": 3}))).output_json
    assert [message["message_id"] for message in first["messages"]] == [30, 20]
    assert first["next_before_message_id"] == 20
    assert first["pagination_inconclusive"] is False
    assert first["messages"][0]["sender_ref"] == "telegram-user:999"
    assert first["messages"][0]["file_refs"] == [f"telegram-file:{account_ref}:42"]
    assert len(first["messages"][0]["text_preview"]) == 160
    assert "text" not in first["messages"][0]
    assert "/secret/native/path" not in str(first)
    runtime.short_anchor_once = True
    second = (
        await connector.execute(
            request({"limit": 3, "before_message_id": 20, "include_content": True})
        )
    ).output_json
    assert [message["message_id"] for message in second["messages"]] == [10]
    assert second["messages"][0]["text"] == "a" * 200
    assert second["next_before_message_id"] == 10
    assert second["probe_count"] == 1
    assert any(
        call["@type"] == "getChatHistory" and call["from_message_id"] == 19
        for call in runtime.calls
    )
    terminal = (await connector.execute(request({"limit": 3, "before_message_id": 10}))).output_json
    assert terminal["messages"] == []
    assert terminal["pagination_inconclusive"] is True
    assert terminal["next_before_message_id"] is None
    assert terminal["probe_count"] == 2
    assert "stop this traversal" in terminal["next_action"]
    assert (
        sum(
            call["@type"] == "getChatHistory" and call["from_message_id"] == 9
            for call in runtime.calls
        )
        == 2
    )
    assert connector.validate(request({"limit": 51}))
    assert connector.validate(request({"surface_ref": "123"}))
    with pytest.raises(ValidationError, match="Account-known TDLib"):
        await connector.execute(request({"surface_ref": "telegram-chat:9007199254740992"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["message.get", "message.history"])
async def test_live_message_reads_reject_secret_chats_before_message_fetch(
    session: Session, project_id: int, operation: str
) -> None:
    account_ref = _account(session, project_id, kind="user")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation=f"communications.telegram.{operation}",
    )
    runtime = SecretChatTelegram()
    input_json = {"profile_ref": "ops", "surface_ref": "telegram-chat:11"}
    if operation == "message.get":
        input_json["message_id"] = 30
    with pytest.raises(ValidationError, match="Secret chats"):
        await TelegramActionConnector(runtime).execute(
            ActionConnectorRequest(
                project_id=project_id,
                plugin_slug="communications",
                action_key=f"telegram.{operation}",
                action_ref=f"communications.telegram.{operation}",
                provider_key="telegram",
                operation=operation,
                config_json={},
                credential=resolved,
                session=session,
                input_json=input_json,
            )
        )
    assert [call["@type"] for call in runtime.calls] == ["getChat"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("identity.get", {}),
        ("chat.list", {}),
        ("chat.resolve", {"chat_id": 11}),
        ("chat.inspect", {"surface_ref": "telegram-chat:11"}),
        ("message.history", {"surface_ref": "telegram-chat:11"}),
        ("message.get", {"surface_ref": "telegram-chat:11", "message_id": 30}),
        ("file.download", {}),
    ],
)
async def test_explicit_disconnect_rejects_live_reads_before_native_call(
    session: Session, project_id: int, operation: str, payload: dict
) -> None:
    account_ref = _account(session, project_id, kind="user")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation=f"communications.telegram.{operation}",
    )
    account = session.exec(select(Credential).where(Credential.credential_ref == account_ref)).one()
    account.status = "disconnected"
    account.config_json = {**(account.config_json or {}), "telegram_desired_connected": False}
    session.add(account)
    session.commit()
    runtime = PagedTelegram()
    if operation == "file.download":
        payload = {"file_ref": f"telegram-file:{account_ref}:42"}
    with pytest.raises(ActionConnectorError) as error:
        await TelegramActionConnector(runtime).execute(
            ActionConnectorRequest(
                project_id=project_id,
                plugin_slug="communications",
                action_key=f"telegram.{operation}",
                action_ref=f"communications.telegram.{operation}",
                provider_key="telegram",
                operation=operation,
                config_json={},
                credential=resolved,
                session=session,
                input_json={"profile_ref": "ops", **payload},
            )
        )
    assert error.value.output_json["status"] == "not_connected"
    assert error.value.output_json["next_action"] == "account.session.connect"
    assert error.value.output_json["provider_executed"] is False
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_retired_tdlib_session_returns_connect_repair_for_live_read(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.chat.list",
    )
    runtime = DisconnectedTelegram()
    with pytest.raises(ActionConnectorError) as error:
        await TelegramActionConnector(runtime).execute(
            ActionConnectorRequest(
                project_id=project_id,
                plugin_slug="communications",
                action_key="telegram.chat.list",
                action_ref="communications.telegram.chat.list",
                provider_key="telegram",
                operation="chat.list",
                config_json={},
                credential=resolved,
                session=session,
                input_json={"profile_ref": "ops"},
            )
        )
    assert error.value.output_json == {
        "status": "not_connected",
        "credential_ref": account_ref,
        "next_action": "account.session.connect",
        "provider_executed": False,
        "retry_safe": True,
    }
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_live_navigation_uses_normal_action_audit_without_storing_message_resources(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id, kind="user")
    before = len(session.exec(select(ResourceRecord)).all())
    runtime = PagedTelegram()
    registry = ActionConnectorRegistry()
    registry.register(TelegramActionConnector(runtime))
    repo = ActionRepository(session, connectors=registry)
    listed = (
        await repo.execute(
            project_id=project_id,
            action_ref="communications.telegram.chat.list",
            credential_ref=account_ref,
            input_json={"profile_ref": "ops", "limit": 2},
            allow_transient_response=True,
        )
    ).data
    history = (
        await repo.execute(
            project_id=project_id,
            action_ref="communications.telegram.message.history",
            credential_ref=account_ref,
            input_json={"profile_ref": "ops", "surface_ref": "telegram-chat:11", "limit": 2},
            allow_transient_response=True,
        )
    ).data
    resolved = (
        await repo.execute(
            project_id=project_id,
            action_ref="communications.telegram.chat.resolve",
            credential_ref=account_ref,
            input_json={"profile_ref": "ops", "chat_id": 11},
            allow_transient_response=True,
        )
    ).data
    inspected = (
        await repo.execute(
            project_id=project_id,
            action_ref="communications.telegram.chat.inspect",
            credential_ref=account_ref,
            input_json={"profile_ref": "ops", "surface_ref": "telegram-chat:11"},
            allow_transient_response=True,
        )
    ).data
    selected = (
        await repo.execute(
            project_id=project_id,
            action_ref="communications.telegram.message.get",
            credential_ref=account_ref,
            input_json={"profile_ref": "ops", "surface_ref": "telegram-chat:11", "message_id": 30},
            allow_transient_response=True,
        )
    ).data
    assert listed.output_json["chats"][0]["surface_ref"] == "telegram-chat:11"
    assert history.output_json["messages"][0]["message_ref"] == "telegram-message:11:30"
    assert resolved.output_json["chat"]["title"] == "Chat 11"
    assert inspected.output_json["chat"]["title"] == "Chat 11"
    assert selected.output_json["message"]["text"] == "selected message text"
    assert len(session.exec(select(ResourceRecord)).all()) == before
    calls = session.exec(select(ActionCall)).all()
    assert len(calls) == 5
    assert all("Chat 11" not in str(call.response_json) for call in calls)
    assert all("telegram-message:11:30" not in str(call.response_json) for call in calls)


@pytest.mark.asyncio
async def test_native_download_registers_artifact_without_exposing_session_path(
    session: Session, project_id: int, tmp_path: Path
) -> None:
    account_ref = _account(session, project_id)
    files = tmp_path / "private-account-files"
    files.mkdir()
    (files / "test.txt").write_text("downloaded content")
    assets = tmp_path / "generated-assets"
    assets.mkdir()
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id, provider_key="telegram", credential_ref=account_ref, operation="test"
    )
    result = await TelegramActionConnector(FakeTelegram(files)).execute(
        ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.file.download",
            action_ref="communications.telegram.file.download",
            provider_key="telegram",
            operation="file.download",
            config_json={},
            credential=resolved,
            session=session,
            asset_dir=assets,
            input_json={
                "profile_ref": "ops",
                "file_ref": f"telegram-file:{account_ref}:42",
            },
        )
    )
    output = result.output_json
    assert output["artifact_id"]
    assert output["file_ref"] == f"telegram-file:{account_ref}:42"
    assert "id" not in output["file"]
    assert (
        assets / output["artifact_ref"].removeprefix("/generated-assets/")
    ).read_text() == "downloaded content"
    assert str(files) not in str(output)


@pytest.mark.asyncio
async def test_photo_navigation_excludes_embedded_preview_refs_even_when_larger(
    session: Session, project_id: int
) -> None:
    account_ref = _account(session, project_id)
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.message.get",
    )
    result = await TelegramActionConnector(PhotoReadTelegram()).execute(
        ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="telegram.message.get",
            action_ref="communications.telegram.message.get",
            provider_key="telegram",
            operation="message.get",
            config_json={},
            credential=resolved,
            session=session,
            input_json={
                "profile_ref": "ops",
                "surface_ref": "telegram-chat:11",
                "message_id": 7408189440,
            },
        )
    )
    assert result.output_json["message"]["file_refs"] == [f"telegram-file:{account_ref}:1522"]
    receipt = _safe_native_with_file_refs(
        {
            "@type": "message",
            "content": {
                "photo": {
                    "sizes": [
                        _photo_size("m", 1522, 0),
                        _photo_size("i", 1521, 0),
                        _photo_size("j", 1523, 100),
                    ]
                }
            },
        },
        account_ref,
    )
    assert [size["type"] for size in receipt["content"]["photo"]["sizes"]] == ["m"]


@pytest.mark.asyncio
async def test_native_download_timeout_reports_retry_without_artifact(
    session: Session, project_id: int, tmp_path: Path
) -> None:
    account_ref = _account(session, project_id)
    assets = tmp_path / "generated-assets"
    assets.mkdir()
    resolved = await AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="telegram",
        credential_ref=account_ref,
        operation="communications.telegram.file.download",
    )
    runtime = DownloadTimeoutTelegram()
    with pytest.raises(ActionConnectorError) as error:
        await TelegramActionConnector(runtime).execute(
            ActionConnectorRequest(
                project_id=project_id,
                plugin_slug="communications",
                action_key="telegram.file.download",
                action_ref="communications.telegram.file.download",
                provider_key="telegram",
                operation="file.download",
                config_json={},
                credential=resolved,
                session=session,
                asset_dir=assets,
                input_json={
                    "profile_ref": "ops",
                    "file_ref": f"telegram-file:{account_ref}:1522",
                },
            )
        )
    assert error.value.output_json["status"] == "retryable_timeout"
    assert error.value.output_json["retry_safe"] is True
    assert error.value.output_json["next_action"] == "communications.telegram.file.download"
    assert error.value.output_json["file_ref"] == f"telegram-file:{account_ref}:1522"
    assert not session.exec(select(Artifact)).all()
    assert [call["@type"] for call in runtime.calls] == ["downloadFile"]
