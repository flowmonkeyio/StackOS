"""TDLib update normalization into the shared communication processor."""

from __future__ import annotations

import base64
import json

import pytest
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.communications import communication_surface_binding_external_id
from stackos.communications.provider_ids import telegram_callback_button_external_id
from stackos.db.models import Credential, CredentialAccount
from stackos.integrations.telegram_tdlib.updates import process_telegram_update
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository


@pytest.mark.parametrize("account_kind", ["bot", "user"])
def test_tdlib_retention_requires_selected_update_type_and_surface(
    session: Session,
    project_id: int,
    account_kind: str,
) -> None:
    credential_ref = _connected_account(session, project_id, account_kind=account_kind)

    def new_message(message_id: int, chat_id: int = 111) -> dict[str, object]:
        return {
            "@type": "updateNewMessage",
            "message": {
                "id": message_id,
                "chat_id": chat_id,
                "sender_id": {"@type": "messageSenderUser", "user_id": chat_id},
                "content": {"@type": "messageText", "text": {"text": "Test message"}},
            },
        }

    def profile(visibility_policy: dict[str, object]) -> None:
        _store_profile(
            session,
            project_id,
            credential_ref=credential_ref,
            key="selective",
            visibility_policy=visibility_policy,
            access_policy={"user_mode": "disabled"},
        )

    profile({})
    assert (
        process_telegram_update(session, credential_ref=credential_ref, update=new_message(1))[
            0
        ].policy_status
        == "update_blocked"
    )
    outgoing = new_message(10)
    outgoing["message"]["is_outgoing"] = True
    assert (
        process_telegram_update(session, credential_ref=credential_ref, update=outgoing)[
            0
        ].policy_status
        == "update_blocked"
    )
    assert not _records(session, project_id, "communication-event")

    profile({"allowed_update_types": ["updateNewMessage"]})
    assert (
        process_telegram_update(session, credential_ref=credential_ref, update=new_message(2))[
            0
        ].policy_status
        == "surface_blocked"
    )

    profile(
        {
            "dm_mode": "allowlist",
            "allowed_surface_refs": ["telegram-chat:111"],
            "allowed_update_types": ["updateMessageEdited"],
        }
    )
    assert (
        process_telegram_update(session, credential_ref=credential_ref, update=new_message(3))[
            0
        ].policy_status
        == "update_blocked"
    )

    profile(
        {
            "dm_mode": "allowlist",
            "allowed_surface_refs": ["telegram-chat:111"],
            "allowed_update_types": ["updateNewMessage"],
            "store_non_trigger_messages": True,
        }
    )
    assert (
        process_telegram_update(
            session, credential_ref=credential_ref, update=new_message(4, chat_id=222)
        )[0].policy_status
        == "surface_blocked"
    )
    assert (
        process_telegram_update(session, credential_ref=credential_ref, update=new_message(5))[
            0
        ].policy_status
        == "invoker_blocked"
    )
    assert [
        item.data_json["provider_message_id"]
        for item in _records(session, project_id, "communication-message")
    ] == [5]
    assert not AgentRequestRepository(session).list(project_id=project_id).items


@pytest.mark.parametrize("account_kind", ["bot", "user"])
def test_retained_photo_attachment_skips_embedded_preview_sizes(
    session: Session, project_id: int, account_kind: str
) -> None:
    credential_ref = _connected_account(session, project_id, account_kind=account_kind)
    _store_profile(session, project_id, credential_ref=credential_ref, key="ops")
    process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewMessage",
            "message": {
                "id": 7408189440,
                "chat_id": 111,
                "sender_id": {"@type": "messageSenderUser", "user_id": 111},
                "content": {
                    "@type": "messagePhoto",
                    "photo": {
                        "sizes": [
                            {
                                "@type": "photoSize",
                                "type": "m",
                                "photo": {"@type": "file", "id": 1522, "size": 0},
                            },
                            {
                                "@type": "photoSize",
                                "type": "i",
                                "photo": {"@type": "file", "id": 1521, "size": 0},
                            },
                            {
                                "@type": "photoSize",
                                "type": "j",
                                "photo": {"@type": "file", "id": 1523, "size": 100},
                            },
                        ]
                    },
                },
            },
        },
    )
    messages = _records(session, project_id, "communication-message")
    assert len(messages) == 1
    assert [attachment["file_ref"] for attachment in messages[0].data_json["attachments"]] == [
        f"telegram-file:{credential_ref}:1522"
    ]


