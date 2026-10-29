from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from stackos import install


@pytest.fixture
def skills(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text("Canonical instructions")
    (source / "references/setup.md").write_text("Canonical setup")
    monkeypatch.setattr(install, "_resolve_source", lambda _kind: source)
    return tmp_path / "skills"


def receipt(skills: Path) -> Path:
    return skills / ".stackos-ownership.json"


def test_managed_copy_records_identity_and_preserves_inode_and_extra_files(skills):
    target = install.copy_managed_stackos_skill_to(skills)
    before = target.stat().st_ino
    data = json.loads(receipt(skills).read_text())
    assert data["inode"] == before
    assert data["skills_root"] == str(skills.resolve())
    assert data["paths"] == ["SKILL.md", "references/setup.md"]
    (target / "personal.txt").write_text("Keep me")
    install.copy_managed_stackos_skill_to(skills)
    assert target.stat().st_ino == before
    assert (target / "personal.txt").read_text() == "Keep me"
    assert not install.stackos_skill_is_current(skills)
    assert install.stackos_skill_is_current(skills, allow_extra=True)
    assert install.stackos_skill_is_owned(skills)


def test_partial_copy_is_owned_before_any_files_are_written(skills, monkeypatch):
    def fail(skills_root, **_kwargs):
        assert install.stackos_skill_is_owned(skills_root)
        (skills_root / "stackos/SKILL.md").write_text("Incomplete")
        raise OSError("Synthetic interrupted copy")

    monkeypatch.setattr(install, "copy_stackos_skill_to", fail)
    with pytest.raises(OSError):
        install.copy_managed_stackos_skill_to(skills)
    assert install.stackos_skill_is_owned(skills)
    assert install.remove_managed_stackos_skill_from(skills)
    assert not (skills / "stackos").exists()
    assert not receipt(skills).exists()


@pytest.mark.parametrize("deleted", [False, True])
def test_cleanup_owns_modified_and_missing_files_without_mcp_entry(skills, deleted):
    target = install.copy_managed_stackos_skill_to(skills)
    (target / "SKILL.md").write_text("Modified instructions")
    if deleted:
        (target / "references/setup.md").unlink()
    assert install.stackos_skill_is_owned(skills)
    assert install.remove_managed_stackos_skill_from(skills)
    assert not target.exists()
    assert install.remove_managed_stackos_skill_from(skills)


def test_cleanup_preserves_unlisted_files_and_sibling_skills(skills):
    target = install.copy_managed_stackos_skill_to(skills)
    (target / "references/personal.md").write_text("Keep personal notes")
    sibling = skills / "other/SKILL.md"
    sibling.parent.mkdir()
    sibling.write_text("Keep sibling")
    assert install.remove_managed_stackos_skill_from(skills)
    assert (target / "references/personal.md").read_text() == "Keep personal notes"
    assert sibling.read_text() == "Keep sibling"
    assert not (target / "SKILL.md").exists()
    assert not receipt(skills).exists()
    assert not install.stackos_skill_is_owned(skills)
    assert not install.remove_managed_stackos_skill_from(skills)


def test_receipt_survives_partial_removal_for_retry(skills, monkeypatch):
    target = install.copy_managed_stackos_skill_to(skills)
    original_unlink = Path.unlink

    def fail(path, *args, **kwargs):
        if path == target / "references/setup.md":
            raise PermissionError("Synthetic permission failure")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        assert not install.remove_managed_stackos_skill_from(skills)
    assert not (target / "SKILL.md").exists()
    assert receipt(skills).exists()
    assert install.stackos_skill_is_owned(skills)
    assert install.remove_managed_stackos_skill_from(skills)
    assert not receipt(skills).exists()


def test_missing_entire_tree_remains_owned_for_receipt_cleanup(skills):
    target = install.copy_managed_stackos_skill_to(skills)
    shutil.rmtree(target)
    assert install.stackos_skill_is_owned(skills)
    assert install.remove_managed_stackos_skill_from(skills)
    assert not receipt(skills).exists()


def test_missing_entire_tree_can_be_repaired(skills):
    target = install.copy_managed_stackos_skill_to(skills)
    shutil.rmtree(target)
    install.copy_managed_stackos_skill_to(skills)
    assert install.stackos_skill_is_owned(skills)
    assert install.stackos_skill_is_current(skills)


def test_receipted_cleanup_does_not_require_install_source_assets(skills, monkeypatch):
    target = install.copy_managed_stackos_skill_to(skills)

    def missing(_kind):
        raise FileNotFoundError("Installation assets no longer exist")

    monkeypatch.setattr(install, "_resolve_source", missing)
    assert install.stackos_skill_is_owned(skills)
    assert install.remove_managed_stackos_skill_from(skills)
    assert not target.exists()


@pytest.mark.parametrize("adopt", [False, True])
def test_replaced_tree_cannot_inherit_old_receipt(skills, adopt):
    target = install.copy_managed_stackos_skill_to(skills)
    target.rename(skills / "original")
    target.mkdir()
    personal = target / "SKILL.md"
    personal.write_text("Replacement is foreign")
    assert not install.stackos_skill_is_owned(skills)
    with pytest.raises(OSError):
        install.copy_managed_stackos_skill_to(skills, adopt_existing=adopt)
    assert not install.remove_managed_stackos_skill_from(skills, adopt_existing=adopt)
    assert personal.read_text() == "Replacement is foreign"
    assert receipt(skills).exists()


@pytest.mark.parametrize("location", ["root", "target", "reference", "receipt"])
def test_symlink_paths_are_refused_without_traversal(skills, tmp_path, location):
    target = install.copy_managed_stackos_skill_to(skills)
    path = {
        "root": skills,
        "target": target,
        "reference": target / "references",
        "receipt": receipt(skills),
    }[location]
    moved = tmp_path / f"moved-{location}"
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    before = (
        {p.relative_to(tmp_path): p.read_bytes() for p in moved.rglob("*") if p.is_file()}
        if moved.is_dir()
        else moved.read_bytes()
    )
    assert not install.stackos_skill_is_owned(skills)
    with pytest.raises(OSError):
        install.copy_managed_stackos_skill_to(skills, adopt_existing=True)
    assert not install.remove_managed_stackos_skill_from(skills, adopt_existing=True)
    after = (
        {p.relative_to(tmp_path): p.read_bytes() for p in moved.rglob("*") if p.is_file()}
        if moved.is_dir()
        else moved.read_bytes()
    )
    assert before == after


def test_exact_canonical_mirror_can_be_adopted_without_existing_receipt(skills):
    install.copy_stackos_skill_to(skills)
    assert not install.stackos_skill_is_owned(skills)
    assert install.remove_managed_stackos_skill_from(skills)
    assert not (skills / "stackos").exists()


def test_legacy_adoption_requires_explicit_authority_for_drifted_tree(skills):
    target = install.copy_stackos_skill_to(skills)
    (target / "SKILL.md").write_text("Legacy instructions")
    with pytest.raises(OSError):
        install.copy_managed_stackos_skill_to(skills)
    assert not install.remove_managed_stackos_skill_from(skills)
    install.copy_managed_stackos_skill_to(skills, adopt_existing=True)
    assert install.stackos_skill_is_owned(skills)
    assert install.stackos_skill_is_current(skills)


@pytest.mark.parametrize("bad_path", ["../outside", "/tmp/outside", ".", "a/../outside"])
def test_receipt_paths_cannot_escape_owned_tree(skills, bad_path):
    install.copy_managed_stackos_skill_to(skills)
    data = json.loads(receipt(skills).read_text())
    data["paths"].append(bad_path)
    receipt(skills).write_text(json.dumps(data))
    assert not install.stackos_skill_is_owned(skills)
    assert not install.remove_managed_stackos_skill_from(skills, adopt_existing=True)


def test_receipt_retains_original_paths_across_source_upgrade_and_copy_failure(skills, monkeypatch):
    install.copy_managed_stackos_skill_to(skills)
    source = install._resolve_source("skills")
    (source / "references/setup.md").unlink()
    (source / "references/new.md").write_text("New instructions")

    def fail(*_args, **_kwargs):
        raise OSError("Synthetic interruption")

    monkeypatch.setattr(install, "copy_stackos_skill_to", fail)
    with pytest.raises(OSError):
        install.copy_managed_stackos_skill_to(skills)
    assert json.loads(receipt(skills).read_text())["paths"] == [
        "SKILL.md",
        "references/new.md",
        "references/setup.md",
    ]
    assert install.remove_managed_stackos_skill_from(skills)


def test_source_upgrade_prunes_only_previously_owned_retired_files(skills):
    target = install.copy_managed_stackos_skill_to(skills)
    (target / "references/personal.md").write_text("Keep user addition")
    source = install._resolve_source("skills")
    (source / "references/setup.md").unlink()
    (source / "references/new.md").write_text("New instructions")
    install.copy_managed_stackos_skill_to(skills)
    assert not (target / "references/setup.md").exists()
    assert (target / "references/new.md").read_text() == "New instructions"
    assert (target / "references/personal.md").read_text() == "Keep user addition"
    assert "references/setup.md" in json.loads(receipt(skills).read_text())["paths"]
    assert install.stackos_skill_is_current(skills, allow_extra=True)
