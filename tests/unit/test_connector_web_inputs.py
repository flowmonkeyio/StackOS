from stackos.actions.package_inputs.web import prepare_web_input


def test_ga_preserves_alias_and_default_without_changing_original():
    data = {"property_ref": "main", "request": {"limit": "10"}}
    config = {"default_property_ref": "properties/123", "property_refs": {"other": "456"}}
    native = prepare_web_input("google-analytics", data, config)
    assert native == {"property_id": "properties/123", "request": {"limit": "10"}}
    native["request"]["limit"] = "20"
    assert data == {"property_ref": "main", "request": {"limit": "10"}}
    assert prepare_web_input("google-analytics", {"property_ref": "other"}, config) == {
        "property_id": "456"
    }


def test_gtm_resolves_each_selector_and_keeps_native_paths():
    assert prepare_web_input(
        "google-tag-manager",
        {
            "account_ref": "main",
            "container_ref": "website",
            "workspace_ref": "work",
        },
        {
            "default_account_ref": "accounts/123",
            "container_refs": {"website": "456"},
            "workspace_refs": {"work": "accounts/123/containers/456/workspaces/789"},
        },
    ) == {
        "account_id": "accounts/123",
        "container_id": "456",
        "workspace_id": "accounts/123/containers/456/workspaces/789",
    }


def test_unknown_selectors_keep_existing_fallback_and_other_inputs_are_unchanged():
    assert prepare_web_input("google-analytics", {"property_ref": "unmapped"}, {}) == {
        "property_id": "unmapped"
    }
    assert prepare_web_input("serper", {"query": "hello"}, None) == {"query": "hello"}
    assert prepare_web_input("google-analytics", {"page_size": 20}, None) == {"page_size": 20}
