"""StackOS generic action execution foundation."""

from __future__ import annotations

from content_stack.action_availability import ActionAvailabilityOut, build_action_availability
from content_stack.actions.ahrefs import AhrefsActionConnector
from content_stack.actions.apollo import ApolloActionConnector
from content_stack.actions.clay import ClayActionConnector
from content_stack.actions.connectors import (
    DEFAULT_ACTION_CONNECTORS,
    ActionConnector,
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.dataforseo import DataForSeoActionConnector
from content_stack.actions.firecrawl import FirecrawlActionConnector
from content_stack.actions.ghost import GhostActionConnector
from content_stack.actions.google_ads import GoogleAdsActionConnector
from content_stack.actions.google_workspace import GoogleWorkspaceActionConnector
from content_stack.actions.http import HttpActionConnector
from content_stack.actions.hubspot import HubSpotActionConnector
from content_stack.actions.jina import JinaActionConnector
from content_stack.actions.manifest import (
    ACTION_MANIFEST_SCHEMA_VERSION,
    ExecutableActionManifest,
    parse_action_manifest,
)
from content_stack.actions.meta_ads import MetaAdsActionConnector
from content_stack.actions.microsoft_graph import MicrosoftGraphActionConnector
from content_stack.actions.mock_provider import MockProviderActionConnector
from content_stack.actions.openai_images import OpenAIImagesActionConnector
from content_stack.actions.outreach import OutreachActionConnector
from content_stack.actions.pipedrive import PipedriveActionConnector
from content_stack.actions.reddit import RedditActionConnector
from content_stack.actions.repository import (
    ActionCallAuditOut,
    ActionCallOut,
    ActionDescribeOut,
    ActionExecutionOut,
    ActionRepository,
    ActionValidationOut,
)
from content_stack.actions.salesforce import SalesforceActionConnector
from content_stack.actions.salesloft import SalesloftActionConnector
from content_stack.actions.sitemap import SitemapActionConnector
from content_stack.actions.taboola import TaboolaActionConnector
from content_stack.actions.telegram_bot import TelegramBotActionConnector
from content_stack.actions.wordpress import WordPressActionConnector

DEFAULT_ACTION_CONNECTORS.register(OpenAIImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(FirecrawlActionConnector())
DEFAULT_ACTION_CONNECTORS.register(JinaActionConnector())
DEFAULT_ACTION_CONNECTORS.register(RedditActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SitemapActionConnector())
DEFAULT_ACTION_CONNECTORS.register(DataForSeoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(AhrefsActionConnector())
DEFAULT_ACTION_CONNECTORS.register(WordPressActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GhostActionConnector())
DEFAULT_ACTION_CONNECTORS.register(HttpActionConnector())
DEFAULT_ACTION_CONNECTORS.register(HubSpotActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SalesforceActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ApolloActionConnector())
DEFAULT_ACTION_CONNECTORS.register(PipedriveActionConnector())
DEFAULT_ACTION_CONNECTORS.register(OutreachActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SalesloftActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleWorkspaceActionConnector())
DEFAULT_ACTION_CONNECTORS.register(MicrosoftGraphActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ClayActionConnector())
DEFAULT_ACTION_CONNECTORS.register(MetaAdsActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleAdsActionConnector())
DEFAULT_ACTION_CONNECTORS.register(TaboolaActionConnector())
DEFAULT_ACTION_CONNECTORS.register(TelegramBotActionConnector())
DEFAULT_ACTION_CONNECTORS.register(MockProviderActionConnector())

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
    "ApolloActionConnector",
    "ClayActionConnector",
    "DataForSeoActionConnector",
    "ExecutableActionManifest",
    "FirecrawlActionConnector",
    "GhostActionConnector",
    "GoogleAdsActionConnector",
    "GoogleWorkspaceActionConnector",
    "HttpActionConnector",
    "HubSpotActionConnector",
    "JinaActionConnector",
    "MetaAdsActionConnector",
    "MicrosoftGraphActionConnector",
    "MockProviderActionConnector",
    "OpenAIImagesActionConnector",
    "OutreachActionConnector",
    "PipedriveActionConnector",
    "RedditActionConnector",
    "SalesforceActionConnector",
    "SalesloftActionConnector",
    "SitemapActionConnector",
    "TaboolaActionConnector",
    "TelegramBotActionConnector",
    "WordPressActionConnector",
    "build_action_availability",
    "parse_action_manifest",
]
