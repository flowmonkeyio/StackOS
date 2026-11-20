"""The real provider catalog keeps existing methods and adds reviewed JSON setup."""

import pytest

from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS

PROVIDERS = {
    provider.key: provider for plugin in BUILTIN_PLUGIN_MANIFESTS for provider in plugin.providers
}


@pytest.mark.parametrize(
    "key",
    [
        "google-search-console",
        "google-analytics",
        "google-tag-manager",
        "google-ads",
        "google-workspace",
    ],
)
def test_google_service_account_is_an_additive_explicit_method(key):
    assert PROVIDERS[key].auth_methods[0].key == "oauth2_authorization_code"
    methods = {method.key: method for method in PROVIDERS[key].auth_methods}
    method = methods["service-account"]
    assert method.auth_type == "oauth"
    assert method.interactive is False
    assert method.payload_format == "json"
    assert method.permission_verification.evidence_source == "oauth_response"
    assert method.permission_verification.enforcement == "local_required"
    fields = {field.key: field for field in method.fields}
    assert fields["service_account_json"].secret is True
    assert fields["service_account_json"].required is True
    assert not ({"access_token", "client_secret", "client_id"} & fields.keys())
    assert methods["oauth2_authorization_code"].interactive is True
    assert ("delegated_subject" in fields) == (key == "google-workspace")
    if key == "google-workspace":
        assert not fields["delegated_subject"].secret
        assert not fields["delegated_subject"].required
    if key == "google-ads":
        assert fields["developer_token"].required and fields["developer_token"].secret
        assert not fields["manager_account_ref"].secret
        assert "customer_ref" in fields


@pytest.mark.parametrize("key", ["google-gemini-image", "google-veo"])
def test_google_media_keeps_reviewed_developer_api_key_transport(key):
    assert [method.key for method in PROVIDERS[key].auth_methods] == ["api_key"]
    assert PROVIDERS[key].auth_methods[0].payload_format == "raw"


def test_google_action_inventory_and_scope_gates_remain_unchanged():
    counts = {}
    for plugin in BUILTIN_PLUGIN_MANIFESTS:
        for action in plugin.actions:
            if action.provider in PROVIDERS and action.provider.startswith("google-"):
                counts[action.provider] = counts.get(action.provider, 0) + 1
                if action.provider not in {"google-gemini-image", "google-veo"}:
                    assert action.config["required_scopes"]
    assert counts == {
        "google-indexing": 4,
        "google-search-console": 7,
        "google-analytics": 4,
        "google-tag-manager": 6,
        "google-ads": 10,
        "google-workspace": 2,
        "google-gemini-image": 2,
        "google-veo": 1,
    }


def test_indexing_declares_only_nondelegated_service_account_method():
    provider = PROVIDERS["google-indexing"]
    assert [method.key for method in provider.auth_methods] == ["service-account"]
    method = provider.auth_methods[0]
    assert not method.interactive
    assert method.permission_verification.evidence_source == "oauth_response"
    assert method.permission_verification.enforcement == "local_required"
    assert [field.key for field in method.fields] == ["service_account_json"]
    assert method.fields[0].secret and method.fields[0].required
    assert provider.config["scopes"] == ["https://www.googleapis.com/auth/indexing"]
