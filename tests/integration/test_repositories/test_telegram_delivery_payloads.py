"""Normal communication intent uses the same typed Telegram action producer."""

import pytest
from sqlmodel import Session

from stackos.actions.telegram_schema import TelegramAlbumSend, TelegramMessageSend
from stackos.auth_providers import AuthRepository
from stackos.operations.communication_delivery.payloads import _build_provider_payload
from stackos.operations.communication_delivery.schemas import (
    CommunicationContentInput,
    CommunicationContextInput,
    CommunicationDeliveryInput,
)
from stackos.operations.communication_platform.schemas import CommunicationTargetOut


@pytest.mark.parametrize("count", [0, 1, 2])
def test_native_telegram_intent_payload(session: Session, project_id: int, count: int) -> None:
    account = (
        AuthRepository(session)
        .store_credential(
            provider_key="telegram",
            auth_method_key="tdlib-bot-token",
            display_name="Telegram payload test bot",
            fields={
                "api_id": 12345,
                "api_hash": "test-telegram-application-hash",
                "bot_token": "123456:test-bot-token",
            },
            attach_project_id=project_id,
        )
        .data
    )
    target = CommunicationTargetOut(
        record_id=1,
        enabled=True,
        action_input_defaults={},
        metadata_json={"action_mode": "auto"},
        project_id=project_id,
        key="news",
        target_ref="communication-target:news",
        provider_key="telegram",
        surface_ref="telegram-chat:-100123",
        profile_ref="communication-profile:ops",
        action_ref="communications.telegram.message.send",
        send_policy={"mode": "explicit-target"},
    )
    result = _build_provider_payload(
        session=session,
        project_id=project_id,
        provider_key="telegram",
        action_ref=target.action_ref,
        actor={
            "profile_ref": "communication-profile:ops",
            "profile_key": "ops",
            "credential_ref": account.credential_ref,
        },
        target=target,
        content=CommunicationContentInput(
            text="Update",
            format="html",
            attachments=[
                {"type": "image", "file_id": f"telegram-file:cred_test:{42 + i}"}
                for i in range(count)
            ],
            controls=[] if count > 1 else [{"label": "Read", "url": "https://example.test"}],
        ),
        delivery=CommunicationDeliveryInput(disable_notification=True, reply_mode="message_reply"),
        context=CommunicationContextInput(reply_to="telegram-message:-100123:1048576"),
        source={},
        surface={},
        operation="communication.send",
    )
    model = TelegramAlbumSend if count > 1 else TelegramMessageSend
    parsed = model.model_validate(result["input_json"])
    assert parsed.surface_ref == target.surface_ref
    assert parsed.options.disable_notification is True
    assert parsed.reply_to_message_id == 1048576
    assert (
        result["action_ref"]
        == f"communications.telegram.{'album.send' if count > 1 else 'message.send'}"
    )
    if count == 1:
        assert parsed.content.kind == "photo"
        assert parsed.content.file.file_ref == "telegram-file:cred_test:42"
        assert parsed.content.caption == "Update"
