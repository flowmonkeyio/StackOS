"""Unit tests for StackOS plugin manifests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import content_stack.plugins.manifest as manifest_module
from content_stack.plugins.manifest import (
    BUILTIN_PLUGIN_MANIFESTS,
    PluginManifest,
    ProviderManifest,
    load_plugin_manifest_file,
    load_plugin_manifest_files,
)


def _auth_field_keys(provider: ProviderManifest, method_key: str | None = None) -> list[str]:
    methods = provider.auth_methods
    if method_key is not None:
        methods = [method for method in methods if method.key == method_key]
    assert methods
    return [field.key for field in methods[0].fields]


def test_builtin_plugin_manifests_validate() -> None:
    slugs = [manifest.slug for manifest in BUILTIN_PLUGIN_MANIFESTS]

    assert slugs == [
        "communications",
        "core",
        "gtm",
        "media-buying",
        "publishing",
        "seo",
        "utils",
    ]
    for manifest in BUILTIN_PLUGIN_MANIFESTS:
        assert manifest.capabilities
        assert manifest.resources
        assert manifest.model_dump(mode="json")["slug"] == manifest.slug

    resources_by_plugin = {
        manifest.slug: {resource.key for resource in manifest.resources}
        for manifest in BUILTIN_PLUGIN_MANIFESTS
    }
    assert resources_by_plugin["communications"] >= {
        "communication-channel",
        "communication-message",
        "communication-cursor",
    }
    assert resources_by_plugin["core"] >= {"learning", "experiment"}
    assert resources_by_plugin["gtm"] >= {"account", "lead", "pipeline-snapshot"}
    assert resources_by_plugin["media-buying"] >= {"campaign", "creative"}
    assert resources_by_plugin["publishing"] >= {"published-post", "publish-target"}
    assert resources_by_plugin["seo"] >= {"keyword-opportunity", "content-piece"}
    assert resources_by_plugin["utils"] >= {"generated-image", "web-document"}
    publishing = next(
        manifest for manifest in BUILTIN_PLUGIN_MANIFESTS if manifest.slug == "publishing"
    )
    publishing_actions = {action.key: action for action in publishing.actions}
    assert publishing_actions["wordpress.post.create"].config == {
        "schema_version": "stackos.action.v1",
        "connector": "wordpress",
        "operation": "post.create",
        "requires_credential": True,
    }
    assert publishing_actions["ghost.post.create"].config["connector"] == "ghost"
    assert publishing_actions["wordpress.post.create"].input_schema["required"] == ["post"]
    utils = next(manifest for manifest in BUILTIN_PLUGIN_MANIFESTS if manifest.slug == "utils")
    utils_actions = {action.key: action for action in utils.actions}
    assert utils_actions["web.scrape"].config["connector"] == "firecrawl"
    assert utils_actions["web.read"].config == {
        "schema_version": "stackos.action.v1",
        "connector": "jina",
        "operation": "read",
        "requires_credential": False,
        "allows_credential": True,
        "budget_kind": "jina",
        "enforce_budget": False,
    }
    assert utils_actions["sitemap.fetch"].config == {
        "schema_version": "stackos.action.v1",
        "connector": "sitemap",
        "operation": "fetch",
        "requires_credential": False,
        "allows_credential": False,
    }
    assert utils_actions["reddit.search-subreddit"].config["connector"] == "reddit"


def test_communications_plugin_yaml_facade_validates() -> None:
    manifest = load_plugin_manifest_file(Path("plugins/communications/plugin.yaml"))

    assert manifest.slug == "communications"
    assert manifest.ui is not None
    assert manifest.ui["nav"]["section"] == "Communications"
    assert {capability.key for capability in manifest.capabilities} >= {
        "messaging",
        "email-send",
        "email-inbox",
        "agent-triggering",
    }
    assert {provider.key for provider in manifest.providers} == {
        "telegram-bot",
        "smtp",
        "imap",
    }
    providers = {provider.key: provider for provider in manifest.providers}
    assert _auth_field_keys(providers["telegram-bot"], "bot-token")[:2] == [
        "bot_token",
        "webhook_secret_token",
    ]
    assert (
        providers["telegram-bot"]
        .config["setup_note"]
        .startswith("Telegram polling and webhook modes are mutually exclusive")
    )
    assert _auth_field_keys(providers["smtp"], "smtp-password")[:4] == [
        "password",
        "host",
        "port",
        "tls_mode",
    ]
    assert _auth_field_keys(providers["imap"], "imap-password")[:4] == [
        "password",
        "host",
        "port",
        "tls_mode",
    ]
    actions = {action.key: action for action in manifest.actions}
    assert actions["telegram-bot.identity.get"].provider == "telegram-bot"
    assert actions["telegram-bot.message.send"].risk_level == "write"
    assert actions["telegram-bot.updates.poll"].capability == "agent-triggering"
    assert actions["telegram-bot.identity.get"].config["execution_mode"] == "deferred-connector"
    assert "connector" not in actions["telegram-bot.identity.get"].config
    assert actions["smtp.email.send"].config["execution_mode"] == "deferred-connector"
    assert actions["imap.message.fetch"].input_schema["required"] == [
        "mailbox_ref",
        "uid",
    ]
    assert {resource.key for resource in manifest.resources} >= {
        "communication-channel",
        "communication-thread",
        "communication-message",
        "communication-event",
        "communication-cursor",
        "agent-request-source",
    }


def test_gtm_plugin_yaml_facade_validates() -> None:
    manifest = load_plugin_manifest_file(Path("plugins/gtm/plugin.yaml"))

    assert manifest.slug == "gtm"
    assert manifest.ui is not None
    assert manifest.ui["nav"]["section"] == "GTM"
    assert {capability.key for capability in manifest.capabilities} >= {
        "account-research",
        "lead-management",
        "crm-operations",
        "outbound-operations",
        "pipeline-management",
        "enrichment",
    }
    assert {provider.key for provider in manifest.providers} >= {
        "hubspot",
        "salesforce",
        "apollo",
        "clay",
        "outreach",
        "salesloft",
        "custom-gtm-tool",
    }
    providers = {provider.key: provider for provider in manifest.providers}
    assert _auth_field_keys(providers["hubspot"])[:2] == ["access_token", "portal_ref"]
    assert providers["custom-gtm-tool"].auth_type == "local"
    assert (
        providers["custom-gtm-tool"].config["connection_setup"] == "project-local-plugin-required"
    )
    actions = {action.key: action for action in manifest.actions}
    assert actions["hubspot.crm.companies.batch_upsert"].provider == "hubspot"
    assert actions["hubspot.crm.companies.batch_upsert"].risk_level == "write"
    assert actions["hubspot.crm.companies.batch_upsert"].input_schema["required"] == [
        "id_property",
        "inputs",
    ]
    assert actions["salesforce.lead.upsert_by_external_id"].provider == "salesforce"
    assert actions["salesforce.lead.upsert_by_external_id"].input_schema["required"] == [
        "external_id_policy_ref",
        "lead",
        "update_only",
    ]
    assert actions["apollo.people.enrich"].capability == "enrichment"
    assert actions["outreach.sequence_state.create"].provider == "outreach"
    assert actions["custom_gtm.pipeline.fetch"].provider == "custom-gtm-tool"
    assert actions["hubspot.crm.companies.batch_upsert"].config["connector"] == "hubspot"
    assert actions["salesforce.lead.upsert_by_external_id"].config["connector"] == "salesforce"
    assert actions["apollo.people.enrich"].config["connector"] == "apollo"
    assert actions["outreach.sequence_state.create"].config["connector"] == "outreach"
    assert actions["custom_gtm.pipeline.fetch"].config["execution_mode"] == "project-local-http"
    assert {resource.key for resource in manifest.resources} >= {
        "account",
        "company",
        "contact",
        "lead",
        "opportunity",
        "deal",
        "sequence",
        "task",
        "touchpoint",
        "enrichment-record",
        "pipeline-snapshot",
    }


def test_media_buying_plugin_yaml_facade_validates() -> None:
    manifest = load_plugin_manifest_file(Path("plugins/media-buying/plugin.yaml"))

    assert manifest.slug == "media-buying"
    assert manifest.ui is not None
    assert manifest.ui["nav"]["section"] == "Media Buying"
    assert {capability.key for capability in manifest.capabilities} >= {
        "campaign-management",
        "creative-operations",
        "media-measurement",
        "media-experimentation",
    }
    assert {provider.key for provider in manifest.providers} >= {
        "meta-ads",
        "google-ads",
        "outbrain",
        "taboola",
        "custom-media-tool",
    }
    providers = {provider.key: provider for provider in manifest.providers}
    assert _auth_field_keys(providers["meta-ads"])[:2] == ["access_token", "business_ref"]
    assert providers["custom-media-tool"].auth_type == "local"
    assert (
        providers["custom-media-tool"].config["connection_setup"] == "project-local-plugin-required"
    )
    actions = {action.key: action for action in manifest.actions}
    assert actions["meta.campaign.create"].provider == "meta-ads"
    assert actions["meta.campaign.create"].risk_level == "write"
    assert actions["meta.campaign.create"].input_schema["required"] == [
        "account_ref",
        "campaign",
    ]
    assert actions["custom_media.campaign.create"].provider == "custom-media-tool"
    assert actions["custom_media.performance.fetch"].capability == "media-measurement"
    assert actions["meta.campaign.create"].config["connector"] == "meta-ads"
    assert actions["google.campaign.create"].config["connector"] == "google-ads"
    assert actions["taboola.campaign.create"].config["connector"] == "taboola"
    assert actions["outbrain.campaign.create"].config["execution_mode"] == "deferred-partner-api"
    assert actions["custom_media.campaign.create"].config["execution_mode"] == "project-local-http"
    assert {resource.key for resource in manifest.resources} >= {
        "ad-account",
        "campaign",
        "ad-set",
        "ad",
        "creative",
        "audience",
        "landing-page",
        "performance-snapshot",
        "budget-change",
        "media-experiment",
    }


def test_seo_plugin_yaml_facade_validates() -> None:
    manifest = load_plugin_manifest_file(Path("plugins/seo/plugin.yaml"))

    assert manifest.slug == "seo"
    assert manifest.ui is not None
    assert manifest.ui["nav"]["section"] == "SEO"
    assert {capability.key for capability in manifest.capabilities} >= {
        "seo-content",
        "seo-research",
        "seo-measurement",
    }
    assert {provider.key for provider in manifest.providers} >= {
        "dataforseo",
        "ahrefs",
    }
    providers = {provider.key: provider for provider in manifest.providers}
    assert _auth_field_keys(providers["dataforseo"]) == ["login", "password"]
    assert {resource.key for resource in manifest.resources} >= {
        "keyword-opportunity",
        "serp-snapshot",
        "content-brief",
        "content-piece",
        "content-refresh",
        "search-performance-snapshot",
    }
    actions = {action.key: action for action in manifest.actions}
    assert actions["keyword.research"].config == {
        "schema_version": "stackos.action.v1",
        "connector": "dataforseo",
        "operation": "keyword.research",
        "requires_credential": True,
        "budget_kind": "dataforseo",
        "enforce_budget": True,
    }
    assert actions["serp.analyze"].config["connector"] == "dataforseo"
    assert actions["paa.extract"].config == {
        "schema_version": "stackos.action.v1",
        "connector": "dataforseo",
        "operation": "paa",
        "requires_credential": True,
        "budget_kind": "dataforseo",
        "enforce_budget": True,
    }
    assert actions["competitor.keywords"].config["connector"] == "ahrefs"
    assert actions["backlink.research"].config["connector"] == "ahrefs"
    assert actions["keyword.research"].input_schema["required"] == ["keywords"]
    assert actions["paa.extract"].input_schema["required"] == ["keyword"]


def test_publishing_plugin_yaml_facade_validates() -> None:
    manifest = load_plugin_manifest_file(Path("plugins/publishing/plugin.yaml"))

    assert manifest.slug == "publishing"
    assert manifest.ui is not None
    assert manifest.ui["nav"]["section"] == "Publishing"
    assert {provider.key for provider in manifest.providers} == {"wordpress", "ghost"}
    providers = {provider.key: provider for provider in manifest.providers}
    assert _auth_field_keys(providers["wordpress"]) == [
        "username",
        "application_password",
        "wp_url",
    ]
    assert _auth_field_keys(providers["ghost"])[:2] == ["admin_api_key", "ghost_url"]
    actions = {action.key: action for action in manifest.actions}
    assert actions["wordpress.post.create"].provider == "wordpress"
    assert actions["wordpress.post.create"].risk_level == "write"
    assert actions["wordpress.post.create"].config["connector"] == "wordpress"
    assert actions["ghost.post.create"].provider == "ghost"
    assert actions["ghost.post.create"].config["connector"] == "ghost"
    assert {resource.key for resource in manifest.resources} >= {
        "published-post",
        "publish-target",
    }


def test_plugin_manifest_loader_reads_bundled_assets_when_repo_plugins_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "seo" / "plugin.yaml"
    bundled.parent.mkdir()
    bundled.write_text(
        Path("plugins/seo/plugin.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    monkeypatch.setattr(manifest_module, "_plugin_manifest_paths", lambda: [])
    monkeypatch.setattr(manifest_module, "_bundled_plugin_manifest_nodes", lambda: [bundled])

    manifests = load_plugin_manifest_files()

    assert [manifest.slug for manifest in manifests] == ["seo"]
    assert manifests[0].ui is not None
    assert manifests[0].ui["nav"]["section"] == "SEO"


def test_manifest_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(
            {
                "slug": "bad-plugin",
                "name": "Bad",
                "unexpected": True,
            }
        )


def test_manifest_rejects_invalid_identifier() -> None:
    with pytest.raises(ValidationError):
        PluginManifest.model_validate({"slug": "Bad Plugin", "name": "Bad"})


def test_manifest_accepts_provider_native_snake_segments() -> None:
    manifest = PluginManifest.model_validate(
        {
            "slug": "media-buying",
            "name": "Media Buying",
            "actions": [
                {
                    "key": "meta.ad_set.create",
                    "name": "Create Meta Ad Set",
                    "provider": "meta-ads",
                    "capability": "campaign-management",
                }
            ],
        }
    )

    assert manifest.actions[0].key == "meta.ad_set.create"
