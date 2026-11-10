"""Real bearer middleware coverage for bounded browser plugin setup."""

import pytest

from stackos.auth import derive_ui_token


def ui_headers(settings):
    token = settings.token_path.read_text().strip()
    return {"Authorization": f"Bearer {derive_ui_token(token)}"}


def test_ui_enable_preserves_config_and_daemon_configuration(api, project_id, settings):
    path = f"/api/v1/projects/{project_id}/plugins/finance/enable"
    config = {"workspaces": [{"key": "retained", "path": "/synthetic/not-opened"}]}
    configured = api.post(path, json={"config_json": config})
    assert configured.status_code == 200, configured.text
    assert configured.json()["data"]["config_json"] == config
    for kwargs in ({}, {"json": {}}):
        api.post(path.replace("/enable", "/disable"))
        enabled = api.post(path, headers=ui_headers(settings), **kwargs)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["data"]["enabled"] is True
        assert enabled.json()["data"]["config_json"] == config
    assert not (settings.data_dir / "projects").exists()


@pytest.mark.parametrize(
    "body",
    [
        '{"config_json":{}}',
        '{"config_json":null}',
        '{"config_json":{"x":1}}',
        '{"unknown":true}',
        "null",
        "[]",
        "false",
        '""',
        "{",
        " ",
    ],
)
def test_ui_enable_rejects_all_nonempty_bodies_before_mutation(api, project_id, settings, body):
    path = f"/api/v1/projects/{project_id}/plugins/finance/enable"
    response = api.post(path, headers=ui_headers(settings), content=body)
    assert response.status_code == 403, response.text
    plugin = api.get("/api/v1/plugins/finance", params={"project_id": project_id}).json()
    assert plugin["enabled_for_project"] is None


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/projects/1/plugins/finance/disable"),
        ("POST", "/api/v1/projects/1/plugins/finance/install"),
        ("POST", "/api/v1/projects/1/plugins/finance/enable/extra"),
        ("POST", "/api/v1/projects/1/plugins/finance/enable/"),
        ("POST", "/api/v1/projects/0/plugins/finance/enable"),
        ("POST", "/api/v1/projects/-1/plugins/finance/enable"),
        ("POST", "/api/v1/projects/no/plugins/finance/enable"),
        ("PATCH", "/api/v1/projects/1/plugins/finance/enable"),
        ("DELETE", "/api/v1/projects/1/plugins/finance/enable"),
        ("POST", "/api/v1/operations/plugin.enable/call"),
        ("POST", "/mcp"),
    ],
)
def test_ui_enable_does_not_expand_sibling_authority(api, settings, method, path):
    response = api.request(method, path, headers=ui_headers(settings), json={})
    assert response.status_code == 403, response.text


def test_ui_enable_retains_normal_validation_and_requires_auth(api, project_id, settings):
    path = f"/api/v1/projects/{project_id}/plugins/finance/enable"
    assert api.post(path, headers={"Authorization": ""}).status_code == 401
    assert api.post(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert (
        api.post(path.replace("finance", "missing"), headers=ui_headers(settings)).status_code
        == 404
    )
    assert (
        api.post(
            "/api/v1/projects/999999/plugins/finance/enable", headers=ui_headers(settings)
        ).status_code
        == 404
    )
