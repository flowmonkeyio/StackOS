"""Shared transport expectations for the built-in plugin catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS


def assert_builtin_plugin_catalog(plugins: Sequence[Mapping[str, Any]]) -> None:
    """Check wire completeness/order against manifests, independent of repositories.

    Manifest tests own the full product inventory. Transport tests preserve that
    inventory and its declared display order, with critical product families
    explicit here so removing a source manifest cannot silently erase them.
    """
    expected = [
        manifest.slug
        for manifest in sorted(
            BUILTIN_PLUGIN_MANIFESTS,
            key=lambda manifest: (manifest.display_order, manifest.slug),
        )
    ]
    slugs = [plugin["slug"] for plugin in plugins]
    assert slugs == expected, "Plugin transport inventory/order differs from source manifests"
    assert set(slugs) >= {
        "core",
        "utils",
        "engineering",
        "support",
        "finance",
        "agency",
        "communications",
        "seo",
        "branding",
        "linear",
    }