@pytest.mark.parametrize("account_kind", ["bot", "user"])
@pytest.mark.parametrize(
    ("update_type", "update"),
    [
        ("updateUser", {"@type": "updateUser", "user": {"id": 222, "first_name": "Ada"}}),
        ("updateFile", {"@type": "updateFile", "file": {"id": 77, "size": 10}}),
    ],
)
def test_account_scoped_tdlib_updates_require_explicit_broad_surface_scope(
    session: Session,
    project_id: int,
    account_kind: str,
    update_type: str,
    update: dict[str, object],
) -> None:
    credential_ref = _connected_account(session, project_id, account_kind=account_kind)
    _store_profile(
        session,
        project_id,
        credential_ref=credential_ref,
        key="selective",
        visibility_policy={
            "surface_mode": "allowlist",
            "allowed_surface_refs": ["telegram-chat:111"],
            "allowed_update_types": [update_type],
        },
    )
    blocked = process_telegram_update(session, credential_ref=credential_ref, update=update)
    assert blocked[0].policy_status == "surface_blocked"
    assert not _records(session, project_id, "communication-event")

    _store_profile(
        session,
        project_id,
        credential_ref=credential_ref,
        key="selective",
        visibility_policy={
            "surface_mode": "all",
            "dm_mode": "allowlist",
            "group_mode": "allowlist",
            "channel_mode": "allowlist",
            "allowed_surface_refs": ["telegram-chat:111"],
            "allowed_update_types": [update_type],
            "store_non_trigger_messages": True,
        },
    )
    retained = process_telegram_update(session, credential_ref=credential_ref, update=update)
    assert retained[0].policy_status in {"observed", "invoker_blocked"}
    events = _records(session, project_id, "communication-event")
    assert len(events) == 1
    assert events[0].data_json["update_type"] == update_type
    assert events[0].data_json["surface_ref"] is None


def test_new_message_routes_only_to_ready_attached_profile_and_uses_canonical_surface(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _connected_account(session, project_id)
    _store_profile(session, project_id, credential_ref=credential_ref, key="ops")
    detached_project_id = project_id + 1
    from stackos.repositories.projects import ProjectRepository

    ProjectRepository(session).create(
        slug="detached-telegram-project",
        name="Detached Telegram Project",
        domain="detached.example.test",
        locale="en-US",
    )
    _store_profile(
        session,
        detached_project_id,
        credential_ref=credential_ref,
        key="detached",
    )

    results = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewMessage",
            "message": {
                "@type": "message",
                "id": 44,
                "chat_id": 111,
                "date": 1_770_000_000,
                "sender_id": {"@type": "messageSenderUser", "user_id": 111},
                "content": {"@type": "messageText", "text": {"text": "/help Need access"}},
            },
        },
    )

    assert len(results) == 1
    assert results[0].profile_key == "ops"
    assert results[0].policy_status == "request_created"
    assert results[0].agent_request_id is not None

    channels = _records(session, project_id, "communication-channel")
    assert len(channels) == 1
    assert channels[0].external_id == communication_surface_binding_external_id(
        provider_key="telegram",
        profile_ref="communication-profile:ops",
        surface_ref="telegram-chat:111",
    )
    assert channels[0].data_json["surface_ref"] == "telegram-chat:111"
    assert channels[0].data_json["profile_ref"] == "communication-profile:ops"
    assert _records(session, project_id, "communication-message")[0].data_json == {
        "provider_key": "telegram",
        "profile_key": "ops",
        "profile_ref": "communication-profile:ops",
        "credential_ref": credential_ref,
        "direction": "inbound",
        "surface_ref": "telegram-chat:111",
        "channel_ref": "telegram-chat:111",
        "thread_ref": None,
        "message_ref": "telegram-message:111:44",
        "provider_message_id": 44,
        "content_type": "text",
        "text_preview": "/help Need access",
        "attachments": [],
        "transport_status": "received",
        "attention_status": "unread",
        "from_ref": "telegram-user:111",
        "policy_status": "request_created",
    }
    contacts = _records(session, project_id, "communication-contact")
    assert len(contacts) == 1
    assert contacts[0].data_json["provider_refs"] == {"telegram": ["telegram-user:111"]}
    assert not _records(session, detached_project_id, "communication-event")


