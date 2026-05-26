"""Operation specifications for communication delivery operations."""

from __future__ import annotations

from stackos.mcp.contract import WriteEnvelope
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)

from .handlers import communication_reply, communication_send
from .schemas import CommunicationReplyInput, CommunicationSendInput, CommunicationSendOut


def _surfaces(name: str, command: str) -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=f"/api/v1/operations/{name}/call"),
        cli=OperationSurface(enabled=True, command=command),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="communication.send",
            summary="Send a provider-neutral message to a named communication target.",
            input_model=CommunicationSendInput,
            output_model=WriteEnvelope[CommunicationSendOut],
            handler=communication_send,
            surfaces=_surfaces("communication.send", "ops call communication.send"),
            purpose=(
                "Use this as the normal agent path for Slack, Telegram, email, and future "
                "communication sends. The agent provides actor/destination/content/context; "
                "StackOS resolves profile, target, provider action, credential, policy, "
                "capabilities, idempotency, and audit."
            ),
            prerequisites=(
                "Pass to as a configured communication target key/ref.",
                "Pass from only when multiple profiles could send; otherwise StackOS resolves it.",
                "Set dry_run=true to validate payload and write a dry-run "
                "action_call audit row without calling the provider connector.",
                "Unsupported rich features reject by default with model-readable repair context.",
            ),
            returns=(
                "A compact sent/validated result with message_ref and action_call_id.",
                "effects states whether the provider connector was called or "
                "only a dry-run audit row was written.",
            ),
            examples=(
                OperationExample(
                    title="Send update to roadmap",
                    arguments={
                        "project_id": 1,
                        "from": "ops-bot",
                        "to": "slack-roadmap",
                        "text": "Done. The fix shipped.",
                    },
                ),
            ),
            grant_policy="direct-communication-send",
        ),
        OperationSpec(
            name="communication.reply",
            summary="Reply to the origin of one agent request.",
            input_model=CommunicationReplyInput,
            output_model=WriteEnvelope[CommunicationSendOut],
            handler=communication_reply,
            surfaces=_surfaces("communication.reply", "ops call communication.reply"),
            purpose=(
                "Use this when an inbound Telegram or Slack agent request should receive "
                "a response in its origin surface/thread without manually reconstructing "
                "provider ids or credentials."
            ),
            prerequisites=(
                "Pass request_id for a stored agent request.",
                "StackOS resolves origin provider, surface, thread, actor profile, credential, "
                "provider payload, and idempotency.",
                "Set dry_run=true to validate payload and write a dry-run "
                "action_call audit row without calling the provider connector.",
            ),
            returns=(
                "A compact sent/validated result with message_ref and action_call_id.",
                "effects states whether the provider connector was called or "
                "only a dry-run audit row was written.",
            ),
            examples=(
                OperationExample(
                    title="Reply to inbound request",
                    arguments={"project_id": 1, "request_id": 42, "text": "Done."},
                ),
            ),
            grant_policy="direct-communication-send",
        ),
    ]


__all__ = [
    "CommunicationReplyInput",
    "CommunicationSendInput",
    "CommunicationSendOut",
    "communication_reply",
    "communication_send",
    "operation_specs",
]
