"""StackOS generic action execution foundation."""

from __future__ import annotations

from content_stack.actions.connectors import (
    DEFAULT_ACTION_CONNECTORS,
    ActionConnector,
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.manifest import (
    ACTION_MANIFEST_SCHEMA_VERSION,
    ExecutableActionManifest,
    parse_action_manifest,
)
from content_stack.actions.openai_images import OpenAIImagesActionConnector
from content_stack.actions.repository import (
    ActionCallAuditOut,
    ActionCallOut,
    ActionDescribeOut,
    ActionExecutionOut,
    ActionRepository,
    ActionValidationOut,
)

DEFAULT_ACTION_CONNECTORS.register(OpenAIImagesActionConnector())

__all__ = [
    "ACTION_MANIFEST_SCHEMA_VERSION",
    "DEFAULT_ACTION_CONNECTORS",
    "ActionCallAuditOut",
    "ActionCallOut",
    "ActionConnector",
    "ActionConnectorRegistry",
    "ActionConnectorRequest",
    "ActionConnectorResult",
    "ActionDescribeOut",
    "ActionExecutionOut",
    "ActionRepository",
    "ActionValidationIssue",
    "ActionValidationOut",
    "ExecutableActionManifest",
    "OpenAIImagesActionConnector",
    "parse_action_manifest",
]