def test_tdlib_message_mutations_are_stored_without_creating_extra_requests(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _connected_account(session, project_id)
    _store_profile(session, project_id, credential_ref=credential_ref, key="ops")
    process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewMessage",
            "message": {
                "id": 44,
                "chat_id": 111,
                "sender_id": {"@type": "messageSenderUser", "user_id": 111},
                "content": {"@type": "messageText", "text": {"text": "First"}},
            },
        },
    )

    edited = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateMessageEdited",
            "chat_id": 111,
            "message_id": 44,
            "edit_date": 1_770_000_100,
        },
    )
    content_updated = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateMessageContent",
            "chat_id": 111,
            "message_id": 44,
            "new_content": {"@type": "messageText", "text": {"text": "Edited"}},
        },
    )
    deleted = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateDeleteMessages",
            "chat_id": 111,
            "message_ids": [44],
            "is_permanent": True,
            "from_cache": False,
        },
    )

    assert edited[0].policy_status == content_updated[0].policy_status == "observed"
    assert deleted[0].policy_status == "observed"
    assert len(AgentRequestRepository(session).list(project_id=project_id).items) == 1
    message = _records(session, project_id, "communication-message")[0]
    assert message.data_json["text_preview"] == "Edited"
    assert message.data_json["edited_at"] == 1_770_000_100
    assert message.data_json["transport_status"] == "deleted"
    assert message.data_json["delete_permanent"] is True


def test_tdlib_does_not_process_a_disabled_telegram_profile_facet(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _connected_account(session, project_id)
    _store_profile(
        session,
        project_id,
        credential_ref=credential_ref,
        key="disabled-ops",
        facet_enabled=False,
    )

    results = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewMessage",
            "message": {
                "id": 45,
                "chat_id": 111,
                "sender_id": {"@type": "messageSenderUser", "user_id": 111},
                "content": {"@type": "messageText", "text": {"text": "/help"}},
            },
        },
    )

    assert results == []
    assert not _records(session, project_id, "communication-event")
    assert not AgentRequestRepository(session).list(project_id=project_id).items


def test_tdlib_uses_verified_account_identity_and_suppresses_outgoing_channel_messages(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _connected_account(session, project_id)
    _store_profile(session, project_id, credential_ref=credential_ref, key="ops")

    mentioned = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewMessage",
            "message": {
                "id": 51,
                "chat_id": -10077,
                "sender_id": {"@type": "messageSenderUser", "user_id": 111},
                "is_outgoing": False,
                "content": {
                    "@type": "messageText",
                    "text": {"text": "/help@stackos_test_bot Need access"},
                },
            },
        },
    )
    outgoing = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewMessage",
            "message": {
                "id": 52,
                "chat_id": -10077,
                "sender_id": {"@type": "messageSenderChat", "chat_id": -10077},
                "is_outgoing": True,
                "content": {
                    "@type": "messageText",
                    "text": {"text": "/help@stackos_test_bot Status"},
                },
            },
        },
    )

    assert mentioned[0].policy_status == "request_created"
    assert outgoing[0].policy_status == "self_message_ignored"
    assert len(AgentRequestRepository(session).list(project_id=project_id).items) == 1
    messages = _records(session, project_id, "communication-message")
    assert [item.data_json["provider_message_id"] for item in messages] == [51]


