"""Repository tests for StackOS plugin catalog primitives."""

from __future__ import annotations

import asyncio
import weakref

import pytest
from sqlmodel import Session, select

from stackos import action_availability
from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import Action, Provider
from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.repositories.base import NotFoundError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.projects import (
    IntegrationBudgetRepository,
    IntegrationCredentialRepository,
)


def test_builtin_plugins_sync_and_list(session: Session) -> None:
    repo = PluginRepository(session)

    plugins = repo.list_plugins()

    assert [p.slug for p in plugins] == [
        "engineering",
        "support",
        "communications",
        "gtm",
        "media-buying",
        "trackbooth",
        "publishing",
        "seo",
        "core",
        "utils",
    ]
    seo = repo.get_plugin("seo")
    assert seo.name == "SEO"
    assert seo.enabled_for_project is None
    assert seo.manifest_json["ui"]["nav"]["section"] == "SEO"
    gtm = repo.get_plugin("gtm")
    assert gtm.name == "GTM And RevOps"
    assert gtm.manifest_json["ui"]["nav"]["section"] == "GTM"
    media = repo.get_plugin("media-buying")
    assert media.name == "Media Buying"
    assert media.manifest_json["ui"]["nav"]["section"] == "Media Buying"
    trackbooth = repo.get_plugin("trackbooth")
    assert trackbooth.name == "Trackbooth"
    assert trackbooth.manifest_json["ui"]["nav"]["section"] == "Trackbooth"
    assert trackbooth.manifest_json["config"]["default_api_base_url"] == (
        "https://apis.trackbooth.com"
    )
    publishing = repo.get_plugin("publishing")
    assert publishing.name == "Publishing"
    assert publishing.manifest_json["ui"]["nav"]["section"] == "Publishing"
    communications = repo.get_plugin("communications")
    assert communications.name == "Communications"
    assert communications.manifest_json["ui"]["nav"]["section"] == "Communications"
    engineering = repo.get_plugin("engineering")
    assert engineering.name == "Engineering"
    assert engineering.manifest_json["ui"]["nav"]["section"] == "Engineering"
    assert engineering.manifest_json["display_order"] == 10
    support = repo.get_plugin("support")
    assert support.name == "Support"
    assert support.manifest_json["ui"]["nav"]["section"] == "Support"


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
    assert {cap.key for cap in utils.capabilities} >= {
        "image-generation",
        "web-retrieval",
        "model-access",
        "integration-testing",
    }
    assert {provider.key for provider in utils.providers} >= {
        "openai-images",
        "openrouter",
        "firecrawl",
        "mock-provider",
    }
    assert {action.key for action in utils.actions} >= {
        "image.generate",
        "web.scrape",
        "mock.echo",
    }
    assert {resource.key for resource in utils.resources} >= {"generated-image", "web-document"}
    utils_actions = {action.key: action for action in utils.actions}
    assert utils_actions["web.scrape"].config_json["connector"] == "firecrawl"
    assert utils_actions["web.read"].config_json["connector"] == "jina"
    assert utils_actions["web.read"].config_json["requires_credential"] is False
    assert utils_actions["sitemap.fetch"].config_json["connector"] == "sitemap"
    assert utils_actions["sitemap.fetch"].config_json["requires_credential"] is False
    assert utils_actions["mock.echo"].config_json["connector"] == "mock-provider"
    assert utils_actions["mock.echo"].config_json["requires_credential"] is True
    assert all(action.provider_key != "openrouter" for action in utils.actions)

    seo = repo.catalog(plugin_slug="seo").plugins[0]
    assert {cap.key for cap in seo.capabilities} >= {"seo-content", "seo-research"}
    assert {provider.key for provider in seo.providers} >= {"dataforseo", "serper", "ahrefs"}
    assert {action.key for action in seo.actions} >= {
        "keyword.research",
        "serp.analyze",
        "paa.extract",
        "serper.search",
        "competitor.keywords",
    }
    seo_actions = {action.key: action for action in seo.actions}
    assert seo_actions["keyword.research"].config_json["connector"] == "dataforseo"
    assert seo_actions["serp.analyze"].config_json["connector"] == "dataforseo"
    assert seo_actions["paa.extract"].config_json["connector"] == "dataforseo"
    assert seo_actions["paa.extract"].config_json["operation"] == "paa"
    assert seo_actions["serper.search"].config_json["connector"] == "serper"
    assert seo_actions["serper.search"].operation == "search"
    assert seo_actions["competitor.keywords"].config_json["connector"] == "ahrefs"
    assert seo_actions["backlink.research"].config_json["connector"] == "ahrefs"
    assert {resource.key for resource in seo.resources} >= {
        "keyword-opportunity",
        "content-piece",
        "content-refresh",
    }

    gtm = repo.catalog(plugin_slug="gtm").plugins[0]
    assert {cap.key for cap in gtm.capabilities} >= {
        "account-research",
        "lead-management",
        "crm-operations",
        "pipeline-management",
    }
    assert {provider.key for provider in gtm.providers} >= {
        "hubspot",
        "salesforce",
        "apollo",
        "outreach",
        "custom-gtm-tool",
    }
    assert {action.key for action in gtm.actions} >= {
        "hubspot.crm.companies.batch_upsert",
        "salesforce.lead.upsert_by_external_id",
        "apollo.people.enrich",
        "outreach.sequence_state.create",
        "custom_gtm.pipeline.fetch",
    }
    gtm_actions = {action.key: action for action in gtm.actions}
    company_upsert = gtm_actions["hubspot.crm.companies.batch_upsert"]
    assert company_upsert.connector_key == "hubspot"
    assert company_upsert.operation == "crm.companies.batch_upsert"
    assert company_upsert.requires_credential is True
    assert company_upsert.availability.status == "unknown"
    assert {resource.key for resource in gtm.resources} >= {
        "account",
        "lead",
        "sequence",
        "enrichment-record",
        "pipeline-snapshot",
    }

    engineering = repo.catalog(plugin_slug="engineering").plugins[0]
    assert {cap.key for cap in engineering.capabilities} >= {
        "engineering-delivery",
        "engineering-review",
    }
    assert {resource.key for resource in engineering.resources} >= {
        "engineering-decision",
        "engineering-evidence",
    }
    engineering_resources = {resource.key: resource for resource in engineering.resources}
    assert engineering_resources["engineering-decision"].ui_schema_json is not None
    assert engineering_resources["engineering-decision"].config_json is not None
    assert engineering_resources["engineering-evidence"].ui_schema_json is not None
    assert engineering_resources["engineering-evidence"].config_json is not None

    media = repo.catalog(plugin_slug="media-buying").plugins[0]
    assert {cap.key for cap in media.capabilities} >= {
        "campaign-management",
        "creative-operations",
        "media-measurement",
    }
    assert {provider.key for provider in media.providers} >= {
        "meta-ads",
        "google-ads",
        "outbrain",
        "taboola",
        "custom-media-tool",
    }
    assert {action.key for action in media.actions} >= {
        "meta.campaign.create",
        "meta.ad_set.create",
        "meta.ad_creative.create",
        "google.campaign.create",
        "outbrain.promoted_link.create",
        "taboola.item.create",
        "outbrain.campaign.create",
        "taboola.campaign.create",
        "custom_media.campaign.create",
    }
    media_actions = {action.key: action for action in media.actions}
    campaign_create = media_actions["meta.campaign.create"]
    assert campaign_create.connector_key == "meta-ads"
    assert campaign_create.operation == "campaign.create"
    assert campaign_create.requires_credential is True
    assert campaign_create.availability.status == "unknown"
    assert {resource.key for resource in media.resources} >= {
        "campaign",
        "creative",
        "performance-snapshot",
        "budget-change",
        "media-experiment",
    }

    trackbooth = repo.catalog(plugin_slug="trackbooth").plugins[0]
    assert {cap.key for cap in trackbooth.capabilities} >= {"agent-api"}
    assert {provider.key for provider in trackbooth.providers} == {"trackbooth"}
    trackbooth_action_keys = {action.key for action in trackbooth.actions}
    assert len(trackbooth_action_keys) == 3
    assert trackbooth_action_keys >= {
        "catalog.sync",
        "catalog.search",
        "operation.describe",
    }
    trackbooth_actions = {action.key: action for action in trackbooth.actions}
    assert trackbooth_actions["catalog.sync"].connector_key == "trackbooth"
    assert trackbooth_actions["catalog.sync"].operation == "catalog.sync"
    assert trackbooth_actions["catalog.sync"].requires_credential is True
    assert trackbooth_actions["catalog.search"].connector_key == "trackbooth"
    assert trackbooth_actions["catalog.search"].operation == "catalog.search"
    assert trackbooth_actions["catalog.search"].requires_credential is True
    assert trackbooth_actions["catalog.search"].availability.status == "unknown"
    assert {resource.key for resource in trackbooth.resources} >= {
        "agent-api-operation",
        "agent-api-schema",
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


def test_trackbooth_plugin_sync_hides_removed_generic_rest_actions(
    session: Session,
    project_id: int,
) -> None:
    repo = PluginRepository(session)
    trackbooth = repo.get_plugin("trackbooth")
    provider = session.exec(
        select(Provider).where(Provider.plugin_id == trackbooth.id, Provider.key == "trackbooth")
    ).first()
    assert provider is not None
    for action_key in ("rest.read", "rest.write"):
        session.add(
            Action(
                plugin_id=trackbooth.id,
                provider_id=provider.id,
                key=action_key,
                name=f"Legacy {action_key}",
                description="Legacy Trackbooth generic REST action",
                capability_key="agent-api",
                risk_level="write" if action_key == "rest.write" else "read",
                input_schema_json={"type": "object", "additionalProperties": True},
                output_schema_json={"type": "object", "additionalProperties": True},
                config_json={
                    "schema_version": "stackos.action.v1",
                    "connector": "trackbooth",
                    "operation": action_key,
                    "requires_credential": True,
                },
            )
        )
    session.commit()

    repo.sync_builtin_plugins()
    action_keys = {
        action.key for action in repo.list_actions(plugin_slug="trackbooth", project_id=project_id)
    }

    assert action_keys == {"catalog.sync", "catalog.search", "operation.describe"}
    legacy = session.exec(select(Action).where(Action.key == "rest.read")).first()
    assert legacy is not None
    assert legacy.config_json["trackbooth_removed_action"] is True
    assert legacy.config_json["execution_mode"] == "deferred.removed"
    actions = ActionRepository(session)
    for action_ref in ("trackbooth.rest.read", "trackbooth.rest.write"):
        with pytest.raises(NotFoundError):
            actions.describe(project_id=project_id, action_ref=action_ref)
        with pytest.raises(NotFoundError):
            actions.validate(project_id=project_id, action_ref=action_ref, input_json={})
        with pytest.raises(NotFoundError):
            asyncio.run(
                actions.execute(project_id=project_id, action_ref=action_ref, input_json={})
            )


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
    assert missing_setup["mock.echo"].availability.status == "missing_credential"
    assert missing_setup["mock.echo"].availability.reasons == ["credential_required"]

    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="firecrawl",
        secret_payload=b"firecrawl-key",
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


def test_catalog_syncs_builtin_manifests_once_per_repository_instance(
    session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(PluginRepository, "_builtin_sync_engines", weakref.WeakSet())
    repo = PluginRepository(session)
    synced_slugs: list[str] = []
    sync_manifest = repo._sync_manifest

    def counted_sync_manifest(manifest):
        synced_slugs.append(manifest.slug)
        return sync_manifest(manifest)

    monkeypatch.setattr(repo, "_sync_manifest", counted_sync_manifest)

    catalog = repo.catalog()

    assert {plugin.plugin.slug for plugin in catalog.plugins} >= {
        "communications",
        "core",
        "engineering",
        "gtm",
        "media-buying",
        "trackbooth",
        "publishing",
        "seo",
        "utils",
    }
    assert synced_slugs == [manifest.slug for manifest in BUILTIN_PLUGIN_MANIFESTS]


def test_catalog_builds_action_availability_context_once(
    session: Session,
    project_id: int,
    monkeypatch,
) -> None:
    repo = PluginRepository(session)
    context_calls = 0
    build_context = action_availability.build_action_availability_context

    def counted_build_context(*args, **kwargs):
        nonlocal context_calls
        context_calls += 1
        return build_context(*args, **kwargs)

    monkeypatch.setattr(
        action_availability,
        "build_action_availability_context",
        counted_build_context,
    )

    catalog = repo.catalog(project_id=project_id)

    assert sum(len(plugin.actions) for plugin in catalog.plugins) >= 100
    assert context_calls == 1


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
