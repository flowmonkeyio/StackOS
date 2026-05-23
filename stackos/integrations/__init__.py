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
from stackos.integrations.dataforseo import DataForSeoIntegration
from stackos.integrations.firecrawl import FirecrawlIntegration
from stackos.integrations.ghost import GhostIntegration
from stackos.integrations.google_paa import GooglePaaIntegration
from stackos.integrations.imap import ImapIntegration
from stackos.integrations.jina_reader import JinaReaderIntegration
from stackos.integrations.openai_images import OpenAIImagesIntegration
from stackos.integrations.reddit import RedditIntegration
from stackos.integrations.smtp import SmtpIntegration
from stackos.integrations.telegram_bot import TelegramBotIntegration
from stackos.integrations.wordpress import WordPressIntegration

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
    "smtp": SmtpIntegration,
    "imap": ImapIntegration,
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
    "ImapIntegration",
    "IntegrationCallResult",
    "JinaReaderIntegration",
    "OpenAIImagesIntegration",
    "RedditIntegration",
    "SmtpIntegration",
    "TelegramBotIntegration",
    "WordPressIntegration",
    "integration_class_for",
]
