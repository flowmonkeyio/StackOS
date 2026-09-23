"""Telegram communication intent must never silently discard requested content."""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import Session, select

from stackos.actions.telegram_payloads import reply_markup
from stackos.actions.telegram_schema import TelegramMessageSend
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall, Credential
from stackos.operations.communication_delivery.payloads import _telegram_buttons, _telegram_contents
from stackos.operations.communication_delivery.schemas import CommunicationContentInput

from .conftest import MCPClient


@pytest.fixture
def telegram_target(mcp_client: MCPClient, seeded_project: dict) -> tuple[MCPClient, int]:
    project_id = int(seeded_project["data"]["id"])
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential_ref = (
            AuthRepository(session)
            .store_credential(
                provider_key="telegram",
                auth_method_key="tdlib-bot-token",
                display_name="payload-contract-bot",
                fields={
                    "api_id": 12345,
                    "api_hash": "test-telegram-application-hash",
                    "bot_token": "123456:ABC",
                    "proxy_enabled": False,
                },
                attach_project_id=project_id,
            )
            .data.credential_ref
        )
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        credential.status = "connected"
        session.add(credential)
        session.commit()
    mcp_client.call_tool_structured(
        "communicationProfile.upsert",
        {
            "project_id": project_id,
            "key": "payload-contract-bot",
            "identity": {"display_name": "Payload Contract Bot"},
            "provider_facets": {"telegram": {"credential_ref": credential_ref}},
        },
    )
    mcp_client.call_tool_structured(
        "communicationTarget.upsert",
        {
            "project_id": project_id,
            "key": "payload-contract-chat",
            "provider_key": "telegram",
            "surface_ref": "telegram-chat:12345",
            "profile_ref": "communication-profile:payload-contract-bot",
            "metadata_json": {"action_mode": "auto"},
            "send_policy": {
                "mode": "explicit-target",
                "allowed_profile_refs": ["communication-profile:payload-contract-bot"],
                "allowed_target_refs": ["communication-target:payload-contract-chat"],
            },
        },
    )
    return mcp_client, project_id


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("content", "failed_path"),
    [
        ({"text": "*Heading*", "format": "mrkdwn"}, "/content/format"),
        ({"text": "Body", "subject": "Heading"}, "/content/subject"),
        ({"text": "Body", "html": "<b>Body</b>"}, "/content/html"),
        (
            {
                "text": "Message caption",
                "attachments": [
                    {
                        "type": "image",
                        "url": "https://example.test/photo.jpg",
                        "caption": "Attachment caption",
                    }
                ],
            },
            "/content/attachments/0/caption",
        ),
    ],
)
def test_telegram_content_that_cannot_be_preserved_rejects_before_action(
    telegram_target: tuple[MCPClient, int],
    content: dict[str, Any],
    failed_path: str,
    dry_run: bool,
) -> None:
    mcp_client, project_id = telegram_target
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        before = len(session.exec(select(ActionCall)).all())

    err = mcp_client.call_tool_error(
        "communication.send",
        {
            "project_id": project_id,
            "to": "payload-contract-chat",
            "content": content,
            "dry_run": dry_run,
        },
    )

    detail = err["data"]["error"]
    assert detail["code"] == "COMM_UNSUPPORTED_CONTENT_SHAPE"
    assert detail["effect"] == "none"
    assert failed_path in {item["path"] for item in detail["failed_paths"]}
    with Session(engine) as session:
        assert len(session.exec(select(ActionCall)).all()) == before


@pytest.mark.parametrize(
    ("format_value", "expected"),
    [("markdown", "markdown"), ("html", "html")],
)
def test_telegram_supported_text_format_is_preserved(format_value: str, expected: str) -> None:
    content = CommunicationContentInput(text="<b>Body</b>", format=format_value)
    assert _telegram_contents(content) == [
        {"kind": "text", "text": "<b>Body</b>", "format": expected}
    ]


def test_telegram_control_style_is_preserved_in_action_payload() -> None:
    content = CommunicationContentInput(
        text="Approve?",
        controls=[
            {"label": "Approve", "value": "approve:1", "style": "primary"},
            {"label": "Reject", "value": "reject:1", "style": "danger"},
        ],
    )
    assert _telegram_buttons(content) == [
        [
            {"text": "Approve", "callback_data": "approve:1", "style": "primary"},
            {"text": "Reject", "callback_data": "reject:1", "style": "danger"},
        ]
    ]
    parsed = TelegramMessageSend.model_validate(
        {
            "profile_ref": "communication-profile:payload-contract-bot",
            "surface_ref": "telegram-chat:12345",
            "content": _telegram_contents(content)[0],
            "buttons": _telegram_buttons(content),
        }
    )
    markup = reply_markup(parsed.buttons)
    assert markup is not None
    assert [button["style"]["@type"] for button in markup["rows"][0]] == [
        "buttonStylePrimary",
        "buttonStyleDanger",
    ]


def test_telegram_styled_control_survives_public_dry_run(
    telegram_target: tuple[MCPClient, int],
) -> None:
    mcp_client, project_id = telegram_target
    result = mcp_client.call_tool_structured(
        "communication.send",
        {
            "project_id": project_id,
            "to": "payload-contract-chat",
            "content": {
                "text": "Approve?",
                "controls": [{"label": "Approve", "value": "approve:1", "style": "primary"}],
            },
            "dry_run": True,
        },
    )
    assert result["data"]["dry_run"] is True
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        call = session.get(ActionCall, result["data"]["action_call_id"])
        assert call is not None
        assert call.request_json["buttons"][0][0]["style"] == "primary"