def test_tdlib_callback_and_safe_chat_membership_and_file_facts(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _connected_account(session, project_id)
    _store_profile(
        session,
        project_id,
        credential_ref=credential_ref,
        key="ops",
        trigger_policy={"allow_unknown_interactions": False},
    )
    message_ref = "telegram-message:-10055:8"
    callback_token = "approuvé"
    button_external_id = telegram_callback_button_external_id(
        profile_key="ops",
        message_ref=message_ref,
        callback_data=callback_token,
    )
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
        external_id=button_external_id,
        title="Approve",
        data_json={
            "provider_key": "telegram",
            "profile_key": "ops",
            "surface_ref": "telegram-chat:-10055",
            "message_ref": message_ref,
            "status": "active",
        },
        provenance_json={"source": "test"},
    )

    callback = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewCallbackQuery",
            "id": "9007199254740993",
            "chat_id": -10055,
            "message_id": 8,
            "sender_user_id": 111,
            "payload": {
                "@type": "callbackQueryPayloadData",
                "data": base64.b64encode(callback_token.encode()).decode("ascii"),
            },
        },
    )
    chat = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewChat",
            "chat": {
                "id": -10055,
                "title": "Operations",
                "type": {"@type": "chatTypeSupergroup", "supergroup_id": 55},
            },
        },
    )
    membership = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateChatMember",
            "chat_id": -10055,
            "old_chat_member": {"status": {"@type": "chatMemberStatusLeft"}},
            "new_chat_member": {
                "member_id": {"@type": "messageSenderUser", "user_id": 111},
                "status": {"@type": "chatMemberStatusMember"},
            },
        },
    )
    file_update = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateFile",
            "file": {
                "id": 71,
                "size": 512,
                "expected_size": 512,
                "local": {
                    "path": "/private/telegram/never-store-me.jpg",
                    "is_downloading_active": False,
                    "is_downloading_completed": True,
                    "downloaded_size": 512,
                },
            },
        },
    )

    assert callback[0].policy_status == "request_created"
    assert (
        chat[0].policy_status
        == membership[0].policy_status
        == file_update[0].policy_status
        == "observed"
    )
    assert len(AgentRequestRepository(session).list(project_id=project_id).items) == 1
    button = next(
        item
        for item in _records(session, project_id, "communication-interaction")
        if item.external_id == button_external_id
    )
    assert button.data_json["status"] == "clicked"
    assert button.data_json["last_callback_ref"] == "telegram-callback:9007199254740993"
    members = _records(session, project_id, "communication-membership")
    assert members[0].data_json["member_ref"] == "telegram-user:111"
    assert members[0].data_json["status"] == "joined"
    rendered = json.dumps(
        [item.data_json for item in _records(session, project_id, "communication-event")]
    )
    assert "/private/telegram/never-store-me.jpg" not in rendered
    assert f'"file_ref": "telegram-file:{credential_ref}:71"' in rendered


