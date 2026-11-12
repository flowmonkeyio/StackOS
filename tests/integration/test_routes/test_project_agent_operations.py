"""Local console authority and live bytes, independent of workspace binding."""

from sqlmodel import Session, select

from stackos.auth import derive_ui_token
from stackos.db.models import TaskTracker, WorkspaceBinding
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import NotFoundError


def call(api, name, arguments, *, headers=None):
    return api.post(
        f"/api/v1/operations/projectAgent.{name}/call",
        json={"arguments": arguments},
        headers=headers,
    )


def bind(api, project_id, root):
    root.mkdir(exist_ok=True)
    with Session(api.app.state.engine) as session:
        session.add(
            WorkspaceBinding(
                project_id=project_id, repo_fingerprint=f"fixture:{root}", last_known_root=str(root)
            )
        )
        session.commit()
    return {"project_id": project_id, "project_dir": str(root), "host_key": "codex"}


def test_explicit_project_folder_setup_readback_preserves_worktree_authority(
    api, project_id, tmp_path, settings
):
    from stackos.repositories.projects import ProjectRepository

    primary = bind(api, project_id, tmp_path / "primary")
    task = bind(api, project_id, tmp_path / "task")
    with Session(api.app.state.engine) as session:
        bindings = session.exec(
            select(WorkspaceBinding).where(WorkspaceBinding.project_id == project_id)
        ).all()
        binding_id = next(
            row.id for row in bindings if row.last_known_root == primary["project_dir"]
        )
        other_project = (
            ProjectRepository(session)
            .create(slug="other-folder", name="Other", domain="other.test")
            .data.id
        )
    # Browser authority remains narrower than explicit local-admin setup authority.
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    arguments = {"project_id": project_id, "patch": {"primary_workspace_binding_id": binding_id}}
    assert (
        api.post(
            "/api/v1/operations/project.update/call", json={"arguments": arguments}, headers=ui
        ).status_code
        == 403
    )
    chosen = api.post("/api/v1/operations/project.update/call", json={"arguments": arguments})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["data"]["primary_workspace_binding_id"] == binding_id
    read = api.post(
        "/api/v1/operations/project.get/call",
        json={"arguments": {"project_id": project_id, "response_mode": "raw"}},
        headers=ui,
    )
    assert read.status_code == 200, read.text
    assert read.json()["project_dir"] == primary["project_dir"]
    # Both direct and operation adapters use the same owner; wrong-project choices reject.
    assert api.get(f"/api/v1/projects/{project_id}").json()["project_dir"] == primary["project_dir"]
    rejected = api.patch(
        f"/api/v1/projects/{other_project}", json={"primary_workspace_binding_id": binding_id}
    )
    assert rejected.status_code == 422, rejected.text
    for scope in (primary, task):
        created = call(
            api,
            "create",
            {
                **scope,
                "fields": {
                    "name": "research",
                    "description": "d",
                    "developer_instructions": scope["project_dir"],
                },
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["data"]["project_dir"] == scope["project_dir"]
    assert call(api, "list", {**task, "project_id": other_project}).status_code == 422


def test_local_ui_add_get_edit_exact_readback(api, project_id, tmp_path, settings):
    scope = bind(api, project_id, tmp_path / "workspace")
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    fields = {
        "name": "research",
        "description": "Research",
        "developer_instructions": "All instructions\n" * 100,
        "model_reasoning_effort": "high",
        "features": {"shell_tool": False},
    }
    created = call(api, "create", {**scope, "fields": fields}, headers=ui)
    assert created.status_code == 200, created.text
    assert created.headers["cache-control"] == "no-store"
    row = created.json()["data"]
    path = tmp_path / "workspace" / row["source_ref"]["source_path"]
    assert path.is_file()
    read = call(
        api, "get", {**scope, "source_ref": row["source_ref"], "response_mode": "raw"}, headers=ui
    )
    assert read.json() == row
    saved = call(
        api,
        "update",
        {
            **scope,
            "source_ref": row["source_ref"],
            "expected_digest": row["digest"],
            "patch": {"description": "Edited"},
        },
        headers=ui,
    )
    assert saved.status_code == 200, saved.text
    assert (
        saved.json()["data"]["fields"]["developer_instructions"] == fields["developer_instructions"]
    )
    stale = call(
        api,
        "update",
        {
            **scope,
            "source_ref": row["source_ref"],
            "expected_digest": row["digest"],
            "patch": {"description": "Lost"},
        },
        headers=ui,
    )
    assert stale.status_code == 409, stale.text
    assert "stale_source" in stale.text
    listing = call(api, "list", {**scope, "response_mode": "compact"}, headers=ui)
    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["digest"] == saved.json()["data"]["digest"]
    assert fields["developer_instructions"] not in listing.text


def test_overlong_registered_path_returns_diagnostic_and_preserves_healthy_get_update(
    api, project_id, tmp_path
):
    root = tmp_path / "long-reference"
    scope = bind(api, project_id, root)
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {"name": "research", "description": "d", "developer_instructions": "i"},
        },
    )
    assert created.status_code == 200, created.text
    healthy = created.json()["data"]
    overlong = "sub/" * 250 + "bad.toml"
    (root / ".codex/config.toml").write_text(f'[agents.bad]\nconfig_file="{overlong}"\n')
    for mode in ("raw", "compact"):
        response = call(api, "list", {**scope, "response_mode": mode})
        assert response.status_code == 200, response.text
        listing = response.json() if mode == "raw" else response.json()["data"]
        assert len(listing["items"]) == 1
        assert listing["diagnostics"][0]["code"] == "invalid_definition"
        assert listing["diagnostics"][0]["registration_key"] == "bad"
        assert overlong not in response.text
    current = call(
        api, "get", {**scope, "source_ref": healthy["source_ref"], "response_mode": "raw"}
    )
    assert current.status_code == 200, current.text
    assert current.json()["digest"] == healthy["digest"]
    saved = call(
        api,
        "update",
        {
            **scope,
            "source_ref": healthy["source_ref"],
            "expected_digest": healthy["digest"],
            "patch": {"description": "Healthy edit"},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["fields"]["description"] == "Healthy edit"


def test_permissions_surfaces_and_project_scope(api, project_id, tmp_path):
    scope = bind(api, project_id, tmp_path / "bound")
    for name in ("list", "get", "create", "update"):
        spec = build_operation_registry().get(f"projectAgent.{name}")
        assert spec.surfaces.rest.browser_safe
        for surface in ("mcp", "cli"):
            try:
                build_operation_registry().get(spec.name, surface=surface)
            except NotFoundError:
                pass
            else:
                raise AssertionError(f"Unexpected filesystem access on {surface}")
    assert call(api, "list", scope, headers={"Authorization": ""}).status_code == 401
    assert call(api, "list", {**scope, "run_token": "forged"}).status_code == 403
    assert call(api, "list", scope, headers={"X-StackOS-Client-Surface": "cli"}).status_code == 403
    denied = call(api, "list", {**scope, "project_id": project_id + 999})
    assert denied.status_code in {403, 422}
    other = tmp_path / "unbound"
    other.mkdir()
    assert call(api, "list", {**scope, "project_dir": str(other)}).status_code == 422
    assert call(api, "list", {"project_id": project_id}).status_code == 422
    assert call(api, "list", {**scope, "host_key": "claude-code"}).status_code == 422
    assert (
        call(
            api, "create", {**scope, "fields": {"name": "forged"}, "response_mode": "ack"}
        ).status_code
        == 422
    )
    assert list(other.iterdir()) == []


def test_saved_project_references_require_reassignment(api, project_id, tmp_path):
    scope = bind(api, project_id, tmp_path / "bound")
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {
                "name": "research",
                "description": "d",
                "developer_instructions": "instructions",
            },
        },
    )
    assert created.status_code == 200, created.text
    row = created.json()["data"]
    with Session(api.app.state.engine) as session:
        tracker = TaskTracker(
            project_id=project_id,
            key="default",
            name="Default",
            config_json={"delivery": {"agents": {"researcher": {"source_ref": row["source_ref"]}}}},
        )
        session.add(tracker)
        session.commit()
        tracker_id = tracker.id
    args = {
        **scope,
        "source_ref": row["source_ref"],
        "expected_digest": row["digest"],
        "patch": {"name": "renamed"},
    }
    rejected = call(api, "update", args)
    assert rejected.status_code == 409, rejected.text
    assert "referenced_identity" in rejected.text and "project_assignment" in rejected.text
    with Session(api.app.state.engine) as session:
        session.delete(session.get(TaskTracker, tracker_id))
        session.commit()
    saved = call(api, "update", args)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["source_ref"]["source_path"] == row["source_ref"]["source_path"]


