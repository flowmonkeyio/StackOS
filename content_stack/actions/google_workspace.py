"""Google Workspace action connector.

Official docs verified:
- Gmail send: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send
- Gmail MIME send guide: https://developers.google.com/gmail/api/guides/sending
- Calendar event insert: https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
"""

from __future__ import annotations

from typing import Any

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.provider_utils import (
    bearer_headers,
    dict_field,
    q,
    required_str,
    resolve_ref,
    result,
    send_json,
    unknown_operation,
)
from content_stack.repositories.base import ValidationError

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


def _user_id(request: ActionConnectorRequest) -> str:
    value = request.input_json.get("user_ref") or "me"
    return str(resolve_ref(request, value, "users", "mailboxes"))


class GoogleWorkspaceActionConnector:
    """Decision-free adapter for Gmail and Calendar APIs."""

    key = "google-workspace"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "gmail.message.send":
                dict_field(payload, "message", issues, required=True)
            case "calendar.event.create":
                required_str(payload, "calendar_ref", issues)
                dict_field(payload, "event", issues, required=True)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        headers = bearer_headers(request, "access_token", "token")
        payload = request.input_json
        match request.operation:
            case "gmail.message.send":
                message = payload.get("message")
                if not isinstance(message, dict) or not isinstance(message.get("raw"), str):
                    raise ValidationError("Gmail send requires message.raw base64url MIME")
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_GMAIL_BASE}/users/{q(_user_id(request))}/messages/send",
                    headers=headers,
                    json_body=message,
                )
            case "calendar.event.create":
                event = payload.get("event")
                if not isinstance(event, dict):
                    raise ValidationError("Google Calendar event must be an object")
                params: dict[str, Any] = {}
                event_param_keys = (
                    "sendUpdates",
                    "send_updates",
                    "conferenceDataVersion",
                    "conference_data_version",
                )
                for key in event_param_keys:
                    if key in payload:
                        params[_camel(key)] = payload[key]
                calendar_id = str(resolve_ref(request, payload["calendar_ref"], "calendars"))
                status, body, response_headers = await send_json(
                    method="POST",
                    url=f"{_CALENDAR_BASE}/calendars/{q(calendar_id)}/events",
                    headers=headers,
                    params=params,
                    json_body=event,
                )
            case _:
                raise ValidationError(
                    f"unsupported Google Workspace operation {request.operation!r}"
                )
        return result(
            provider="google-workspace",
            operation=request.operation,
            status_code=status,
            body=body,
            headers=response_headers,
        )


def _camel(key: str) -> str:
    return {
        "send_updates": "sendUpdates",
        "conference_data_version": "conferenceDataVersion",
    }.get(key, key)


__all__ = ["GoogleWorkspaceActionConnector"]
