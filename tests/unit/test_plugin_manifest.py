"""Unit tests for StackOS plugin manifests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import content_stack.plugins.manifest as manifest_module
from content_stack.plugins.manifest import (
    BUILTIN_PLUGIN_MANIFESTS,
    PluginManifest,
    load_plugin_manifest_file,
    load_plugin_manifest_files,
)


def test_builtin_plugin_manifests_validate() -> None:
    slugs = [manifest.slug for manifest in BUILTIN_PLUGIN_MANIFESTS]

    assert slugs == ["core", "seo", "utils"]
    for manifest in BUILTIN_PLUGIN_MANIFESTS:
        assert manifest.capabilities
        assert manifest.resources
        assert manifest.model_dump(mode="json")["slug"] == manifest.slug

    resources_by_plugin = {
        manifest.slug: {resource.key for resource in manifest.resources}
        for manifest in BUILTIN_PLUGIN_MANIFESTS
    }
    assert resources_by_plugin["core"] >= {"learning", "experiment"}
    assert resources_by_plugin["seo"] >= {"keyword-opportunity", "content-piece"}
    assert resources_by_plugin["utils"] >= {"generated-image", "web-document"}
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
    assert utils_actions["reddit.search-subreddit"].config["connector"] == "reddit"


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
    assert actions["competitor.keywords"].config["connector"] == "ahrefs"
    assert actions["backlink.research"].config["connector"] == "ahrefs"
    assert actions["keyword.research"].input_schema["required"] == ["keywords"]


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
