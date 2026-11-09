"""Native source identity, preserving writes and filesystem safety on disposable trees."""

import hashlib
import os

import pytest

from stackos.host_mcp.project_agents import ProjectAgentError, ProjectAgentService


def definition(root, name="research", *, filename=None, extra=""):
    path = root / ".codex/agents" / (filename or f"{name}.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'# provenance\nname = "{name}"\ndescription = "Research" # keep\n'
        'developer_instructions = """Full\ninstructions\n"""\n' + extra
    )
    return path


def test_standalone_registered_dedup_and_legacy_metadata(tmp_path):
    definition(tmp_path)
    legacy = tmp_path / ".codex/legacy.toml"
    legacy.write_text('developer_instructions = "Legacy full instructions"\n')
    (tmp_path / ".codex/config.toml").write_text(
        '[agents.research]\nconfig_file="agents/research.toml"\n'
        '[agents.legacy]\nconfig_file="legacy.toml"\ndescription="Legacy"\n'
    )
    service = ProjectAgentService(tmp_path)
    rows = service.list().items
    assert len(rows) == 2
    modern = next(r for r in rows if r.source_ref.native_name == "research")
    assert modern.registration_keys == ["research"]
    assert (
        modern.digest
        == hashlib.sha256((tmp_path / modern.source_ref.source_path).read_bytes()).hexdigest()
    )
    old = next(r for r in rows if r.source_ref.native_name == "legacy")
    assert old.fields.name == "legacy" and old.fields.description == "Legacy"
    assert service.get(old.source_ref).fields.developer_instructions == "Legacy full instructions"


@pytest.mark.parametrize("legacy", [False, True])
def test_same_file_registration_alias_deduplicates_before_parsing_and_updates(tmp_path, legacy):
    path = definition(tmp_path)
    if legacy:
        path.write_text('# legacy provenance\ndeveloper_instructions="Full instructions"\n')
    alternate = path.with_name("RESEARCH.toml")
    if not alternate.exists() or not alternate.samefile(path):
        pytest.skip("Requires case-insensitive source aliases on the current filesystem")
    config = tmp_path / ".codex/config.toml"
    config.write_text(
        '[agents.research]\nconfig_file="agents/RESEARCH.toml"\ndescription="Research"\n'
    )
    registration_bytes = config.read_bytes()
    service = ProjectAgentService(tmp_path)
    listing = service.list()
    assert len(listing.items) == 1 and listing.diagnostics == []
    row = listing.items[0]
    assert row.source_ref.source_path == ".codex/agents/research.toml"
    assert row.source_ref.native_name == "research" and row.diagnostics == []
    assert row.registration_keys == ["research"]
    assert service.get(row.source_ref) == row
    before = path.read_bytes()
    saved = service.update(row.source_ref, row.digest, {"description": "Edited"})
    assert saved.source_ref == row.source_ref and saved.digest != row.digest
    assert service.get(saved.source_ref) == saved
    assert alternate.read_bytes() == path.read_bytes() != before
    assert saved.fields.developer_instructions == row.fields.developer_instructions
    assert config.read_bytes() == registration_bytes


def test_same_file_aliases_keep_conflicting_registration_names_ambiguous(tmp_path):
    path = definition(tmp_path)
    alternate = path.with_name("RESEARCH.toml")
    if not alternate.exists() or not alternate.samefile(path):
        pytest.skip("Requires case-insensitive source aliases on the current filesystem")
    (tmp_path / ".codex/config.toml").write_text(
        '[agents.research]\nconfig_file="agents/research.toml"\n'
        '[agents.other]\nconfig_file="agents/RESEARCH.toml"\n'
    )
    service = ProjectAgentService(tmp_path)
    rows = service.list().items
    assert len(rows) == 1
    assert rows[0].registration_keys == ["other", "research"]
    assert rows[0].diagnostics[0]["code"] == "ambiguous_identity"
    with pytest.raises(ProjectAgentError, match="ambiguous_identity"):
        service.get(rows[0].source_ref)


