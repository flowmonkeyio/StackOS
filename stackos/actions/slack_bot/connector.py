"""Slack Web API action connector.

Official docs verified:
- auth.test: https://docs.slack.dev/reference/methods/auth.test/
- chat.postMessage: https://docs.slack.dev/reference/methods/chat.postMessage/
- files.getUploadURLExternal: https://docs.slack.dev/reference/methods/files.getUploadURLExternal/
- files.completeUploadExternal: https://docs.slack.dev/reference/methods/files.completeUploadExternal/
- conversations.open: https://docs.slack.dev/reference/methods/conversations.open/
- conversations.info: https://docs.slack.dev/reference/methods/conversations.info/
- conversations.list: https://docs.slack.dev/reference/methods/conversations.list/
- conversations.members: https://docs.slack.dev/reference/methods/conversations.members/
- Block Kit buttons: https://docs.slack.dev/reference/block-kit/block-elements/button-element/
- Actions block: https://docs.slack.dev/reference/block-kit/blocks/actions-block/
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.artifacts import redact_secrets
from stackos.repositories.base import ValidationError

from .files import _upload_files
from .payloads import (
    _conversation_history_params,
    _conversation_info_params,
    _conversation_list_params,
    _conversation_members_params,
    _conversation_open_payload,
    _message_delete_payload,
    _message_payload,
    _reaction_add_payload,
)
from .profile import _communication_profile_key
from .results import (
    _conversation_history_result,
    _conversation_info_result,
    _conversation_list_result,
    _conversation_members_result,
    _conversation_open_result,
    _identity_result,
    _message_delete_result,
    _message_result,
    _reaction_add_result,
)
from .storage import (
    _mark_message_deleted,
    _store_conversation_from_body,
    _store_conversation_list,
    _store_memberships_from_body,
    _store_outbound_message,
    _store_reaction_add,
)
from .validation import history_content_option_issues, validate_slack_request


class SlackBotActionConnector:
    """Resolve host references and project native Slack receipts into communication state."""

    key = "slack-bot"

    def __init__(self, *, client=None, options=None):
        self._client = client
        self._options = options

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        return validate_slack_request(request)

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        return 0

    async def _native(self, request, prepared):
        from stackos_connectors import ConnectorClient
        from stackos_connectors.catalog import load_registry

        from stackos.actions.package_bridge import PackageActionConnector

        if self._client is None:
            self._client = ConnectorClient(
                registry=load_registry("connectors/slack_bot/catalog.json")
            )
        return await PackageActionConnector(
            "slack-bot", client=self._client, options=self._options
        ).execute_native(replace(request, input_json=prepared))

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if option_issues := history_content_option_issues(request):
            raise ValidationError(option_issues[0].message)
        if request.operation != "identity.get":
            _communication_profile_key(request)
        if request.operation == "file.upload":
            return await _upload_files(request, self)
        builders = {
            "identity.get": lambda request: {},
            "message.send": _message_payload,
            "reaction.add": _reaction_add_payload,
            "message.delete": _message_delete_payload,
            "conversation.open": _conversation_open_payload,
            "conversation.info": _conversation_info_params,
            "conversation.list": _conversation_list_params,
            "conversation.members": _conversation_members_params,
            "conversation.history": _conversation_history_params,
        }
        if request.operation not in builders:
            raise ValidationError(f"unsupported Slack operation {request.operation!r}")
        prepared = builders[request.operation](request)
        native = await self._native(request, prepared)
        output = native.output_json
        status, body, headers = (
            output["status_code"],
            output["data"],
            httpx.Headers(output["headers"]),
        )
        projectors: dict[str, Callable[..., ActionConnectorResult]] = {
            "identity.get": _identity_result,
            "message.send": _message_result,
            "reaction.add": _reaction_add_result,
            "message.delete": _message_delete_result,
            "conversation.open": _conversation_open_result,
            "conversation.info": _conversation_info_result,
            "conversation.list": _conversation_list_result,
            "conversation.members": _conversation_members_result,
            "conversation.history": _conversation_history_result,
        }
        result = projectors[request.operation](
            request,
            status,
            body,
            headers,
            *(
                [prepared]
                if request.operation in {"message.send", "reaction.add", "message.delete"}
                else []
            ),
        )
        result.metadata_json = {**(result.metadata_json or {}), **(native.metadata_json or {})}
        result.output_json = redact_secrets(result.output_json)
        result.metadata_json = redact_secrets(result.metadata_json)
        try:
            match request.operation:
                case "message.send":
                    _store_outbound_message(request, body, prepared)
                case "reaction.add":
                    _store_reaction_add(request, prepared)
                case "message.delete":
                    _mark_message_deleted(request, prepared)
                case "conversation.open" | "conversation.info":
                    _store_conversation_from_body(request, body)
                case "conversation.list":
                    _store_conversation_list(request, body)
                case "conversation.members":
                    _store_memberships_from_body(request, body)
        except Exception as exc:
            raise ActionConnectorError(
                "Slack operation completed but communication state could not be stored",
                output_json=result.output_json,
                metadata_json={
                    **(result.metadata_json or {}),
                    "provider_executed": True,
                    "retry_safe": False,
                },
            ) from exc
        return result
