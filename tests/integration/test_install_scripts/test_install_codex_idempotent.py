"""`scripts/install-codex.sh` mirrors `skills/` into a sandbox HOME.

Re-running must yield the same end state (audit B-24) and the count
must equal the source repo's `SKILL.md` count.
"""

from __future__ import annotations

import filecmp
import os
import subprocess
from pathlib import Path


def _expected_skill_count(repo_root: Path) -> int:
    return sum(1 for _ in (repo_root / "skills").rglob("SKILL.md"))


def _run(script: Path, home: Path) -> str:
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "STACKOS_HOME": str(home)},
        check=True,
    )
    return result.stdout


def _snapshot(target: Path) -> dict[str, bytes]:
    """Return ``{relative_posix_path: bytes}`` for every file under ``target``."""
    out: dict[str, bytes] = {}
    for p in sorted(target.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(target))] = p.read_bytes()
    return out


def test_install_codex_creates_target(
    sandbox_home: Path, scripts_dir: Path, repo_root: Path
) -> None:
    target = sandbox_home / ".codex" / "skills" / "stackos"
    assert not target.exists()
    output = _run(scripts_dir / "install-codex.sh", sandbox_home)
    assert target.is_dir()
    expected = _expected_skill_count(repo_root)
    assert f"Installed {expected} skills" in output


def test_install_codex_skill_count_matches_repo(
    sandbox_home: Path, scripts_dir: Path, repo_root: Path
) -> None:
    _run(scripts_dir / "install-codex.sh", sandbox_home)
    target = sandbox_home / ".codex" / "skills" / "stackos"
    installed = sum(1 for _ in target.rglob("SKILL.md"))
    assert installed == _expected_skill_count(repo_root)


def test_install_codex_idempotent(sandbox_home: Path, scripts_dir: Path) -> None:
    _run(scripts_dir / "install-codex.sh", sandbox_home)
    target = sandbox_home / ".codex" / "skills" / "stackos"
    snap1 = _snapshot(target)

    _run(scripts_dir / "install-codex.sh", sandbox_home)
    snap2 = _snapshot(target)

    assert snap1 == snap2, "second run produced a different layout"


def test_install_codex_deletes_stale(sandbox_home: Path, scripts_dir: Path) -> None:
    """A file that lives only in the target (not in source) is removed on re-install."""
    _run(scripts_dir / "install-codex.sh", sandbox_home)
    target = sandbox_home / ".codex" / "skills" / "stackos"
    stale = target / "01-research" / "stale-leftover.md"
    stale.write_text("old SKILL drift\n", encoding="utf-8")

    _run(scripts_dir / "install-codex.sh", sandbox_home)
    assert not stale.exists(), "rsync --delete must remove stale files"


def test_install_codex_matches_source(
    sandbox_home: Path, scripts_dir: Path, repo_root: Path
) -> None:
    """Installed tree must mirror `skills/` byte-for-byte."""
    _run(scripts_dir / "install-codex.sh", sandbox_home)
    target = sandbox_home / ".codex" / "skills" / "stackos"
    cmp = filecmp.dircmp(str(repo_root / "skills"), str(target))
    # Allow only `.DS_Store` differences (excluded by --exclude).
    assert cmp.left_only == [] or cmp.left_only == [".DS_Store"]
    assert cmp.right_only == []
    assert cmp.diff_files == []
