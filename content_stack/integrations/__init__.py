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

from content_stack.integrations._base import BaseIntegration, IntegrationCallResult
from content_stack.integrations.ahrefs import AhrefsIntegration
from content_stack.integrations.dataforseo import DataForSeoIntegration
from content_stack.integrations.firecrawl import FirecrawlIntegration
from content_stack.integrations.ghost import GhostIntegration
from content_stack.integrations.google_paa import GooglePaaIntegration
from content_stack.integrations.jina_reader import JinaReaderIntegration
from content_stack.integrations.openai_images import OpenAIImagesIntegration
from content_stack.integrations.reddit import RedditIntegration
from content_stack.integrations.telegram_bot import TelegramBotIntegration
from content_stack.integrations.wordpress import WordPressIntegration

if TYPE_CHECKING:
    pass


REGISTRY: dict[str, type[BaseIntegration]] = {
    "dataforseo": DataForSeoIntegration,
    "firecrawl": FirecrawlIntegration,
    "openai-images": OpenAIImagesIntegration,
    "reddit": RedditIntegration,
    "google-paa": GooglePaaIntegration,
    "jina": JinaReaderIntegration,
    "ahrefs": AhrefsIntegration,
    "wordpress": WordPressIntegration,
    "ghost": GhostIntegration,
    "telegram-bot": TelegramBotIntegration,
}


def integration_class_for(kind: str) -> type[BaseIntegration] | None:
    """Resolve the wrapper class for an ``integration_credentials.kind``.

    Returns ``None`` if no wrapper is registered. Runtime LLM keys for
    the current operator agent live outside StackOS; the daemon should not
    register prose-generation wrappers or spawn hidden writer sessions.
    """
    return REGISTRY.get(kind)


__all__ = [
    "REGISTRY",
    "AhrefsIntegration",
    "BaseIntegration",
    "DataForSeoIntegration",
    "FirecrawlIntegration",
    "GhostIntegration",
    "GooglePaaIntegration",
    "IntegrationCallResult",
    "JinaReaderIntegration",
    "OpenAIImagesIntegration",
    "RedditIntegration",
    "TelegramBotIntegration",
    "WordPressIntegration",
    "integration_class_for",
]
