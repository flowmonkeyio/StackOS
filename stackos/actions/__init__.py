"""StackOS generic action execution foundation."""

from __future__ import annotations

from stackos.action_availability import (
    ActionAvailabilityOut,
    ActionExposureOut,
    build_action_availability,
    build_action_exposure,
)
from stackos.actions.ahrefs import AhrefsActionConnector
from stackos.actions.aignc import AigncActionConnector
from stackos.actions.alibaba_wan import AlibabaWanVideoActionConnector
from stackos.actions.branding import BrandingActionConnector
from stackos.actions.byteplus_seedance import BytePlusSeedanceVideoActionConnector
from stackos.actions.byteplus_seedream import BytePlusSeedreamImageActionConnector
from stackos.actions.connectors import (
    DEFAULT_ACTION_CONNECTORS,
    ActionConnector,
    ActionConnectorError,
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.google_gemini_image import GoogleGeminiImageActionConnector
from stackos.actions.google_veo import GoogleVeoVideoActionConnector
from stackos.actions.http import HttpActionConnector
from stackos.actions.hubspot import HubSpotActionConnector
from stackos.actions.ideogram_images import IdeogramImagesActionConnector
from stackos.actions.imap import ImapActionConnector
from stackos.actions.kling_video import KlingVideoActionConnector
from stackos.actions.linear import LinearActionConnector
from stackos.actions.manifest import (
    ACTION_MANIFEST_SCHEMA_VERSION,
    ExecutableActionManifest,
    parse_action_manifest,
)
from stackos.actions.mock_provider import MockProviderActionConnector
from stackos.actions.openai_images import OpenAIImagesActionConnector
from stackos.actions.package_bridge import PackageActionConnector
from stackos.actions.repository import (
    ActionCallAuditOut,
    ActionCallOut,
    ActionDescribeOut,
    ActionExecutionOut,
    ActionRepository,
    ActionValidationOut,
)
from stackos.actions.reve_images import ReveImagesActionConnector
from stackos.actions.shopify import ShopifyActionConnector
from stackos.actions.slack_bot import SlackBotActionConnector
from stackos.actions.smtp import SmtpActionConnector
from stackos.actions.stripe import StripeActionConnector
from stackos.actions.telegram import TelegramActionConnector
from stackos.actions.trackbooth import TrackboothActionConnector
from stackos.actions.xai_imagine import XAIImagineActionConnector

DEFAULT_ACTION_CONNECTORS.register(OpenAIImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(AigncActionConnector())
DEFAULT_ACTION_CONNECTORS.register(AlibabaWanVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(XAIImagineActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ReveImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleGeminiImageActionConnector())
DEFAULT_ACTION_CONNECTORS.register(GoogleVeoVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(IdeogramImagesActionConnector())
DEFAULT_ACTION_CONNECTORS.register(KlingVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(LinearActionConnector())
DEFAULT_ACTION_CONNECTORS.register(BytePlusSeedreamImageActionConnector())
DEFAULT_ACTION_CONNECTORS.register(BytePlusSeedanceVideoActionConnector())
DEFAULT_ACTION_CONNECTORS.register(AhrefsActionConnector())
DEFAULT_ACTION_CONNECTORS.register(HttpActionConnector())
DEFAULT_ACTION_CONNECTORS.register(HubSpotActionConnector())
DEFAULT_ACTION_CONNECTORS.register(BrandingActionConnector())
DEFAULT_ACTION_CONNECTORS.register(TelegramActionConnector())
DEFAULT_ACTION_CONNECTORS.register(TrackboothActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ShopifyActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SlackBotActionConnector())
DEFAULT_ACTION_CONNECTORS.register(SmtpActionConnector())
DEFAULT_ACTION_CONNECTORS.register(ImapActionConnector())
DEFAULT_ACTION_CONNECTORS.register(StripeActionConnector())
DEFAULT_ACTION_CONNECTORS.register(MockProviderActionConnector())

DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("apollo"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("clay"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("cloudflare"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("dataforseo"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("firecrawl"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("ftp"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("ghost"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("google-ads"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("google-analytics"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("google-indexing"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("google-search-console"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("google-tag-manager"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("google-workspace"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("jina"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("meta-ads"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("microsoft-365"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("outreach"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("pipedrive"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("reddit"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("aws-s3"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("salesforce"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("salesloft"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("serper"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("sitemap"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("taboola"))
DEFAULT_ACTION_CONNECTORS.register(PackageActionConnector("wordpress"))

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
    "AigncActionConnector",
    "AlibabaWanVideoActionConnector",
    "BrandingActionConnector",
    "BytePlusSeedanceVideoActionConnector",
    "BytePlusSeedreamImageActionConnector",
    "ExecutableActionManifest",
    "GoogleGeminiImageActionConnector",
    "GoogleVeoVideoActionConnector",
    "HttpActionConnector",
    "HubSpotActionConnector",
    "IdeogramImagesActionConnector",
    "ImapActionConnector",
    "KlingVideoActionConnector",
    "LinearActionConnector",
    "MockProviderActionConnector",
    "OpenAIImagesActionConnector",
    "ReveImagesActionConnector",
    "ShopifyActionConnector",
    "SlackBotActionConnector",
    "SmtpActionConnector",
    "StripeActionConnector",
    "TelegramActionConnector",
    "TrackboothActionConnector",
    "XAIImagineActionConnector",
    "build_action_availability",
    "build_action_exposure",
    "parse_action_manifest",
]
