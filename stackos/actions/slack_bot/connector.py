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

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.repositories.base import ValidationError

from .files import _upload_files
from .http import _slack_api
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
from .validation import validate_slack_request


class SlackBotActionConnector:
    """Decision-free adapter for explicit Slack Web API calls."""

    key = "slack-bot"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        return validate_slack_request(request)

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        match request.operation:
            case "identity.get":
                # Slack auth.test:
                # https://docs.slack.dev/reference/methods/auth.test/
                status, body, headers = await _slack_api(request, "POST", "auth.test")
                return _identity_result(request, status, body, headers)
            case "message.send":
                _communication_profile_key(request)
                body_json = _message_payload(request)
                # Slack chat.postMessage:
                # https://docs.slack.dev/reference/methods/chat.postMessage/
                status, body, headers = await _slack_api(
                    request,
                    "POST",
                    "chat.postMessage",
                    json_body=body_json,
                )
                _store_outbound_message(request, body, body_json)
                return _message_result(request, status, body, headers, body_json)
            case "file.upload":
                _communication_profile_key(request)
                # Slack external file upload:
                # https://docs.slack.dev/reference/methods/files.getUploadURLExternal/
                # https://docs.slack.dev/reference/methods/files.completeUploadExternal/
                return await _upload_files(request)
            case "reaction.add":
                _communication_profile_key(request)
                body_json = _reaction_add_payload(request)
                # Slack reactions.add:
                # https://docs.slack.dev/reference/methods/reactions.add/
                status, body, headers = await _slack_api(
                    request,
                    "POST",
                    "reactions.add",
                    json_body=body_json,
                )
                _store_reaction_add(request, body_json)
                return _reaction_add_result(request, status, body, headers, body_json)
            case "message.delete":
                _communication_profile_key(request)
                body_json = _message_delete_payload(request)
                # Slack chat.delete:
                # https://docs.slack.dev/reference/methods/chat.delete/
                status, body, headers = await _slack_api(
                    request,
                    "POST",
                    "chat.delete",
                    json_body=body_json,
                )
                _mark_message_deleted(request, body_json)
                return _message_delete_result(request, status, body, headers, body_json)
            case "conversation.open":
                _communication_profile_key(request)
                # Slack conversations.open:
                # https://docs.slack.dev/reference/methods/conversations.open/
                status, body, headers = await _slack_api(
                    request,
                    "POST",
                    "conversations.open",
                    json_body=_conversation_open_payload(request),
                )
                _store_conversation_from_body(request, body)
                return _conversation_open_result(request, status, body, headers)
            case "conversation.info":
                _communication_profile_key(request)
                # Slack conversations.info:
                # https://docs.slack.dev/reference/methods/conversations.info/
                status, body, headers = await _slack_api(
                    request,
                    "GET",
                    "conversations.info",
                    params=_conversation_info_params(request),
                )
                _store_conversation_from_body(request, body)
                return _conversation_info_result(request, status, body, headers)
            case "conversation.list":
                _communication_profile_key(request)
                # Slack conversations.list:
                # https://docs.slack.dev/reference/methods/conversations.list/
                status, body, headers = await _slack_api(
                    request,
                    "GET",
                    "conversations.list",
                    params=_conversation_list_params(request),
                )
                _store_conversation_list(request, body)
                return _conversation_list_result(request, status, body, headers)
            case "conversation.members":
                _communication_profile_key(request)
                # Slack conversations.members:
                # https://docs.slack.dev/reference/methods/conversations.members/
                status, body, headers = await _slack_api(
                    request,
                    "GET",
                    "conversations.members",
                    params=_conversation_members_params(request),
                )
                _store_memberships_from_body(request, body)
                return _conversation_members_result(request, status, body, headers)
            case "conversation.history":
                _communication_profile_key(request)
                # Slack conversations.history:
                # https://docs.slack.dev/reference/methods/conversations.history/
                status, body, headers = await _slack_api(
                    request,
                    "GET",
                    "conversations.history",
                    params=_conversation_history_params(request),
                )
                return _conversation_history_result(request, status, body, headers)
            case _:
                raise ValidationError(f"unsupported Slack operation {request.operation!r}")
