"""Host account-probe routing to native clients and explicit host compositions."""

from typing import Any

from stackos_connectors.connectors.ahrefs.integration import AhrefsIntegration
from stackos_connectors.connectors.aignc.integration import AigncIntegration
from stackos_connectors.connectors.alibaba_wan.integration import AlibabaWanIntegration
from stackos_connectors.connectors.aws_s3.integration import S3Integration
from stackos_connectors.connectors.cloudflare.integration import CloudflareIntegration
from stackos_connectors.connectors.dataforseo.integration import DataForSeoIntegration
from stackos_connectors.connectors.firecrawl.integration import FirecrawlIntegration
from stackos_connectors.connectors.ftp.integration import FtpIntegration
from stackos_connectors.connectors.ghost.integration import GhostIntegration
from stackos_connectors.connectors.google_analytics.integration import GoogleAnalyticsIntegration
from stackos_connectors.connectors.google_gemini_image.integration import (
    GoogleGeminiImageIntegration,
)
from stackos_connectors.connectors.google_indexing.integration import GoogleIndexingIntegration
from stackos_connectors.connectors.google_search_console.integration import (
    GoogleSearchConsoleIntegration,
)
from stackos_connectors.connectors.google_tag_manager.integration import GoogleTagManagerIntegration
from stackos_connectors.connectors.google_veo.integration import GoogleVeoIntegration
from stackos_connectors.connectors.hubspot.integration import HubSpotIntegration
from stackos_connectors.connectors.ideogram.integration import IdeogramImagesIntegration
from stackos_connectors.connectors.imap.integration import ImapIntegration
from stackos_connectors.connectors.jina.integration import JinaReaderIntegration
from stackos_connectors.connectors.kling_video.integration import KlingVideoIntegration
from stackos_connectors.connectors.linear.integration import LinearIntegration
from stackos_connectors.connectors.openai_images.integration import OpenAIImagesIntegration
from stackos_connectors.connectors.openrouter.integration import OpenRouterIntegration
from stackos_connectors.connectors.pipedrive.integration import PipedriveIntegration
from stackos_connectors.connectors.reddit.integration import RedditIntegration
from stackos_connectors.connectors.reve.integration import ReveImagesIntegration
from stackos_connectors.connectors.salesloft.integration import SalesloftIntegration
from stackos_connectors.connectors.serper.integration import SerperIntegration
from stackos_connectors.connectors.shopify.integration import ShopifyIntegration
from stackos_connectors.connectors.slack_bot.integration import SlackBotIntegration
from stackos_connectors.connectors.smtp.integration import SmtpIntegration
from stackos_connectors.connectors.stripe.integration import StripeIntegration
from stackos_connectors.connectors.trackbooth.integration import TrackboothIntegration
from stackos_connectors.connectors.wordpress.integration import WordPressIntegration
from stackos_connectors.connectors.xai_imagine.integration import XAIImagineIntegration
from stackos_connectors.shared.byteplus.ark import BytePlusArkIntegration

from stackos.integrations.google_paa import GooglePaaIntegration

REGISTRY: dict[str, type[Any]] = {
    "aignc": AigncIntegration,
    "dataforseo": DataForSeoIntegration,
    "alibaba-wan": AlibabaWanIntegration,
    "serper": SerperIntegration,
    "shopify": ShopifyIntegration,
    "firecrawl": FirecrawlIntegration,
    "ftp": FtpIntegration,
    "aws-s3": S3Integration,
    "cloudflare": CloudflareIntegration,
    "openai-images": OpenAIImagesIntegration,
    "xai-imagine": XAIImagineIntegration,
    "reve": ReveImagesIntegration,
    "google-gemini-image": GoogleGeminiImageIntegration,
    "google-veo": GoogleVeoIntegration,
    "ideogram": IdeogramImagesIntegration,
    "kling": KlingVideoIntegration,
    "linear": LinearIntegration,
    "byteplus-ark": BytePlusArkIntegration,
    "openrouter": OpenRouterIntegration,
    "reddit": RedditIntegration,
    "google-paa": GooglePaaIntegration,
    "jina": JinaReaderIntegration,
    "ahrefs": AhrefsIntegration,
    "google-search-console": GoogleSearchConsoleIntegration,
    "google-indexing": GoogleIndexingIntegration,
    "google-analytics": GoogleAnalyticsIntegration,
    "google-tag-manager": GoogleTagManagerIntegration,
    "hubspot": HubSpotIntegration,
    "pipedrive": PipedriveIntegration,
    "salesloft": SalesloftIntegration,
    "wordpress": WordPressIntegration,
    "ghost": GhostIntegration,
    "trackbooth": TrackboothIntegration,
    "slack-bot": SlackBotIntegration,
    "smtp": SmtpIntegration,
    "imap": ImapIntegration,
    "stripe": StripeIntegration,
}


def integration_class_for(kind: str) -> type[Any] | None:
    """Return the existing account-probe implementation for a provider key."""
    return REGISTRY.get(kind)


__all__ = ["REGISTRY", "integration_class_for"]
