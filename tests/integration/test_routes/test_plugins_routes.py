"""REST route tests for StackOS plugin/catalog endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from stackos.db.models import Action
from stackos.repositories.plugins import ActionOut, PluginRepository
from tests.helpers.plugin_catalog import assert_builtin_plugin_catalog


def test_directory_metadata_operations_are_complete_and_keep_detailed_schema_search(
    api: TestClient, project_id: int, monkeypatch
) -> None:
    # A real route/dispatcher/repository call, not a mocked action-array envelope.
    with Session(api.app.state.engine) as session:
        repo = PluginRepository(session)
        expected = repo.list_actions(project_id=project_id)
        action = session.exec(select(Action).where(Action.key == "mock.echo")).one()
        action.input_schema_json = {
            "type": "object",
            "properties": {
                "uniquedirectoryschemaneedle": {"type": "string", "description": "x" * 20000},
            },
        }
        session.add(action)
        session.commit()

    def forbid_full_projection(*_args, **_kwargs):
        raise AssertionError("directory summaries must not construct ActionOut")

    with monkeypatch.context() as patch:
        patch.setattr(ActionOut, "__init__", forbid_full_projection)
        listed = api.post(
            "/api/v1/operations/action.list/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "include_unavailable_integrations": True,
                    "response_mode": "raw",
                }
            },
        )
        inventory = api.post(
            "/api/v1/operations/integration.list/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "include_unavailable": True,
                    "include_actions": True,
                    "response_mode": "raw",
                }
            },
        )
    assert listed.status_code == inventory.status_code == 200
    data = listed.json()
    assert data["count"] == len(data["items"]) == len(expected) > 25
    assert [row["action_ref"] for row in data["items"]] == [row.action_ref for row in expected]
    assert all("input_schema_json" not in row and "config_json" not in row for row in data["items"])
    assert any(not row["exposure"]["visible_by_default"] for row in data["items"])
    provider_counts: dict[tuple[str, str], int] = {}
    for row in expected:
        key = (row.plugin_slug, row.provider_key)
        provider_counts[key] = provider_counts.get(key, 0) + 1
    for item in inventory.json()["items"]:
        key = (item["plugin_slug"], item["provider_key"])
        assert item["total_action_count"] == provider_counts.get(key, 0)
        assert len(item["actions"]) == item["total_action_count"]

    searched = api.post(
        "/api/v1/operations/action.list/call",
        json={
            "arguments": {
                "project_id": project_id,
                "query": "uniquedirectoryschemaneedle",
                "include_unavailable_integrations": True,
                "response_mode": "raw",
            }
        },
    )
    assert searched.status_code == 200
    assert [row["action_ref"] for row in searched.json()["items"]] == ["utils.mock.echo"]
    detail = api.get(
        "/api/v1/actions/mock.echo", params={"project_id": project_id, "plugin_slug": "utils"}
    )
    assert detail.status_code == 200
    assert "uniquedirectoryschemaneedle" in detail.json()["input_schema_json"]["properties"]


def test_plugin_catalog_routes(api: TestClient) -> None:
    plugins = api.get("/api/v1/plugins")
    assert plugins.status_code == 200
    assert_builtin_plugin_catalog(plugins.json())

    compact_plugins = api.get("/api/v1/plugins", params={"compact": True})
    assert compact_plugins.status_code == 200
    assert [p["slug"] for p in compact_plugins.json()] == [p["slug"] for p in plugins.json()]
    assert all(
        set(plugin["manifest_json"]).issubset({"display_order", "ui"})
        for plugin in compact_plugins.json()
    )
    assert len(compact_plugins.content) < len(plugins.content)
    for response in (plugins, compact_plugins):
        by_slug = {plugin["slug"]: plugin for plugin in response.json()}
        for slug in ("trackbooth", "shopify", "linear", "publishing"):
            assert by_slug[slug]["manifest_json"]["ui"] is None

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
        "custom-gtm-tool",
    }
    assert {a["key"] for a in gtm_catalog.json()["actions"]} >= {
        "hubspot.crm.companies.batch_upsert",
        "salesforce.lead.upsert_by_external_id",
        "apollo.people.enrich",
        "outreach.sequence_state.create",
        "custom_gtm.pipeline.fetch",
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
        "custom-media-tool",
    }
    assert {a["key"] for a in media_catalog.json()["actions"]} >= {
        "meta.campaign.create",
        "meta.ad_creative.create",
        "google.campaign.create",
        "outbrain.promoted_link.create",
        "taboola.item.create",
        "outbrain.campaign.create",
        "taboola.campaign.create",
        "custom_media.campaign.create",
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

    trackbooth_catalog = api.get("/api/v1/catalog/trackbooth")
    assert trackbooth_catalog.status_code == 200
    assert trackbooth_catalog.json()["plugin"]["manifest_json"]["ui"] is None
    assert {p["key"] for p in trackbooth_catalog.json()["providers"]} == {"trackbooth"}
    trackbooth_action_keys = {a["key"] for a in trackbooth_catalog.json()["actions"]}
    assert len(trackbooth_action_keys) == 3
    assert trackbooth_action_keys >= {
        "catalog.sync",
        "catalog.search",
        "operation.describe",
    }
    assert {r["key"] for r in trackbooth_catalog.json()["resources"]} >= {
        "agent-api-operation",
        "agent-api-schema",
    }

    trackbooth_action = api.get(
        "/api/v1/actions/catalog.search",
        params={"plugin_slug": "trackbooth"},
    )
    assert trackbooth_action.status_code == 200
    assert trackbooth_action.json()["connector_key"] == "trackbooth"
    assert trackbooth_action.json()["availability"]["status"] == "unknown"
    assert "api_url" not in trackbooth_action.json()["input_schema_json"]["properties"]

    publishing_catalog = api.get("/api/v1/catalog/publishing")
    assert publishing_catalog.status_code == 200
    assert publishing_catalog.json()["plugin"]["manifest_json"]["ui"] is None
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

    branding_catalog = api.get("/api/v1/catalog/branding")
    assert branding_catalog.status_code == 200
    assert branding_catalog.json()["plugin"]["manifest_json"]["ui"]["nav"]["section"] == (
        "Branding"
    )
    assert {r["key"] for r in branding_catalog.json()["resources"]} >= {
        "evidence-item",
        "content-piece",
        "distribution-record",
    }
    assert {a["key"] for a in branding_catalog.json()["actions"]} >= {
        "publish.git-blog",
        "evidence.capture",
        "distribution.snapshot",
    }


def test_single_action_describe_can_be_project_aware(api: TestClient, project_id: int) -> None:
    credential = api.post(
        "/api/v1/auth/accounts/openai-images",
        json={
            "auth_method_key": "api_key",
            "display_name": "OpenAI Images - Default",
            "attach_project_id": project_id,
            "fields": {"api_key": "sk-test"},
        },
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
    assert ("seo", "content-piece") not in {(r["plugin_slug"], r["key"]) for r in resources.json()}
