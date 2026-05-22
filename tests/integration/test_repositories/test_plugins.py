"""Repository tests for StackOS plugin catalog primitives."""

from __future__ import annotations

from sqlmodel import Session

from content_stack.auth_providers import AuthRepository
from content_stack.repositories.plugins import PluginRepository
from content_stack.repositories.projects import (
    IntegrationBudgetRepository,
    IntegrationCredentialRepository,
)


def test_builtin_plugins_sync_and_list(session: Session) -> None:
    repo = PluginRepository(session)

    plugins = repo.list_plugins()

    assert [p.slug for p in plugins] == ["core", "publishing", "seo", "utils"]
    seo = repo.get_plugin("seo")
    assert seo.name == "SEO"
    assert seo.enabled_for_project is None
    assert seo.manifest_json["ui"]["nav"]["section"] == "SEO"
    publishing = repo.get_plugin("publishing")
    assert publishing.name == "Publishing"
    assert publishing.manifest_json["ui"]["nav"]["section"] == "Publishing"


def test_project_enable_disable_plugin(session: Session, project_id: int) -> None:
    repo = PluginRepository(session)

    enabled = repo.enable(project_id=project_id, plugin_slug="utils").data
    assert enabled.project_id == project_id
    assert enabled.plugin_slug == "utils"
    assert enabled.enabled is True

    annotated = repo.get_plugin("utils", project_id=project_id)
    assert annotated.enabled_for_project is True

    disabled = repo.disable(project_id=project_id, plugin_slug="utils").data
    assert disabled.enabled is False
    assert disabled.disabled_at is not None

    annotated_after = repo.get_plugin("utils", project_id=project_id)
    assert annotated_after.enabled_for_project is False


def test_catalog_describes_capabilities_providers_and_actions(session: Session) -> None:
    repo = PluginRepository(session)

    catalog = repo.catalog(plugin_slug="utils")
    utils = catalog.plugins[0]

    assert utils.plugin.slug == "utils"
    assert {cap.key for cap in utils.capabilities} >= {"image-generation", "web-retrieval"}
    assert {provider.key for provider in utils.providers} >= {"openai-images", "firecrawl"}
    assert {action.key for action in utils.actions} >= {"image.generate", "web.scrape"}
    assert {resource.key for resource in utils.resources} >= {"generated-image", "web-document"}
    utils_actions = {action.key: action for action in utils.actions}
    assert utils_actions["web.scrape"].config_json["connector"] == "firecrawl"
    assert utils_actions["web.read"].config_json["connector"] == "jina"
    assert utils_actions["web.read"].config_json["requires_credential"] is False
    assert utils_actions["sitemap.fetch"].config_json["connector"] == "sitemap"
    assert utils_actions["sitemap.fetch"].config_json["requires_credential"] is False

    seo = repo.catalog(plugin_slug="seo").plugins[0]
    assert {cap.key for cap in seo.capabilities} >= {"seo-content", "seo-research"}
    assert {provider.key for provider in seo.providers} >= {"dataforseo", "ahrefs"}
    assert {action.key for action in seo.actions} >= {
        "keyword.research",
        "serp.analyze",
        "paa.extract",
        "competitor.keywords",
    }
    seo_actions = {action.key: action for action in seo.actions}
    assert seo_actions["keyword.research"].config_json["connector"] == "dataforseo"
    assert seo_actions["serp.analyze"].config_json["connector"] == "dataforseo"
    assert seo_actions["paa.extract"].config_json["connector"] == "dataforseo"
    assert seo_actions["paa.extract"].config_json["operation"] == "paa"
    assert seo_actions["competitor.keywords"].config_json["connector"] == "ahrefs"
    assert seo_actions["backlink.research"].config_json["connector"] == "ahrefs"
    assert {resource.key for resource in seo.resources} >= {
        "keyword-opportunity",
        "content-piece",
        "content-refresh",
    }

    publishing = repo.catalog(plugin_slug="publishing").plugins[0]
    assert {cap.key for cap in publishing.capabilities} >= {"cms-publishing"}
    assert {provider.key for provider in publishing.providers} >= {"wordpress", "ghost"}
    assert {action.key for action in publishing.actions} >= {
        "wordpress.post.create",
        "ghost.post.create",
    }
    publishing_actions = {action.key: action for action in publishing.actions}
    assert publishing_actions["wordpress.post.create"].config_json["connector"] == "wordpress"
    assert publishing_actions["ghost.post.create"].config_json["connector"] == "ghost"
    assert {resource.key for resource in publishing.resources} >= {
        "published-post",
        "publish-target",
    }


def test_project_catalog_reports_action_availability(session: Session, project_id: int) -> None:
    repo = PluginRepository(session)

    missing_setup = {
        action.key: action
        for action in repo.list_actions(plugin_slug="utils", project_id=project_id)
    }
    assert missing_setup["web.scrape"].availability.status == "missing_credential"
    assert missing_setup["web.scrape"].availability.executable is False
    assert missing_setup["web.scrape"].availability.reasons == [
        "credential_required",
        "budget_required",
    ]
    assert missing_setup["web.read"].availability.status == "ready"
    assert missing_setup["sitemap.fetch"].availability.status == "ready"
    assert missing_setup["sitemap.fetch"].availability.executable is True

    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        plaintext_payload=b"firecrawl-key",
    )
    AuthRepository(session).status(project_id=project_id, provider_key="firecrawl")
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        monthly_budget_usd=10.0,
    )

    ready_setup = {
        action.key: action
        for action in repo.list_actions(plugin_slug="utils", project_id=project_id)
    }
    scrape = ready_setup["web.scrape"]
    assert scrape.connector_key == "firecrawl"
    assert scrape.requires_credential is True
    assert scrape.enforce_budget is True
    assert scrape.availability.status == "ready"
    assert scrape.availability.executable is True
    assert scrape.availability.credential_state == "available"
    assert scrape.availability.budget_state == "available"


def test_capability_provider_describe_supports_plugin_filter(session: Session) -> None:
    repo = PluginRepository(session)

    capability = repo.get_capability(key="seo-content", plugin_slug="seo")
    provider = repo.get_provider(key="openai-images", plugin_slug="utils")

    assert capability.plugin_slug == "seo"
    assert provider.plugin_slug == "utils"


def test_disabled_plugin_is_filtered_from_project_catalog(
    session: Session,
    project_id: int,
) -> None:
    repo = PluginRepository(session)

    repo.enable(project_id=project_id, plugin_slug="seo")
    repo.disable(project_id=project_id, plugin_slug="seo")

    plugins = repo.list_plugins(project_id=project_id)
    seo = next(plugin for plugin in plugins if plugin.slug == "seo")
    assert seo.enabled_for_project is False

    assert "seo" not in {
        plugin.plugin.slug for plugin in repo.catalog(project_id=project_id).plugins
    }
    assert "seo-content" not in {
        capability.key for capability in repo.list_capabilities(project_id=project_id)
    }
    assert "content-piece" not in {
        resource.key for resource in repo.list_resources(project_id=project_id)
    }
