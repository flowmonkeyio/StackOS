"""Canonical provider facts merge with additive StackOS policy before persistence."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from stackos_connectors import describe

from stackos.plugins.manifest import (
    BUILTIN_PLUGIN_MANIFESTS,
    ProviderManifest,
)


def provider_input(**changes):
    return {
        "key": "google-workspace",
        "name": "Google Workspace",
        "auth_catalog_ref": "google-workspace",
        **changes,
    }


def test_catalog_normalization_keeps_host_fields_and_enforcement_additive():
    provider = ProviderManifest.model_validate(
        provider_input(
            auth_methods=[
                {
                    "key": "service-account",
                    "fields": [{"key": "workspace_ref", "label": "Workspace"}],
                    "permission_verification": {"enforcement": "local_required"},
                }
            ]
        )
    )
    method = next(m for m in provider.auth_methods if m.key == "service-account")
    assert [field.key for field in method.fields] == [
        "service_account_json",
        "delegated_subject",
        "workspace_ref",
    ]
    assert method.permission_verification.model_dump() == {
        "evidence_source": "oauth_response",
        "enforcement": "local_required",
    }
    assert "auth_catalog_ref" not in provider.model_dump()
    assert (
        describe("google-workspace")["auth_methods"][2]["setup"]["fields"][-1]["key"]
        == "delegated_subject"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"auth_catalog_ref": "../catalog.json"},
        {"key": "nonexistent", "auth_catalog_ref": "nonexistent"},
        {"auth_methods": [{"key": "missing"}]},
        {"auth_methods": [{"key": "service-account"}, {"key": "service-account"}]},
        {"auth_methods": [{"key": "service-account", "auth_type": "api-key"}]},
        {
            "auth_methods": [
                {
                    "key": "service-account",
                    "fields": [{"key": "service_account_json", "label": "Override"}],
                }
            ]
        },
        {
            "auth_methods": [
                {
                    "key": "service-account",
                    "fields": [{"key": "ref", "label": "One"}, {"key": "ref", "label": "Two"}],
                }
            ]
        },
        {
            "auth_methods": [
                {
                    "key": "service-account",
                    "permission_verification": {
                        "evidence_source": "unavailable",
                        "enforcement": "provider_enforced",
                    },
                }
            ]
        },
        {"config": {"scopes": ["invented"]}},
        {"config": {"token_endpoint": "https://attacker.example"}},
        {"config": {"setup": {"credential_url": "https://attacker.example"}}},
        {"config": {"setup": {"url_confidence": {"credential_url": "verified"}}}},
    ],
)
def test_invalid_refs_or_provider_fact_replacement_reject_before_model_creation(changes):
    with pytest.raises(ValidationError):
        ProviderManifest.model_validate(provider_input(**changes))


def test_optional_bundle_policy_does_not_override_provider_scope_facts():
    original = deepcopy(describe("hubspot")["config"]["scope_bundles"])
    provider = ProviderManifest.model_validate(
        {
            "key": "hubspot",
            "name": "HubSpot",
            "auth_catalog_ref": "hubspot",
            "auth_type": "oauth-or-api-key",
            "config": {"scope_bundles": {"sales": {"readiness_group": "sales"}}},
        }
    )
    assert provider.config["scope_bundles"]["sales"]["readiness_group"] == "sales"
    assert (
        provider.config["scope_bundles"]["sales"]["optional_scopes"]
        == original["sales"]["optional_scopes"]
    )
    assert describe("hubspot")["config"]["scope_bundles"] == original
    for overlay in [
        {"sales": {"optional_scopes": ["invented"]}},
        {"unknown": {"readiness_group": "sales"}},
    ]:
        with pytest.raises(ValidationError):
            ProviderManifest.model_validate(
                {
                    "key": "hubspot",
                    "name": "HubSpot",
                    "auth_catalog_ref": "hubspot",
                    "auth_type": "oauth-or-api-key",
                    "config": {"scope_bundles": overlay},
                }
            )


def test_yaml_and_python_built_providers_share_canonical_methods():
    providers = {p.key: p for plugin in BUILTIN_PLUGIN_MANIFESTS for p in plugin.providers}
    for key in ["reddit", "google-workspace", "linear", "hubspot"]:
        declared = describe(key)["auth_methods"]
        assert [m.key for m in providers[key].auth_methods] == [m["key"] for m in declared]
        for method, catalog in zip(providers[key].auth_methods, declared, strict=True):
            assert method.auth_type == catalog["setup"]["auth_type"]
            assert method.interactive == catalog["setup"]["interactive"]
            assert method.description == catalog["setup"]["description"]
    assert providers["linear"].auth_type == "oauth-or-api-key"


@pytest.mark.parametrize(
    "key,auth_type,overlay",
    [
        ("reddit", "oauth-client-credentials", {"credential_payload": {"format": "raw"}}),
        ("linear", "oauth-or-api-key", {"endpoint": "https://example.invalid/other"}),
    ],
)
def test_dead_provider_fact_copies_are_not_host_overlays(key, auth_type, overlay):
    with pytest.raises(ValidationError, match="unknown host-only auth configuration overlay"):
        ProviderManifest.model_validate(
            {
                "key": key,
                "name": key,
                "auth_type": auth_type,
                "auth_catalog_ref": key,
                "config": overlay,
            }
        )
    providers = {p.key: p for plugin in BUILTIN_PLUGIN_MANIFESTS for p in plugin.providers}
    assert not set(overlay) & set(providers[key].config or {})


@pytest.mark.parametrize(
    "order",
    [
        [],
        ["service_account_json"],
        ["service_account_json", "delegated_subject", "unknown"],
        ["service_account_json", "service_account_json"],
        "service_account_json,delegated_subject",
    ],
)
def test_field_order_cannot_drop_duplicate_or_invent_fields(order):
    with pytest.raises(ValidationError, match="field_order"):
        ProviderManifest.model_validate(
            provider_input(
                auth_methods=[
                    {
                        "key": "service-account",
                        "field_order": order,
                    }
                ]
            )
        )


def test_field_order_is_host_presentation_only():
    provider = ProviderManifest.model_validate(
        provider_input(
            auth_methods=[
                {
                    "key": "service-account",
                    "fields": [{"key": "workspace_ref", "label": "Workspace"}],
                    "field_order": ["service_account_json", "workspace_ref", "delegated_subject"],
                }
            ]
        )
    )
    method = next(m for m in provider.auth_methods if m.key == "service-account")
    assert [field.key for field in method.fields] == [
        "service_account_json",
        "workspace_ref",
        "delegated_subject",
    ]
    assert "field_order" not in method.model_dump()


def test_host_cannot_replace_existing_catalog_repair_guidance(monkeypatch):
    from types import SimpleNamespace

    from stackos_connectors.contracts import freeze

    from stackos.plugins import manifest

    metadata = deepcopy(describe("google-workspace"))
    metadata["config"]["setup"]["repair_note"] = "Provider guidance"
    monkeypatch.setattr(
        manifest,
        "get_default_client",
        lambda: SimpleNamespace(
            registry=SimpleNamespace(connector_metadata={"google-workspace": freeze(metadata)}),
        ),
    )
    with pytest.raises(ValidationError, match="replace provider setup"):
        ProviderManifest.model_validate(
            provider_input(config={"setup": {"repair_note": "Override"}})
        )


# Frozen released c76238b provider metadata; descriptions stay package-canonical.
RELEASED_METHODS = {
    "google-ads": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "6d8971a249b5f5795553197e77fbec36c09ae68d55b6a94c0abc924116cf2ab9",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_refresh_token": {
            "auth_type": "oauth",
            "fields_sha256": "73c6e0349b45057c49040d9523613bccdbe8f638ced30044b0445a615d8eebc7",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "service-account": {
            "auth_type": "oauth",
            "fields_sha256": "862580626df0d3facf21608bd7e3a6fdad0b3b26d9568198d77b3b9cf36aac08",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
    },
    "google-analytics": {
        "oauth2_access_token": {
            "auth_type": "oauth",
            "fields_sha256": "a7cccb25b3e1e5809a79c36e92c3461dd9e4ee987f66f74f287098c2c31dcdf3",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "e51598a11339f460a0a124dbc4e8a7e36e0029fdc426766e13812cac9f7773c8",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_refresh_token": {
            "auth_type": "oauth",
            "fields_sha256": "99009dbb249d77df833015e7dd3180702b5bbe8908d0af1d1d6bedffb797fc3e",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "service-account": {
            "auth_type": "oauth",
            "fields_sha256": "d7341622e503cf8f7a2d4876501b492bae114e990f399876c0cdb91d74c42359",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
    },
    "google-search-console": {
        "oauth2_access_token": {
            "auth_type": "oauth",
            "fields_sha256": "2b97c19802691c615ee8932b2692de32b27f64f99ea1c1d20e733a69dc26e3b7",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "13a93c2995e043d4114818a832aac1b611c4ebeddb646dc2b1d17a675da169f4",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_refresh_token": {
            "auth_type": "oauth",
            "fields_sha256": "0bc39e165e76838ac4af47554ac1609e223da09c5cfed604ff6e41cef82d6912",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "service-account": {
            "auth_type": "oauth",
            "fields_sha256": "94d949acfe573afda2bddc347bae8aba3d6db6a75bcf26785ff7c90d592660c5",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
    },
    "google-tag-manager": {
        "oauth2_access_token": {
            "auth_type": "oauth",
            "fields_sha256": "aa3aeedbf65dd08d7cf505ccc8e81ad83e12867fbe8fab7688e079a55286086e",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "5755cf7b1faefbfb091467a8fd447c9b8856ffd2d7967d74daddc61c1254b6b5",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_refresh_token": {
            "auth_type": "oauth",
            "fields_sha256": "fbaab8bb63465852ef535f61b2e05bd9ed75fb4e865fbf2e60f216c6f89e7f03",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "service-account": {
            "auth_type": "oauth",
            "fields_sha256": "bfad1a22b8db10ec852ad0803731e5d47de1e06e453c7892343261fa9ecaa298",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
    },
    "google-workspace": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "7827c84a084459ecc861cee11bdfe22c27cedb4735a093bf5032b6e93f76d79c",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_token": {
            "auth_type": "oauth",
            "fields_sha256": "dbadd1c511a3922cf9a8c23a45d552e3de426abb6e448710fb88fe95b5f084c3",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "service-account": {
            "auth_type": "oauth",
            "fields_sha256": "bdf1691540690eb54c2762b1bf67695490265095ff5f26d0cb06e6d391037082",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
    },
    "hubspot": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "846a8c31405cd976fc3ba65d1bb40d4c4c956d69e3557a6e5a0486e16cfb148b",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
        "private_app_token": {
            "auth_type": "api-key",
            "fields_sha256": "a132215e1ca19baf231e2e739d0eaf09468de128bcb9aaf2d352a0b36c1be249",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "provider_probe",
            },
        },
    },
    "linear": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "e76d257af4813262c5030c239b256ba71f759337b8f8e763b61a1ef9629a7972",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
        "personal_api_key": {
            "auth_type": "api-key",
            "fields_sha256": "5021ffb856e31fb399469b1d4cc61c3610f25b3948da93f48924dce4ac81a97f",
            "interactive": False,
            "payload_field": "api_key",
            "payload_format": "raw",
            "permission_verification": {
                "enforcement": "provider_enforced",
                "evidence_source": "unavailable",
            },
        },
    },
    "meta-ads": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "8d760ee06196d7429a7957da4f5a42a069684691355ba6f0fc8c54244bcd8c22",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
        "oauth2_token": {
            "auth_type": "oauth",
            "fields_sha256": "372f361366434a3ddf8993de262b1ef310e539251437a7a497cd2315b2c5f349",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "unavailable",
            },
        },
    },
    "microsoft-365": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "eef8e274cf8dfe349b69d348331b12e42c91a9bd2455eae9b89670b1b36f5327",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_token": {
            "auth_type": "oauth",
            "fields_sha256": "73821e4bdc117841fb280a1eea31c60448b69520d2c10cf84bd4a2e4a69844bc",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
    },
    "outreach": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "aaba576a544eee2d48afed4f330d4c0f949cfa21dc9736e5bec545665f5811b2",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_token": {
            "auth_type": "oauth",
            "fields_sha256": "d9c5b5791a0f57d78138dc65529b5edba031cdddb2caf1442cfcbe6d95a1c7e9",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
    },
    "pipedrive": {
        "api_token": {
            "auth_type": "api-token",
            "fields_sha256": "5d43b108a8648fcbdc050e781928e12516012ce5d04306d74e1ff53acfe656f6",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "provider_enforced",
                "evidence_source": "unavailable",
            },
        },
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "98d994f1d7e53c38ec2d3fa377455ee0f8df41c3040b7319265425e29e474694",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
        "oauth2_token": {
            "auth_type": "oauth",
            "fields_sha256": "94e082639f8dbae0ed066ae53edd48b6ffb2c1479280db4b41d482021b0f169b",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "unavailable",
            },
        },
    },
    "reddit": {
        "client_credentials": {
            "auth_type": "oauth-client-credentials",
            "fields_sha256": "77b7f9b9c904d3da9511e91c5edc5cf1f81e33465cb50016f8017d63f9cfeba0",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        }
    },
    "salesforce": {
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "761c945be3fcf6f24e0a15c4ff07cfb040cb45cbada1b4787f1bd0c749df9c5f",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
        "oauth2_token": {
            "auth_type": "oauth",
            "fields_sha256": "260961128df83052a49ae3aee57360b6e9f24383fe44d153e93bed35a6b28cde",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        },
    },
    "salesloft": {
        "api_key": {
            "auth_type": "api-key",
            "fields_sha256": "df251b7d13ff820ab18a4057a40e404032f49d55bfd30ed64808998985ff1bf8",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "provider_enforced",
                "evidence_source": "unavailable",
            },
        },
        "oauth2_authorization_code": {
            "auth_type": "oauth",
            "fields_sha256": "11289daf8a81ac102370f6d8863b00e99c604e5a18ca3a1dd045fbfc95d73608",
            "interactive": True,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "oauth_response",
            },
        },
        "oauth2_token": {
            "auth_type": "oauth",
            "fields_sha256": "fb5bbafc44a9d72b4197b9aec40e6e55122b6df2b9404079b31dbf31e00767a4",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": {
                "enforcement": "local_required",
                "evidence_source": "unavailable",
            },
        },
    },
    "taboola": {
        "client_credentials": {
            "auth_type": "oauth-client-credentials",
            "fields_sha256": "dbe2485adf2689444608d4f3a21875719d53fdd45a352eb62d5f88bd6d49e47e",
            "interactive": False,
            "payload_field": None,
            "payload_format": "json",
            "permission_verification": None,
        }
    },
}


@pytest.mark.parametrize("provider_key", sorted(RELEASED_METHODS))
def test_released_host_fields_and_policy_are_preserved(provider_key):
    import hashlib
    import json

    providers = {p.key: p for plugin in BUILTIN_PLUGIN_MANIFESTS for p in plugin.providers}
    methods = {method.key: method for method in providers[provider_key].auth_methods}
    assert set(methods) == set(RELEASED_METHODS[provider_key])
    for key, expected in RELEASED_METHODS[provider_key].items():
        method = methods[key].model_dump()
        fields_digest = hashlib.sha256(
            json.dumps(method["fields"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert fields_digest == expected["fields_sha256"], (provider_key, key)
        for field in [
            "permission_verification",
            "auth_type",
            "interactive",
            "payload_format",
            "payload_field",
        ]:
            assert method[field] == expected[field], (provider_key, key, field)