def test_overlong_registration_is_diagnostic_and_healthy_source_stays_editable(tmp_path):
    path = definition(tmp_path)
    service = ProjectAgentService(tmp_path)
    healthy = service.list().items[0]
    overlong = "sub/" * 250 + "bad.toml"
    (tmp_path / ".codex/config.toml").write_text(f'[agents.bad]\nconfig_file="{overlong}"\n')
    listing = service.list()
    assert len(listing.items) == 1
    assert len(listing.diagnostics) == 1
    diagnostic = listing.diagnostics[0]
    assert diagnostic["code"] == "invalid_definition"
    assert diagnostic["reason"] == "source_path_too_long"
    assert diagnostic["registration_key"] == "bad" and diagnostic["repair"]
    assert overlong not in str(listing.model_dump())
    assert service.get(healthy.source_ref).digest == healthy.digest
    saved = service.update(healthy.source_ref, healthy.digest, {"description": "Healthy edit"})
    assert saved.fields.description == "Healthy edit" and saved.digest != healthy.digest
    assert 'description = "Healthy edit"' in path.read_text()


def test_preserving_update_conflict_and_optional_inheritance(tmp_path):
    path = definition(
        tmp_path,
        extra=(
            'model = "chosen"\nunknown = { nested = "untouched" }\n'
            "[features]\nshell_tool = false # restriction\n"
        ),
    )
    before = path.read_bytes()
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    assert row.unsupported_fields == ["unknown"]
    saved = service.update(row.source_ref, row.digest, {"description": "New"})
    assert path.read_bytes() == before.replace(b'"Research"', b'"New"')
    assert saved.fields.developer_instructions == row.fields.developer_instructions
    assert saved.native_settings["features"] == {"shell_tool": False}
    current = path.read_bytes()
    with pytest.raises(ProjectAgentError, match="stale_source"):
        service.update(row.source_ref, row.digest, {"description": "Lost update"})
    assert path.read_bytes() == current
    saved = service.update(saved.source_ref, saved.digest, {"model": None})
    assert "model" in saved.inherited_fields and "model =" not in path.read_text()


def test_duplicate_native_names_are_visible_and_not_selectable(tmp_path):
    definition(tmp_path, filename="one.toml")
    definition(tmp_path, filename="two.toml")
    service = ProjectAgentService(tmp_path)
    rows = service.list().items
    assert len(rows) == 2
    assert all(r.diagnostics[0]["code"] == "ambiguous_identity" for r in rows)
    with pytest.raises(ProjectAgentError, match="ambiguous_identity"):
        service.get(rows[0].source_ref)


def test_exclusive_add_readback_and_rename_keeps_filename(tmp_path):
    service = ProjectAgentService(tmp_path)
    fields = {
        "name": "research",
        "description": "Research",
        "developer_instructions": "Entire instructions",
    }
    row = service.create(fields)
    path = tmp_path / row.source_ref.source_path
    assert service.get(row.source_ref) == row
    before = path.read_bytes()
    with pytest.raises(ProjectAgentError):
        service.create(fields)
    assert path.read_bytes() == before
    saved = service.update(row.source_ref, row.digest, {"name": "new_name"})
    assert saved.source_ref.source_path == row.source_ref.source_path
    assert saved.source_ref.native_name == "new_name"
    assert not (tmp_path / ".codex/config.toml").exists()


@pytest.mark.parametrize(
    "bad", ["../outside.toml", "/tmp/outside.toml", ".codex/agents/../../outside.toml"]
)
def test_arbitrary_paths_are_not_readers(tmp_path, bad):
    service = ProjectAgentService(tmp_path)
    with pytest.raises(ProjectAgentError):
        service.get({"host_key": "codex", "native_name": "research", "source_path": bad})


def test_malformed_oversized_symlink_and_unrelated_healthy_sources(tmp_path):
    definition(tmp_path)
    agents = tmp_path / ".codex/agents"
    (agents / "bad.toml").write_text('developer_instructions = "PRIVATE CONTENT"\nbroken = [')
    (agents / "large.toml").write_bytes(b"x" * (1024 * 1024 + 1))
    outside = tmp_path / "outside.toml"
    outside.write_text('name="must-not-read"')
    (agents / "link.toml").symlink_to(outside)
    service = ProjectAgentService(tmp_path)
    result = service.list()
    assert len(result.items) == 4
    assert "PRIVATE CONTENT" not in str(result.model_dump())
    healthy = next(r for r in result.items if r.source_ref.native_name == "research")
    assert service.get(healthy.source_ref).digest
    assert sum(bool(r.diagnostics) for r in result.items) == 3


def test_registration_escape_and_directory_symlink_are_not_followed(tmp_path):
    definition(tmp_path)
    (tmp_path / ".codex/config.toml").write_text(
        '[agents.escape]\nconfig_file="../../personal.toml"\n'
    )
    assert ProjectAgentService(tmp_path).list().diagnostics[0]["code"] == "outside_root"
    other = tmp_path / "other"
    other.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / ".codex").symlink_to(other, target_is_directory=True)
    with pytest.raises(ProjectAgentError):
        ProjectAgentService(root).create(
            {"name": "research", "description": "d", "developer_instructions": "i"}
        )
    assert list(other.iterdir()) == []


