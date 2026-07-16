"""Integration clients for the M4 vendor wrappers.

Each per-vendor module exposes ``XxxIntegration(BaseIntegration)`` with
op-specific async methods. ``BaseIntegration`` (``_base.py``) handles the
cross-cutting work: token-bucket rate limiting (``_rate_limit.py``),
budget pre-emption + reconciliation, retry/backoff on 429/5xx, run-step
audit trail, request/response sanitisation.

The ``REGISTRY`` dict maps the ``integration_credentials.kind`` value to
its wrapper class so the auth-provider boundary and REST admin routes can
look up the right vendor without an enum-to-class switch on every call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations.ahrefs import AhrefsIntegration
from stackos.integrations.alibaba_wan import AlibabaWanIntegration
from stackos.integrations.byteplus_ark import BytePlusArkIntegration
from stackos.integrations.cloudflare import CloudflareIntegration
from stackos.integrations.dataforseo import DataForSeoIntegration
from stackos.integrations.firecrawl import FirecrawlIntegration
from stackos.integrations.ftp import FtpIntegration
from stackos.integrations.ghost import GhostIntegration
from stackos.integrations.google_analytics import GoogleAnalyticsIntegration
from stackos.integrations.google_gemini_image import GoogleGeminiImageIntegration
from stackos.integrations.google_paa import GooglePaaIntegration
from stackos.integrations.google_search_console import GoogleSearchConsoleIntegration
from stackos.integrations.google_tag_manager import GoogleTagManagerIntegration
from stackos.integrations.google_veo import GoogleVeoIntegration
from stackos.integrations.ideogram_images import IdeogramImagesIntegration
from stackos.integrations.imap import ImapIntegration
from stackos.integrations.jina_reader import JinaReaderIntegration
from stackos.integrations.kling_video import KlingVideoIntegration
from stackos.integrations.openai_images import OpenAIImagesIntegration
from stackos.integrations.openrouter import OpenRouterIntegration
from stackos.integrations.reddit import RedditIntegration
from stackos.integrations.reve_images import ReveImagesIntegration
from stackos.integrations.serper import SerperIntegration
from stackos.integrations.shopify import ShopifyIntegration
from stackos.integrations.slack_bot import SlackBotIntegration
from stackos.integrations.smtp import SmtpIntegration
from stackos.integrations.telegram_bot import TelegramBotIntegration
from stackos.integrations.trackbooth import TrackboothIntegration
from stackos.integrations.wordpress import WordPressIntegration
from stackos.integrations.xai_imagine import XAIImagineIntegration

if TYPE_CHECKING:
    pass


REGISTRY: dict[str, type[BaseIntegration]] = {
    "dataforseo": DataForSeoIntegration,
    "alibaba-wan": AlibabaWanIntegration,
    "serper": SerperIntegration,
    "shopify": ShopifyIntegration,
    "firecrawl": FirecrawlIntegration,
    "ftp": FtpIntegration,
    "cloudflare": CloudflareIntegration,
    "openai-images": OpenAIImagesIntegration,
    "xai-imagine": XAIImagineIntegration,
    "reve": ReveImagesIntegration,
    "google-gemini-image": GoogleGeminiImageIntegration,
    "google-veo": GoogleVeoIntegration,
    "ideogram": IdeogramImagesIntegration,
    "kling": KlingVideoIntegration,
    "byteplus-ark": BytePlusArkIntegration,
    "openrouter": OpenRouterIntegration,
    "reddit": RedditIntegration,
    "google-paa": GooglePaaIntegration,
    "jina": JinaReaderIntegration,
    "ahrefs": AhrefsIntegration,
    "google-search-console": GoogleSearchConsoleIntegration,
    "google-analytics": GoogleAnalyticsIntegration,
    "google-tag-manager": GoogleTagManagerIntegration,
    "wordpress": WordPressIntegration,
    "ghost": GhostIntegration,
    "telegram-bot": TelegramBotIntegration,
    "trackbooth": TrackboothIntegration,
    "slack-bot": SlackBotIntegration,
    "smtp": SmtpIntegration,
    "imap": ImapIntegration,
}


def integration_class_for(kind: str) -> type[BaseIntegration] | None:
    """Resolve the wrapper class for an ``integration_credentials.kind``.

    Returns ``None`` if no wrapper is registered. Runtime LLM keys for
    the current operator agent live outside StackOS; the daemon may register
    setup probes such as OpenRouter, but should not register generic
    prose-generation actions or spawn hidden writer sessions.
    """
    return REGISTRY.get(kind)


__all__ = [
    "REGISTRY",
    "AhrefsIntegration",
    "AlibabaWanIntegration",
    "BaseIntegration",
    "BytePlusArkIntegration",
    "CloudflareIntegration",
    "DataForSeoIntegration",
    "FirecrawlIntegration",
    "FtpIntegration",
    "GhostIntegration",
    "GoogleAnalyticsIntegration",
    "GoogleGeminiImageIntegration",
    "GooglePaaIntegration",
    "GoogleSearchConsoleIntegration",
    "GoogleTagManagerIntegration",
    "GoogleVeoIntegration",
    "IdeogramImagesIntegration",
    "ImapIntegration",
    "IntegrationCallResult",
    "JinaReaderIntegration",
    "KlingVideoIntegration",
    "OpenAIImagesIntegration",
    "OpenRouterIntegration",
    "RedditIntegration",
    "ReveImagesIntegration",
    "SerperIntegration",
    "ShopifyIntegration",
    "SlackBotIntegration",
    "SmtpIntegration",
    "TelegramBotIntegration",
    "TrackboothIntegration",
    "WordPressIntegration",
    "XAIImagineIntegration",
    "integration_class_for",
]
