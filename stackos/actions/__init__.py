"""StackOS generic action execution foundation."""

from __future__ import annotations

from stackos.action_availability import (
    ActionAvailabilityOut,
    ActionExposureOut,
    build_action_availability,
    build_action_exposure,
)
from stackos.actions.ahrefs import AhrefsActionConnector
from stackos.actions.alibaba_wan import AlibabaWanVideoActionConnector
from stackos.actions.apollo import ApolloActionConnector
from stackos.actions.branding import BrandingActionConnector
from stackos.actions.byteplus_seedance import BytePlusSeedanceVideoActionConnector
from stackos.actions.byteplus_seedream import BytePlusSeedreamImageActionConnector
from stackos.actions.clay import ClayActionConnector
from stackos.actions.connectors import (
    DEFAULT_ACTION_CONNECTORS,
    ActionConnector,
    ActionConnectorError,
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.dataforseo import DataForSeoActionConnector
from stackos.actions.firecrawl import FirecrawlActionConnector
from stackos.actions.ghost import GhostActionConnector
from stackos.actions.google_ads import GoogleAdsActionConnector
from stackos.actions.google_analytics import GoogleAnalyticsActionConnector
from stackos.actions.google_gemini_image import GoogleGeminiImageActionConnector
from stackos.actions.google_search_console import GoogleSearchConsoleActionConnector
from stackos.actions.google_tag_manager import GoogleTagManagerActionConnector
from stackos.actions.google_veo import GoogleVeoVideoActionConnector
from stackos.actions.google_workspace import GoogleWorkspaceActionConnector
from stackos.actions.http import HttpActionConnector
from stackos.actions.hubspot import HubSpotActionConnector
from stackos.actions.ideogram_images import IdeogramImagesActionConnector
from stackos.actions.imap import ImapActionConnector
from stackos.actions.jina import JinaActionConnector
from stackos.actions.kling_video import KlingVideoActionConnector
from stackos.actions.manifest import (
    ACTION_MANIFEST_SCHEMA_VERSION,
    ExecutableActionManifest,
    parse_action_manifest,
)
from stackos.actions.meta_ads import MetaAdsActionConnector
from stackos.actions.microsoft_graph import MicrosoftGraphActionConnector
from stackos.actions.mock_provider import MockProviderActionConnector
from stackos.actions.openai_images import OpenAIImagesActionConnector
from stackos.actions.outreach import OutreachActionConnector
from stackos.actions.pipedrive import PipedriveActionConnector
from stackos.actions.reddit import RedditActionConnector
from stackos.actions.repository import (
    ActionCallAuditOut,
    ActionCallOut,
    ActionDescribeOut,
    ActionExecutionOut,
    ActionRepository,
    ActionValidationOut,
)
from stackos.actions.reve_images import ReveImagesActionConnector
from stackos.actions.salesforce import SalesforceActionConnector
from stackos.actions.salesloft import SalesloftActionConnector
from stackos.actions.serper import SerperActionConnector
from stackos.actions.shopify import ShopifyActionConnector
from stackos.actions.sitemap import SitemapActionConnector
from stackos.actions.slack_bot import SlackBotActionConnector
from stackos.actions.smtp import SmtpActionConnector
from stackos.actions.taboola import TaboolaActionConnector
from stackos.actions.telegram_bot import TelegramBotActionConnector
from stackos.actions.trackbooth import TrackboothActionConnector
from stackos.actions.wordpress import WordPressActionConnector
from stackos.actions.xai_imagine import XAIImagineActionConnector

DEFAULT_ACTION_CONNECTORS.register(OpenAIImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(AlibabaWanVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(XAIImagineActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ReveImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleGeminiImageActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleVeoVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(IdeogramImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(KlingVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(BytePlusSeedreamImageActionConnector())
DEFAULT_ACTION_CONNECTORS.register(BytePlusSeedanceVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(FirecrawlActionConnector())
DEFAULT_ACTION_CONNECTORS.register(JinaActionConnector())
DEFAULT_ACTION_CONNECTORS.register(RedditActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SitemapActionConnector())
DEFAULT_ACTION_CONNECTORS.register(DataForSeoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SerperActionConnector())
DEFAULT_ACTION_CONNECTORS.register(AhrefsActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleSearchConsoleActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleAnalyticsActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleTagManagerActionConnector())
DEFAULT_ACTION_CONNECTORS.register(WordPressActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GhostActionConnector())
DEFAULT_ACTION_CONNECTORS.register(HttpActionConnector())
DEFAULT_ACTION_CONNECTORS.register(HubSpotActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SalesforceActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ApolloActionConnector())
DEFAULT_ACTION_CONNECTORS.register(BrandingActionConnector())
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
DEFAULT_ACTION_CONNECTORS.register(TrackboothActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ShopifyActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SlackBotActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SmtpActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ImapActionConnector())
DEFAULT_ACTION_CONNECTORS.register(MockProviderActionConnector())

__all__ = [
    "ACTION_MANIFEST_SCHEMA_VERSION",
    "DEFAULT_ACTION_CONNECTORS",
    "ActionAvailabilityOut",
    "ActionCallAuditOut",
    "ActionCallOut",
    "ActionConnector",
    "ActionConnectorError",
    "ActionConnectorRegistry",
    "ActionConnectorRequest",
    "ActionConnectorResult",
    "ActionDescribeOut",
    "ActionExecutionOut",
    "ActionExposureOut",
    "ActionRepository",
    "ActionValidationIssue",
    "ActionValidationOut",
    "AhrefsActionConnector",
    "AlibabaWanVideoActionConnector",
    "ApolloActionConnector",
    "BrandingActionConnector",
    "BytePlusSeedanceVideoActionConnector",
    "BytePlusSeedreamImageActionConnector",
    "ClayActionConnector",
    "DataForSeoActionConnector",
    "ExecutableActionManifest",
    "FirecrawlActionConnector",
    "GhostActionConnector",
    "GoogleAdsActionConnector",
    "GoogleAnalyticsActionConnector",
    "GoogleGeminiImageActionConnector",
    "GoogleSearchConsoleActionConnector",
    "GoogleTagManagerActionConnector",
    "GoogleVeoVideoActionConnector",
    "GoogleWorkspaceActionConnector",
    "HttpActionConnector",
    "HubSpotActionConnector",
    "IdeogramImagesActionConnector",
    "ImapActionConnector",
    "JinaActionConnector",
    "KlingVideoActionConnector",
    "MetaAdsActionConnector",
    "MicrosoftGraphActionConnector",
    "MockProviderActionConnector",
    "OpenAIImagesActionConnector",
    "OutreachActionConnector",
    "PipedriveActionConnector",
    "RedditActionConnector",
    "ReveImagesActionConnector",
    "SalesforceActionConnector",
    "SalesloftActionConnector",
    "SerperActionConnector",
    "ShopifyActionConnector",
    "SitemapActionConnector",
    "SlackBotActionConnector",
    "SmtpActionConnector",
    "TaboolaActionConnector",
    "TelegramBotActionConnector",
    "TrackboothActionConnector",
    "WordPressActionConnector",
    "XAIImagineActionConnector",
    "build_action_availability",
    "build_action_exposure",
    "parse_action_manifest",
]