def test_readonly_source_and_failed_replace_preserve_bytes(tmp_path, monkeypatch):
    path = definition(tmp_path)
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    before = path.read_bytes()
    path.chmod(0o444)
    with pytest.raises(ProjectAgentError):
        service.update(row.source_ref, row.digest, {"description": "denied"})
    path.chmod(0o644)
    monkeypatch.setattr("os.replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("PRIVATE")))
    with pytest.raises(ProjectAgentError, match="write_failed"):
        service.update(row.source_ref, row.digest, {"description": "failed"})
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_referenced_rename_refused_before_write(tmp_path):
    path = definition(tmp_path)
    service = ProjectAgentService(tmp_path, references=lambda ref: [{"kind": "schedule", "id": 9}])
    row = service.list().items[0]
    before = path.read_bytes()
    with pytest.raises(ProjectAgentError, match="referenced_identity") as error:
        service.update(row.source_ref, row.digest, {"name": "renamed"})
    assert error.value.data["references"] == [{"kind": "schedule", "id": 9}]
    assert path.read_bytes() == before


def test_two_roots_and_unsupported_authority_do_not_fallback(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    path = definition(a)
    definition(b, extra='model="other"\n')
    first, second = ProjectAgentService(a), ProjectAgentService(b)
    row = first.list().items[0]
    assert row.digest != second.get(row.source_ref).digest
    with pytest.raises(ProjectAgentError, match="unsupported_setting"):
        first.update(row.source_ref, row.digest, {"sandbox_mode": "danger-full-access"})
    assert "sandbox_mode" not in path.read_text()


@pytest.mark.parametrize(
    "patch",
    [
        {"features": {"shell_tool": True}},
        {"skills": {"include_instructions": True}},
        {"model_reasoning_effort": "invented"},
    ],
)
def test_unsupported_or_authority_expanding_settings_rejected(tmp_path, patch):
    definition(tmp_path)
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    with pytest.raises(ProjectAgentError, match="unsupported_setting"):
        service.update(row.source_ref, row.digest, patch)


def test_nested_patch_preserves_unknown_siblings_and_native_restrictions(tmp_path):
    path = definition(
        tmp_path,
        extra=(
            '[features]\nshell_tool = false # retained\nfuture = "private"\n'
            "[skills]\ninclude_instructions = false # retained too\n"
            'config = [{path="skills/one", enabled=false}]\n'
            "[skills.bundled]\nenabled = false\n"
        ),
    )
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    before = path.read_bytes()
    saved = service.update(row.source_ref, row.digest, {"features": {"apps": False}})
    assert 'future = "private"' in path.read_text()
    assert "shell_tool = false # retained" in path.read_text()
    assert saved.fields.skills == row.fields.skills
    assert saved.fields.features == {"shell_tool": False, "apps": False}
    assert saved.native_settings["features"] == {"shell_tool": False, "apps": False}
    assert saved.unsupported_fields == ["features.future"]
    assert path.read_bytes().replace(b"apps = false\n", b"") == before


@pytest.mark.parametrize("changed", ["source", "registration"])
def test_external_change_during_preparation_rejects_without_overwrite(
    tmp_path, monkeypatch, changed
):
    path = definition(tmp_path)
    config = tmp_path / ".codex/config.toml"
    config.write_text("# original registration state\n")
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    original = path.read_bytes()
    real_sync = os.fsync

    def concurrent_change(fd):
        if changed == "source":
            path.write_bytes(original + b"# external\n")
        else:
            config.write_text("# external registration state\n")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", concurrent_change)
    with pytest.raises(ProjectAgentError, match="stale_source"):
        service.update(row.source_ref, row.digest, {"description": "Lost"})
    assert path.read_bytes() == original + (b"# external\n" if changed == "source" else b"")
    assert list(path.parent.iterdir()) == [path]


def test_create_readback_failure_rolls_back_only_owned_definition(tmp_path, monkeypatch):
    service = ProjectAgentService(tmp_path)
    original_get = service.get

    def fail_readback(ref):
        raise ProjectAgentError("unavailable")

    monkeypatch.setattr(service, "get", fail_readback)
    with pytest.raises(ProjectAgentError):
        service.create({"name": "research", "description": "d", "developer_instructions": "i"})
    assert list((tmp_path / ".codex/agents").iterdir()) == []
    monkeypatch.setattr(service, "get", original_get)
    assert service.create({"name": "research", "description": "d", "developer_instructions": "i"})


def test_hardlink_fifo_and_symlink_do_not_expose_content(tmp_path):
    path = definition(tmp_path)
    os.link(path, path.with_name("hardlink.toml"))
    os.mkfifo(path.with_name("fifo.toml"))
    listing = ProjectAgentService(tmp_path).list()
    assert len(listing.items) == 3
    assert all(row.fields is None and row.diagnostics for row in listing.items)


def test_registration_aliases_and_legacy_rename_need_explicit_reassignment(tmp_path):
    path = definition(tmp_path)
    config = tmp_path / ".codex/config.toml"
    config.write_text('[agents.alias]\nconfig_file="agents/research.toml"\n')
    service = ProjectAgentService(tmp_path)
    assert service.list().items[0].diagnostics[0]["code"] == "ambiguous_identity"
    config.write_text('[agents.research]\nconfig_file="agents/research.toml"\n')
    row = service.list().items[0]
    before = path.read_bytes()
    with pytest.raises(ProjectAgentError, match="referenced_identity"):
        service.update(row.source_ref, row.digest, {"name": "changed"})
    assert path.read_bytes() == before


def test_long_source_filename_and_bound_on_source_count(tmp_path, monkeypatch):
    definition(tmp_path, filename="a" * 180 + ".toml")
    assert ProjectAgentService(tmp_path).list().items[0].fields.name == "research"
    definition(tmp_path, filename="other.toml", name="other")
    monkeypatch.setattr("stackos.host_mcp.project_agents.MAX_SOURCES", 1)
    with pytest.raises(ProjectAgentError, match="invalid_definition"):
        ProjectAgentService(tmp_path).list()


def test_source_readback_failure_reports_unconfirmed_saved_bytes(tmp_path, monkeypatch):
    path = definition(tmp_path)
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    original_get = service.get
    reads = 0

    def fail_final_readback(ref):
        nonlocal reads
        reads += 1
        if reads > 1:
            raise ProjectAgentError("unavailable")
        return original_get(ref)

    monkeypatch.setattr(service, "get", fail_final_readback)
    with pytest.raises(ProjectAgentError, match="write_failed") as error:
        service.update(row.source_ref, row.digest, {"description": "Saved"})
    assert error.value.data["write_state"] == "unconfirmed"
    assert 'description = "Saved"' in path.read_text()


def test_rename_checks_references_again_before_replacement(tmp_path):
    path = definition(tmp_path)
    calls = 0

    def new_reference(ref):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else [{"kind": "schedule", "id": 4}]

    service = ProjectAgentService(tmp_path, references=new_reference)
    row = service.list().items[0]
    before = path.read_bytes()
    with pytest.raises(ProjectAgentError, match="referenced_identity"):
        service.update(row.source_ref, row.digest, {"name": "changed"})
    assert path.read_bytes() == before and calls == 2


def test_directory_move_cannot_redirect_replacement_even_when_source_inode_is_same(
    tmp_path, monkeypatch
):
    path = definition(tmp_path)
    moved = tmp_path / "moved-directory"
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    before = path.read_bytes()
    real_sync = os.fsync

    def swap_parent(fd):
        real_sync(fd)
        path.parent.rename(moved)
        path.parent.mkdir()
        (moved / path.name).rename(path)

    monkeypatch.setattr(os, "fsync", swap_parent)
    with pytest.raises(ProjectAgentError):
        service.update(row.source_ref, row.digest, {"description": "Must not be saved"})
    assert path.read_bytes() == before
    assert list(moved.iterdir()) == []


def test_literal_dotted_unknown_keys_are_preserved_without_exposing_or_editing_values(tmp_path):
    path = definition(
        tmp_path,
        extra=(
            '"features.shell_tool" = "private-root"\n'
            '[features]\n"future.secret" = "private-value"\nshell_tool = false\n'
        ),
    )
    service = ProjectAgentService(tmp_path)
    row = service.list().items[0]
    assert row.unsupported_fields == ["'features.shell_tool'", "features.future.secret"]
    assert row.fields.features == {"shell_tool": False}
    before = path.read_bytes()
    with pytest.raises(ProjectAgentError, match="unsupported_setting"):
        service.update(row.source_ref, row.digest, {"features": {"future.secret": "changed"}})
    assert path.read_bytes() == before
