"""Repository tests for StackOS plugin catalog primitives."""

from __future__ import annotations

from sqlmodel import Session

from content_stack.repositories.plugins import PluginRepository


def test_builtin_plugins_sync_and_list(session: Session) -> None:
    repo = PluginRepository(session)

    plugins = repo.list_plugins()

    assert [p.slug for p in plugins] == ["core", "seo", "utils"]
    seo = repo.get_plugin("seo")
    assert seo.name == "SEO"
    assert seo.enabled_for_project is None


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


def test_capability_provider_describe_supports_plugin_filter(session: Session) -> None:
    repo = PluginRepository(session)

    capability = repo.get_capability(key="seo-content", plugin_slug="seo")
    provider = repo.get_provider(key="openai-images", plugin_slug="utils")

    assert capability.plugin_slug == "seo"
    assert provider.plugin_slug == "utils"
