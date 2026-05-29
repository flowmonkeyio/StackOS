"""Slack Web API action input validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest, ActionValidationIssue
from stackos.actions.provider_utils import issue, unknown_operation

from .constants import (
    _CONVERSATION_TYPES,
    _MAX_ACTION_BLOCK_ELEMENTS,
    _MAX_BLOCKS,
    _MAX_BUTTON_ACTION_ID_CHARS,
    _MAX_BUTTON_TEXT_CHARS,
    _MAX_BUTTON_URL_CHARS,
    _MAX_BUTTON_VALUE_CHARS,
    _MAX_CONVERSATION_LIMIT,
    _MAX_OPEN_USERS,
    _MAX_TEXT_CHARS,
    _SECRETISH_BUTTON_RE,
)

_MAX_REACTION_NAME_CHARS = 128
_MAX_UPLOAD_FILES = 10


def validate_slack_request(request: ActionConnectorRequest) -> list[ActionValidationIssue]:
    payload = request.input_json
    issues: list[ActionValidationIssue] = []
    match request.operation:
        case "identity.get":
            return []
        case "message.send":
            _required_any(payload, ("channel_ref", "surface_ref"), issues)
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_text(payload, "surface_ref", issues)
            _optional_text(payload, "text", issues, max_chars=_MAX_TEXT_CHARS)
            _optional_text(payload, "thread_ref", issues)
            _optional_bool(payload, "reply_broadcast", issues)
            _optional_bool(payload, "unfurl_links", issues)
            _optional_bool(payload, "unfurl_media", issues)
            _optional_int(payload, "source_agent_request_id", issues, minimum=1)
            _blocks(payload.get("blocks"), issues)
            if not _has_text(payload.get("text")) and not isinstance(payload.get("blocks"), list):
                issues.append(issue("$", "text or blocks is required", "required"))
        case "file.upload":
            _required_any(payload, ("channel_ref", "surface_ref"), issues)
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_text(payload, "surface_ref", issues)
            _optional_text(payload, "thread_ref", issues)
            _optional_text(payload, "initial_comment", issues, max_chars=_MAX_TEXT_CHARS)
            _optional_int(payload, "source_agent_request_id", issues, minimum=1)
            _optional_bool(payload, "delete_after_upload", issues)
            _file_or_files(payload, issues)
        case "reaction.add":
            _required_text(payload, "message_ref", issues)
            _required_text(payload, "name", issues, max_chars=_MAX_REACTION_NAME_CHARS)
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_text(payload, "surface_ref", issues)
        case "message.delete":
            _required_text(payload, "message_ref", issues)
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_text(payload, "surface_ref", issues)
        case "conversation.open":
            if "users" not in payload and "channel_ref" not in payload:
                issues.append(issue("$", "users or channel_ref is required", "required"))
            _optional_text(payload, "profile_ref", issues)
            _user_list(payload.get("users"), issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_bool(payload, "return_im", issues)
        case "conversation.info":
            _required_any(payload, ("channel_ref", "surface_ref"), issues)
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_text(payload, "surface_ref", issues)
            _optional_bool(payload, "include_num_members", issues)
        case "conversation.list":
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "cursor", issues)
            _optional_text(payload, "team_id", issues)
            _optional_bool(payload, "exclude_archived", issues)
            _optional_int(
                payload,
                "limit",
                issues,
                minimum=1,
                maximum=_MAX_CONVERSATION_LIMIT,
            )
            _conversation_types(payload.get("types"), issues)
        case "conversation.members":
            _required_any(payload, ("channel_ref", "surface_ref"), issues)
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_text(payload, "surface_ref", issues)
            _optional_text(payload, "cursor", issues)
            _optional_int(
                payload,
                "limit",
                issues,
                minimum=1,
                maximum=_MAX_CONVERSATION_LIMIT,
            )
        case "conversation.history":
            _required_any(payload, ("channel_ref", "surface_ref"), issues)
            _optional_text(payload, "profile_ref", issues)
            _optional_text(payload, "channel_ref", issues)
            _optional_text(payload, "surface_ref", issues)
            _optional_text(payload, "cursor", issues)
            _optional_text(payload, "latest", issues)
            _optional_text(payload, "oldest", issues)
            _optional_bool(payload, "inclusive", issues)
            _optional_int(
                payload,
                "limit",
                issues,
                minimum=1,
                maximum=200,
            )
        case _:
            issues.extend(unknown_operation(request))
    return issues


def _required_any(
    payload: Mapping[str, Any],
    keys: tuple[str, ...],
    issues: list[ActionValidationIssue],
) -> None:
    if any(_has_text(payload.get(key)) for key in keys):
        return
    issues.append(issue("$", f"one of {', '.join(keys)} is required", "required"))


def _required_text(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    max_chars: int | None = None,
) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if max_chars is not None and len(value) > max_chars:
        issues.append(issue(f"$.{key}", f"{key} must be at most {max_chars} characters", "length"))


def _optional_text(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    max_chars: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} must be a non-empty string", "type_error"))
        return
    if max_chars is not None and len(value) > max_chars:
        issues.append(issue(f"$.{key}", f"{key} must be at most {max_chars} characters", "length"))


def _optional_bool(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        issues.append(issue(f"$.{key}", f"{key} must be a boolean", "type_error"))


def _optional_int(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        issues.append(issue(f"$.{key}", f"{key} must be an integer >= {minimum}", "range"))
        return
    if maximum is not None and value > maximum:
        issues.append(issue(f"$.{key}", f"{key} must be <= {maximum}", "range"))


def _user_list(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value:
        issues.append(issue("$.users", "users must be a non-empty array", "type_error"))
        return
    if len(value) > _MAX_OPEN_USERS:
        issues.append(issue("$.users", f"users must contain at most {_MAX_OPEN_USERS} items"))
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(issue(f"$.users[{index}]", "user ref must be a string", "type_error"))


def _conversation_types(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value:
        issues.append(issue("$.types", "types must be a non-empty array", "type_error"))
        return
    for index, item in enumerate(value):
        if item not in _CONVERSATION_TYPES:
            issues.append(issue(f"$.types[{index}]", "unsupported conversation type", "enum"))


def _blocks(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(issue("$.blocks", "blocks must be an array", "type_error"))
        return
    if len(value) > _MAX_BLOCKS:
        issues.append(issue("$.blocks", f"blocks must contain at most {_MAX_BLOCKS} items"))
    for block_index, block in enumerate(value):
        if not isinstance(block, Mapping):
            issues.append(issue(f"$.blocks[{block_index}]", "block must be an object"))
            continue
        if block.get("type") != "actions":
            continue
        elements = block.get("elements")
        if not isinstance(elements, list):
            issues.append(
                issue(
                    f"$.blocks[{block_index}].elements",
                    "actions block elements must be an array",
                    "type_error",
                )
            )
            continue
        if len(elements) > _MAX_ACTION_BLOCK_ELEMENTS:
            issues.append(
                issue(
                    f"$.blocks[{block_index}].elements",
                    f"actions block may contain at most {_MAX_ACTION_BLOCK_ELEMENTS} elements",
                    "length",
                )
            )
        for element_index, element in enumerate(elements):
            _button_element(
                element,
                issues,
                f"$.blocks[{block_index}].elements[{element_index}]",
            )


def _button_element(value: Any, issues: list[ActionValidationIssue], path: str) -> None:
    if not isinstance(value, Mapping) or value.get("type") != "button":
        return
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        issues.append(issue(f"{path}.action_id", "button action_id is required", "required"))
    elif len(action_id) > _MAX_BUTTON_ACTION_ID_CHARS:
        issues.append(
            issue(
                f"{path}.action_id",
                f"button action_id must be at most {_MAX_BUTTON_ACTION_ID_CHARS} characters",
                "length",
            )
        )
    text = value.get("text")
    if not isinstance(text, Mapping) or not _has_text(text.get("text")):
        issues.append(issue(f"{path}.text", "button text object is required", "required"))
    elif len(str(text.get("text"))) > _MAX_BUTTON_TEXT_CHARS:
        issues.append(
            issue(
                f"{path}.text.text",
                f"button text must be at most {_MAX_BUTTON_TEXT_CHARS} characters",
                "length",
            )
        )
    button_value = value.get("value")
    if button_value is not None:
        if not isinstance(button_value, str):
            issues.append(issue(f"{path}.value", "button value must be a string", "type_error"))
        else:
            if len(button_value) > _MAX_BUTTON_VALUE_CHARS:
                issues.append(
                    issue(
                        f"{path}.value",
                        f"button value must be at most {_MAX_BUTTON_VALUE_CHARS} characters",
                        "length",
                    )
                )
            if _SECRETISH_BUTTON_RE.search(button_value):
                issues.append(
                    issue(
                        f"{path}.value",
                        "button value must be an opaque non-secret routing token",
                        "secret_like",
                    )
                )
    url = value.get("url")
    if url is not None:
        if not isinstance(url, str):
            issues.append(issue(f"{path}.url", "button url must be a string", "type_error"))
        elif len(url) > _MAX_BUTTON_URL_CHARS:
            issues.append(
                issue(
                    f"{path}.url",
                    f"button url must be at most {_MAX_BUTTON_URL_CHARS} characters",
                    "length",
                )
            )


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _file_or_files(payload: Mapping[str, Any], issues: list[ActionValidationIssue]) -> None:
    has_file = isinstance(payload.get("file"), Mapping)
    has_files = isinstance(payload.get("files"), list)
    if has_file == has_files:
        issues.append(issue("$", "exactly one of file or files is required", "one_of"))
        return
    if has_file:
        _upload_file(payload.get("file"), issues, "$.file")
    else:
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            issues.append(issue("$.files", "files must be a non-empty array", "required"))
            return
        if len(files) > _MAX_UPLOAD_FILES:
            issues.append(
                issue("$.files", f"files must contain at most {_MAX_UPLOAD_FILES} items", "length")
            )
        for index, item in enumerate(files):
            _upload_file(item, issues, f"$.files[{index}]")


def _upload_file(value: Any, issues: list[ActionValidationIssue], path: str) -> None:
    if not isinstance(value, Mapping):
        issues.append(issue(path, "file must be an object", "type_error"))
        return
    artifact_ref = value.get("artifact_ref")
    if not isinstance(artifact_ref, str) or not artifact_ref.strip():
        issues.append(issue(f"{path}.artifact_ref", "artifact_ref is required", "required"))
    file_type = value.get("type")
    if file_type is not None and file_type not in {"image", "document", "file"}:
        issues.append(issue(f"{path}.type", "unsupported file type", "enum"))
    for key in ("filename", "title", "mime_type", "caption"):
        raw = value.get(key)
        if raw is not None and not isinstance(raw, str):
            issues.append(issue(f"{path}.{key}", f"{key} must be a string", "type_error"))
