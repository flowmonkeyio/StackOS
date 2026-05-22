"""REST route tests for StackOS plugin/catalog endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_plugin_catalog_routes(api: TestClient) -> None:
    plugins = api.get("/api/v1/plugins")
    assert plugins.status_code == 200
    assert [p["slug"] for p in plugins.json()] == [
        "core",
        "gtm",
        "media-buying",
        "publishing",
        "seo",
        "utils",
    ]

    catalog = api.get("/api/v1/catalog/utils")
    assert catalog.status_code == 200
    assert catalog.json()["plugin"]["slug"] == "utils"
    assert {r["key"] for r in catalog.json()["resources"]} >= {"generated-image", "web-document"}

    providers = api.get("/api/v1/providers", params={"plugin_slug": "utils"})
    assert providers.status_code == 200
    assert {p["key"] for p in providers.json()} >= {"openai-images", "firecrawl"}

    actions = api.get("/api/v1/actions", params={"plugin_slug": "utils"})
    assert actions.status_code == 200
    assert {a["key"] for a in actions.json()} >= {"image.generate", "web.scrape"}
    web_read = next(a for a in actions.json() if a["key"] == "web.read")
    assert web_read["action_ref"] == "utils.web.read"
    assert web_read["connector_key"] == "jina"
    assert web_read["availability"]["status"] == "ready"
    assert web_read["availability"]["executable"] is True
    sitemap_fetch = next(a for a in actions.json() if a["key"] == "sitemap.fetch")
    assert sitemap_fetch["action_ref"] == "utils.sitemap.fetch"
    assert sitemap_fetch["connector_key"] == "sitemap"
    assert sitemap_fetch["requires_credential"] is False
    assert sitemap_fetch["availability"]["status"] == "ready"

    action = api.get("/api/v1/actions/image.generate", params={"plugin_slug": "utils"})
    assert action.status_code == 200
    assert action.json()["input_schema_json"]["required"] == ["prompt"]
    assert action.json()["availability"]["status"] == "unknown"

    capability = api.get(
        "/api/v1/capabilities/seo-content",
        params={"plugin_slug": "seo"},
    )
    assert capability.status_code == 200
    assert capability.json()["plugin_slug"] == "seo"

    seo_catalog = api.get("/api/v1/catalog/seo")
    assert seo_catalog.status_code == 200
    assert seo_catalog.json()["plugin"]["manifest_json"]["ui"]["nav"]["section"] == "SEO"
    assert {a["key"] for a in seo_catalog.json()["actions"]} >= {
        "keyword.research",
        "serp.analyze",
        "paa.extract",
        "competitor.keywords",
    }
    assert {r["key"] for r in seo_catalog.json()["resources"]} >= {
        "keyword-opportunity",
        "content-piece",
        "content-refresh",
    }

    gtm_catalog = api.get("/api/v1/catalog/gtm")
    assert gtm_catalog.status_code == 200
    assert gtm_catalog.json()["plugin"]["manifest_json"]["ui"]["nav"]["section"] == "GTM"
    assert {p["key"] for p in gtm_catalog.json()["providers"]} >= {
        "hubspot",
        "salesforce",
        "apollo",
        "outreach",
        "gtm-webhook",
    }
    assert {a["key"] for a in gtm_catalog.json()["actions"]} >= {
        "hubspot.crm.companies.batch_upsert",
        "salesforce.lead.upsert_by_external_id",
        "apollo.people.enrich",
        "outreach.sequence_state.create",
        "webhook.pipeline.fetch",
    }
    assert {r["key"] for r in gtm_catalog.json()["resources"]} >= {
        "account",
        "lead",
        "sequence",
        "pipeline-snapshot",
    }

    gtm_action = api.get(
        "/api/v1/actions/hubspot.crm.companies.batch_upsert",
        params={"plugin_slug": "gtm"},
    )
    assert gtm_action.status_code == 200
    assert gtm_action.json()["connector_key"] == "hubspot"
    assert gtm_action.json()["availability"]["status"] == "unknown"

    media_catalog = api.get("/api/v1/catalog/media-buying")
    assert media_catalog.status_code == 200
    assert media_catalog.json()["plugin"]["manifest_json"]["ui"]["nav"]["section"] == (
        "Media Buying"
    )
    assert {p["key"] for p in media_catalog.json()["providers"]} >= {
        "meta-ads",
        "google-ads",
        "outbrain",
        "taboola",
        "media-buying-webhook",
    }
    assert {a["key"] for a in media_catalog.json()["actions"]} >= {
        "meta.campaign.create",
        "meta.ad_creative.create",
        "google.campaign.create",
        "outbrain.promoted_link.create",
        "taboola.item.create",
        "outbrain.campaign.create",
        "taboola.campaign.create",
        "webhook.media_campaign.create",
    }
    assert {r["key"] for r in media_catalog.json()["resources"]} >= {
        "campaign",
        "creative",
        "performance-snapshot",
        "media-experiment",
    }

    media_action = api.get(
        "/api/v1/actions/meta.campaign.create",
        params={"plugin_slug": "media-buying"},
    )
    assert media_action.status_code == 200
    assert media_action.json()["connector_key"] == "meta-ads"
    assert media_action.json()["availability"]["status"] == "unknown"

    publishing_catalog = api.get("/api/v1/catalog/publishing")
    assert publishing_catalog.status_code == 200
    assert publishing_catalog.json()["plugin"]["manifest_json"]["ui"]["nav"]["section"] == (
        "Publishing"
    )
    assert {p["key"] for p in publishing_catalog.json()["providers"]} >= {
        "wordpress",
        "ghost",
    }
    assert {a["key"] for a in publishing_catalog.json()["actions"]} >= {
        "wordpress.post.create",
        "ghost.post.create",
    }
    assert {r["key"] for r in publishing_catalog.json()["resources"]} >= {
        "published-post",
        "publish-target",
    }


def test_single_action_describe_can_be_project_aware(api: TestClient, project_id: int) -> None:
    credential = api.post(
        f"/api/v1/projects/{project_id}/auth/openai-images/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "sk-test"}},
    )
    assert credential.status_code == 201
    budget = api.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "openai-images", "monthly_budget_usd": 10.0},
    )
    assert budget.status_code == 200

    action = api.get(
        "/api/v1/actions/image.generate",
        params={"plugin_slug": "utils", "project_id": project_id},
    )

    assert action.status_code == 200
    assert action.json()["availability"]["status"] == "ready"
    assert action.json()["availability"]["executable"] is True


def test_project_plugin_enable_disable_routes(api: TestClient, project_id: int) -> None:
    enabled = api.post(f"/api/v1/projects/{project_id}/plugins/utils/enable", json={})
    assert enabled.status_code == 200
    assert enabled.json()["data"]["enabled"] is True

    annotated = api.get("/api/v1/plugins", params={"project_id": project_id}).json()
    utils = next(p for p in annotated if p["slug"] == "utils")
    assert utils["enabled_for_project"] is True

    disabled = api.post(f"/api/v1/projects/{project_id}/plugins/utils/disable")
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False


def test_disabled_seo_plugin_is_filtered_from_project_catalog(
    api: TestClient,
    project_id: int,
) -> None:
    assert api.post(f"/api/v1/projects/{project_id}/plugins/seo/enable", json={}).status_code == 200
    assert api.post(f"/api/v1/projects/{project_id}/plugins/seo/disable").status_code == 200

    annotated = api.get("/api/v1/plugins", params={"project_id": project_id}).json()
    seo = next(p for p in annotated if p["slug"] == "seo")
    assert seo["enabled_for_project"] is False

    catalog = api.get("/api/v1/catalog", params={"project_id": project_id})
    assert catalog.status_code == 200
    assert "seo" not in {p["plugin"]["slug"] for p in catalog.json()["plugins"]}

    seo_catalog = api.get("/api/v1/catalog/seo", params={"project_id": project_id})
    assert seo_catalog.status_code == 404

    capabilities = api.get("/api/v1/capabilities", params={"project_id": project_id})
    assert capabilities.status_code == 200
    assert "seo-content" not in {c["key"] for c in capabilities.json()}

    resources = api.get("/api/v1/resources", params={"project_id": project_id})
    assert resources.status_code == 200
    assert "content-piece" not in {r["key"] for r in resources.json()}
