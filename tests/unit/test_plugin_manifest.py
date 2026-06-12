"""Unit tests for StackOS plugin manifests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import stackos.plugins.manifest as manifest_module
from stackos.plugins.manifest import (
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
        "engineering",
        "support",
        "communications",
        "gtm",
        "marketing",
        "branding",
        "media-buying",
        "trackbooth",
        "publishing",
        "seo",
        "core",
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
        "communication-interaction",
        "communication-cursor",
    }
    assert resources_by_plugin["core"] >= {"learning", "experiment"}
    assert resources_by_plugin["engineering"] >= {
        "engineering-decision",
        "engineering-evidence",
    }
    assert resources_by_plugin["support"] >= {"support-investigation"}
    engineering = next(
        manifest for manifest in BUILTIN_PLUGIN_MANIFESTS if manifest.slug == "engineering"
    )
    engineering_resources = {resource.key: resource for resource in engineering.resources}
    assert engineering.display_order == 10
    assert engineering_resources["engineering-decision"].ui_schema is not None
    assert engineering_resources["engineering-decision"].config is not None
    assert engineering_resources["engineering-decision"].schema_data["required"] == [
        "title",
        "decision",
        "status",
    ]
    assert engineering_resources["engineering-evidence"].ui_schema is not None
    assert engineering_resources["engineering-evidence"].config is not None
    assert resources_by_plugin["gtm"] >= {"account", "lead", "pipeline-snapshot"}
    assert resources_by_plugin["media-buying"] >= {"campaign", "creative"}
    assert resources_by_plugin["trackbooth"] >= {
        "agent-api-operation",
        "agent-api-schema",
    }
    assert resources_by_plugin["publishing"] >= {"published-post", "publish-target"}
    assert resources_by_plugin["seo"] >= {"keyword-opportunity", "content-piece"}
    assert resources_by_plugin["utils"] >= {"generated-image", "generated-video", "web-document"}
    assert resources_by_plugin["branding"] >= {
        "brand-profile",
        "position",
        "evidence-item",
        "stream",
        "channel",
        "routing-policy",
        "content-piece",
        "distribution-record",
    }
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
    utils_providers = {provider.key: provider for provider in utils.providers}
    assert "openrouter" in utils_providers
    assert "xai-imagine" in utils_providers
    assert "reve" in utils_providers
    assert "google-gemini-image" in utils_providers
    assert "google-veo" in utils_providers
    assert "ideogram" in utils_providers
    assert "byteplus-ark" in utils_providers
    assert "alibaba-wan" in utils_providers
    assert "kling" in utils_providers
    assert _auth_field_keys(utils_providers["reve"]) == ["api_key"]
    assert _auth_field_keys(utils_providers["google-gemini-image"]) == ["api_key"]
    assert _auth_field_keys(utils_providers["google-veo"]) == ["api_key"]
    assert _auth_field_keys(utils_providers["ideogram"]) == ["api_key"]
    assert _auth_field_keys(utils_providers["byteplus-ark"]) == ["api_key"]
    assert _auth_field_keys(utils_providers["alibaba-wan"]) == ["api_key"]
    assert _auth_field_keys(utils_providers["kling"], "access_key_secret") == [
        "access_key",
        "secret_key",
    ]
    assert _auth_field_keys(utils_providers["openrouter"]) == [
        "api_key",
        "http_referer",
        "app_title",
    ]
    assert "Unified model API provider connection" in utils_providers["openrouter"].description
    assert {capability.key for capability in utils.capabilities} >= {"model-access"}
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
    assert all(action.provider != "openrouter" for action in utils.actions)
    assert {capability.key for capability in utils.capabilities} >= {
        "image-generation",
        "video-generation",
    }
    image_generate_capabilities = utils_actions["image.generate"].config["capability_metadata"]
    assert image_generate_capabilities["modalities"] == {
        "input": ["text"],
        "output": ["image"],
    }
    assert image_generate_capabilities["modes"] == ["text-to-image"]
    assert image_generate_capabilities["execution"]["mode"] == "sync"
    assert image_generate_capabilities["models"]["gpt-image-2"]["sizes"] == [
        "auto",
        "1024x1024",
        "1536x1024",
        "1024x1536",
    ]
    assert "auto" in image_generate_capabilities["models"]["gpt-image-1.5"]["sizes"]
    assert (
        utils_actions["image.generate"].input_schema["properties"]["prompt"]["maxLength"] == 32000
    )
    assert utils_actions["image.generate"].output_schema["properties"]["data"]["type"] == "array"
    assert (
        "output_compression parameter"
        in image_generate_capabilities["unsupported_provider_features"]
    )
    assert (
        "gpt-image-2 custom WxH sizes beyond StackOS presets"
        in image_generate_capabilities["unsupported_provider_features"]
    )
    assert image_generate_capabilities["limits"]["prompt_max_chars"] == 32000
    assert utils_actions["image.edit"].config["connector"] == "openai-images"
    assert utils_actions["image.edit"].config["operation"] == "image.edit"
    assert utils_actions["image.edit"].config["budget_kind"] == "openai-images"
    image_edit_capabilities = utils_actions["image.edit"].config["capability_metadata"]
    assert image_edit_capabilities["modalities"] == {
        "input": ["text", "image"],
        "output": ["image"],
    }
    assert image_edit_capabilities["modes"] == ["image-to-image", "reference-image-compose"]
    assert image_edit_capabilities["models"]["gpt-image-2"]["max_input_images"] == 16
    assert "auto" in image_edit_capabilities["models"]["gpt-image-1"]["sizes"]
    assert image_edit_capabilities["models"]["gpt-image-2"]["max_input_image_bytes"] == 52_428_800
    assert (
        image_edit_capabilities["models"]["gpt-image-2"]["input_fidelity"]
        == "always-high; do not send parameter"
    )
    assert image_edit_capabilities["models"]["gpt-image-1-mini"]["input_fidelity"] == [
        "low",
        "high",
    ]
    assert image_edit_capabilities["limits"]["prompt_max_chars"] == 32000
    assert image_edit_capabilities["limits"]["max_input_image_bytes"] == 52_428_800
    assert "explicit mask uploads" in image_edit_capabilities["unsupported_provider_features"]
    assert (
        "gpt-image-2 custom WxH sizes beyond StackOS presets"
        in image_edit_capabilities["unsupported_provider_features"]
    )
    assert "moderation parameter" in image_edit_capabilities["unsupported_provider_features"]
    assert utils_actions["image.edit"].input_schema["required"] == [
        "prompt",
        "input_image_refs",
    ]
    xai_image_generate = utils_actions["xai.image.generate"]
    assert xai_image_generate.provider == "xai-imagine"
    assert xai_image_generate.config["connector"] == "xai-imagine"
    assert xai_image_generate.config["operation"] == "image.generate"
    assert xai_image_generate.config["budget_kind"] == "xai-imagine"
    assert xai_image_generate.config["default_model"] == "grok-imagine-image-quality"
    xai_generate_capabilities = xai_image_generate.config["capability_metadata"]
    assert xai_generate_capabilities["models"]["grok-imagine-image-quality"]["status"] == (
        "StackOS v1 quality image model"
    )
    assert xai_generate_capabilities["limits"]["resolutions"] == ["1k", "2k"]
    assert (
        "cheaper grok-imagine-image model"
        in xai_generate_capabilities["unsupported_provider_features"]
    )
    assert (
        "legacy grok-imagine-image-pro model"
        in xai_generate_capabilities["unsupported_provider_features"]
    )
    xai_image_edit = utils_actions["xai.image.edit"]
    assert xai_image_edit.config["operation"] == "image.edit"
    xai_edit_capabilities = xai_image_edit.config["capability_metadata"]
    assert xai_edit_capabilities["limits"]["max_input_images"] == 3
    assert xai_edit_capabilities["limits"]["max_input_image_bytes"] == 20_971_520
    assert "multi-image edits" in xai_edit_capabilities["limits"]["aspect_ratio"]
    assert (
        "OpenAI SDK multipart images.edit shape"
        in xai_edit_capabilities["unsupported_provider_features"]
    )
    xai_video_generate = utils_actions["xai.video.generate"]
    assert xai_video_generate.provider == "xai-imagine"
    assert xai_video_generate.config["connector"] == "xai-imagine"
    assert xai_video_generate.config["operation"] == "video.generate"
    xai_video_capabilities = xai_video_generate.config["capability_metadata"]
    assert xai_video_capabilities["execution"]["mode"] == "async"
    assert xai_video_capabilities["modes"] == [
        "text-to-video",
        "image-to-video",
        "reference-to-video",
    ]
    assert xai_video_capabilities["limits"]["reference_to_video_duration_max_seconds"] == 10
    assert set(xai_video_capabilities["models"]) == {"grok-imagine-video"}
    assert (
        "grok-imagine-video-1.5-preview image-to-video-only preview model"
        in xai_video_capabilities["unsupported_provider_features"]
    )
    assert "video editing endpoint" in xai_video_capabilities["unsupported_provider_features"]
    google_video = utils_actions["google.video.generate"]
    assert google_video.provider == "google-veo"
    assert google_video.config["connector"] == "google-veo"
    assert google_video.input_schema["properties"]["duration_seconds"]["enum"] == [4, 6, 8]
    google_video_capabilities = google_video.config["capability_metadata"]
    assert set(google_video_capabilities["models"]) == {
        "veo-3.1-generate-preview",
        "veo-3.1-fast-generate-preview",
        "veo-3.1-lite-generate-preview",
    }
    assert google_video_capabilities["limits"]["resolutions_by_model"][
        "veo-3.1-lite-generate-preview"
    ] == ["720p", "1080p"]
    assert google_video_capabilities["limits"]["person_generation"] == {
        "text-to-video": ["allow_all"],
        "image-to-video": ["allow_adult"],
        "first-last-frame": ["allow_adult"],
    }
    byteplus_video = utils_actions["byteplus.video.generate"]
    assert byteplus_video.provider == "byteplus-ark"
    assert byteplus_video.config["connector"] == "byteplus-seedance"
    assert byteplus_video.input_schema["properties"]["region"]["enum"] == ["ap-southeast-1"]
    assert byteplus_video.input_schema["properties"]["priority"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 9,
    }
    byteplus_capabilities = byteplus_video.config["capability_metadata"]
    assert byteplus_capabilities["limits"]["duration_seconds"]["seedance-1-5-pro-251215"] == [
        -1,
        4,
        12,
    ]
    assert byteplus_capabilities["limits"]["duration_seconds"]["seedance-1-0-pro-250528"] == [
        2,
        12,
    ]
    assert byteplus_capabilities["limits"]["reference_to_video_models"] == [
        "dreamina-seedance-2-0-260128",
        "dreamina-seedance-2-0-fast-260128",
    ]
    assert byteplus_capabilities["models"]["seedance-1-0-pro-fast-251015"]["modes"] == [
        "text-to-video",
        "image-to-video",
    ]
    assert (
        "eu-west-1 Seedance video region until official docs publish it"
        in byteplus_capabilities["unsupported_provider_features"]
    )
    alibaba_video = utils_actions["alibaba.video.generate"]
    assert alibaba_video.provider == "alibaba-wan"
    assert alibaba_video.config["connector"] == "alibaba-wan"
    assert alibaba_video.input_schema["properties"]["region"]["enum"] == ["singapore", "beijing"]
    assert alibaba_video.config["default_models"]["text-to-video"] == "wan2.6-t2v"
    assert set(alibaba_video.config["capability_metadata"]["models"]) == {
        "wan2.6-t2v",
        "wan2.7-i2v",
    }
    assert (
        "wan2.7-t2v until Alibaba publishes a matching executable T2V API contract"
        in alibaba_video.config["capability_metadata"]["unsupported_provider_features"]
    )
    assert (
        "wan2.7-r2v reference-to-video"
        in alibaba_video.config["capability_metadata"]["unsupported_provider_features"]
    )
    assert (
        "wan2.7-videoedit video editing"
        in alibaba_video.config["capability_metadata"]["unsupported_provider_features"]
    )
    kling_video = utils_actions["kling.video.generate"]
    assert kling_video.provider == "kling"
    assert kling_video.config["connector"] == "kling-video"
    assert kling_video.input_schema["properties"]["model_name"]["enum"] == ["kling-v3"]
    assert set(kling_video.config["capability_metadata"]["models"]) == {"kling-v3"}
    assert "callback_url" not in kling_video.input_schema["properties"]
    assert (
        "camera_control"
        in kling_video.config["capability_metadata"]["unsupported_provider_features"]
    )
    assert (
        "callback_url until StackOS owns a configured ingress route"
        in kling_video.config["capability_metadata"]["unsupported_provider_features"]
    )
    assert (
        "kling-v3-omni model until Omni-specific schemas are added"
        in kling_video.config["capability_metadata"]["unsupported_provider_features"]
    )
    assert (
        "kling-video-o1 model until O1-specific schemas are added"
        in kling_video.config["capability_metadata"]["unsupported_provider_features"]
    )
    reve_image_generate = utils_actions["reve.image.generate"]
    assert reve_image_generate.provider == "reve"
    assert reve_image_generate.config["connector"] == "reve"
    assert reve_image_generate.config["operation"] == "image.create"
    assert reve_image_generate.config["budget_kind"] == "reve"
    assert reve_image_generate.input_schema["properties"]["prompt"]["maxLength"] == 2560
    reve_generate_capabilities = reve_image_generate.config["capability_metadata"]
    assert reve_generate_capabilities["execution"]["mode"] == "sync"
    assert reve_generate_capabilities["pricing"]["base_credits"] == 18
    assert "auto" in reve_generate_capabilities["limits"]["aspect_ratios"]
    assert (
        "postprocessing upscale/remove_background/fit_image/effect"
        in reve_generate_capabilities["unsupported_provider_features"]
    )
    reve_image_edit = utils_actions["reve.image.edit"]
    assert reve_image_edit.config["operation"] == "image.edit"
    assert reve_image_edit.input_schema["properties"]["edit_instruction"]["maxLength"] == 2560
    reve_edit_capabilities = reve_image_edit.config["capability_metadata"]
    assert reve_edit_capabilities["limits"]["max_input_images"] == 1
    assert reve_edit_capabilities["limits"]["max_input_image_bytes"] == 10_485_760
    assert "max_total_input_image_bytes" not in reve_edit_capabilities["limits"]
    assert reve_edit_capabilities["pricing"]["base_credits"]["fast"] == 5
    reve_image_remix = utils_actions["reve.image.remix"]
    assert reve_image_remix.config["operation"] == "image.remix"
    reve_remix_capabilities = reve_image_remix.config["capability_metadata"]
    assert reve_remix_capabilities["limits"]["max_input_images"] == 6
    assert reve_remix_capabilities["limits"]["max_total_input_pixels"] == 32_000_000
    assert "max_total_input_image_bytes" not in reve_remix_capabilities["limits"]
    assert reve_remix_capabilities["models"]["reve-remix-fast@20251030"]["status"] == (
        "Pinned fast remix version"
    )
    google_image_generate = utils_actions["google.image.generate"]
    assert google_image_generate.provider == "google-gemini-image"
    assert google_image_generate.config["connector"] == "google-gemini-image"
    assert google_image_generate.config["operation"] == "image.generate"
    assert google_image_generate.config["budget_kind"] == "google-gemini-image"
    google_generate_capabilities = google_image_generate.config["capability_metadata"]
    assert google_generate_capabilities["execution"]["mode"] == "sync"
    assert google_generate_capabilities["models"]["gemini-3.1-flash-image"]["label"] == (
        "Nano Banana 2"
    )
    assert google_generate_capabilities["models"]["gemini-3.1-flash-image"]["image_sizes"] == [
        "512",
        "1K",
        "2K",
        "4K",
    ]
    assert (
        google_generate_capabilities["models"]["gemini-3.1-flash-image"]["pricing_usd_per_output"][
            "512"
        ]
        == 0.045
    )
    assert (
        google_generate_capabilities["models"]["gemini-3.1-flash-image"]["pricing_usd_per_output"][
            "2K"
        ]
        == 0.101
    )
    assert (
        "512/0.5K request size for Gemini 3.1 Flash Image"
        not in google_generate_capabilities["unsupported_provider_features"]
    )
    google_image_edit = utils_actions["google.image.edit"]
    assert google_image_edit.config["operation"] == "image.edit"
    assert google_image_edit.input_schema["required"] == ["prompt", "input_image_refs"]
    google_edit_capabilities = google_image_edit.config["capability_metadata"]
    assert google_edit_capabilities["limits"]["max_input_images_by_model"] == {
        "gemini-3.1-flash-image": 14,
        "gemini-3-pro-image": 14,
        "gemini-2.5-flash-image": 3,
    }
    assert google_edit_capabilities["limits"]["inline_request_max_bytes"] == 20_000_000
    assert (
        google_edit_capabilities["models"]["gemini-3-pro-image"]["pricing_usd_per_input_image"]
        == 0.0011
    )
    ideogram_image_generate = utils_actions["ideogram.image.generate"]
    assert ideogram_image_generate.provider == "ideogram"
    assert ideogram_image_generate.config["connector"] == "ideogram"
    assert ideogram_image_generate.config["operation"] == "image.generate"
    assert ideogram_image_generate.config["budget_kind"] == "ideogram"
    ideogram_generate_capabilities = ideogram_image_generate.config["capability_metadata"]
    ideogram_resolutions = ideogram_generate_capabilities["models"]["ideogram-v4"]["resolutions"]
    assert len(ideogram_resolutions) == 23
    assert ideogram_resolutions[0] == "2048x2048"
    assert ideogram_resolutions[-1] == "3072x1024"
    assert ideogram_generate_capabilities["models"]["ideogram-v4"]["rendering_speeds"] == [
        "TURBO",
        "DEFAULT",
        "QUALITY",
    ]
    assert (
        ideogram_generate_capabilities["models"]["ideogram-v4"]["pricing_usd_per_output"]["QUALITY"]
        == 0.10
    )
    assert (
        "rendering_speed FLASH" in ideogram_generate_capabilities["unsupported_provider_features"]
    )
    ideogram_image_remix = utils_actions["ideogram.image.remix"]
    assert ideogram_image_remix.config["operation"] == "image.remix"
    assert ideogram_image_remix.input_schema["required"] == ["text_prompt", "input_image_ref"]
    ideogram_remix_capabilities = ideogram_image_remix.config["capability_metadata"]
    assert ideogram_remix_capabilities["limits"]["max_input_images"] == 1
    assert ideogram_remix_capabilities["limits"]["max_input_image_bytes"] == 10_000_000
    assert ideogram_remix_capabilities["limits"]["input_image_formats"] == [
        "jpg",
        "jpeg",
        "png",
        "webp",
    ]
    byteplus_generate = utils_actions["byteplus.image.generate"]
    assert byteplus_generate.provider == "byteplus-ark"
    assert byteplus_generate.config["connector"] == "byteplus-seedream"
    assert byteplus_generate.config["operation"] == "image.generate"
    assert byteplus_generate.config["budget_kind"] == "byteplus-ark"
    byteplus_generate_capabilities = byteplus_generate.config["capability_metadata"]
    assert (
        byteplus_generate_capabilities["models"]["seedream-5-0-lite-260128"][
            "pricing_usd_per_successful_output"
        ]
        == 0.035
    )
    assert (
        byteplus_generate_capabilities["models"]["seedream-4-5-251128"][
            "pricing_usd_per_successful_output"
        ]
        == 0.04
    )
    assert byteplus_generate_capabilities["limits"]["size_keywords_by_model"][
        "seedream-4-0-250828"
    ] == ["1K", "2K", "4K"]
    assert (
        byteplus_generate_capabilities["limits"]["custom_size_min_pixels_by_model"][
            "seedream-4-0-250828"
        ]
        == 921_600
    )
    assert (
        "non-lite seedream-5-0-260128 until pricing is modeled"
        in byteplus_generate_capabilities["unsupported_provider_features"]
    )
    byteplus_edit = utils_actions["byteplus.image.edit"]
    assert byteplus_edit.input_schema["required"] == ["prompt", "input_image_refs"]
    byteplus_edit_capabilities = byteplus_edit.config["capability_metadata"]
    assert byteplus_edit_capabilities["limits"]["max_input_images"] == 14
    assert byteplus_edit_capabilities["limits"]["max_input_image_bytes"] == 30_000_000
    assert byteplus_edit_capabilities["limits"]["input_image_formats"] == [
        "jpg",
        "jpeg",
        "png",
        "webp",
    ]
    assert byteplus_edit_capabilities["limits"]["min_input_image_side_px"] == 15
    assert byteplus_edit_capabilities["limits"]["max_input_image_pixels"] == 36_000_000
    assert "video-generation" in utils_providers
    assert _auth_field_keys(utils_providers["video-generation"]) == ["api_key"]
    video_generate = utils_actions["video.generate"]
    assert video_generate.provider == "video-generation"
    assert video_generate.config["execution_mode"] == "deferred-video-backend-selection"
    assert video_generate.config["budget_kind"] == "video-generation"
    assert video_generate.config["capability_metadata"]["execution"]["mode"] == "deferred"
    assert "connector" not in video_generate.config
    trackbooth = next(
        manifest for manifest in BUILTIN_PLUGIN_MANIFESTS if manifest.slug == "trackbooth"
    )
    assert trackbooth.name == "Trackbooth"
    assert trackbooth.display_order == 45
    trackbooth_providers = {provider.key: provider for provider in trackbooth.providers}
    assert set(trackbooth_providers) == {"trackbooth"}
    assert _auth_field_keys(trackbooth_providers["trackbooth"], "api-key") == [
        "api_key",
        "api_base_url",
    ]
    assert trackbooth_providers["trackbooth"].config["default_api_base_url"] == (
        "https://apis.trackbooth.com"
    )
    assert trackbooth_providers["trackbooth"].config["connection_category"] == "Affiliation"
    assert trackbooth.config["inventory_refresh"] == {
        "mode": "manual",
        "action_ref": "trackbooth.catalog.sync",
    }
    trackbooth_actions = {action.key: action for action in trackbooth.actions}
    assert set(trackbooth_actions) == {
        "catalog.sync",
        "catalog.search",
        "operation.describe",
    }
    sync_config = trackbooth_actions["catalog.sync"].config
    assert sync_config["schema_version"] == "stackos.action.v1"
    assert sync_config["connector"] == "trackbooth"
    assert sync_config["operation"] == "catalog.sync"
    assert sync_config["requires_credential"] is True
    assert (
        sync_config["provider_context_schema"]["properties"]["acting_as_account"]["type"]
        == "string"
    )
    search_config = trackbooth_actions["catalog.search"].config
    assert search_config["schema_version"] == "stackos.action.v1"
    assert search_config["connector"] == "trackbooth"
    assert search_config["operation"] == "catalog.search"
    assert search_config["requires_credential"] is True
    assert search_config["provider_context_schema"] == sync_config["provider_context_schema"]
    assert "api_url" not in trackbooth_actions["catalog.sync"].input_schema["properties"]
    assert "acting_as_account" not in trackbooth_actions["catalog.sync"].input_schema["properties"]


def test_all_builtin_providers_declare_self_service_setup_metadata() -> None:
    providers = [
        (plugin.slug, provider)
        for plugin in BUILTIN_PLUGIN_MANIFESTS
        for provider in plugin.providers
    ]

    assert len(providers) == 48
    for plugin_slug, provider in providers:
        config = provider.config or {}
        setup = config.get("setup")
        assert isinstance(setup, dict), f"{plugin_slug}:{provider.key} missing setup metadata"
        assert setup.get("setup_note") or config.get("setup_note"), (
            f"{plugin_slug}:{provider.key} missing setup note"
        )
        assert setup.get("verified_at") == "2026-06-11"
        if provider.auth_type not in {"none", "local"}:
            assert setup.get("credential_label"), (
                f"{plugin_slug}:{provider.key} missing credential label"
            )
            assert any(
                setup.get(key)
                for key in (
                    "homepage_url",
                    "signup_url",
                    "console_url",
                    "api_key_url",
                    "docs_url",
                    "fallback_url",
                )
            ), f"{plugin_slug}:{provider.key} missing setup URL"
        for key, value in setup.items():
            if key.endswith("_url") and value is not None:
                assert isinstance(value, str)
                assert value.startswith(("https://", "http://")), (
                    f"{plugin_slug}:{provider.key} has non-URL {key}"
                )


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
        "local-agent-chat",
        "slack-bot",
        "telegram-bot",
        "smtp",
        "imap",
    }
    providers = {provider.key: provider for provider in manifest.providers}
    assert providers["local-agent-chat"].auth_type == "none"
    assert _auth_field_keys(providers["telegram-bot"], "bot-token")[:2] == [
        "bot_token",
        "webhook_secret_token",
    ]
    assert _auth_field_keys(providers["slack-bot"], "bot-token")[:2] == [
        "bot_token",
        "signing_secret",
    ]
    assert (
        providers["telegram-bot"]
        .config["setup_note"]
        .startswith("Store only Telegram token material and transport endpoints here")
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
    reply_markup = actions["telegram-bot.message.send"].input_schema["properties"]["reply_markup"]
    button_schema = reply_markup["properties"]["inline_keyboard"]["items"]["items"]
    assert button_schema["properties"]["callback_data"]["maxLength"] == 64
    assert button_schema["additionalProperties"] is False
    assert actions["telegram-bot.photo.send"].provider == "telegram-bot"
    assert actions["telegram-bot.photo.send"].config["connector"] == "telegram-bot"
    assert actions["telegram-bot.photo.send"].config["operation"] == "photo.send"
    assert actions["telegram-bot.photo.send"].input_schema["required"] == [
        "chat_ref",
        "profile_key",
        "photo",
    ]
    photo_schema = actions["telegram-bot.photo.send"].input_schema["properties"]["photo"]
    assert photo_schema["oneOf"] == [
        {"required": ["file_id"]},
        {"required": ["url"]},
        {"required": ["artifact_ref"]},
    ]
    assert photo_schema["properties"]["url"]["pattern"] == "^https://"
    assert actions["telegram-bot.file.download"].config["operation"] == "file.download"
    assert (
        actions["telegram-bot.file.download"].input_schema["properties"]["max_bytes"]["maximum"]
        == 20 * 1024 * 1024
    )
    assert actions["telegram-bot.file.upload"].config["operation"] == "file.upload"
    assert actions["telegram-bot.file.upload"].input_schema["properties"]["files"]["maxItems"] == 10
    assert actions["telegram-bot.callback.answer"].capability == "agent-triggering"
    assert actions["telegram-bot.callback.answer"].config["operation"] == "callback.answer"
    assert actions["telegram-bot.updates.poll"].capability == "agent-triggering"
    assert actions["telegram-bot.updates.poll"].config["operation"] == "updates.poll"
    assert actions["telegram-bot.identity.get"].config["connector"] == "telegram-bot"
    assert actions["telegram-bot.webhook.set"].config["connector"] == "telegram-bot"
    assert actions["telegram-bot.webhook.set"].config["operation"] == "webhook.set"
    assert actions["slack-bot.identity.get"].provider == "slack-bot"
    assert actions["slack-bot.identity.get"].config["operation"] == "identity.get"
    assert actions["slack-bot.message.send"].risk_level == "write"
    assert actions["slack-bot.message.send"].input_schema["properties"]["profile_ref"]["type"] == (
        "string"
    )
    assert actions["slack-bot.file.upload"].config["operation"] == "file.upload"
    assert actions["slack-bot.file.upload"].input_schema["properties"]["files"]["maxItems"] == 10
    assert actions["slack-bot.conversation.members"].config["connector"] == "slack-bot"
    assert actions["smtp.email.send"].config["connector"] == "smtp"
    assert actions["smtp.email.send"].config["operation"] == "email.send"
    assert "body_artifact_ref" not in actions["smtp.email.send"].input_schema["properties"]
    assert actions["imap.message.fetch"].input_schema["required"] == [
        "mailbox_ref",
        "uid",
    ]
    assert actions["imap.messages.search"].config["connector"] == "imap"
    assert actions["imap.messages.search"].config["operation"] == "messages.search"
    assert (
        actions["imap.messages.search"].input_schema["properties"]["criteria"][
            "additionalProperties"
        ]
        is False
    )
    assert {resource.key for resource in manifest.resources} >= {
        "ingress-endpoint",
        "communication-profile",
        "communication-contact",
        "communication-target",
        "communication-route",
        "communication-membership",
        "communication-channel",
        "communication-thread",
        "communication-message",
        "communication-interaction",
        "communication-event",
        "communication-cursor",
        "agent-request-source",
    }


def test_branding_plugin_yaml_facade_validates() -> None:
    manifest = load_plugin_manifest_file(Path("plugins/branding/plugin.yaml"))

    assert manifest.slug == "branding"
    assert manifest.ui is not None
    assert manifest.ui["nav"]["section"] == "Branding"
    assert {capability.key for capability in manifest.capabilities} >= {
        "authority-content",
        "evidence-governance",
        "content-distribution",
        "outcome-learning",
    }
    assert {provider.key for provider in manifest.providers} == {
        "git-remote",
        "x-api",
        "linkedin-api",
        "esp",
        "channel-metrics",
    }
    providers = {provider.key: provider for provider in manifest.providers}
    assert _auth_field_keys(providers["git-remote"], "deploy_key_or_token")[:2] == [
        "remote_url",
        "branch",
    ]
    assert _auth_field_keys(providers["x-api"], "oauth2_token")[:2] == [
        "access_token",
        "refresh_token",
    ]
    assert _auth_field_keys(providers["linkedin-api"], "oauth2_token")[:2] == [
        "access_token",
        "refresh_token",
    ]
    assert _auth_field_keys(providers["esp"], "api_key")[0] == "api_key"
    actions = {action.key: action for action in manifest.actions}
    assert set(actions) == {
        "publish.git-blog",
        "publish.x",
        "publish.linkedin",
        "publish.email",
        "distribution.snapshot",
        "evidence.capture",
        "evidence.sanitize-mark",
    }
    assert actions["publish.git-blog"].config["execution_mode"] == "deferred-git-publisher"
    assert actions["publish.x"].config["execution_mode"] == "deferred-social-publisher"
    assert actions["publish.linkedin"].config["execution_mode"] == "deferred-social-publisher"
    assert actions["publish.email"].config["execution_mode"] == "deferred-esp-publisher"
    assert actions["distribution.snapshot"].config["execution_mode"] == ("deferred-channel-metrics")
    assert actions["evidence.capture"].config["connector"] == "branding"
    assert actions["evidence.capture"].config["operation"] == "evidence.capture"
    assert actions["evidence.capture"].config["default_sanitization_status"] == "raw"
    assert (
        actions["evidence.capture"].input_schema["properties"]["sanitization_status"]["default"]
        == "raw"
    )
    assert actions["evidence.capture"].input_schema["properties"]["sanitization_status"][
        "enum"
    ] == ["raw"]
    assert "evidence_payload" in actions["evidence.capture"].input_schema["properties"]
    assert actions["evidence.capture"].output_schema["required"] == [
        "evidence_ref",
        "resource_record_id",
        "sanitization_status",
    ]
    assert actions["evidence.sanitize-mark"].config["connector"] == "branding"
    assert actions["evidence.sanitize-mark"].config["operation"] == "evidence.sanitize-mark"
    assert "decision_ref" in actions["evidence.sanitize-mark"].input_schema["required"]
    assert "decision_ref" in actions["evidence.sanitize-mark"].input_schema["properties"]
    assert actions["evidence.sanitize-mark"].output_schema["required"] == [
        "evidence_ref",
        "resource_record_id",
        "verdict",
    ]
    assert all(
        action.config.get("deferred_reason")
        for key, action in actions.items()
        if key
        not in {
            "evidence.capture",
            "evidence.sanitize-mark",
        }
    )
    resources = {resource.key: resource for resource in manifest.resources}
    assert set(resources) == {
        "brand-profile",
        "position",
        "evidence-item",
        "stream",
        "channel",
        "routing-policy",
        "content-piece",
        "distribution-record",
    }
    assert all(resource.ui_schema is not None for resource in resources.values())
    assert all(
        resource.config["schema_version"] == "stackos.resource.v1"
        for resource in resources.values()
    )
    assert {key: resource.config["record_kind"] for key, resource in resources.items()} == {
        "brand-profile": "brand-profile",
        "position": "position",
        "evidence-item": "evidence-item",
        "stream": "stream",
        "channel": "channel",
        "routing-policy": "routing-policy",
        "content-piece": "content-piece",
        "distribution-record": "distribution-record",
    }
    assert all(resource.config["agent_guidance"] for resource in resources.values())
    assert resources["content-piece"].schema_data["properties"]["status"]["enum"] == [
        "captured",
        "routed",
        "drafted",
        "evidence-checked",
        "voice-checked",
        "sanitized",
        "approved",
        "renditions-ready",
        "published",
        "measured",
    ]
    evidence_schema = resources["evidence-item"].schema_data
    verdict_schema = evidence_schema["properties"]["sanitization_verdict"]
    assert verdict_schema["required"] == [
        "verdict",
        "reviewer",
        "reason",
        "decided_at",
        "decision_ref",
    ]
    assert evidence_schema["allOf"][0]["then"]["required"] == ["sanitization_verdict"]
    claim_map_item_schema = resources["content-piece"].schema_data["properties"][
        "claim_evidence_map"
    ]["items"]
    assert claim_map_item_schema["allOf"][0]["then"]["properties"]["evidence_refs"]["minItems"] == 1
    content_piece_all_of = resources["content-piece"].schema_data["allOf"]
    assert content_piece_all_of[0]["then"]["required"] == ["channel_plan"]
    assert {"approval_ref", "review_log"} <= set(content_piece_all_of[3]["then"]["required"])
    assert len(content_piece_all_of[3]["then"]["properties"]["review_log"]["allOf"]) == 3
    assert "canonical_url" in content_piece_all_of[-1]["then"]["required"]
    distribution_schema = resources["distribution-record"].schema_data
    assert distribution_schema["properties"]["qualitative_outcomes"]["items"]["required"] == [
        "outcome_type",
        "summary",
        "source_ref",
    ]
    assert "canonical_mode" in distribution_schema["required"]
    required_by_condition = [set(item["then"]["required"]) for item in distribution_schema["allOf"]]
    assert {"canonical_url"} in required_by_condition
    assert {"native_short_collection_ref"} in required_by_condition
    assert {"provider_publish_ref", "published_at"} in required_by_condition
    assert {"operator_handoff_packet_ref"} in required_by_condition
    assert {"operator_handoff_packet_ref", "operator_publication_ref"} in required_by_condition


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
        "serper",
        "ahrefs",
    }
    providers = {provider.key: provider for provider in manifest.providers}
    assert _auth_field_keys(providers["dataforseo"]) == ["login", "password"]
    assert _auth_field_keys(providers["serper"]) == ["api_key"]
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
    assert actions["serper.search"].config == {
        "schema_version": "stackos.action.v1",
        "connector": "serper",
        "operation": "search",
        "requires_credential": True,
        "budget_kind": "serper",
        "enforce_budget": False,
    }
    assert actions["serper.search"].input_schema["required"] == ["query"]
    assert actions["serper.search"].input_schema["properties"]["num"]["maximum"] == 100
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


def test_manifest_rejects_secret_like_provider_setup_metadata() -> None:
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(
            {
                "slug": "bad-provider-setup",
                "name": "Bad Provider Setup",
                "providers": [
                    {
                        "key": "bad-provider",
                        "name": "Bad Provider",
                        "config": {
                            "setup": {
                                "credential_label": "API key",
                                "setup_note": "Use api_key=sk-real-value here",
                                "verified_at": "2026-06-11",
                            }
                        },
                    }
                ],
            }
        )


def test_manifest_rejects_secret_like_provider_setup_url() -> None:
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(
            {
                "slug": "bad-provider-setup-url",
                "name": "Bad Provider Setup URL",
                "providers": [
                    {
                        "key": "bad-provider",
                        "name": "Bad Provider",
                        "config": {
                            "setup": {
                                "credential_label": "API key",
                                "setup_note": "Use the setup page.",
                                "api_key_url": "https://example.com/setup?token=secret-value",
                                "verified_at": "2026-06-11",
                            }
                        },
                    }
                ],
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