def test_retry_rechecks_authority_and_current_workspace_instead_of_replaying_prompt(
    api, project_id, tmp_path
):
    scope = bind(api, project_id, tmp_path / "retry")
    arguments = {
        **scope,
        "idempotency_key": "fixture-agent-create",
        "fields": {
            "name": "research",
            "description": "d",
            "developer_instructions": "Full source instructions",
        },
    }
    created = call(api, "create", arguments)
    assert created.status_code == 200, created.text
    for denied in (
        call(api, "create", arguments, headers={"X-StackOS-Client-Surface": "cli"}),
        call(api, "create", {**arguments, "run_token": "forged"}),
    ):
        assert denied.status_code == 403, denied.text
        assert "Full source instructions" not in denied.text
    other = tmp_path / "unbound-retry"
    other.mkdir()
    retry = call(api, "create", {**arguments, "project_dir": str(other)})
    assert retry.status_code == 422, retry.text
    assert "Full source instructions" not in retry.text
    # Retrying an exclusive create is an actionable collision, never an overwrite.
    assert call(api, "create", arguments).status_code == 409


def test_two_associated_trees_and_existing_other_project_are_separate(api, project_id, tmp_path):
    first = bind(api, project_id, tmp_path / "first")
    second = bind(api, project_id, tmp_path / "second")
    fields = {"name": "research", "description": "d", "developer_instructions": "first tree"}
    a = call(api, "create", {**first, "fields": fields}).json()["data"]
    b = call(
        api, "create", {**second, "fields": {**fields, "developer_instructions": "second tree"}}
    ).json()["data"]
    assert a["source_ref"] == b["source_ref"] and a["digest"] != b["digest"]
    for scope, row in ((first, a), (second, b)):
        current = call(
            api, "get", {**scope, "source_ref": row["source_ref"], "response_mode": "raw"}
        )
        assert current.json() == row
    other = api.post(
        "/api/v1/projects",
        json={"slug": "other-project", "name": "Other", "domain": "other.test"},
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["data"]["id"]
    foreign = bind(api, other_id, tmp_path / "foreign")
    rejected = call(api, "create", {**foreign, "project_id": project_id, "fields": fields})
    assert rejected.status_code == 422, rejected.text
    assert list((tmp_path / "foreign").iterdir()) == []
