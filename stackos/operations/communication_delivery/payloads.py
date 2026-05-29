"""Provider payload construction and capability validation."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from stackos.artifacts import redact_secrets
from stackos.operations.communication_platform import (
    CommunicationTargetOut,
    _target_action_defaults,
)

from .errors import _reject
from .policy import (
    _ensure_provider_action_ref,
    _ensure_target_allows_resolved_action_ref,
)
from .schemas import (
    CommunicationContentInput,
    CommunicationContextInput,
    CommunicationControlInput,
    CommunicationDeliveryInput,
)
from .utils import _has_text, _stable_digest


def _build_provider_payload(
    *,
    session: Session,
    project_id: int,
    provider_key: str,
    action_ref: str | None,
    actor: dict[str, Any],
    target: CommunicationTargetOut,
    content: CommunicationContentInput,
    delivery: CommunicationDeliveryInput,
    context: CommunicationContextInput,
    source: dict[str, Any],
    surface: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    if action_ref is None:
        _reject(
            code="COMM_PROVIDER_ACTION_MISSING",
            category="provider",
            message=f"Provider {provider_key} has no configured message send action.",
            resolved={"operation": operation, "provider": provider_key},
            failed_paths=[{"path": "/to", "requested": target.target_ref}],
        )
    assert action_ref is not None
    capabilities = _effective_capabilities(provider_key, surface)
    _validate_delivery_options(
        operation=operation,
        provider_key=provider_key,
        target=target,
        delivery=delivery,
    )
    _validate_content_shape(
        operation=operation,
        provider_key=provider_key,
        target=target,
        content=content,
    )
    required = _required_capabilities(content, delivery=delivery, provider_key=provider_key)
    unsupported = [item for item in required if item["capability"] not in capabilities]
    if unsupported:
        _reject_unsupported_capability(
            operation=operation,
            provider_key=provider_key,
            target=target,
            actor_ref=actor["profile_ref"],
            requested=unsupported,
            capabilities=capabilities,
        )
    if not bool(surface.get("send_enabled", True)):
        _reject(
            code="COMM_SURFACE_SEND_DISABLED",
            category="policy",
            message=f"Surface {target.surface_ref} is not enabled for sends.",
            resolved={
                "operation": operation,
                "provider": provider_key,
                "surface_ref": target.surface_ref,
            },
            failed_paths=[{"path": "/to", "requested": target.target_ref}],
        )

    defaults = _target_action_defaults(session, target)
    source_request_id = (
        context.source_request_id
        if context.source_request_id is not None
        else source.get("source_request_id")
    )
    _ensure_delivery_context(
        operation=operation,
        provider_key=provider_key,
        target=target,
        delivery=delivery,
        context=context,
        source=source,
    )
    if provider_key == "slack-bot":
        resolved_action_ref = (
            "communications.slack-bot.file.upload"
            if content.attachments
            else "communications.slack-bot.message.send"
        )
        _ensure_provider_action_ref(
            operation=operation,
            provider_key=provider_key,
            action_ref=action_ref,
            allowed={
                "communications.slack-bot.message.send",
                "communications.slack-bot.file.upload",
            },
            target=target,
        )
        _ensure_target_allows_resolved_action_ref(
            operation=operation,
            provider_key=provider_key,
            configured_action_ref=action_ref,
            resolved_action_ref=resolved_action_ref,
            target=target,
        )
        input_json = {
            **defaults,
            "profile_ref": actor["profile_ref"],
            "surface_ref": target.surface_ref,
        }
        thread_ref = _delivery_thread_ref(delivery, context, target=target, source=source)
        if content.attachments:
            input_json["files"] = _file_items(content)
            if _has_text(content.text):
                input_json["initial_comment"] = content.text
            if thread_ref:
                input_json["thread_ref"] = thread_ref
            if source_request_id is not None:
                input_json["source_agent_request_id"] = source_request_id
            input_json["delete_after_upload"] = True
            return {"action_ref": resolved_action_ref, "input_json": input_json}
        if _has_text(content.text):
            input_json["text"] = content.text
        blocks = _slack_blocks(content)
        if blocks:
            input_json["blocks"] = blocks
        if thread_ref:
            input_json["thread_ref"] = thread_ref
        if delivery.reply_broadcast is not None:
            input_json["reply_broadcast"] = delivery.reply_broadcast
        if source_request_id is not None:
            input_json["source_agent_request_id"] = source_request_id
        control_metadata = _control_metadata(content)
        if control_metadata:
            input_json["control_metadata"] = control_metadata
        return {"action_ref": action_ref, "input_json": input_json}

    if provider_key == "telegram-bot":
        resolved_action_ref = (
            "communications.telegram-bot.file.upload"
            if content.attachments
            else "communications.telegram-bot.message.send"
        )
        _ensure_provider_action_ref(
            operation=operation,
            provider_key=provider_key,
            action_ref=action_ref,
            allowed={
                "communications.telegram-bot.message.send",
                "communications.telegram-bot.photo.send",
                "communications.telegram-bot.file.upload",
            },
            target=target,
        )
        _ensure_target_allows_resolved_action_ref(
            operation=operation,
            provider_key=provider_key,
            configured_action_ref=action_ref,
            resolved_action_ref=resolved_action_ref,
            target=target,
        )
        input_json = {
            **defaults,
            "profile_key": actor["profile_key"],
            "chat_ref": target.surface_ref,
        }
        if delivery.disable_notification is not None:
            input_json["disable_notification"] = delivery.disable_notification
        if source_request_id is not None:
            input_json["source_agent_request_id"] = source_request_id
        thread_ref = _delivery_thread_ref(delivery, context, target=target, source=source)
        if thread_ref:
            input_json["thread_ref"] = thread_ref
        if delivery.reply_mode == "message_reply" and (
            context.reply_to or source.get("message_ref")
        ):
            input_json["reply_to_message_ref"] = context.reply_to or source.get("message_ref")
        reply_markup = _telegram_reply_markup(content)
        if reply_markup:
            input_json["reply_markup"] = reply_markup
        parse_mode = _telegram_parse_mode(content.format)
        if parse_mode:
            input_json["parse_mode"] = parse_mode
        control_metadata = _control_metadata(content, max_token_bytes=64)
        if control_metadata:
            input_json["control_metadata"] = control_metadata
        if content.attachments:
            items = _file_items(content)
            if len(items) == 1:
                input_json["file"] = items[0]
            else:
                input_json["files"] = items
            caption = content.text or content.attachments[0].caption
            if caption:
                input_json["caption"] = caption
            input_json["delete_after_upload"] = True
            return {"action_ref": resolved_action_ref, "input_json": input_json}
        input_json["text"] = content.text or ""
        return {"action_ref": resolved_action_ref, "input_json": input_json}

    if provider_key == "smtp":
        _ensure_provider_action_ref(
            operation=operation,
            provider_key=provider_key,
            action_ref=action_ref,
            allowed={"communications.smtp.email.send"},
            target=target,
        )
        input_json = {**defaults}
        if _has_text(content.subject):
            input_json["subject"] = content.subject
        if _has_text(content.html):
            input_json["html"] = content.html
        if _has_text(content.text):
            input_json["text"] = content.text
        if source_request_id is not None:
            input_json["source_agent_request_id"] = source_request_id
        missing = [key for key in ("recipients", "subject") if key not in input_json]
        if missing:
            _reject(
                code="COMM_EMAIL_FIELD_REQUIRED",
                category="input",
                message=f"SMTP target requires {', '.join(missing)} before sending.",
                resolved={
                    "operation": operation,
                    "provider": provider_key,
                    "target_ref": target.target_ref,
                },
                failed_paths=[
                    {"path": f"/content/{key}", "requested": key}
                    for key in missing
                    if key == "subject"
                ]
                + [
                    {"path": "/to", "requested": "target.action_input_defaults.recipients"}
                    for key in missing
                    if key == "recipients"
                ],
                repair_options=[
                    {
                        "id": "provide_email_fields",
                        "description": (
                            "Add subject and configure recipients on the communication target."
                        ),
                    }
                ],
            )
        return {"action_ref": action_ref, "input_json": input_json}

    _reject(
        code="COMM_UNSUPPORTED_PROVIDER",
        category="provider",
        message=f"Provider {provider_key!r} is not supported by communication.send.",
        resolved={"operation": operation, "provider": provider_key},
        failed_paths=[{"path": "/to", "requested": target.target_ref}],
    )
    raise AssertionError("unreachable")


def _effective_capabilities(provider_key: str, surface: dict[str, Any]) -> set[str]:
    caps = set(_provider_capabilities(provider_key))
    raw = dict(surface.get("capabilities") or {})
    mapping = {
        "can_write": "text",
        "can_thread": "thread",
        "buttons": "control.button.callback",
        "callback_buttons": "control.button.callback",
        "url_buttons": "control.button.url",
        "images": "attachment.image",
        "image": "attachment.image",
        "html": "html",
        "threads": "thread",
        "reactions": "reaction",
    }
    for key, cap in mapping.items():
        if raw.get(key) is True:
            caps.add(cap)
        elif raw.get(key) is False and cap in caps:
            caps.remove(cap)
    explicit = raw.get("supported")
    if isinstance(explicit, list):
        caps.update(str(item) for item in explicit if str(item).strip())
    unsupported = raw.get("unsupported")
    if isinstance(unsupported, list):
        caps.difference_update(str(item) for item in unsupported if str(item).strip())
    return caps


def _validate_delivery_options(
    *,
    operation: str,
    provider_key: str,
    target: CommunicationTargetOut,
    delivery: CommunicationDeliveryInput,
) -> None:
    failed: list[dict[str, Any]] = []
    if operation == "communication.send" and delivery.visibility != "channel":
        failed.append(
            {
                "path": "/delivery/visibility",
                "requested": delivery.visibility,
                "required_capability": f"visibility.{delivery.visibility}",
                "target_supports": ["channel"],
            }
        )
    if operation == "communication.reply" and delivery.visibility == "private":
        failed.append(
            {
                "path": "/delivery/visibility",
                "requested": delivery.visibility,
                "required_capability": "visibility.private",
                "target_supports": ["channel", "origin"],
            }
        )
    if delivery.reply_mode == "new_thread":
        failed.append(
            {
                "path": "/delivery/reply_mode",
                "requested": "new_thread",
                "required_capability": "thread.create",
                "target_supports": ["default", "same_thread", "message_reply", "none"],
            }
        )
    if delivery.disable_notification is not None and provider_key != "telegram-bot":
        failed.append(
            {
                "path": "/delivery/disable_notification",
                "requested": "disable_notification",
                "required_capability": "notification.silent",
                "target_supports": ["telegram-bot"],
            }
        )
    if delivery.reply_broadcast is not None and provider_key != "slack-bot":
        failed.append(
            {
                "path": "/delivery/reply_broadcast",
                "requested": "reply_broadcast",
                "required_capability": "thread.reply_broadcast",
                "target_supports": ["slack-bot"],
            }
        )
    if failed:
        _reject(
            code="COMM_UNSUPPORTED_DELIVERY_OPTION",
            category="capability",
            message="Target provider does not support one or more requested delivery options.",
            resolved={
                "operation": operation,
                "provider": provider_key,
                "target_ref": target.target_ref,
                "surface_ref": target.surface_ref,
            },
            failed_paths=failed,
            repair_options=[
                {
                    "id": "change_delivery",
                    "description": (
                        "Retry with only supported delivery options. This is semantic and "
                        "requires agent decision."
                    ),
                    "requires_agent_decision": True,
                }
            ],
        )


def _validate_content_shape(
    *,
    operation: str,
    provider_key: str,
    target: CommunicationTargetOut,
    content: CommunicationContentInput,
) -> None:
    if provider_key == "telegram-bot":
        if content.controls and not _has_text(content.text) and not content.attachments:
            _reject(
                code="COMM_TEXT_OR_ATTACHMENT_REQUIRED",
                category="input",
                message="Telegram inline controls must be attached to a text or photo message.",
                resolved={
                    "operation": operation,
                    "provider": provider_key,
                    "target_ref": target.target_ref,
                },
                failed_paths=[
                    {
                        "path": "/content/controls",
                        "requested": "controls_without_message",
                        "required_capability": "message.container",
                    }
                ],
                repair_options=[
                    {
                        "id": "add_text_or_image",
                        "description": (
                            "Add content.text or one image attachment for Telegram controls."
                        ),
                    }
                ],
            )
        if len(content.attachments) > 1 and content.controls:
            _reject(
                code="COMM_UNSUPPORTED_CONTENT_SHAPE",
                category="capability",
                message="Telegram media groups do not support inline controls.",
                resolved={
                    "operation": operation,
                    "provider": provider_key,
                    "target_ref": target.target_ref,
                },
                failed_paths=[
                    {
                        "path": "/content/attachments/1",
                        "requested": "multiple_attachments_with_controls",
                        "required_capability": "media_group.controls",
                    }
                ],
                repair_options=[
                    {
                        "id": "remove_controls_or_send_one_file",
                        "description": (
                            "Send the media group without controls, or send one attachment "
                            "with controls."
                        ),
                    }
                ],
            )
        if len(content.attachments) > 10:
            _reject(
                code="COMM_UNSUPPORTED_CONTENT_SHAPE",
                category="capability",
                message="Telegram media groups support at most 10 attachments.",
                resolved={
                    "operation": operation,
                    "provider": provider_key,
                    "target_ref": target.target_ref,
                },
                failed_paths=[
                    {
                        "path": "/content/attachments/10",
                        "requested": "too_many_attachments",
                        "required_capability": "media_group.max_10",
                    }
                ],
            )
    if provider_key == "slack-bot" and content.attachments and content.controls:
        _reject(
            code="COMM_UNSUPPORTED_CONTENT_SHAPE",
            category="capability",
            message=(
                "Slack file uploads support text plus files in one message, "
                "but not Block Kit controls."
            ),
            resolved={
                "operation": operation,
                "provider": provider_key,
                "target_ref": target.target_ref,
            },
            failed_paths=[
                {
                    "path": "/content/controls",
                    "requested": "controls_with_file_upload",
                    "required_capability": "file_upload.controls",
                }
            ],
            repair_options=[
                {
                    "id": "remove_controls_or_send_text_message",
                    "description": (
                        "Send the file upload without controls, or send controls separately."
                    ),
                    "requires_agent_decision": True,
                }
            ],
        )


def _provider_capabilities(provider_key: str) -> set[str]:
    if provider_key == "slack-bot":
        return {
            "text",
            "markdown",
            "mrkdwn",
            "attachment.document",
            "attachment.file",
            "attachment.image",
            "control.button.callback",
            "control.button.url",
            "thread",
        }
    if provider_key == "telegram-bot":
        return {
            "text",
            "markdown",
            "html",
            "control.button.callback",
            "control.button.url",
            "attachment.document",
            "attachment.file",
            "attachment.image",
            "thread",
            "message_reply",
        }
    if provider_key == "smtp":
        return {"text", "html"}
    return set()


def _required_capabilities(
    content: CommunicationContentInput,
    *,
    delivery: CommunicationDeliveryInput,
    provider_key: str,
) -> list[dict[str, str]]:
    required: list[dict[str, str]] = []

    def add(capability: str, path: str, requested: str | None = None) -> None:
        if any(item["capability"] == capability for item in required):
            return
        required.append(
            {
                "capability": capability,
                "path": path,
                "requested": requested or capability,
            }
        )

    if _has_text(content.text):
        add("text", "/content/text")
    if _has_text(content.html) or content.format == "html":
        add("html", "/content/html")
    if content.format in {"markdown", "mrkdwn"}:
        add("markdown" if provider_key != "slack-bot" else "mrkdwn", "/content/format")
    for index, attachment in enumerate(content.attachments):
        if attachment.type == "image":
            add("attachment.image", f"/content/attachments/{index}")
        else:
            add(f"attachment.{attachment.type}", f"/content/attachments/{index}")
        if not (attachment.artifact_ref or attachment.url or attachment.file_id):
            _reject(
                code="COMM_ATTACHMENT_SOURCE_REQUIRED",
                category="input",
                message=f"Attachment {index} requires artifact_ref, url, or file_id.",
                failed_paths=[
                    {
                        "path": f"/content/attachments/{index}",
                        "requested": f"attachment.{attachment.type}",
                    }
                ],
            )
    for index, control in enumerate(content.controls):
        if control.type != "button":
            add(f"control.{control.type}", f"/content/controls/{index}")
            continue
        if control.url:
            add("control.button.url", f"/content/controls/{index}")
        else:
            add("control.button.callback", f"/content/controls/{index}")
        if control.payload and not (control.value or control.callback_data or control.action):
            _reject(
                code="COMM_CONTROL_TOKEN_REQUIRED",
                category="input",
                message=(
                    "Button payload requires value, callback_data, or action so "
                    "callbacks stay routable."
                ),
                failed_paths=[
                    {
                        "path": f"/content/controls/{index}",
                        "requested": "control.button.callback",
                    }
                ],
                repair_options=[
                    {
                        "id": "add_callback_token",
                        "description": "Add value, callback_data, or action to the button.",
                    }
                ],
            )
    if delivery.reply_mode == "same_thread":
        add("thread", "/delivery/reply_mode", "same_thread")
    if delivery.reply_mode == "message_reply":
        add("message_reply", "/delivery/reply_mode", "message_reply")
    return required


def _reject_unsupported_capability(
    *,
    operation: str,
    provider_key: str,
    target: CommunicationTargetOut,
    actor_ref: str,
    requested: list[dict[str, str]],
    capabilities: set[str],
) -> None:
    _reject(
        code="COMM_UNSUPPORTED_CAPABILITY",
        category="capability",
        message=(
            f"Target {target.key} does not support: "
            f"{', '.join(item['capability'] for item in requested)}."
        ),
        resolved={
            "operation": operation,
            "to": target.key,
            "from": actor_ref,
            "provider": provider_key,
            "surface_ref": target.surface_ref,
        },
        failed_paths=[
            {
                "path": item["path"],
                "requested": item["requested"],
                "required_capability": item["capability"],
                "target_supports": sorted(capabilities),
                "target_does_not_support": sorted(
                    {unsupported["capability"] for unsupported in requested}
                ),
            }
            for item in requested
        ],
        repair_options=[
            {
                "id": "choose_different_target",
                "description": (
                    "Use a target whose provider/surface supports the requested capability."
                ),
            },
            {
                "id": "change_content",
                "description": (
                    "Change the requested content. This is semantic and requires agent decision."
                ),
                "requires_agent_decision": True,
            },
        ],
    )


def _slack_blocks(content: CommunicationContentInput) -> list[dict[str, Any]]:
    if not content.controls:
        return []
    elements = []
    for control in content.controls:
        token = _control_token(control)
        element: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": control.label},
            "action_id": control.action or token,
        }
        if control.url:
            element["url"] = control.url
        else:
            element["value"] = token
        elements.append(element)
    return [{"type": "actions", "block_id": "stackos-controls", "elements": elements}]


def _telegram_reply_markup(content: CommunicationContentInput) -> dict[str, Any] | None:
    if not content.controls:
        return None
    row = []
    for control in content.controls:
        item: dict[str, Any] = {"text": control.label}
        if control.url:
            item["url"] = control.url
        else:
            item["callback_data"] = _control_token(control, max_bytes=64)
        row.append(item)
    return {"inline_keyboard": [row]}


def _control_token(control: CommunicationControlInput, *, max_bytes: int | None = None) -> str:
    token = control.callback_data or control.value or control.action
    if not token:
        token = (
            f"control:{_stable_digest({'label': control.label, 'payload': control.payload})[:16]}"
        )
    if max_bytes is not None and len(token.encode("utf-8")) > max_bytes:
        token = f"c:{_stable_digest({'token': token, 'payload': control.payload})[:20]}"
    return token


def _control_metadata(
    content: CommunicationContentInput,
    *,
    max_token_bytes: int | None = None,
) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for control in content.controls:
        token = _control_token(control)
        bounded_token = _control_token(control, max_bytes=max_token_bytes)
        item = {
            "type": control.type,
            "label": control.label,
            "action": control.action,
            "value": control.value,
            "callback_data": control.callback_data,
            "url": control.url,
            "payload": control.payload,
            "style": control.style,
        }
        clean = redact_secrets(
            {key: value for key, value in item.items() if value not in (None, {}, [])}
        )
        if clean:
            metadata[token] = clean
            metadata[bounded_token] = clean
        if control.action and control.action != token:
            metadata[control.action] = clean
    return metadata


def _file_items(content: CommunicationContentInput) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for attachment in content.attachments:
        item = {
            key: value
            for key, value in {
                "type": attachment.type,
                "artifact_ref": attachment.artifact_ref,
                "url": attachment.url,
                "file_id": attachment.file_id,
                "caption": attachment.caption,
                "filename": attachment.filename,
                "title": attachment.filename,
                "mime_type": attachment.mime_type,
            }.items()
            if value
        }
        items.append(item)
    return items


def _telegram_parse_mode(format_value: str) -> str | None:
    if format_value == "html":
        return "HTML"
    if format_value == "markdown":
        return "Markdown"
    return None


def _delivery_thread_ref(
    delivery: CommunicationDeliveryInput,
    context: CommunicationContextInput,
    *,
    target: CommunicationTargetOut,
    source: dict[str, Any],
) -> str | None:
    if delivery.reply_mode == "none":
        return None
    if context.thread_ref:
        return context.thread_ref
    if context.thread == "same" or delivery.reply_mode == "same_thread":
        return str(source.get("thread_ref") or target.thread_ref or "") or None
    if delivery.reply_mode == "default":
        return target.thread_ref
    return None


def _ensure_delivery_context(
    *,
    operation: str,
    provider_key: str,
    target: CommunicationTargetOut,
    delivery: CommunicationDeliveryInput,
    context: CommunicationContextInput,
    source: dict[str, Any],
) -> None:
    if (delivery.reply_mode == "same_thread" or context.thread == "same") and not (
        context.thread_ref or source.get("thread_ref") or target.thread_ref
    ):
        _reject(
            code="COMM_DELIVERY_CONTEXT_REQUIRED",
            category="input",
            message="same_thread delivery requires a resolvable thread_ref.",
            resolved={
                "operation": operation,
                "provider": provider_key,
                "target_ref": target.target_ref,
                "surface_ref": target.surface_ref,
            },
            failed_paths=[
                {
                    "path": "/delivery/reply_mode",
                    "requested": "same_thread",
                    "required_context": "thread_ref",
                }
            ],
            repair_options=[
                {
                    "id": "provide_thread_ref",
                    "description": (
                        "Pass context.thread_ref or choose a non-threaded delivery mode."
                    ),
                }
            ],
        )
    if delivery.reply_mode == "message_reply" and not (
        context.reply_to or source.get("message_ref")
    ):
        _reject(
            code="COMM_DELIVERY_CONTEXT_REQUIRED",
            category="input",
            message="message_reply delivery requires a resolvable source message ref.",
            resolved={
                "operation": operation,
                "provider": provider_key,
                "target_ref": target.target_ref,
                "surface_ref": target.surface_ref,
            },
            failed_paths=[
                {
                    "path": "/delivery/reply_mode",
                    "requested": "message_reply",
                    "required_context": "reply_to",
                }
            ],
            repair_options=[
                {
                    "id": "provide_reply_to",
                    "description": "Pass context.reply_to or choose a non-message-reply mode.",
                }
            ],
        )


def _reply_delivery(delivery: CommunicationDeliveryInput) -> CommunicationDeliveryInput:
    return delivery
