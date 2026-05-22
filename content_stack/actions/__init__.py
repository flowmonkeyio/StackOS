"""StackOS generic action execution foundation."""

from __future__ import annotations

from content_stack.action_availability import ActionAvailabilityOut, build_action_availability
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
from content_stack.actions.vendor_connectors import (
    AhrefsActionConnector,
    DataForSeoActionConnector,
    FirecrawlActionConnector,
    JinaActionConnector,
    RedditActionConnector,
)

DEFAULT_ACTION_CONNECTORS.register(OpenAIImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(FirecrawlActionConnector())
DEFAULT_ACTION_CONNECTORS.register(JinaActionConnector())
DEFAULT_ACTION_CONNECTORS.register(RedditActionConnector())
DEFAULT_ACTION_CONNECTORS.register(DataForSeoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(AhrefsActionConnector())

__all__ = [
    "ACTION_MANIFEST_SCHEMA_VERSION",
    "DEFAULT_ACTION_CONNECTORS",
    "ActionAvailabilityOut",
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
    "AhrefsActionConnector",
    "DataForSeoActionConnector",
    "ExecutableActionManifest",
    "FirecrawlActionConnector",
    "JinaActionConnector",
    "OpenAIImagesActionConnector",
    "RedditActionConnector",
    "build_action_availability",
    "parse_action_manifest",
]