@pytest.mark.parametrize(
    ("callback_id", "payload"),
    [
        (999, {"@type": "callbackQueryPayloadData", "data": "YXBwcm92ZQ=="}),
        ("not-an-id", {"@type": "callbackQueryPayloadData", "data": "YXBwcm92ZQ=="}),
        ("9223372036854775808", {"@type": "callbackQueryPayloadData", "data": "YXBwcm92ZQ=="}),
        ("999", {"@type": "callbackQueryPayloadData", "data": "approve"}),
        ("999", {"@type": "callbackQueryPayloadData", "data": "//8="}),
        ("999", {"@type": "callbackQueryPayloadData", "data": ""}),
        (
            "999",
            {
                "@type": "callbackQueryPayloadData",
                "data": base64.b64encode(b"x" * 65).decode("ascii"),
            },
        ),
        ("999", {"@type": "callbackQueryPayloadGame", "game_short_name": "x"}),
    ],
)
def test_tdlib_malformed_callback_is_not_retained(
    session: Session,
    project_id: int,
    callback_id: object,
    payload: dict[str, str],
) -> None:
    credential_ref = _connected_account(session, project_id)
    _store_profile(session, project_id, credential_ref=credential_ref, key="ops")

    result = process_telegram_update(
        session,
        credential_ref=credential_ref,
        update={
            "@type": "updateNewCallbackQuery",
            "id": callback_id,
            "chat_id": 111,
            "message_id": 8,
            "sender_user_id": 111,
            "payload": payload,
        },
    )

    assert result == []
    assert _records(session, project_id, "communication-event") == []


def _connected_account(session: Session, project_id: int, *, account_kind: str = "bot") -> str:
    is_bot = account_kind == "bot"
    stored = (
        AuthRepository(session)
        .store_credential(
            provider_key="telegram",
            auth_method_key="tdlib-bot-token" if is_bot else "tdlib-user-session",
            display_name=f"Telegram test {account_kind}",
            fields=(
                {"api_id": 12345, "api_hash": "test-api-hash", "bot_token": "1:test-token"}
                if is_bot
                else {"api_id": 12345, "api_hash": "test-api-hash"}
            ),
            attach_project_id=project_id,
        )
        .data
    )
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    credential.status = "connected"
    session.add(credential)
    assert credential.id is not None
    session.add(
        CredentialAccount(
            credential_id=credential.id,
            provider_account_id="700",
            display_name="@stackos_test_bot" if is_bot else "@stackos_test_user",
            metadata_json={
                "username": "stackos_test_bot" if is_bot else "stackos_test_user",
                "is_bot": is_bot,
            },
        )
    )
    session.commit()
    return stored.credential_ref


def _store_profile(
    session: Session,
    project_id: int,
    *,
    credential_ref: str,
    key: str,
    trigger_policy: dict[str, object] | None = None,
    facet_enabled: bool = True,
    visibility_policy: dict[str, object] | None = None,
    access_policy: dict[str, object] | None = None,
) -> None:
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id=f"communication-profile:{key}",
        title=key,
        data_json={
            "key": key,
            "profile_ref": f"communication-profile:{key}",
            "enabled": True,
            "identity": {"display_name": key, "purpose": "Test Telegram policy."},
            "provider_facets": {
                "telegram": {
                    "credential_ref": credential_ref,
                    "account_kind": "bot",
                    "enabled": facet_enabled,
                }
            },
            "access_policy": access_policy
            if access_policy is not None
            else {
                "user_mode": "allowlist",
                "allowed_user_refs": ["telegram-user:111"],
            },
            "visibility_policy": visibility_policy
            if visibility_policy is not None
            else {
                "surface_mode": "all",
                "dm_mode": "all",
                "group_mode": "all",
                "channel_mode": "all",
                "store_non_trigger_messages": True,
                "allowed_update_types": [
                    "updateNewMessage",
                    "updateMessageContent",
                    "updateMessageEdited",
                    "updateDeleteMessages",
                    "updateNewCallbackQuery",
                    "updateNewChat",
                    "updateChatMember",
                    "updateUser",
                    "updateFile",
                ],
            },
            "trigger_policy": trigger_policy
            or {
                "dm_trigger": "always",
                "group_trigger": "mention_or_command",
                "commands": [{"command": "/help"}],
            },
            "agent_guidance": {"boundaries": "No secrets."},
            "context_policy": {},
            "response_policy": {},
        },
        provenance_json={"source": "test"},
    )


def _records(session: Session, project_id: int, resource_key: str):
    return (
        ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key=resource_key,
        )
        .items
    )
