"""Finance and backup administration retain independent local console authority."""

from stackos.plugins.builtin_finance import helpers


def test_browser_can_inspect_finance_and_configure_backup_independently(api, project_id, tmp_path):
    enabled = api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable")
    assert enabled.status_code == 200, enabled.text
    master = helpers().workspace.initialize(tmp_path / "external-finance")
    original = master.read_bytes()
    ui = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}

    def call(name, arguments):
        return api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"response_mode": "raw", **arguments}},
            headers=ui,
        )

    selected = call(
        "financeWorkspace.select",
        {
            "project_id": project_id,
            "workspace_ref": "finance:synthetic-integration",
            "root": str(master.parent),
            "modes": ["read"],
            "authority_ref": "operator:synthetic-integration",
        },
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["data"]["generation"] == 1
    assert selected.headers["Cache-Control"] == "no-store"
    assert selected.headers["X-Content-Type-Options"] == "nosniff"

    before = call("backup.status", {})
    assert before.status_code == 200, before.text
    assert before.json()["targets"] == []
    configured = call("backup.configure", {"enabled": False, "interval_hours": 24})
    assert configured.status_code == 200, configured.text
    assert configured.json()["data"]["interval_hours"] == 24
    assert configured.json()["data"]["targets"] == []

    finance = call(
        "financeWorkspace.status",
        {"project_id": project_id, "workspace_ref": "finance:synthetic-integration"},
    )
    assert finance.status_code == 200, finance.text
    assert finance.json()["readiness"] == "ready"
    assert finance.json()["access"][0]["generation"] == 1
    assert finance.json()["access"][0]["modes"] == ["read"]
    assert finance.headers["Cache-Control"] == "no-store"
    assert finance.headers["X-Content-Type-Options"] == "nosniff"
    assert master.read_bytes() == original
    assert call("backup.status", {}).json()["targets"] == []

    unrelated = call(
        "resource.upsert",
        {"project_id": project_id, "resource_type": "note", "key": "blocked"},
    )
    assert unrelated.status_code == 403, unrelated.text
