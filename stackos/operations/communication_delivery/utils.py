"""Shared helpers for communication delivery handlers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session

from stackos.mcp.context import MCPContext
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository

from .errors import _reject
from .schemas import (
    CommunicationAttachmentInput,
    CommunicationContentInput,
    CommunicationControlInput,
)


def _resolve_project_id(project_id: int | None, ctx: MCPContext) -> int:
    resolved = project_id if project_id is not None else ctx.project_id
    if resolved is None:
        raise ValidationError(
            "project_id is required unless the agent bridge resolved the workspace project"
        )
    return int(resolved)


def _require_project(session: Session, project_id: int) -> None:
    ProjectRepository(session).get(project_id)


def _normalize_content(
    *,
    text: str | None,
    content: CommunicationContentInput | None,
    attachments: list[CommunicationAttachmentInput],
    controls: list[CommunicationControlInput],
) -> CommunicationContentInput:
    if content is None:
        content = CommunicationContentInput(text=text)
    elif text is not None and content.text is not None and text.strip() != content.text.strip():
        _reject(
            code="COMM_INPUT_AMBIGUOUS",
            category="input",
            message="Pass text either at the top level or inside content.text, not both.",
            failed_paths=[
                {"path": "/text", "requested": "message.text"},
                {"path": "/content/text", "requested": "message.text"},
            ],
            repair_options=[
                {
                    "id": "use_one_text_field",
                    "description": "Retry with only content.text or only top-level text.",
                }
            ],
        )
    elif text is not None and content.text is None:
        content.text = text
    content.attachments.extend(attachments)
    content.controls.extend(controls)
    if (
        not _has_text(content.text)
        and not _has_text(content.html)
        and not _has_text(content.template_ref)
        and not content.attachments
        and not content.controls
    ):
        _reject(
            code="COMM_CONTENT_REQUIRED",
            category="input",
            message=(
                "Communication delivery requires text, html, a template_ref, an attachment, "
                "or controls."
            ),
            failed_paths=[{"path": "/content", "requested": "deliverable_content"}],
            repair_options=[
                {
                    "id": "add_content",
                    "description": (
                        "Retry with text, html, template_ref, attachments, or controls."
                    ),
                }
            ],
        )
    return content


def _derive_idempotency_key(
    *,
    project_id: int,
    operation: str,
    action_ref: str,
    actor_ref: str | None,
    destination_ref: str,
    content: CommunicationContentInput,
    source_request_id: int | None,
    intent_id: str | None,
    request_id: str,
) -> str:
    source = {
        "scope": "communication",
        "project_id": project_id,
        "operation": operation,
        "action_ref": action_ref,
        "actor_ref": actor_ref,
        "destination_ref": destination_ref,
        "content": content.model_dump(mode="json"),
        "source_request_id": source_request_id,
        "intent_id": (intent_id or "").strip() or None,
        "request_id": None if intent_id else request_id,
    }
    return f"communication:{_stable_digest(source)}"


def _stable_digest(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())
